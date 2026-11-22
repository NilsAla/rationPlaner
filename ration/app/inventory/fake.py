"""In-Memory-Backend für Tests. Muss die Grocy-Semantik nachbilden (Contract-Tests)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date

from app.inventory.base import Batch, Location, Product


class InsufficientStockError(Exception):
    pass


class FakeBackend:
    def __init__(self, products: list[Product], locations: list[Location]):
        self._products = {p.id: p for p in products}
        self._locations = {loc.id: loc for loc in locations}
        self._batches: list[Batch] = []
        self._ids = itertools.count(1)

    async def list_products(self) -> list[Product]:
        return list(self._products.values())

    async def list_locations(self) -> list[Location]:
        return list(self._locations.values())

    async def get_batches(self, product_id: str) -> list[Batch]:
        return [b for b in self._batches if b.product_id == product_id]

    async def add_stock(
        self,
        product_id: str,
        amount: float,
        best_before: date | None,
        location_id: str,
        price: float | None = None,
    ) -> None:
        self._batches.append(
            Batch(str(next(self._ids)), product_id, amount, best_before, location_id)
        )

    async def consume(self, product_id: str, amount: float, *, fefo: bool = True) -> None:
        batches = await self.get_batches(product_id)
        if sum(b.amount for b in batches) < amount:
            raise InsufficientStockError(product_id)
        if fefo:
            batches.sort(key=lambda b: (b.best_before is None, b.best_before or date.max))
        rest = amount
        for batch in batches:
            if rest <= 0:
                break
            take = min(batch.amount, rest)
            rest -= take
            idx = self._batches.index(batch)
            if take == batch.amount:
                self._batches.pop(idx)
            else:
                self._batches[idx] = replace(batch, amount=batch.amount - take)
