"""Ausgaben aus Kassenbons: jede Bon-Zeile mit Kategorie, geplant oder spontan, Monats-Recap.

Ausgaben liegen nur in der App-DB, nie im Inventar (Snacks, Alkohol, Drogerie werden nicht
gebucht). Der Code rechnet alles; die KI liefert beim Bon-Lesen nur einen Kategorie-Vorschlag.
Kategorie-Reihenfolge: gemerkter Bon-Text > Produkt-Kategorie > KI > Sonstiges.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base, BonImport, Einstellung, PlanEintrag
from app.deps import backend, session
from app.inventory.base import InventoryBackend, Product

log = logging.getLogger("ration.ausgaben")
router = APIRouter(prefix="/api/v1")

KATEGORIEN = [
    "Gemüse", "Obst", "Fleisch & Fisch", "Milch & Eier", "Käse", "Brot & Backwaren",
    "Vorrat", "Tiefkühl", "Snacks & Süßes", "Getränke", "Alkohol", "Drogerie", "Haushalt",
    "Pfand", "Sonstiges",
]  # fmt: skip
# Produkt-Kategorie (Katalog) → Ausgaben-Kategorie
AUS_PRODUKT = {
    "Gemüse": "Gemüse", "Kräuter": "Gemüse", "Obst": "Obst", "Fleisch": "Fleisch & Fisch",
    "Fisch": "Fleisch & Fisch", "Milchprodukte": "Milch & Eier", "Eier": "Milch & Eier",
    "Käse": "Käse", "Brot": "Brot & Backwaren", "Nudeln & Reis": "Vorrat",
    "Hülsenfrüchte": "Vorrat", "Konserven": "Vorrat", "Mehl & Backen": "Vorrat",
    "Öl & Essig": "Vorrat", "Gewürze": "Vorrat", "Saucen": "Vorrat", "Nüsse & Saaten": "Vorrat",
    "Süßes": "Snacks & Süßes", "Getränke": "Getränke", "Tiefkühl": "Tiefkühl",
}  # fmt: skip
EINKAUF_VORLAUF = 7  # Einkaufsliste gilt für Bons bis zu 7 Tage danach
PLAN_NACHLAUF = 7  # Gerichte, die bis 7 Tage nach dem Einkauf geplant sind


class Ausgabe(Base):
    __tablename__ = "ausgabe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bon_id: Mapped[int] = mapped_column(Integer, index=True)
    index: Mapped[int] = mapped_column(Integer)  # Zeile im Bon
    datum: Mapped[date] = mapped_column(Date, index=True)
    markt: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(String)
    betrag: Mapped[float] = mapped_column(Float)  # nach Rabatt/Storno; Leergut negativ
    typ: Mapped[str] = mapped_column(String)  # artikel/non_food/pfand/leergut
    kategorie: Mapped[str] = mapped_column(String)
    produkt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    geplant: Mapped[bool] = mapped_column(Boolean, default=False)


class KategorieAlias(Base):
    """Bon-Text → Ausgaben-Kategorie, gemerkt aus dem Review."""

    __tablename__ = "ausgabe_kategorie_alias"

    text: Mapped[str] = mapped_column(String, primary_key=True)  # normalisiert
    kategorie: Mapped[str] = mapped_column(String)


class EinkaufEintrag(Base):
    """Was die Einkaufsliste geschrieben hat (Grundlage für „geplant“)."""

    __tablename__ = "einkauf_eintrag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datum: Mapped[date] = mapped_column(Date, index=True)
    produkt_id: Mapped[str] = mapped_column(String, index=True)
    angelegt: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def kategorie_fuer(
    text_norm: str,
    typ: str,
    produkt: Product | None,
    ki: str | None,
    aliase: dict[str, str],
) -> str:
    if typ in ("pfand", "leergut"):
        return "Pfand"
    if text_norm in aliase:
        return aliase[text_norm]
    if produkt and produkt.category in AUS_PRODUKT:
        return AUS_PRODUKT[produkt.category]
    if ki in KATEGORIEN:
        return ki  # type: ignore[return-value]
    return "Drogerie" if typ == "non_food" else "Sonstiges"


def einkauf_merken(s: Session, produkt_ids: list[str], datum: date | None = None) -> None:
    datum = datum or date.today()
    for pid in set(produkt_ids):
        s.add(EinkaufEintrag(datum=datum, produkt_id=pid))


async def geplante_produkte(s: Session, b: InventoryBackend, datum: date) -> set[str]:
    """Produkte, die zum Einkaufsdatum geplant waren: Einkaufsliste der Tage davor oder Zutat
    eines Gerichts, das in den Tagen danach im Plan steht (Varianten zählen für das Elternteil)."""
    ids = set(
        s.scalars(
            select(EinkaufEintrag.produkt_id).where(
                EinkaufEintrag.datum >= datum - timedelta(days=EINKAUF_VORLAUF),
                EinkaufEintrag.datum <= datum,
            )
        )
    )
    for e in s.scalars(
        select(PlanEintrag).where(
            PlanEintrag.datum >= datum, PlanEintrag.datum <= datum + timedelta(days=PLAN_NACHLAUF)
        )
    ):
        try:
            r = await b.get_recipe(e.rezept_id)
        except Exception:  # noqa: BLE001 - gelöschtes Rezept
            continue
        ids |= {z.product_id for z in r.ingredients}
    return ids


async def aus_bon(
    s: Session,
    b: InventoryBackend,
    bon: BonImport,
    zuordnung: dict[int, str],
    kategorien: dict[int, str] | None = None,
) -> int:
    """Ausgaben eines Bons (neu) anlegen. zuordnung: Zeile → gebuchtes Produkt. kategorien:
    im Review gewählte Kategorie je Zeile, wird für den Bon-Text gemerkt."""
    from app.bon import norm_text  # zyklischer Import vermeiden

    kategorien = kategorien or {}
    s.execute(delete(Ausgabe).where(Ausgabe.bon_id == bon.id))
    produkte = {p.id: p for p in await b.list_products()}
    eltern = {p.id: p.parent_id for p in produkte.values()}
    aliase = {a.text: a.kategorie for a in s.scalars(select(KategorieAlias))}
    datum = bon.datum or date.today()
    geplant_ids = await geplante_produkte(s, b, datum)
    n = 0
    for pos in bon.positionen or []:
        typ = pos.get("typ")
        if typ not in ("artikel", "non_food", "pfand", "leergut") or pos.get("storniert"):
            continue
        betrag = pos.get("betrag")
        if betrag is None or abs(betrag) < 0.005:
            continue
        idx = pos["index"]
        pid = zuordnung.get(idx) or pos.get("produkt_id")
        produkt = produkte.get(pid) if pid else None
        tn = norm_text(pos["text"])
        if (wahl := kategorien.get(idx)) in KATEGORIEN:
            s.merge(KategorieAlias(text=tn, kategorie=wahl))
            aliase[tn] = wahl  # type: ignore[assignment]
        kat = kategorie_fuer(tn, typ, produkt, pos.get("kategorie"), aliase)
        s.add(
            Ausgabe(
                bon_id=bon.id,
                index=idx,
                datum=datum,
                markt=bon.markt,
                text=pos["text"],
                betrag=round(float(betrag), 2),
                typ=typ,
                kategorie=kat,
                produkt_id=produkt.id if produkt else None,
                geplant=bool(produkt and ({produkt.id, eltern.get(produkt.id)} & geplant_ids)),
            )
        )
        n += 1
    return n


def _monat(m: str | None) -> tuple[date, date]:
    try:
        start = date.fromisoformat((m or date.today().isoformat()[:7]) + "-01")
    except ValueError as err:
        raise HTTPException(422, "monat als JJJJ-MM") from err
    ende = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, ende


def recap(s: Session, monat: str | None = None) -> dict:
    start, ende = _monat(monat)
    zeilen = list(s.scalars(select(Ausgabe).where(Ausgabe.datum >= start, Ausgabe.datum < ende)))
    vor_start, _ = _monat((start - timedelta(days=1)).isoformat()[:7])
    vormonat = s.scalar(
        select(func.sum(Ausgabe.betrag)).where(Ausgabe.datum >= vor_start, Ausgabe.datum < start)
    )
    je_kat: dict[str, float] = defaultdict(float)
    je_markt: dict[str, float] = defaultdict(float)
    geplant = spontan = pfand = 0.0
    for z in zeilen:
        je_kat[z.kategorie] += z.betrag
        je_markt[z.markt or "unbekannt"] += z.betrag
        if z.typ in ("pfand", "leergut"):
            pfand += z.betrag
        elif z.geplant:
            geplant += z.betrag
        else:
            spontan += z.betrag
    summe = sum(z.betrag for z in zeilen)
    verlauf = []
    for i in range(5, -1, -1):
        m_start = start
        for _ in range(i):
            m_start = (m_start - timedelta(days=1)).replace(day=1)
        m_ende = (m_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        wert = s.scalar(
            select(func.sum(Ausgabe.betrag)).where(Ausgabe.datum >= m_start, Ausgabe.datum < m_ende)
        )
        verlauf.append({"monat": m_start.isoformat()[:7], "summe": round(wert or 0.0, 2)})
    r2 = lambda x: round(x, 2)  # noqa: E731
    return {
        "monat": start.isoformat()[:7],
        "summe": r2(summe),
        "vormonat": r2(vormonat or 0.0),
        "bons": len({z.bon_id for z in zeilen}),
        "geplant": r2(geplant),
        "spontan": r2(spontan),
        "pfand": r2(pfand),
        "kategorien": sorted(
            ({"kategorie": k, "summe": r2(v)} for k, v in je_kat.items()), key=lambda x: -x["summe"]
        ),
        "maerkte": sorted(
            ({"markt": k, "summe": r2(v)} for k, v in je_markt.items()), key=lambda x: -x["summe"]
        ),
        "top_spontan": [
            {"text": z.text, "betrag": z.betrag, "kategorie": z.kategorie, "datum": z.datum}
            for z in sorted(
                (z for z in zeilen if not z.geplant and z.typ not in ("pfand", "leergut")),
                key=lambda z: -z.betrag,
            )[:8]
        ],
        "verlauf": verlauf,
    }


def recap_text(r: dict) -> str:
    diff = r["summe"] - r["vormonat"]
    teile = [f"{k['kategorie']} {k['summe']:.2f} €" for k in r["kategorien"][:4]]
    text = (
        f"{r['summe']:.2f} € in {r['bons']} Einkäufen"
        + (f" ({'+' if diff >= 0 else ''}{diff:.2f} € zum Vormonat)" if r["vormonat"] else "")
        + f". Geplant {r['geplant']:.2f} €, spontan {r['spontan']:.2f} €. "
        + ", ".join(teile)
    )
    return text.replace(".", ",")


async def recap_schleife(app) -> None:
    """Am Monatsanfang einmal den Recap des Vormonats als HA-Hinweis schicken."""
    while True:
        try:
            heute = date.today()
            vormonat = (heute.replace(day=1) - timedelta(days=1)).isoformat()[:7]
            with Session(app.state.engine) as s:
                gesendet = s.get(Einstellung, "ausgaben_recap")
                if (gesendet.wert if gesendet else None) != vormonat and app.state.ha is not None:
                    r = recap(s, vormonat)
                    if r["bons"]:
                        await app.state.ha.notify(
                            f"Ausgaben {vormonat}", recap_text(r), app.state.settings.notify_service
                        )
                    s.merge(Einstellung(schluessel="ausgaben_recap", wert=vormonat))
                    s.commit()
        except Exception:  # noqa: BLE001 - nächster Versuch in einer Stunde
            log.exception("Ausgaben-Recap fehlgeschlagen")
        await asyncio.sleep(3600)


# --- Endpunkte ------------------------------------------------------------------------------


@router.get("/ausgaben")
async def ausgaben(monat: str | None = None, s: Session = Depends(session)) -> dict:
    return recap(s, monat)


@router.get("/ausgaben/zeilen")
async def ausgaben_zeilen(monat: str | None = None, s: Session = Depends(session)) -> list[dict]:
    start, ende = _monat(monat)
    return [
        {
            "id": z.id,
            "bon_id": z.bon_id,
            "datum": z.datum,
            "markt": z.markt,
            "text": z.text,
            "betrag": z.betrag,
            "typ": z.typ,
            "kategorie": z.kategorie,
            "geplant": z.geplant,
        }  # fmt: skip
        for z in s.scalars(
            select(Ausgabe)
            .where(Ausgabe.datum >= start, Ausgabe.datum < ende)
            .order_by(Ausgabe.datum.desc(), Ausgabe.bon_id, Ausgabe.index)
        )
    ]


class ZeileIn(BaseModel):
    kategorie: str | None = None
    geplant: bool | None = None


@router.patch("/ausgaben/{ausgabe_id}")
async def ausgabe_aendern(ausgabe_id: int, data: ZeileIn, s: Session = Depends(session)) -> dict:
    """Kategorie korrigieren (wird für den Bon-Text gemerkt) oder geplant/spontan umstellen."""
    from app.bon import norm_text

    z = s.get(Ausgabe, ausgabe_id)
    if z is None:
        raise HTTPException(404, "Ausgabe nicht gefunden")
    if data.kategorie is not None:
        if data.kategorie not in KATEGORIEN:
            raise HTTPException(422, f"Kategorie: {', '.join(KATEGORIEN)}")
        z.kategorie = data.kategorie
        s.merge(KategorieAlias(text=norm_text(z.text), kategorie=data.kategorie))
    if data.geplant is not None:
        z.geplant = data.geplant
    s.commit()
    return {"id": z.id, "kategorie": z.kategorie, "geplant": z.geplant}


@router.get("/ausgaben/kategorien")
async def kategorien() -> list[str]:
    return KATEGORIEN


@router.post("/ausgaben/neu-berechnen")
async def neu_berechnen(
    s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Ausgaben aller gebuchten Bons neu aufbauen (z. B. nach dem Update für alte Bons)."""
    n = 0
    for bon in s.scalars(select(BonImport).where(BonImport.status == "gebucht")):
        zuordnung = {
            p["index"]: p["gebucht_produkt_id"]
            for p in bon.positionen or []
            if p.get("gebucht_produkt_id")
        }
        n += await aus_bon(s, b, bon, zuordnung)
    s.commit()
    return {"zeilen": n}
