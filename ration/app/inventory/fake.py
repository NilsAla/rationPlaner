"""In-Memory-Backend für Tests. Bildet die Grocy-Semantik nach (siehe base.py, Contract-Tests)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, timedelta

from app.inventory.base import (
    Batch,
    Ingredient,
    InsufficientStockError,
    Location,
    NotFoundError,
    Product,
    Recipe,
    consume_order,
)


class FakeBackend:
    def __init__(
        self,
        products: list[Product],
        locations: list[Location],
        recipes: list[Recipe] | None = None,
    ):
        self._products = {p.id: p for p in products}
        self._locations = {loc.id: loc for loc in locations}
        self._recipes = {r.id: r for r in recipes or []}
        self._batches: list[Batch] = []
        self._ids = itertools.count(1)

    def add_product(self, product: Product) -> None:
        self._products[product.id] = product

    def add_location(self, location: Location) -> None:
        self._locations[location.id] = location

    async def list_products(self) -> list[Product]:
        return list(self._products.values())

    async def get_product(self, product_id: str) -> Product:
        if product_id not in self._products:
            raise NotFoundError(f"Produkt {product_id}")
        return self._products[product_id]

    async def create_product(self, name: str, unit: str) -> Product:
        for p in self._products.values():
            if p.name == name:
                return p
        location = next(iter(self._locations), None)
        product = Product(
            id=f"p{next(self._ids)}", name=name, unit=unit, default_location_id=location
        )
        self._products[product.id] = product
        return product

    async def create_recipe(
        self, name: str, servings: int, description: str, ingredients: list[Ingredient]
    ) -> Recipe:
        recipe = Recipe(f"r{next(self._ids)}", name, servings, tuple(ingredients), description)
        self._recipes[recipe.id] = recipe
        return recipe

    async def set_pack(self, product_id: str, unit: str, size: float) -> None:
        product = await self.get_product(product_id)
        self._products[product_id] = replace(product, pack_unit=unit, pack_size=size)

    async def list_locations(self) -> list[Location]:
        return list(self._locations.values())

    async def list_recipes(self) -> list[Recipe]:
        return list(self._recipes.values())

    async def get_recipe(self, recipe_id: str) -> Recipe:
        if recipe_id not in self._recipes:
            raise NotFoundError(f"Rezept {recipe_id}")
        return self._recipes[recipe_id]

    async def list_all_batches(self) -> list[Batch]:
        return sorted(self._batches, key=consume_order)

    def _family(self, product_id: str) -> set[str]:
        children = {p.id for p in self._products.values() if p.parent_id == product_id}
        return {product_id} | children

    async def get_batches(self, product_id: str, *, include_children: bool = False) -> list[Batch]:
        ids = self._family(product_id) if include_children else {product_id}
        return sorted((b for b in self._batches if b.product_id in ids), key=consume_order)

    async def get_stock_amount(self, product_id: str) -> float:
        batches = await self.get_batches(product_id, include_children=True)
        return sum(b.amount for b in batches)

    async def add_stock(
        self,
        product_id: str,
        amount: float,
        best_before: date | None,
        location_id: str,
        price: float | None = None,
    ) -> None:
        product = self._products[product_id]
        if best_before is None and product.shelf_life_days > 0:
            best_before = date.today() + timedelta(days=product.shelf_life_days)
        self._batches.append(
            Batch(str(next(self._ids)), product_id, amount, best_before, location_id)
        )

    def _take(self, batches: list[Batch], amount: float) -> list[tuple[Batch, float]]:
        if sum(b.amount for b in batches) + 1e-9 < amount:
            raise InsufficientStockError(batches[0].product_id if batches else "")
        taken, rest = [], amount
        for batch in batches:
            if rest <= 1e-9:
                break
            take = min(batch.amount, rest)
            rest -= take
            idx = self._batches.index(batch)
            if take >= batch.amount - 1e-9:
                self._batches.pop(idx)
            else:
                self._batches[idx] = replace(batch, amount=batch.amount - take)
            taken.append((batch, take))
        return taken

    async def consume(self, product_id: str, amount: float, *, spoiled: bool = False) -> None:
        batches = await self.get_batches(product_id, include_children=True)
        if not batches:
            raise InsufficientStockError(product_id)
        self._take(batches, amount)

    async def transfer(
        self, product_id: str, amount: float, from_location_id: str, to_location_id: str
    ) -> None:
        batches = [
            b for b in await self.get_batches(product_id) if b.location_id == from_location_id
        ]
        if not batches:
            raise InsufficientStockError(product_id)
        product = self._products[product_id]
        was_frozen = self._locations[from_location_id].is_freezer
        to_freezer = self._locations[to_location_id].is_freezer
        for batch, take in self._take(batches, amount):
            best_before = batch.best_before
            if to_freezer and not was_frozen and product.shelf_life_frozen_days > 0:
                best_before = date.today() + timedelta(days=product.shelf_life_frozen_days)
            elif was_frozen and not to_freezer and product.shelf_life_thawed_days > 0:
                best_before = date.today() + timedelta(days=product.shelf_life_thawed_days)
            self._batches.append(
                replace(
                    batch,
                    id=str(next(self._ids)),
                    amount=take,
                    best_before=best_before,
                    location_id=to_location_id,
                )
            )
