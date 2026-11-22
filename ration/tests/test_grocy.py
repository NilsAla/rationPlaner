"""GrocyBackend-Mapping. Reine Mapping-Tests laufen immer, der Rest nur mit echter Instanz."""

import asyncio
import os
from datetime import date

import pytest

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
