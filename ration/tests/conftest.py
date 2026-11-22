import json
from pathlib import Path

import pytest

from app.inventory.base import Location, Product, TrackingMode
from app.inventory.fake import FakeBackend

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
    return FakeBackend(products, locations)
