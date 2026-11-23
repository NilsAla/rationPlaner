from app import API_VERSION


def test_health_und_version(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/version").json()["api_version"] == API_VERSION


def test_index_relativ_fuer_ingress(client):
    resp = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.status_code == 200
    # Alle Pfade relativ, damit es hinter Ingress funktioniert
    assert 'src="static/js/main.js?v=' in resp.text
    assert 'href="/' not in resp.text and 'src="/' not in resp.text
    lib = client.get("/static/js/lib.js")
    assert lib.status_code == 200
    assert 'fetch("api/v1/" + path' in lib.text
    assert lib.headers["cache-control"] == "no-cache"


def test_alle_views_vorhanden(client):
    """Jede Route aus dem Menü und den Detailseiten hat ein View-Modul."""
    import re

    main = client.get("/static/js/main.js").text
    views = set(re.findall(r'view: "(\w+)"', main)) | set(re.findall(r'\], "(\w+)", "', main))
    assert len(views) > 15
    for v in views:
        assert client.get(f"/static/js/views/{v}.js").status_code == 200, v


def test_testdaten_nur_mit_grocy(client):
    assert client.post("/api/v1/testdaten").status_code == 501
