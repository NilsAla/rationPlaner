"""Prüft die Grocy-Semantik, auf der rationPlaner aufbaut, gegen die echte Instanz.

Dieselben Fälle laufen in den Tests gegen das FakeBackend. Legt Testobjekte mit
Präfix "zz ration test" an (eigene Produkte und Lagerorte), bringt deren Bestand
am Ende auf 0 und löscht nichts.

    python scripts/grocy_probe.py
"""

from __future__ import annotations

import asyncio
import sys

from _common import load_env

from app.inventory.contract import TEST_PREFIX, prepare_grocy, run_all
from app.inventory.grocy import GrocyBackend


async def main() -> int:
    url, key = load_env()
    async with GrocyBackend(url, key) as backend:
        info = await backend._get("/system/info")  # noqa: SLF001
        setup = await prepare_grocy(backend)
        results = await run_all(backend, setup)

    print(f"Grocy {info['grocy_version']['Version']} · Testobjekte: '{TEST_PREFIX} …'\n")
    width = max(len(r.question) for r in results)
    for r in results:
        print(f"{'OK  ' if r.ok else 'FAIL'}  {r.question:<{width}}  {r.detail}")
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} bestanden.")
    if failed:
        print("Abweichungen muss ration selbst übernehmen, siehe docs/konzept.md.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
