"""Rezept-Import-Vorschau als Hintergrundaufgabe mit Fortschritt.

Mit einem Reasoning-Modell dauert die KI-Zuordnung 30–60 s. `POST /import/start` legt den Vorgang
an und rechnet im Hintergrund; `GET /import/{id}` liefert Schritt, Fortschritt und Restzeit, am
Ende die fertige Vorschau (dieselbe Form wie `POST /import/vorschau`). Den Fortschritt der KI kennt
niemand: geschätzt wird aus dem Median der letzten echten Laufzeiten im KI-Protokoll.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm_import, rezept_import
from app.db import RezeptImport, RezeptMeta, ZutatAlias
from app.deps import session
from app.protokoll import KiAnfrage

log = logging.getLogger("ration.import")
router = APIRouter(prefix="/api/v1")

STANDARD_DAUER = 30.0  # s, solange es keine Messwerte gibt
VERALTET = timedelta(minutes=15)  # läuft länger: App wurde neu gestartet, Aufgabe ist weg
_AUFGABEN: set[asyncio.Task] = set()  # Referenzen halten, sonst sammelt der GC laufende Tasks ein


class ImportIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    html: str | None = None  # Fallback: Seitenquelltext, falls die Seite den Abruf blockt
    ki: bool = True  # offene Zutaten per KI zuordnen, falls für den Job eingerichtet


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # wie SQLite CURRENT_TIMESTAMP (UTC, naiv)


def erwartete_dauer(s: Session) -> float:
    """Median der letzten fünf erfolgreichen Import-Aufrufe der KI."""
    werte = [
        d
        for d in s.scalars(
            select(KiAnfrage.dauer_s)
            .where(KiAnfrage.job == "import", KiAnfrage.fehler.is_(None))
            .order_by(KiAnfrage.id.desc())
            .limit(5)
        )
        if d
    ]
    return round(statistics.median(werte), 1) if werte else STANDARD_DAUER


async def vorschau_berechnen(
    app, s: Session, data: ImportIn, melden: Callable[..., None] = lambda *a: None
) -> tuple[rezept_import.RohRezept, dict]:
    """Rezept lesen, Code-Zuordnung, KI-Schritt. Gibt (Rohrezept, Vorschau ohne import_id)."""
    melden("Seite lesen")
    roh = await rezept_import.rezept_laden(data.url, data.html)
    melden("Zuordnen")
    aliase = {a.text: (a.produkt_id, a.quelle or "nutzer") for a in s.scalars(select(ZutatAlias))}
    produkte = await app.state.backend.list_products()
    zuordnung = rezept_import.zuordnen(roh, produkte, aliase)
    ki_ = llm_import.KiErgebnis(aktiv=False)
    if data.ki and app.state.ki.verfuegbar("import"):
        if anzahl := len(llm_import.offene(zuordnung)):
            melden("KI", anzahl)
        ki_ = await llm_import.verfeinern(app.state.ki, zuordnung, produkte)
    dublette = s.scalars(select(RezeptMeta).where(RezeptMeta.quelle_id == roh.quelle_id)).first()
    return roh, {
        "status": "fertig",
        "name": roh.name,
        "portionen": roh.portionen,
        "quelle": roh.quelle,
        "quelle_id": roh.quelle_id,
        "url": roh.url,
        "dublette": dublette.rezept_id if dublette else None,
        "schritte": roh.schritte,
        "bild": roh.bild,
        "schritt_bilder": len(roh.schritt_bilder),
        "zutaten": [asdict(z) for z in zuordnung],
        "ki": asdict(ki_),
    }


def status(e: RezeptImport) -> dict:
    """Fertige Vorschau oder Zwischenstand mit Fortschritt (0–100) und geschätzter Restzeit."""
    if e.vorschau is not None and e.status in (None, "fertig"):
        return {**e.vorschau, "status": "fertig"}
    jetzt = _jetzt()
    zustand, fehler = e.status or "fehler", e.fehler
    if zustand == "laeuft" and e.angelegt and jetzt - e.angelegt > VERALTET:
        zustand, fehler = "fehler", "Abgebrochen (App neu gestartet?)"
    fortschritt, rest = {"Seite lesen": 5, "Zuordnen": 12}.get(e.schritt or "", 15), None
    if e.schritt == "KI" and e.ki_start:
        dauer = e.erwartet_s or STANDARD_DAUER
        vergangen = (jetzt - e.ki_start).total_seconds()
        fortschritt = 15 + min(80.0, 80 * vergangen / dauer)  # bleibt bei 95 %, bis fertig
        rest = max(0, round(dauer - vergangen))
    return {
        "import_id": e.id,
        "status": zustand,
        "schritt": e.schritt,
        "anzahl_ki": e.anzahl_ki,
        "fortschritt": round(fortschritt),
        "rest_s": rest,
        "fehler": fehler,
        "url": e.url,
    }


def _fehler(engine, eintrag_id: int, text: str) -> None:
    with Session(engine) as s:
        if e := s.get(RezeptImport, eintrag_id):
            e.status, e.fehler = "fehler", text[:500]
            s.commit()


async def _lauf(app, eintrag_id: int, data: ImportIn) -> None:
    engine = app.state.engine

    def melden(schritt: str, anzahl: int | None = None) -> None:
        with Session(engine) as s:
            e = s.get(RezeptImport, eintrag_id)
            if e is None:
                return
            e.schritt = schritt
            if schritt == "KI":
                e.ki_start, e.anzahl_ki, e.erwartet_s = _jetzt(), anzahl, erwartete_dauer(s)
            s.commit()

    try:
        with Session(engine) as s:
            roh, antwort = await vorschau_berechnen(app, s, data, melden)
        with Session(engine) as s:
            e = s.get(RezeptImport, eintrag_id)
            if e is None:
                return
            e.url, e.quelle_id, e.roh = roh.url, roh.quelle_id, asdict(roh)
            e.vorschau = {"import_id": e.id, **antwort}
            e.status, e.schritt = "fertig", "fertig"
            s.commit()
        log.info("Import-Vorschau %s im Hintergrund fertig: %s", eintrag_id, roh.name)
    except rezept_import.ImportError_ as err:
        _fehler(engine, eintrag_id, str(err))
    except Exception as err:  # noqa: BLE001 - Fehler landet beim Nutzer, nicht im Nirwana
        log.exception("Import-Vorschau %s fehlgeschlagen", eintrag_id)
        _fehler(engine, eintrag_id, f"{type(err).__name__}: {err}")


@router.post("/import/start", status_code=202)
async def import_start(data: ImportIn, request: Request, s: Session = Depends(session)) -> dict:
    """Vorschau im Hintergrund starten; Stand per GET /import/{id} abfragen."""
    e = RezeptImport(url=data.url.strip(), quelle_id="", roh={}, status="laeuft", schritt="Start")
    s.add(e)
    s.commit()
    aufgabe = asyncio.create_task(_lauf(request.app, e.id, data))
    _AUFGABEN.add(aufgabe)
    aufgabe.add_done_callback(_AUFGABEN.discard)
    return status(e)


@router.get("/import")
async def importe(s: Session = Depends(session)) -> list[dict]:
    """Die letzten Import-Vorgänge (neueste zuerst), z. B. um eine Vorschau wieder zu öffnen."""
    eintraege = s.scalars(select(RezeptImport).order_by(RezeptImport.id.desc()).limit(20))
    return [
        {"import_id": e.id, "url": e.url, "name": (e.roh or {}).get("name"),
         "status": status(e)["status"], "rezept_id": e.rezept_id,
         "angelegt": e.angelegt.isoformat() if e.angelegt else None}
        for e in eintraege
    ]  # fmt: skip


@router.get("/import/{import_id}")
async def import_lesen(import_id: int, s: Session = Depends(session)) -> dict:
    """Stand eines Imports: Zwischenstand oder fertige Vorschau (wie /import/vorschau)."""
    e = s.get(RezeptImport, import_id)
    if e is None or (e.status is None and e.vorschau is None):
        raise HTTPException(404, "Import nicht gefunden")
    return status(e)
