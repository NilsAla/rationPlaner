from fastapi.testclient import TestClient

from app import API_VERSION
from app.main import app


def test_health_und_version():
    client = TestClient(app)
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/version").json()["api_version"] == API_VERSION


def test_index_hinter_ingress():
    client = TestClient(app)
    resp = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.status_code == 200
    assert "/api/hassio_ingress/abc" in resp.text
    assert 'fetch("api/v1/health")' in resp.text
