"""Protokolle: KI-Anfragen (ai_task) und Änderungen über die API (UI, Bridge, MCP).

Beides liegt in der App-DB, wird beim Schreiben auf eine feste Länge gekürzt und ist in der
Web-App unter System einsehbar. Geheimnisse (Token, Passwörter) werden nie gespeichert.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.deps import session

log = logging.getLogger("ration.protokoll")
router = APIRouter(prefix="/api/v1")

MAX_KI = 500
MAX_AENDERUNGEN = 3000
MAX_BODY = 8000
GEHEIM = {"api_token", "token", "password", "passwort", "grocy_api_key", "supervisor_token"}
SCHREIBEND = {"POST", "PUT", "PATCH", "DELETE"}
LESEND_POST = {"/api/v1/import/vorschau"}  # POST ohne Änderung an Grocy, trotzdem protokolliert


class KiAnfrage(Base):
    __tablename__ = "ki_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zeit: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    aufgabe: Mapped[str] = mapped_column(String)
    entitaet: Mapped[str] = mapped_column(String)
    anweisung: Mapped[str] = mapped_column(Text)
    struktur: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    anhaenge: Mapped[list | None] = mapped_column(JSON, nullable=True)
    antwort: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fehler: Mapped[str | None] = mapped_column(String, nullable=True)
    dauer_s: Mapped[float] = mapped_column(Float, default=0)


class Aenderung(Base):
    __tablename__ = "aenderung_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zeit: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    quelle: Mapped[str] = mapped_column(String)  # ui / bridge / extern
    methode: Mapped[str] = mapped_column(String)
    pfad: Mapped[str] = mapped_column(String, index=True)
    daten: Mapped[object | None] = mapped_column(JSON, nullable=True)
    status: Mapped[int] = mapped_column(Integer)
    dauer_s: Mapped[float] = mapped_column(Float, default=0)


def _schwaerzen(wert: object) -> object:
    if isinstance(wert, dict):
        return {k: ("***" if k.lower() in GEHEIM else _schwaerzen(v)) for k, v in wert.items()}
    if isinstance(wert, list):
        return [_schwaerzen(v) for v in wert]
    return wert


def _kuerzen(n: int, tabelle, s: Session) -> None:
    grenze = s.scalars(select(tabelle.id).order_by(tabelle.id.desc()).offset(n).limit(1)).first()
    if grenze is not None:
        s.execute(delete(tabelle).where(tabelle.id <= grenze))


class ProtokollHA:
    """Hüllt einen HomeAssistant-Client: jede ai_task-Anfrage landet im KI-Log."""

    def __init__(self, ha, engine):
        self._ha = ha
        self._engine = engine

    def __getattr__(self, name):
        return getattr(self._ha, name)

    async def ai_task(self, entity_id, task_name, instructions, structure, attachments=None):
        start = time.monotonic()
        antwort, fehler = None, None
        try:
            antwort = await self._ha.ai_task(
                entity_id, task_name, instructions, structure, attachments
            )
            return antwort
        except Exception as err:
            fehler = f"{type(err).__name__}: {err}"
            raise
        finally:
            try:
                with Session(self._engine) as s:
                    s.add(
                        KiAnfrage(
                            aufgabe=task_name,
                            entitaet=entity_id,
                            anweisung=instructions,
                            struktur=structure,
                            anhaenge=attachments,
                            antwort=antwort if isinstance(antwort, dict) else None,
                            fehler=fehler,
                            dauer_s=round(time.monotonic() - start, 2),
                        )
                    )
                    _kuerzen(MAX_KI, KiAnfrage, s)
                    s.commit()
            except Exception:  # noqa: BLE001 - Protokoll darf die Anfrage nie stören
                log.exception("KI-Log fehlgeschlagen")


def quelle_von(request: Request, ingress_ip: str) -> str:
    kopf = request.headers.get("x-ration-client", "").lower()
    if kopf in ("ui", "bridge"):
        return kopf
    return "ui" if request.client and request.client.host == ingress_ip else "extern"


async def aenderung_protokollieren(request: Request, call_next, ingress_ip: str):
    pfad = request.url.path
    if request.method not in SCHREIBEND or not pfad.startswith("/api/v1/"):
        return await call_next(request)
    daten: object = None
    if request.headers.get("content-type", "").startswith("application/json"):
        roh = await request.body()
        if roh:
            try:
                daten = _schwaerzen(json.loads(roh))
            except ValueError:
                daten = None
            if daten is not None and len(json.dumps(daten, default=str)) > MAX_BODY:
                daten = {"gekuerzt": True, "bytes": len(roh)}
    elif request.headers.get("content-type", "").startswith("multipart/"):
        daten = {"upload": True}
    start = time.monotonic()
    antwort = await call_next(request)
    try:
        with Session(request.app.state.engine) as s:
            s.add(
                Aenderung(
                    quelle=quelle_von(request, ingress_ip),
                    methode=request.method,
                    pfad=pfad,
                    daten=daten,
                    status=antwort.status_code,
                    dauer_s=round(time.monotonic() - start, 3),
                )
            )
            _kuerzen(MAX_AENDERUNGEN, Aenderung, s)
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("Änderungs-Log fehlgeschlagen")
    return antwort


# --- Endpunkte ---------------------------------------------------------------------------


@router.get("/log/ki")
async def ki_log(limit: int = 100, s: Session = Depends(session)) -> list[dict]:
    rows = s.scalars(select(KiAnfrage).order_by(KiAnfrage.id.desc()).limit(min(limit, 500)))
    return [
        {
            "id": r.id,
            "zeit": r.zeit,
            "aufgabe": r.aufgabe,
            "entitaet": r.entitaet,
            "dauer_s": r.dauer_s,
            "fehler": r.fehler,
            "anhaenge": len(r.anhaenge or []),
            "antwort_bytes": len(json.dumps(r.antwort, ensure_ascii=False)) if r.antwort else 0,
        }
        for r in rows
    ]


@router.get("/log/ki/{eintrag_id}")
async def ki_log_eintrag(eintrag_id: int, s: Session = Depends(session)) -> dict:
    r = s.get(KiAnfrage, eintrag_id)
    if r is None:
        raise HTTPException(404, "Eintrag nicht gefunden")
    return {
        "id": r.id,
        "zeit": r.zeit,
        "aufgabe": r.aufgabe,
        "entitaet": r.entitaet,
        "anweisung": r.anweisung,
        "struktur": r.struktur,
        "anhaenge": r.anhaenge,
        "antwort": r.antwort,
        "fehler": r.fehler,
        "dauer_s": r.dauer_s,
    }


@router.get("/log/aenderungen")
async def aenderungen(
    limit: int = 200, pfad: str = "", quelle: str = "", s: Session = Depends(session)
) -> list[dict]:
    q = select(Aenderung).order_by(Aenderung.id.desc()).limit(min(limit, 1000))
    if pfad:
        q = q.where(Aenderung.pfad.contains(pfad))
    if quelle:
        q = q.where(Aenderung.quelle == quelle)
    return [
        {
            "id": r.id,
            "zeit": r.zeit,
            "quelle": r.quelle,
            "methode": r.methode,
            "pfad": r.pfad,
            "daten": r.daten,
            "status": r.status,
            "dauer_s": r.dauer_s,
        }
        for r in s.scalars(q)
    ]
