"""Contract-Fälle für InventoryBackend.

Dieselben Fälle laufen gegen FakeBackend (pytest) und gegen echtes Grocy
(pytest mit RATION_TEST_GROCY_URL oder scripts/grocy_probe.py). Weichen die
Ergebnisse ab, bildet das FakeBackend Grocy nicht korrekt nach.

Gegen Grocy werden eigene Testobjekte mit dem Präfix TEST_PREFIX angelegt und
wiederverwendet. Es wird nichts gelöscht; der Bestand der Testprodukte wird am
Ende auf 0 gebracht.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta

from app.inventory.base import InsufficientStockError, InventoryBackend, Location, Product
from app.inventory.fake import FakeBackend
from app.inventory.grocy import GrocyBackend

TEST_PREFIX = "zz ration test"
FROZEN_DAYS = 90
THAWED_DAYS = 2


@dataclass(frozen=True)
class ContractSetup:
    parent_id: str
    child_id: str
    fridge_id: str
    freezer_id: str


@dataclass(frozen=True)
class CaseResult:
    name: str
    question: str
    ok: bool
    detail: str


def _in(days: int) -> date:
    return date.today() + timedelta(days=days)


async def clear_stock(backend: InventoryBackend, product_id: str) -> None:
    amount = sum(b.amount for b in await backend.get_batches(product_id))
    if amount > 0:
        await backend.consume(product_id, amount)


async def case_parent_fulfillment(b: InventoryBackend, s: ContractSetup) -> str:
    await b.add_stock(s.child_id, 300, _in(5), s.fridge_id)
    total = await b.get_stock_amount(s.parent_id)
    assert total == 300, f"Bestand übergeordnet = {total}, erwartet 300"
    await b.consume(s.parent_id, 100)
    rest = sum(x.amount for x in await b.get_batches(s.child_id))
    assert rest == 200, f"Variante nach Abbuchung über Parent = {rest}, erwartet 200"
    return "Parent zeigt Bestand der Variante, Abbuchung auf Parent bucht Variante ab"


async def case_fefo(b: InventoryBackend, s: ContractSetup) -> str:
    await b.add_stock(s.child_id, 300, _in(10), s.fridge_id)
    await b.add_stock(s.child_id, 200, _in(3), s.fridge_id)
    await b.consume(s.child_id, 250)
    batches = await b.get_batches(s.child_id)
    assert len(batches) == 1, f"{len(batches)} Chargen übrig, erwartet 1"
    assert batches[0].best_before == _in(10), f"übrig: {batches[0].best_before}"
    assert batches[0].amount == 250, f"übrig: {batches[0].amount}"
    return "früheste Charge zuerst, Rest korrekt auf die nächste verteilt"


async def case_freeze(b: InventoryBackend, s: ContractSetup) -> str:
    await b.add_stock(s.child_id, 200, _in(3), s.fridge_id)
    await b.transfer(s.child_id, 100, s.fridge_id, s.freezer_id)
    batches = await b.get_batches(s.child_id)
    frozen = [x for x in batches if x.location_id == s.freezer_id]
    fresh = [x for x in batches if x.location_id == s.fridge_id]
    assert len(frozen) == 1 and frozen[0].amount == 100, f"Gefrierort: {frozen}"
    assert frozen[0].best_before == _in(FROZEN_DAYS), f"eingefroren bis {frozen[0].best_before}"
    assert len(fresh) == 1 and fresh[0].best_before == _in(3), f"Kühlschrank: {fresh}"
    return f"Einfrieren setzt Ablauf auf heute + {FROZEN_DAYS} Tage, Teilmenge wird gesplittet"


async def case_thaw(b: InventoryBackend, s: ContractSetup) -> str:
    await b.add_stock(s.child_id, 100, _in(3), s.fridge_id)
    await b.transfer(s.child_id, 100, s.fridge_id, s.freezer_id)
    await b.transfer(s.child_id, 40, s.freezer_id, s.fridge_id)
    thawed = [x for x in await b.get_batches(s.child_id) if x.location_id == s.fridge_id]
    assert len(thawed) == 1, f"Kühlschrank: {thawed}"
    assert thawed[0].best_before == _in(THAWED_DAYS), f"aufgetaut bis {thawed[0].best_before}"
    return f"Auftauen setzt Ablauf auf heute + {THAWED_DAYS} Tage"


async def case_insufficient(b: InventoryBackend, s: ContractSetup) -> str:
    await b.add_stock(s.child_id, 50, _in(3), s.fridge_id)
    try:
        await b.consume(s.child_id, 80)
    except InsufficientStockError:
        pass
    else:
        raise AssertionError("keine InsufficientStockError")
    rest = sum(x.amount for x in await b.get_batches(s.child_id))
    assert rest == 50, f"Bestand nach Fehler = {rest}, erwartet unverändert 50"
    return "zu viel abbuchen wirft Fehler und ändert nichts"


Case = Callable[[InventoryBackend, ContractSetup], Awaitable[str]]

CASES: list[tuple[str, str, Case]] = [
    ("parent", "Erfüllt eine Variante ein übergeordnetes Produkt?", case_parent_fulfillment),
    ("fefo", "Bucht Abbuchen die Charge mit frühestem Ablauf zuerst ab?", case_fefo),
    ("einfrieren", "Setzt Umbuchen in den Gefrierort das neue Ablaufdatum?", case_freeze),
    ("auftauen", "Setzt Umbuchen aus dem Gefrierort das Auftau-Datum?", case_thaw),
    ("zu_wenig", "Wird Überbuchung abgelehnt, ohne Bestand zu ändern?", case_insufficient),
]


async def run_case(
    backend: InventoryBackend, setup: ContractSetup, name: str, question: str, case: Case
) -> CaseResult:
    await clear_stock(backend, setup.child_id)
    try:
        detail = await case(backend, setup)
        ok = True
    except AssertionError as err:
        detail, ok = str(err), False
    finally:
        await clear_stock(backend, setup.child_id)
    return CaseResult(name, question, ok, detail)


async def run_all(backend: InventoryBackend, setup: ContractSetup) -> list[CaseResult]:
    return [await run_case(backend, setup, n, q, c) for n, q, c in CASES]


def make_fake() -> tuple[FakeBackend, ContractSetup]:
    setup = ContractSetup(parent_id="p", child_id="c", fridge_id="k", freezer_id="f")
    common = {
        "unit": "g",
        "grams_per_unit": 1.0,
        "shelf_life_days": 5,
        "shelf_life_frozen_days": FROZEN_DAYS,
        "shelf_life_thawed_days": THAWED_DAYS,
    }
    products = [
        Product(id="p", name=f"{TEST_PREFIX} Brokkoli", **common),
        Product(id="c", name=f"{TEST_PREFIX} Brokkoli frisch", parent_id="p", **common),
    ]
    locations = [
        Location(id="k", name=f"{TEST_PREFIX} Kühlschrank"),
        Location(id="f", name=f"{TEST_PREFIX} Gefrierschrank", is_freezer=True),
    ]
    return FakeBackend(products, locations), setup


async def prepare_grocy(backend: GrocyBackend) -> ContractSetup:
    unit = await backend.ensure_quantity_unit("g")
    fridge, _ = await backend.ensure_location(f"{TEST_PREFIX} Kühlschrank")
    freezer, _ = await backend.ensure_location(f"{TEST_PREFIX} Gefrierschrank", is_freezer=True)
    shelf = {
        "shelf_life_days": 5,
        "shelf_life_frozen_days": FROZEN_DAYS,
        "shelf_life_thawed_days": THAWED_DAYS,
    }
    parent = await backend.ensure_product(
        f"{TEST_PREFIX} Brokkoli", unit_id=unit, location_id=fridge, **shelf
    )
    child = await backend.ensure_product(
        f"{TEST_PREFIX} Brokkoli frisch",
        unit_id=unit,
        location_id=fridge,
        parent_id=parent,
        **shelf,
    )
    return ContractSetup(parent_id=parent, child_id=child, fridge_id=fridge, freezer_id=freezer)
