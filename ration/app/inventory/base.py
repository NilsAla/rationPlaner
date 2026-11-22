"""Domain-Objekte und das Protokoll InventoryBackend.

Planer, Bon-Import und Gekocht-Flow arbeiten ausschließlich hiermit. Grocy-IDs,
Endpunkte, Userfields und Einheitenlogik bleiben in app/inventory/grocy.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol


class TrackingMode(StrEnum):
    EXAKT = "exakt"
    MENGE = "menge"
    FUELLSTAND = "fuellstand"
    IMMER_DA = "immer_da"


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    is_freezer: bool = False


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    unit: str
    parent_id: str | None = None
    tracking_mode: TrackingMode | None = None  # None = noch nicht kategorisiert
    grams_per_unit: float | None = None  # Pflicht für Nährwerte, setzt der Kategorisierungslauf
    tolerance_pct: int | None = None


@dataclass(frozen=True)
class Batch:
    id: str
    product_id: str
    amount: float
    best_before: date | None
    location_id: str
    opened: bool = False


class InventoryBackend(Protocol):
    async def list_products(self) -> list[Product]: ...

    async def list_locations(self) -> list[Location]: ...

    async def get_batches(self, product_id: str) -> list[Batch]: ...

    async def add_stock(
        self,
        product_id: str,
        amount: float,
        best_before: date | None,
        location_id: str,
        price: float | None = None,
    ) -> None: ...

    async def consume(self, product_id: str, amount: float, *, fefo: bool = True) -> None:
        """Bucht ab. Mit fefo=True immer zuerst die Charge, die am frühesten abläuft."""
        ...
