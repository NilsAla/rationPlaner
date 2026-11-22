import asyncio
from datetime import date

import pytest

from app.inventory.fake import FakeBackend, InsufficientStockError


def run(coro):
    return asyncio.run(coro)


def test_consume_fefo(fake_backend: FakeBackend):
    run(fake_backend.add_stock("paprika", 300, date(2026, 12, 5), "kuehlschrank"))
    run(fake_backend.add_stock("paprika", 200, date(2026, 12, 1), "kuehlschrank"))

    run(fake_backend.consume("paprika", 250))

    rest = run(fake_backend.get_batches("paprika"))
    assert len(rest) == 1
    assert rest[0].best_before == date(2026, 12, 5)
    assert rest[0].amount == 250


def test_consume_zu_wenig(fake_backend: FakeBackend):
    run(fake_backend.add_stock("nudeln", 100, None, "vorrat"))
    with pytest.raises(InsufficientStockError):
        run(fake_backend.consume("nudeln", 200))
