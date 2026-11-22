"""Legt in Grocy idempotent die Strukturen an, die rationPlaner braucht.

Lagerorte, Einheit g und Userfields. Keine Produkte, keine Rezepte, löscht nichts.
Mehrfach ausführbar: Vorhandenes bleibt unverändert, nur is_freezer wird korrigiert.

    python scripts/grocy_setup.py
"""

from __future__ import annotations

import asyncio

from _common import load_env

from app.inventory.grocy import GrocyBackend


async def main() -> None:
    url, key = load_env()
    async with GrocyBackend(url, key) as backend:
        rows = await backend.ensure_structure()

    width = max(len(r[1]) for r in rows)
    for kind, name, status in rows:
        print(f"{kind:<20} {name:<{width}}  {status}")


if __name__ == "__main__":
    asyncio.run(main())
