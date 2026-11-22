"""Fachlogik des Durchstichs: Gekocht, Bedarf, Status. Rechnet deterministisch."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.inventory.base import InventoryBackend, Product, Recipe, TrackingMode

# Füllstand und Immer-da werden nie abgebucht (Konzept, Bestandsführung).
ABBUCHEN = {TrackingMode.EXAKT, TrackingMode.MENGE, None}
EPS = 1e-6


def wird_abgebucht(product: Product) -> bool:
    return product.tracking_mode in ABBUCHEN


def schritte(description: str) -> list[str]:
    """Zerlegt Grocys HTML-Beschreibung in einzelne Schritte."""
    text = re.sub(r"(?i)</p>|<br\s*/?>|</li>|</h\d>", "\n", description)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return [line.strip() for line in text.splitlines() if line.strip()]


def menge(amount: float) -> str:
    return f"{round(amount, 1):g}"


@dataclass
class Position:
    produkt_id: str
    name: str
    menge: float
    einheit: str


@dataclass
class Einkauf(Position):
    """Fehlmenge, aufgerundet auf ganze Packungen (Einkaufseinheit aus Grocy)."""

    packungen: int | None = None
    packung: str | None = None  # Einkaufseinheit, z. B. "Dose"
    packung_menge: float | None = None  # Bestandseinheit je Packung

    @property
    def text(self) -> str:
        if self.packungen is None:
            return f"{self.name} {menge(math.ceil(self.menge - EPS))} {self.einheit}".strip()
        groesse = f"{menge(self.packung_menge)} {self.einheit}".strip()
        return f"{self.name}, {self.packungen} × {self.packung} ({groesse})"


def auf_packungen(product: Product, fehlmenge: float) -> Einkauf:
    """Rundet eine Fehlmenge auf ganze Packungen. Ohne Packungsgröße bleibt die Menge."""
    if not product.pack_size:
        return Einkauf(product.id, product.name, fehlmenge, product.unit)
    n = max(math.ceil(fehlmenge / product.pack_size - EPS), 1)
    return Einkauf(
        product.id,
        product.name,
        n * product.pack_size,
        product.unit,
        packungen=n,
        packung=product.pack_unit,
        packung_menge=product.pack_size,
    )


@dataclass
class GekochtErgebnis:
    abgebucht: list[Position] = field(default_factory=list)
    fehlend: list[Position] = field(default_factory=list)
    nicht_abgebucht: list[Position] = field(default_factory=list)
    rest_portionen: int = 0


async def gekocht(
    backend: InventoryBackend, recipe: Recipe, portionen_gekocht: int, portionen_gegessen: int
) -> GekochtErgebnis:
    """Bucht die Zutaten ab: Rezeptmenge × Portionen, FEFO, Varianten über das Parent-Produkt.

    Fehlt Bestand, wird abgebucht, was da ist, und der Rest als fehlend gemeldet.
    """
    products = {p.id: p for p in await backend.list_products()}
    faktor = portionen_gekocht / max(recipe.servings, 1)
    ergebnis = GekochtErgebnis(rest_portionen=max(portionen_gekocht - portionen_gegessen, 0))
    for zutat in recipe.ingredients:
        product = products[zutat.product_id]
        bedarf = zutat.amount * faktor
        pos = Position(product.id, product.name, bedarf, product.unit)
        if not wird_abgebucht(product):
            ergebnis.nicht_abgebucht.append(pos)
            continue
        vorhanden = await backend.get_stock_amount(product.id)
        abbuchen = min(bedarf, vorhanden)
        if abbuchen > EPS:
            await backend.consume(product.id, abbuchen)
            ergebnis.abgebucht.append(Position(product.id, product.name, abbuchen, product.unit))
        if bedarf - abbuchen > EPS:
            ergebnis.fehlend.append(
                Position(product.id, product.name, bedarf - abbuchen, product.unit)
            )
    return ergebnis


async def bedarf(backend: InventoryBackend, geplant: list[tuple[Recipe, int]]) -> list[Einkauf]:
    """Was zu kaufen ist: Bedarf der geplanten Gerichte plus Mindestbestand, minus Bestand,
    aufgerundet auf ganze Packungen. Produkte mit Mindestbestand kommen auch ungeplant drauf.
    """
    products = {p.id: p for p in await backend.list_products()}
    summe: dict[str, float] = {}
    for recipe, portionen in geplant:
        faktor = portionen / max(recipe.servings, 1)
        for zutat in recipe.ingredients:
            if wird_abgebucht(products[zutat.product_id]):
                summe[zutat.product_id] = summe.get(zutat.product_id, 0) + zutat.amount * faktor
    for p in products.values():
        if p.min_stock > 0 and wird_abgebucht(p):
            summe.setdefault(p.id, 0)
    einkauf = []
    for product_id, benoetigt in summe.items():
        p = products[product_id]
        differenz = benoetigt + p.min_stock - await backend.get_stock_amount(product_id)
        if differenz > EPS:
            einkauf.append(auf_packungen(p, differenz))
    return sorted(einkauf, key=lambda x: x.name)


@dataclass
class Status:
    artikel: int
    laeuft_bald_ab: list[dict]

    @property
    def text(self) -> str:
        return f"{self.artikel} Artikel · {len(self.laeuft_bald_ab)} laufen bald ab"


async def status(backend: InventoryBackend, tage: int = 3, heute: date | None = None) -> Status:
    heute = heute or date.today()
    grenze = heute + timedelta(days=tage)
    products = {p.id: p for p in await backend.list_products()}
    batches = await backend.list_all_batches()
    bald = [
        {
            "produkt_id": b.product_id,
            "name": products[b.product_id].name if b.product_id in products else b.product_id,
            "menge": b.amount,
            "einheit": products[b.product_id].unit if b.product_id in products else "",
            "ablauf": b.best_before.isoformat(),
            "tage": (b.best_before - heute).days,
        }
        for b in batches
        if b.best_before is not None and b.best_before <= grenze
    ]
    return Status(artikel=len({b.product_id for b in batches}), laeuft_bald_ab=bald)
