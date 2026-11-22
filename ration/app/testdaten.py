"""Legt die mitgelieferten Testrezepte samt Produkten idempotent in Grocy an."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from app.inventory.grocy import UF_REZEPT_STATUS, UF_TOLERANZ_PCT, UF_TRACKING_MODUS, GrocyBackend

TESTDATEN = Path(__file__).parent / "fixtures" / "testdaten.json"


async def anlegen(backend: GrocyBackend) -> dict:
    daten = json.loads(TESTDATEN.read_text(encoding="utf-8"))
    await backend.ensure_structure()
    gramm = await backend.ensure_quantity_unit("g")
    orte = {loc.name: loc.id for loc in await backend.list_locations()}
    produkte: dict[str, str] = {}
    for p in daten["produkte"]:
        pid = await backend.ensure_product(
            p["name"],
            unit_id=gramm,
            location_id=orte[p["lagerort"]],
            shelf_life_days=p["haltbarkeit_tage"],
            shelf_life_frozen_days=p.get("eingefroren_tage", 0),
            shelf_life_thawed_days=p.get("aufgetaut_tage", 0),
        )
        uf = {UF_TRACKING_MODUS: p["tracking_modus"]}
        if "toleranz_pct" in p:
            uf[UF_TOLERANZ_PCT] = p["toleranz_pct"]
        await backend.set_product_userfields(pid, uf)
        produkte[p["name"]] = pid
    rezepte = []
    for r in daten["rezepte"]:
        rid, neu = await backend.ensure_recipe(
            r["name"],
            servings=r["portionen"],
            description="".join(f"<p>{escape(s)}</p>" for s in r["schritte"]),
            ingredients=[(produkte[name], menge) for name, menge in r["zutaten"]],
            userfields={UF_REZEPT_STATUS: "ungetestet"},
        )
        rezepte.append({"id": rid, "name": r["name"], "neu": neu})
    return {"produkte": len(produkte), "rezepte": rezepte}
