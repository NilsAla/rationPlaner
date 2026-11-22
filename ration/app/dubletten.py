"""Dubletten-Check für Produkte: Namensähnlichkeit, nie automatisch zusammenführen."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.inventory.base import Product
from app.rezept_import import _norm, _schluessel, kern_name


@dataclass(frozen=True)
class Kandidat:
    a: Product
    b: Product
    score: float
    grund: str


def paar(a: str, b: str) -> tuple[str, str]:
    """Paar-Schlüssel unabhängig von der Reihenfolge."""
    return (a, b) if a <= b else (b, a)


def aehnlichkeit(a: str, b: str) -> tuple[float, str]:
    """0..1. Gleicher Kern (Plural, Adjektive, Klammern ignoriert) zählt als 1."""
    if _schluessel(kern_name(a)) == _schluessel(kern_name(b)):
        return 1.0, "gleicher Kern-Name"
    return SequenceMatcher(None, _flach(a), _flach(b)).ratio(), "ähnlicher Name"


def _flach(text: str) -> str:
    """Umlaute ausgeschrieben, ohne Leerzeichen: "Oliven Öl" ≈ "Olivenoel"."""
    t = _norm(text)
    for u, e in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(u, e)
    return t.replace(" ", "").replace("-", "")


def kandidaten(
    produkte: list[Product], schwelle: float, ignoriert: set[tuple[str, str]]
) -> list[Kandidat]:
    """Alle Paare ab `schwelle`, ohne als „nicht mergen“ markierte. Varianten (Parent/Kind)
    sind gewollt verschieden und werden nicht vorgeschlagen."""
    result = []
    for i, a in enumerate(produkte):
        for b in produkte[i + 1 :]:
            if paar(a.id, b.id) in ignoriert or a.parent_id == b.id or b.parent_id == a.id:
                continue
            score, grund = aehnlichkeit(a.name, b.name)
            if score >= schwelle:
                result.append(Kandidat(a, b, round(score, 3), grund))
    return sorted(result, key=lambda k: -k.score)
