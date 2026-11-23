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
from app.inventory.base import InventoryBackend, Product, Recipe
from app.katalog import bestand_je_produkt, verfuegbar

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
