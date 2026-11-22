"""Domain-Objekte und das Protokoll InventoryBackend.

Planer, Bon-Import und Gekocht-Flow arbeiten ausschließlich hiermit. Grocy-IDs,
Endpunkte, Userfields und Einheitenlogik bleiben in app/inventory/grocy.py.

Semantik, die jedes Backend einhalten muss (geprüft durch app/inventory/contract.py):
- Abbuchen nimmt zuerst geöffnete Chargen, dann die mit dem frühesten Ablaufdatum.
- Ein übergeordnetes Produkt wird durch den Bestand seiner Varianten erfüllt; Abbuchen
  auf dem übergeordneten Produkt bucht Varianten ab.
- Umbuchen in einen Gefrierort setzt das Ablaufdatum auf heute + Haltbarkeit
  eingefroren, Umbuchen heraus auf heute + Haltbarkeit aufgetaut (jeweils wenn > 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import IntEnum, StrEnum
from typing import Protocol


class InsufficientStockError(Exception):
    """Mehr abbuchen oder umbuchen als vorhanden."""


class NotFoundError(Exception):
    """Produkt, Rezept o. Ä. existiert im Backend nicht."""


class TrackingMode(StrEnum):
    EXAKT = "exakt"
    MENGE = "menge"
    FUELLSTAND = "fuellstand"
    IMMER_DA = "immer_da"


class DueType(IntEnum):
    MHD = 1  # weiche Grenze
    VERBRAUCHSDATUM = 2  # harte Grenze


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
    due_type: DueType = DueType.MHD
    shelf_life_days: int = 0
    shelf_life_opened_days: int = 0
    shelf_life_frozen_days: int = 0
    shelf_life_thawed_days: int = 0
    default_location_id: str | None = None
    pack_unit: str | None = None  # Einkaufseinheit, z. B. "Dose"; None = in Bestandseinheit
    pack_size: float | None = None  # Bestandseinheit je Einkaufseinheit, z. B. 400 (g je Dose)
    min_stock: float = 0  # Mindestbestand in Bestandseinheit, Nachkauf darunter
    category: str | None = None
    fill_level: str | None = None  # nur Modus fuellstand: voll/knapp/leer


@dataclass(frozen=True)
class ProductPatch:
    """Änderungen an einem Produkt. None = unverändert."""

    name: str | None = None
    tracking_mode: TrackingMode | None = None
    category: str | None = None
    tolerance_pct: int | None = None
    min_stock: float | None = None
    fill_level: str | None = None
    grams_per_unit: float | None = None  # Umrechnung Bestandseinheit → g
    due_type: DueType | None = None
    shelf_life_days: int | None = None
    shelf_life_opened_days: int | None = None
    shelf_life_frozen_days: int | None = None
    shelf_life_thawed_days: int | None = None
    default_location_id: str | None = None
    parent_id: str | None = None  # "" entfernt die Zuordnung


@dataclass(frozen=True)
class Ingredient:
    product_id: str
    amount: float  # in der Bestandseinheit des Produkts
    note: str | None = None


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    servings: int
    ingredients: tuple[Ingredient, ...]
    description: str = ""  # Zubereitung, in Grocy als HTML
    tags: tuple[str, ...] = ()
    status: str | None = None  # getestet/ungetestet
    effort: str | None = None  # schnell/normal/aufwaendig
    leftover_days: int | None = None  # Rest-Haltbarkeit in Tagen


@dataclass(frozen=True)
class RecipePatch:
    """Änderungen an einem Rezept. None = unverändert; ingredients ersetzt alle Zutaten."""

    name: str | None = None
    servings: int | None = None
    description: str | None = None
    ingredients: list[Ingredient] | None = None
    tags: list[str] | None = None
    status: str | None = None
    effort: str | None = None
    leftover_days: int | None = None


@dataclass(frozen=True)
class Batch:
    id: str
    product_id: str
    amount: float
    best_before: date | None
    location_id: str
    opened: bool = False


@dataclass(frozen=True)
class BatchPatch:
    """Änderung einer Charge. None = unverändert; ohne_ablauf entfernt das Ablaufdatum."""

    amount: float | None = None
    best_before: date | None = None
    ohne_ablauf: bool = False
    location_id: str | None = None
    opened: bool | None = None


def consume_order(batch: Batch) -> tuple:
    """Grocy-Standard: geöffnet zuerst, dann frühestes Ablaufdatum, dann Einbuchreihenfolge."""
    return (not batch.opened, batch.best_before or date.max, int(batch.id))


class InventoryBackend(Protocol):
    async def list_products(self) -> list[Product]: ...

    async def get_product(self, product_id: str) -> Product: ...

    async def list_locations(self) -> list[Location]: ...

    async def list_recipes(self) -> list[Recipe]: ...

    async def get_recipe(self, recipe_id: str) -> Recipe: ...

    async def list_all_batches(self) -> list[Batch]:
        """Alle Chargen aller Produkte (für Übersicht und Status)."""
        ...

    async def get_batches(self, product_id: str, *, include_children: bool = False) -> list[Batch]:
        """Chargen in Verbrauchsreihenfolge (geöffnet, dann frühestes Ablaufdatum)."""
        ...

    async def get_stock_amount(self, product_id: str) -> float:
        """Bestand inklusive aller Varianten, falls es ein übergeordnetes Produkt ist."""
        ...

    async def add_stock(
        self,
        product_id: str,
        amount: float,
        best_before: date | None,
        location_id: str,
        price: float | None = None,
    ) -> None: ...

    async def consume(self, product_id: str, amount: float, *, spoiled: bool = False) -> None:
        """Bucht ab, Reihenfolge wie get_batches. Wirft InsufficientStockError."""
        ...

    async def create_product(self, name: str, unit: str) -> Product:
        """Legt ein Produkt ohne Attribute an (Import). Existiert der Name, wird es geliefert."""
        ...

    async def create_recipe(
        self, name: str, servings: int, description: str, ingredients: list[Ingredient]
    ) -> Recipe:
        """Legt ein Rezept an, Zutatenmengen in der Bestandseinheit des Produkts."""
        ...

    async def set_pack(self, product_id: str, unit: str, size: float) -> None:
        """Setzt die Standard-Packung: Einkaufseinheit `unit` mit `size` Bestandseinheiten."""
        ...

    async def transfer(
        self, product_id: str, amount: float, from_location_id: str, to_location_id: str
    ) -> None:
        """Bucht um, inklusive Ablaufdatum-Regel für Gefrierorte."""
        ...

    async def update_product(self, product_id: str, patch: ProductPatch) -> Product: ...

    async def update_recipe(self, recipe_id: str, patch: RecipePatch) -> Recipe: ...

    async def update_batch(self, batch_id: str, patch: BatchPatch) -> None:
        """Ändert Menge, Ablauf, Lagerort oder geöffnet einer Charge (Inventur)."""
        ...

    async def consume_batch(self, batch_id: str, amount: float | None, *, spoiled: bool) -> None:
        """Bucht genau diese Charge ab (amount None = alles). Wirft InsufficientStockError."""
        ...

    async def delete_product(self, product_id: str) -> None:
        """Löscht ein Produkt. Grocy löscht dabei Bestand und Rezeptzutaten mit, daher nur
        nach Prüfung (kein Bestand, in keinem Rezept, keine Varianten) und Bestätigung."""
        ...

    async def delete_recipe(self, recipe_id: str) -> None:
        """Löscht ein Rezept samt Zutatenliste. Nur nach Bestätigung."""
        ...

    async def merge_products(self, keep_id: str, remove_id: str) -> None:
        """Hängt Bestand, Rezeptzutaten und Varianten von remove auf keep um, löscht remove.

        Mengen werden über die Umrechnung remove-Bestandseinheit → keep-Bestandseinheit
        umgerechnet. Nur nach ausdrücklicher Bestätigung durch den Nutzer aufrufen.
        """
        ...
