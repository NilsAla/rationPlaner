"""Contract-Tests: gleiche Fälle gegen FakeBackend und (optional) echtes Grocy.

Gegen Grocy nur mit RATION_TEST_GROCY_URL und RATION_TEST_GROCY_API_KEY.
Achtung: legt Testobjekte mit Präfix "zz ration test" in dieser Instanz an.
"""

import asyncio
import os

import pytest

from app.inventory.contract import CASES, make_fake, prepare_grocy, run_case
from app.inventory.grocy import GrocyBackend

GROCY_URL = os.environ.get("RATION_TEST_GROCY_URL")
GROCY_KEY = os.environ.get("RATION_TEST_GROCY_API_KEY", "")

BACKENDS = ["fake"] + (["grocy"] if GROCY_URL else [])


async def _run(backend_name: str, case_name: str):
    name, question, case = next(c for c in CASES if c[0] == case_name)
    if backend_name == "fake":
        backend, setup = make_fake()
        return await run_case(backend, setup, name, question, case)
    async with GrocyBackend(GROCY_URL, GROCY_KEY) as backend:
        setup = await prepare_grocy(backend)
        return await run_case(backend, setup, name, question, case)


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("case_name", [c[0] for c in CASES])
def test_contract(backend_name: str, case_name: str):
    result = asyncio.run(_run(backend_name, case_name))
    assert result.ok, result.detail
