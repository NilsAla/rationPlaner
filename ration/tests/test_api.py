from fastapi.testclient import TestClient

from app import API_VERSION
from app.main import app


def test_health_und_version():
    client = TestClient(app)
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/version").json()["api_version"] == API_VERSION
