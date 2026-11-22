import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.inventory.base import Ingredient, Location, Product, Recipe, TrackingMode
from app.inventory.fake import FakeBackend
from app.main import create_app
from app.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def basis() -> dict:
    return json.loads((FIXTURES / "basis.json").read_text(encoding="utf-8"))


@pytest.fixture
def fake_backend(basis: dict) -> FakeBackend:
    products = [
        Product(**{**p, "tracking_mode": TrackingMode(p["tracking_mode"])})
        for p in basis["products"]
    ]
    locations = [Location(**loc) for loc in basis["locations"]]
    recipes = [
        Recipe(
            id=r["id"],
            name=r["name"],
            servings=r["servings"],
            description=r.get("description", ""),
            ingredients=tuple(Ingredient(i["product_id"], i["amount"]) for i in r["ingredients"]),
        )
        for r in basis["recipes"]
    ]
    return FakeBackend(products, locations, recipes)


class FakeHomeAssistant:
    def __init__(self, items: list[str] | None = None):
        self.items = list(items or [])
        self.ki_antwort: dict | Exception | list = {}  # Liste: eine Antwort je Aufruf
        self.ki_anfragen: list[str] = []
        self.ki_anhaenge: list = []
        self.hinweise: list[tuple[str, str]] = []

    async def todo_items(self, entity_id: str) -> list[str]:
        return list(self.items)

    async def todo_add(self, entity_id: str, item: str) -> None:
        self.items.append(item)

    async def notify(self, title: str, message: str, service: str = "") -> None:
        self.hinweise.append((title, message))

    async def ai_task(
        self,
        entity_id: str,
        task_name: str,
        instructions: str,
        structure: dict,
        attachments: list[dict] | None = None,
    ) -> dict:
        self.ki_anfragen.append(instructions)
        self.ki_anhaenge.append(attachments)
        antwort = self.ki_antwort
        if isinstance(antwort, list):
            antwort = antwort.pop(0) if len(antwort) > 1 else antwort[0]
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


@pytest.fixture
def fake_ha() -> FakeHomeAssistant:
    return FakeHomeAssistant(["Milch"])


@pytest.fixture
def client(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(todo_entity="todo.einkaufsliste", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
    with TestClient(app) as c:
        yield c
