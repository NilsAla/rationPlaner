"""Startseite und HA-Sensoren: Gerichte der nächsten Tage, Vorrat, Reste, Verschieben."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import einstellungen, fotos, kueche, naehrwerte
from app.db import PlanEintrag
from app.deps import backend, session
from app.inventory.base import InventoryBackend, Product, Recipe, TrackingMode
from app.katalog import bestand_je_produkt, verfuegbar

GEWICHT_UNBEKANNT = 100.0  # g, wenn eine Zutat keine Gramm-Umrechnung hat
STUECK = {"stück", "stk", "st"}

router = APIRouter(prefix="/api/v1")


def gericht(
    e: PlanEintrag,
    r: Recipe | None,
    produkte: dict[str, Product],
    bestand: dict[str, float],
    nw: dict,
    ziel: dict,
    bilder: dict[str, int],
) -> dict:
    """Ein Planeintrag mit Zutaten (auf die geplanten Portionen skaliert) und Ampel."""
    basis = {"datum": e.datum, "rezept_id": e.rezept_id, "portionen": e.portionen}
    if r is None:  # in Grocy gelöscht
        return {**basis, "name": "(Rezept fehlt)", "status": e.status, "zutaten": [], "fehlend": []}
    faktor = e.portionen / max(r.servings, 1)
    zutaten = []
    for z in r.ingredients:
        p = produkte.get(z.product_id)
        if p is None:
            continue
        menge = z.amount * faktor
        zutaten.append(
            {
                "produkt_id": p.id,
                "name": p.name,
                "menge": round(menge, 1),
                "einheit": p.unit,
                "ampel": verfuegbar(p, bestand.get(p.id, 0), menge),
            }
        )
    n = naehrwerte.rezept_naehrwerte(r, produkte, nw, ziel=ziel)
    fehlend = [z["name"] for z in zutaten if z["ampel"] != "ok"]
    return {
        **basis,
        "name": r.name,
        "status": e.status,
        "foto": bilder.get(r.id),
        "kcal": n.je_portion["kcal"],
        "protein": n.je_portion["protein"],
        "nutri": n.nutri_score["klasse"] if n.nutri_score else None,
        "aufwand": r.effort,
        "zutaten": zutaten,
        "fehlend": fehlend,
        "kochbar": not fehlend and bool(zutaten),
    }


@router.get("/uebersicht")
async def uebersicht(
    tage: int = 7, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Alles für Startseite und Sensoren in einem Aufruf: Plan ab heute, Vorrat, Reste."""
    heute = date.today()
    tage = max(2, min(tage, 14))
    bald = einstellungen.lesen(s)["anzeige"]["bald_tage"]
    produkte = {p.id: p for p in await b.list_products()}
    rezepte = {r.id: r for r in await b.list_recipes()}
    bestand = await bestand_je_produkt(b, list(produkte.values()))
    nw, ziel, bilder = naehrwerte.alle(s), naehrwerte.ziel_aus(s), fotos.hauptfotos(s)
    plan = {
        e.datum: e
        for e in s.scalars(
            select(PlanEintrag).where(
                PlanEintrag.datum >= heute, PlanEintrag.datum < heute + timedelta(days=tage)
            )
        )
    }
    tage_liste = []
    for i in range(tage):
        d = heute + timedelta(days=i)
        e = plan.get(d)
        g = gericht(e, rezepte.get(e.rezept_id), produkte, bestand, nw, ziel, bilder) if e else None
        tage_liste.append({"datum": d, "gericht": g})
    st = await kueche.status(b, bald, heute)
    return {
        "datum": heute,
        "heute": tage_liste[0]["gericht"],
        "morgen": tage_liste[1]["gericht"],
        "tage": tage_liste,
        "bald_tage": bald,
        "laeuft_ab": sorted(st.laeuft_bald_ab, key=lambda x: x["ablauf"]),
        "reste": st.reste,
        "artikel": st.artikel,
        "status_text": st.text,
    }


class VerschiebenIn(BaseModel):
    ziel: date


@router.post("/plan/{datum}/verschieben")
async def plan_verschieben(datum: date, data: VerschiebenIn, s: Session = Depends(session)) -> dict:
    """Gericht auf einen anderen Tag legen. Ist das Ziel belegt, werden beide getauscht."""
    quelle = s.get(PlanEintrag, datum)
    if quelle is None:
        raise HTTPException(404, f"Am {datum} ist nichts geplant")
    if data.ziel == datum:
        return {"verschoben": [], "getauscht": False}
    ziel = s.get(PlanEintrag, data.ziel)
    a = (quelle.rezept_id, quelle.portionen, quelle.status)
    if ziel is None:
        s.delete(quelle)
        s.flush()
        s.add(PlanEintrag(datum=data.ziel, rezept_id=a[0], portionen=a[1], status=a[2]))
    else:
        quelle.rezept_id, quelle.portionen, quelle.status = (
            ziel.rezept_id,
            ziel.portionen,
            ziel.status,
        )
        ziel.rezept_id, ziel.portionen, ziel.status = a
    s.commit()
    return {"verschoben": [datum, data.ziel], "getauscht": ziel is not None}


# --- Spontan kochbar ----------------------------------------------------------------------


def abdeckung(
    r: Recipe,
    produkte: dict[str, Product],
    bestand: dict[str, float],
    faktor: float,
) -> tuple[float, list[dict]]:
    """Anteil der Zutaten (nach Gramm gewichtet), der vorrätig ist, und was fehlt.
    Basics (immer da, Füllstand) zählen nicht."""
    gesamt = da = 0.0
    fehlt = []
    for z in r.ingredients:
        p = produkte.get(z.product_id)
        if p is None or p.tracking_mode in (TrackingMode.IMMER_DA, TrackingMode.FUELLSTAND):
            continue
        bedarf = z.amount * faktor
        g_je = (naehrwerte.gramm(p, bedarf) or GEWICHT_UNBEKANNT) / bedarf if bedarf else 0.0
        vorhanden = min(bestand.get(p.id, 0.0), bedarf)
        gesamt += bedarf * g_je
        da += vorhanden * g_je
        if bedarf - vorhanden > kueche.EPS:
            rest = bedarf - vorhanden
            stueck = p.unit.strip().lower() in STUECK
            menge = kueche.menge(rest) + ("" if stueck else f" {p.unit}")
            fehlt.append({"produkt_id": p.id, "name": p.name, "menge": round(rest, 1),
                          "einheit": p.unit, "text": f"{menge} {p.name}"})  # fmt: skip
    return (da / gesamt if gesamt else 0.0), fehlt


@router.get("/spontan")
async def spontan(
    limit: int = 6, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Rezepte, die (fast) komplett vorrätig sind: nach Abdeckung, dann nach bald ablaufenden
    Zutaten. Schwelle aus Einstellung anzeige.spontan_pct."""
    einst = einstellungen.lesen(s)
    schwelle = einst["anzeige"]["spontan_pct"] / 100
    bald = einst["anzeige"]["bald_tage"]
    portionen = einst["planer"]["portionen"]
    heute = date.today()
    produkte = {p.id: p for p in await b.list_products()}
    batches = await b.list_all_batches()
    bestand = await bestand_je_produkt(b, list(produkte.values()))
    eltern = {p.id: p.parent_id for p in produkte.values()}
    druck: dict[str, float] = {}  # Produkt → Dringlichkeit bald ablaufender Chargen
    for x in batches:
        if x.best_before and (tage := (x.best_before - heute).days) <= bald:
            for pid in (x.product_id, eltern.get(x.product_id)):
                if pid:
                    druck[pid] = druck.get(pid, 0.0) + 1 / (max(tage, 0) + 1)
    geplant = s.get(PlanEintrag, heute)
    nw, ziel, bilder = naehrwerte.alle(s), naehrwerte.ziel_aus(s), fotos.hauptfotos(s)
    treffer = []
    for r in await b.list_recipes():
        if not r.ingredients or (geplant and geplant.rezept_id == r.id):
            continue
        anteil, fehlt = abdeckung(r, produkte, bestand, portionen / max(r.servings, 1))
        if anteil + 1e-9 < schwelle:
            continue
        n = naehrwerte.rezept_naehrwerte(r, produkte, nw, ziel=ziel)
        treffer.append(
            {
                "rezept_id": r.id,
                "name": r.name,
                "portionen": portionen,
                "abdeckung": round(anteil, 3),
                "fehlt": fehlt,
                "dringend": round(sum(druck.get(z.product_id, 0.0) for z in r.ingredients), 3),
                "foto": bilder.get(r.id),
                "kcal": n.je_portion["kcal"],
                "nutri": n.nutri_score["klasse"] if n.nutri_score else None,
                "aufwand": r.effort,
            }
        )
    treffer.sort(key=lambda x: (-x["abdeckung"], -x["dringend"], x["name"].lower()))
    return {
        "schwelle_pct": einst["anzeige"]["spontan_pct"],
        "heute": {"rezept_id": geplant.rezept_id, "status": geplant.status} if geplant else None,
        "rezepte": treffer[: max(1, min(limit, 12))],
    }


class SpontanIn(BaseModel):
    rezept_id: str
    portionen: int | None = None


@router.post("/plan/spontan")
async def statt_heute(
    data: SpontanIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """„Statt heute“: Rezept auf heute, das bisher geplante Gericht auf den nächsten freien Tag."""
    await b.get_recipe(data.rezept_id)
    heute = date.today()
    portionen = data.portionen or einstellungen.lesen(s)["planer"]["portionen"]
    alt = s.get(PlanEintrag, heute)
    verschoben = None
    if alt is not None and alt.status != "gekocht":
        belegt = set(
            s.scalars(
                select(PlanEintrag.datum).where(
                    PlanEintrag.datum > heute, PlanEintrag.datum <= heute + timedelta(days=60)
                )
            )
        )
        ziel = next(d for i in range(1, 62) if (d := heute + timedelta(days=i)) not in belegt)
        s.add(PlanEintrag(datum=ziel, rezept_id=alt.rezept_id, portionen=alt.portionen))
        verschoben = {"rezept_id": alt.rezept_id, "datum": ziel}
    eintrag = alt or PlanEintrag(datum=heute)
    eintrag.rezept_id, eintrag.portionen, eintrag.status = data.rezept_id, portionen, "geplant"
    s.add(eintrag)
    s.commit()
    return {"datum": heute, "rezept_id": data.rezept_id, "verschoben": verschoben}
