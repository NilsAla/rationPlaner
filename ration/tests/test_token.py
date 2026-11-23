"""Token für Zugriffe außerhalb von Ingress."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def token_client(fake_backend, tmp_path):
    app = create_app(
        inventory=fake_backend,
        app_settings=Settings(api_token="geheim", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 't.db'}",
    )
    with TestClient(app) as c:
        yield c


def test_ohne_token_abgelehnt(token_client):
    assert token_client.get("/api/v1/produkte").status_code == 401
    falsch = {"Authorization": "Bearer nein"}
    assert token_client.get("/api/v1/produkte", headers=falsch).status_code == 401


def test_mit_token(token_client):
    ok = {"Authorization": "Bearer geheim"}
    assert token_client.get("/api/v1/produkte", headers=ok).status_code == 200


def test_health_und_ui_offen(token_client):
    assert token_client.get("/api/v1/health").status_code == 200
    assert token_client.get("/").status_code == 200


def test_ingress_ohne_token(fake_backend, tmp_path):
    app = create_app(
        inventory=fake_backend,
        app_settings=Settings(api_token="geheim", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 't.db'}",
    )
    with TestClient(app, client=("172.30.32.2", 5000)) as c:
        assert c.get("/api/v1/produkte").status_code == 200


def test_ohne_option_offen(client):
    assert client.get("/api/v1/produkte").status_code == 200
