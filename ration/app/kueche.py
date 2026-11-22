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


@dataclass(frozen=True)
class Groesse:
    """Eine kaufbare Packungsgröße: Einheit und Menge in Bestandseinheit."""

    einheit: str
    menge: float
    quelle: str = "standard"  # standard/manuell/bon/angebot/llm


@dataclass
class PackAnzahl:
    anzahl: int
    einheit: str
    menge: float  # je Packung, in Bestandseinheit
    quelle: str = "standard"


@dataclass
class Einkauf(Position):
    """Fehlmenge, aufgerundet auf ganze Packungen. Leere Liste = keine Packungsgröße bekannt."""

    packungen: list[PackAnzahl] = field(default_factory=list)

    @property
    def text(self) -> str:
        if not self.packungen:
            return f"{self.name} {menge(math.ceil(self.menge - EPS))} {self.einheit}".strip()
        teile = [
            f"{p.anzahl} × {p.einheit} ({menge(p.menge)} {self.einheit})".replace(" )", ")")
            for p in self.packungen
        ]
        return f"{self.name}, {' + '.join(teile)}"


def groessen(product: Product, weitere: list[Groesse] | None = None) -> list[Groesse]:
    """Standard-Packung aus Grocy plus weitere Größen, ohne Dubletten."""
    alle = []
    if product.pack_size:
        alle.append(Groesse(product.pack_unit or "Packung", product.pack_size))
    for g in weitere or []:
        if g.menge > EPS and not any(abs(g.menge - x.menge) < EPS for x in alle):
            alle.append(g)
    return alle


def waehle_packungen(fehlmenge: float, auswahl: list[Groesse]) -> list[PackAnzahl]:
    """Kombination aus höchstens zwei Größen mit kleinstem Überhang.

    Gleichstand: weniger Packungen, dann mehr Angebotspackungen, dann mehr Standardpackungen.
    """
    beste: tuple | None = None
    for i, a in enumerate(auswahl):
        for b in auswahl[i:]:
            max_a = math.ceil(fehlmenge / a.menge - EPS)
            for na in range(1 if a is b else 0, max_a + 1):
                rest = fehlmenge - na * a.menge
                nb = 0 if a is b else max(math.ceil(rest / b.menge - EPS), 0)
                if a is b and na != max_a:
                    continue
                kombi = [(n, g) for n, g in ((na, a), (nb, b)) if n > 0]
                ueberhang = sum(n * g.menge for n, g in kombi) - fehlmenge
                key = (
                    round(ueberhang, 6),
                    sum(n for n, _ in kombi),
                    -sum(n for n, g in kombi if g.quelle == "angebot"),
                    -sum(n for n, g in kombi if g.quelle == "standard"),
                )
                if beste is None or key < beste[0]:
                    beste = (key, kombi)
    if beste is None:
        return []
    kombi = sorted(beste[1], key=lambda x: -x[1].menge)
    return [PackAnzahl(n, g.einheit, g.menge, g.quelle) for n, g in kombi]


def auf_packungen(
    product: Product, fehlmenge: float, weitere: list[Groesse] | None = None
) -> Einkauf:
    """Rundet eine Fehlmenge auf ganze Packungen. Ohne Packungsgröße bleibt die Menge."""
    packungen = waehle_packungen(fehlmenge, groessen(product, weitere))
    gesamt = sum(p.anzahl * p.menge for p in packungen) or fehlmenge
    return Einkauf(product.id, product.name, gesamt, product.unit, packungen=packungen)


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


async def bedarf(
    backend: InventoryBackend,
    geplant: list[tuple[Recipe, int]],
    weitere: dict[str, list[Groesse]] | None = None,
) -> list[Einkauf]:
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
            einkauf.append(auf_packungen(p, differenz, (weitere or {}).get(product_id)))
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
