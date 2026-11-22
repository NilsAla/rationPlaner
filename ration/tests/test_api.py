from app import API_VERSION


def test_health_und_version(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/version").json()["api_version"] == API_VERSION


def test_index_relativ_fuer_ingress(client):
    resp = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.status_code == 200
    assert 'fetch("api/v1/" + path' in resp.text


def test_testdaten_nur_mit_grocy(client):
    assert client.post("/api/v1/testdaten").status_code == 501
