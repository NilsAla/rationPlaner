"""GrocyBackend-Mapping. Reine Mapping-Tests laufen immer, der Rest nur mit echter Instanz."""

import asyncio
import os
from datetime import date

import pytest

from app import testdaten
from app.inventory.base import DueType, TrackingMode
from app.inventory.contract import prepare_grocy
from app.inventory.grocy import UF_TOLERANZ_PCT, UF_TRACKING_MODUS, GrocyBackend

GROCY_URL = os.environ.get("RATION_TEST_GROCY_URL")
GROCY_KEY = os.environ.get("RATION_TEST_GROCY_API_KEY", "")


def test_to_product_mapping():
    raw = {
        "id": 7,
        "name": "Hack",
        "qu_id_stock": 2,
        "parent_product_id": None,
        "due_type": 2,
        "default_best_before_days": 2,
        "default_best_before_days_after_freezing": 90,
        "userfields": {UF_TRACKING_MODUS: "exakt", UF_TOLERANZ_PCT: "15"},
    }
    p = GrocyBackend._to_product(raw, {2: "Packung"}, {(7, 2): 500.0})
    assert p.id == "7" and p.parent_id is None
    assert p.tracking_mode is TrackingMode.EXAKT
    assert p.tolerance_pct == 15
    assert p.grams_per_unit == 500.0
    assert p.due_type is DueType.VERBRAUCHSDATUM
    assert p.shelf_life_frozen_days == 90


def test_to_product_ohne_attribute():
    raw = {"id": 1, "name": "Neu", "qu_id_stock": 3, "userfields": None}
    p = GrocyBackend._to_product(raw, {3: "g"}, {})
    assert p.tracking_mode is None and p.tolerance_pct is None
    assert p.grams_per_unit == 1.0


def test_to_batch_nie_ablaufend():
    raw = {
        "id": 4,
        "product_id": 2,
        "amount": "50",
        "best_before_date": "2999-12-31",
        "location_id": 3,
        "open": 1,
    }
    b = GrocyBackend._to_batch(raw)
    assert b.best_before is None and b.opened and b.amount == 50.0
    raw["best_before_date"] = "2026-12-01"
    assert GrocyBackend._to_batch(raw).best_before == date(2026, 12, 1)


@pytest.mark.skipif(not GROCY_URL, reason="RATION_TEST_GROCY_URL nicht gesetzt")
def test_list_products_echtes_grocy():
    async def run():
        async with GrocyBackend(GROCY_URL, GROCY_KEY) as backend:
            await backend.ensure_structure()
            setup = await prepare_grocy(backend)
            await backend.set_product_userfields(
                setup.child_id, {UF_TRACKING_MODUS: "exakt", UF_TOLERANZ_PCT: 30}
            )
            products = {p.id: p for p in await backend.list_products()}
            locations = {loc.id: loc for loc in await backend.list_locations()}
            return setup, products, locations

    setup, products, locations = asyncio.run(run())
    child = products[setup.child_id]
    assert child.parent_id == setup.parent_id
    assert child.tracking_mode is TrackingMode.EXAKT
    assert child.tolerance_pct == 30
    assert child.unit == "g" and child.grams_per_unit == 1.0
    assert locations[setup.freezer_id].is_freezer


@pytest.mark.skipif(not GROCY_URL, reason="RATION_TEST_GROCY_URL nicht gesetzt")
def test_testdaten_idempotent():
    async def run():
        async with GrocyBackend(GROCY_URL, GROCY_KEY) as backend:
            erst = await testdaten.anlegen(backend)
            zweit = await testdaten.anlegen(backend)
            rezepte = {r.name: r for r in await backend.list_recipes()}
            return erst, zweit, rezepte, await backend.list_products()

    erst, zweit, rezepte, erst_produkte = asyncio.run(run())
    assert erst["produkte"] == 6
    assert not any(r["neu"] for r in zweit["rezepte"])
    pasta = rezepte["Test: Paprika-Pasta"]
    assert pasta.servings == 2 and len(pasta.ingredients) == 3
    produkte = {p.name: p for p in erst_produkte}
    assert (produkte["Kidneybohnen"].pack_unit, produkte["Kidneybohnen"].pack_size) == ("Dose", 400)
    assert produkte["Nudeln"].min_stock == 500


def test_to_product_packung_und_mindestbestand():
    raw = {"id": 4, "name": "Kidneybohnen", "qu_id_stock": 1, "qu_id_purchase": 5}
    raw["min_stock_amount"] = "400"
    p = GrocyBackend._to_product(raw, {1: "g", 5: "Dose"}, {}, {(4, 5, 1): 400.0})
    assert (p.pack_unit, p.pack_size, p.min_stock) == ("Dose", 400.0, 400.0)
    ohne = GrocyBackend._to_product({**raw, "qu_id_purchase": 1}, {1: "g"}, {}, {})
    assert ohne.pack_unit is None and ohne.pack_size is None


@pytest.mark.skipif(not GROCY_URL, reason="RATION_TEST_GROCY_URL nicht gesetzt")
def test_set_pack_beide_richtungen():
    async def run():
        async with GrocyBackend(GROCY_URL, GROCY_KEY) as backend:
            await testdaten.anlegen(backend)
            nudeln = next(p for p in await backend.list_products() if p.name == "Nudeln")
            await backend.set_pack(nudeln.id, "Großpackung", 1000)
            await backend.set_pack(nudeln.id, "Großpackung", 1000)  # idempotent
            neu = await backend.get_product(nudeln.id)
            resolved = await backend._get("/objects/quantity_unit_conversions")
            units = {u["name"]: u["id"] for u in await backend._get("/objects/quantity_units")}
            await backend.set_pack(nudeln.id, "Packung", 500)  # zurück für andere Tests
            return neu, resolved, units

    neu, resolved, units = asyncio.run(run())
    assert (neu.pack_unit, neu.pack_size) == ("Großpackung", 1000)
    faktoren = {
        (c["from_qu_id"], c["to_qu_id"]): float(c["factor"])
        for c in resolved
        if c["product_id"] == int(neu.id)
    }
    gross, g = units["Großpackung"], units["g"]
    assert faktoren[(gross, g)] == 1000 and faktoren[(g, gross)] == 0.001


def test_unbekanntes_rezept_404(client):
    resp = client.put("/api/v1/plan/2026-01-01", json={"rezept_id": "gibtsnicht"})
    assert resp.status_code == 404


@pytest.mark.skipif(not GROCY_URL, reason="RATION_TEST_GROCY_URL nicht gesetzt")
def test_create_product_und_recipe():
    from app.inventory.base import Ingredient

    async def run():
        async with GrocyBackend(GROCY_URL, GROCY_KEY) as backend:
            await backend.ensure_structure()
            p = await backend.create_product("zz ration test Importprodukt", "g")
            again = await backend.create_product("zz ration test Importprodukt", "g")
            r = await backend.create_recipe(
                "zz ration test Importrezept", 3, "<p>x</p>", [Ingredient(p.id, 123, "123 g X")]
            )
            return p, again, r

    p, again, r = asyncio.run(run())
    assert p.id == again.id and p.unit == "g" and p.tracking_mode is None
    assert r.servings == 3 and r.ingredients[0].amount == 123
    assert r.ingredients[0].note == "123 g X"
