"""Legt in Grocy idempotent die Strukturen an, die rationPlaner braucht.

Lagerorte, Einheit g und Userfields. Keine Produkte, keine Rezepte, löscht nichts.
Mehrfach ausführbar: Vorhandenes bleibt unverändert, nur is_freezer wird korrigiert.

    python scripts/grocy_setup.py
"""

from __future__ import annotations

import asyncio

from _common import load_env

from app.inventory import grocy as g
from app.inventory.base import TrackingMode
from app.inventory.grocy import GrocyBackend

LOCATIONS = [("Kühlschrank", False), ("Vorrat", False), ("Gefrierschrank", True)]

USERFIELDS = [
    ("products", g.UF_TRACKING_MODUS, "Tracking-Modus", "preset-list", list(TrackingMode)),
    ("products", g.UF_KATEGORIE, "Kategorie", "text-single-line", None),
    ("products", g.UF_FUELLSTAND, "Füllstand", "preset-list", ["voll", "knapp", "leer"]),
    ("products", g.UF_TOLERANZ_PCT, "Toleranz %", "number-integral", None),
    ("recipes", g.UF_REZEPT_STATUS, "Status", "preset-list", ["getestet", "ungetestet"]),
    ("recipes", g.UF_REZEPT_AUFWAND, "Aufwand", "preset-list", ["schnell", "normal", "aufwaendig"]),
    ("recipes", g.UF_REZEPT_TAGS, "Tags", "text-single-line", None),
    ("recipes", g.UF_REZEPT_REST_HALTBARKEIT, "Rest-Haltbarkeit (Tage)", "number-integral", None),
]


async def main() -> None:
    url, key = load_env()
    rows: list[tuple[str, str, str]] = []
    async with GrocyBackend(url, key) as backend:
        await backend.ensure_quantity_unit("g")
        rows.append(("Einheit", "g", "ok"))
        for name, is_freezer in LOCATIONS:
            _, changed = await backend.ensure_location(name, is_freezer=is_freezer)
            rows.append(("Lagerort", name, "angelegt/korrigiert" if changed else "vorhanden"))
        for entity, name, caption, field_type, options in USERFIELDS:
            created = await backend.ensure_userfield(entity, name, caption, field_type, options)
            rows.append((f"Userfield {entity}", name, "angelegt" if created else "vorhanden"))

    width = max(len(r[1]) for r in rows)
    for kind, name, status in rows:
        print(f"{kind:<20} {name:<{width}}  {status}")


if __name__ == "__main__":
    asyncio.run(main())
