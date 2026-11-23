"""Barcode-Scan: Abgleich bekannter EANs, Open Food Facts (gemockt), Einbuchen mit Review-Regeln."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.barcode import off_auswerten
from app.main import create_app
from app.settings import Settings

EAN = "4006381333931"
OFF = {
    "status": 1,
    "product": {
        "product_name_de": "Spaghetti n.5",
        "brands": "Barilla, Barilla G. e R. Fratelli",
        "quantity": "500 g",
        "categories_tags": ["en:pastas", "en:spaghetti"],
        "nutriments": {
            "energy-kcal_100g": 359,
            "proteins_100g": 12.5,
            "fat_100g": 2,
            "saturated-fat_100g": 0.5,
            "carbohydrates_100g": 71.2,
            "sugars_100g": 3.5,
            "fiber_100g": 3,
            "salt_100g": 0.01,
        },  # fmt: skip
    },
}


class OffFake:
    def __init__(self):
        self.aufrufe = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.aufrufe += 1
        assert "rationPlaner" in request.headers["user-agent"]
        if EAN in str(request.url):
            return httpx.Response(200, json=OFF)
        return httpx.Response(404, json={"status": 0})


@pytest.fixture
def off():
    return OffFake()


@pytest.fixture
def s_client(fake_backend, fake_ha, tmp_path, off):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 's.db'}",
        off_http=httpx.AsyncClient(transport=httpx.MockTransport(off)),
    )
    with TestClient(app) as c:
        yield c


def test_off_auswerten_gebinde():
    d = off_auswerten({"product_name": "Wasser", "quantity": "6 x 1,5 l", "brands": "Gut"})
    assert (d["menge"], d["einheit"], d["marke"], d["naehrwerte"]) == (9000, "ml", "Gut", None)


def test_unbekannt_fragt_off_und_schlaegt_produkte_vor(s_client, off):
    r = s_client.get(f"/api/v1/barcode/{EAN}").json()
    assert r["bekannt"] is None
    assert (r["off"]["name"], r["off"]["marke"], r["off"]["menge"]) == (
        "Spaghetti n.5",
        "Barilla",
        500,
    )
    assert r["off"]["naehrwerte"]["kcal"] == 359
    s_client.get(f"/api/v1/barcode/{EAN}")
    assert off.aufrufe == 1  # Cache
    assert s_client.get("/api/v1/barcode/12345678").json()["off"] is None
    assert s_client.get("/api/v1/barcode/123").status_code == 422


def test_einbuchen_merkt_barcode_und_groesse(s_client, fake_backend, off):
    s_client.get(f"/api/v1/barcode/{EAN}")
    r = s_client.post(
        "/api/v1/barcode/einbuchen",
        json={
            "eintraege": [
                {
                    "ean": EAN,
                    "produkt_id": "nudeln",
                    "anzahl": 2,
                    "packung_menge": 500,
                    "marke": "Barilla",
                }
            ]
        },  # fmt: skip
    ).json()
    assert r["gebucht"][0]["menge"] == 1000 and not r["fehler"]
    assert {b["name"]: b["menge"] for b in s_client.get("/api/v1/bestand").json()} == {
        "Nudeln": 1000
    }
    # jetzt bekannt: kein OFF-Aufruf mehr nötig, Größe aus der Variante
    off.aufrufe = 0
    k = s_client.get(f"/api/v1/barcode/{EAN}").json()
    assert k["bekannt"]["produkt_id"] == "nudeln"
    assert (k["groesse"]["menge"], k["groesse"]["quelle"], k["groesse"]["marke"]) == (
        500,
        "variante",
        "Barilla",
    )
    assert off.aufrufe == 0
    # Nährwerte nur als Vorschlag
    vor = [v for v in s_client.get("/api/v1/vorschlaege").json() if v["feld"] == "naehrwerte"]
    assert vor and vor[0]["neu"]["kcal"] == 359
    assert s_client.get("/api/v1/produkte/nudeln/naehrwerte").status_code in (200, 404)


def test_neues_produkt_und_fremder_barcode(s_client, fake_backend):
    r = s_client.post(
        "/api/v1/barcode/einbuchen",
        json={
            "eintraege": [
                {
                    "ean": "12345678",
                    "neu_name": "Hafermilch",
                    "einheit": "ml",
                    "packung_menge": 1000,
                    "naehrwerte": False,
                }
            ]
        },  # fmt: skip
    ).json()
    assert r["neue_produkte"] == ["Hafermilch"]
    neu = next(p for p in fake_backend._products.values() if p.name == "Hafermilch")
    assert (neu.unit, neu.pack_size) == ("ml", 1000)
    # derselbe Barcode für ein anderes Produkt: Fehler je Eintrag, nichts umgehängt
    r = s_client.post(
        "/api/v1/barcode/einbuchen",
        json={"eintraege": [{"ean": "12345678", "produkt_id": "nudeln", "packung_menge": 500}]},
    ).json()
    assert r["gebucht"] == [] and "gehört schon" in r["fehler"][0]["fehler"]


def test_bild_lesen_ohne_barcode(s_client):
    pytest.importorskip("pyzbar.pyzbar", exc_type=ImportError)
    import base64
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(b, "PNG")
    r = s_client.post(
        "/api/v1/barcode/lesen", json={"daten": base64.b64encode(b.getvalue()).decode()}
    )
    assert r.status_code == 404 and "Kein Barcode" in r.json()["detail"]
    assert json.dumps(r.json())


def test_vorschlag_ueber_kategorie(s_client, fake_backend):
    from dataclasses import replace

    fake_backend._products["nudeln"] = replace(
        fake_backend._products["nudeln"], category="Nudeln & Reis"
    )
    r = s_client.get(f"/api/v1/barcode/{EAN}").json()  # "Spaghetti n.5", en:pastas
    assert r["vorschlaege"][0]["produkt_id"] == "nudeln"
