"""Inventar: Chargen einzeln sehen, bearbeiten und in Massen abbuchen oder umlagern."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import backend
from app.inventory.base import (
    BatchPatch,
    InventoryBackend,
)

router = APIRouter(prefix="/api/v1")


@router.get("/inventar")
async def inventar(b: InventoryBackend = Depends(backend)) -> list[dict]:
    """Alle Chargen mit Produkt, Lagerort und Tagen bis zum Ablauf."""
    produkte = {p.id: p for p in await b.list_products()}
    orte = {o.id: o for o in await b.list_locations()}
    heute = date.today()
    out = []
    for c in await b.list_all_batches():
        p = produkte.get(c.product_id)
        ort = orte.get(c.location_id)
        out.append(
            {
                "id": c.id,
                "produkt_id": c.product_id,
                "name": p.name if p else c.product_id,
                "einheit": p.unit if p else "",
                "kategorie": p.category if p else None,
                "tracking_modus": p.tracking_mode if p else None,
                "parent_id": p.parent_id if p else None,
                "menge": c.amount,
                "ablauf": c.best_before,
                "tage": (c.best_before - heute).days if c.best_before else None,
                "lagerort_id": c.location_id,
                "lagerort": ort.name if ort else c.location_id,
                "gefroren": bool(ort and ort.is_freezer),
                "geoeffnet": c.opened,
            }
        )
    return sorted(out, key=lambda x: (x["ablauf"] is None, x["ablauf"] or date.max, x["name"]))


class ChargeIn(BaseModel):
    menge: float | None = Field(default=None, gt=0)
    ablauf: date | None = None
    ohne_ablauf: bool = False
    lagerort_id: str | None = None
    geoeffnet: bool | None = None


@router.patch("/inventar/{charge_id}")
async def charge_aendern(
    charge_id: str, data: ChargeIn, b: InventoryBackend = Depends(backend)
) -> dict:
    if data.lagerort_id and data.lagerort_id not in {o.id for o in await b.list_locations()}:
        raise HTTPException(404, f"Lagerort {data.lagerort_id} gibt es nicht")
    await b.update_batch(
        charge_id,
        BatchPatch(
            amount=data.menge,
            best_before=data.ablauf,
            ohne_ablauf=data.ohne_ablauf,
            location_id=data.lagerort_id,
            opened=data.geoeffnet,
        ),
    )
    return {"ok": True}


class AktionIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    aktion: Literal["verbraucht", "verdorben", "lagerort", "ablauf_plus", "geoeffnet"]
    lagerort_id: str | None = None
    tage: int | None = Field(default=None, ge=-365, le=3650)


@router.post("/inventar/aktion")
async def inventar_aktion(data: AktionIn, b: InventoryBackend = Depends(backend)) -> dict:
    """Massenaktion auf Chargen. Jede Charge einzeln, Fehler stoppen die anderen nicht."""
    if data.aktion == "lagerort" and not data.lagerort_id:
        raise HTTPException(422, "lagerort_id fehlt")
    if data.aktion == "ablauf_plus" and data.tage is None:
        raise HTTPException(422, "tage fehlt")
    chargen = {c.id: c for c in await b.list_all_batches()}
    ok, fehler = [], []
    for cid in data.ids:
        c = chargen.get(cid)
        if c is None:
            fehler.append({"id": cid, "fehler": "Charge gibt es nicht (mehr)"})
            continue
        try:
            if data.aktion in ("verbraucht", "verdorben"):
                await b.consume_batch(cid, None, spoiled=data.aktion == "verdorben")
            elif data.aktion == "lagerort":
                await b.update_batch(cid, BatchPatch(location_id=data.lagerort_id))
            elif data.aktion == "geoeffnet":
                await b.update_batch(cid, BatchPatch(opened=True))
            else:
                basis = c.best_before or date.today()
                await b.update_batch(
                    cid, BatchPatch(best_before=basis + timedelta(days=data.tage or 0))
                )
            ok.append(cid)
        except Exception as err:  # noqa: BLE001 - je Charge melden, weitermachen
            fehler.append({"id": cid, "fehler": str(err) or type(err).__name__})
    return {"ok": ok, "fehler": fehler}
