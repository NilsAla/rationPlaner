"""Austauschbare Zutaten: Fusilli statt Spaghetti (Geschwister unter demselben Parent-Produkt)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import kueche
from app.inventory.base import Ingredient, Location, Product, Recipe, TrackingMode
from app.inventory.fake import FakeBackend
from app.main import create_app
from app.settings import Settings

HEUTE = date.today()


@pytest.fixture
def backend():
    produkte = [
        Product(id="nudeln", name="Nudeln", unit="g", tracking_mode=TrackingMode.MENGE),
        Product(id="spaghetti", name="Spaghetti", unit="g", parent_id="nudeln",
                pack_unit="Packung", pack_size=500, default_location_id="v"),
        Product(id="fusilli", name="Fusilli", unit="g", parent_id="nudeln",
                default_location_id="v"),
        Product(id="lasagne", name="Lasagneplatten", unit="Stück", parent_id="nudeln",
                default_location_id="v"),
        Product(id="tomaten", name="Tomaten passiert", unit="g", default_location_id="v"),
    ]  # fmt: skip
    rezepte = [
        Recipe(id="pasta", name="Spaghetti Pomodoro", servings=2,
               ingredients=(Ingredient("spaghetti", 250), Ingredient("tomaten", 400))),
    ]  # fmt: skip
    return FakeBackend(produkte, [Location(id="v", name="Vorrat")], rezepte)


@pytest.fixture
def client(backend, fake_ha, tmp_path):
    app = create_app(
        inventory=backend,
        ha=fake_ha,
        app_settings=Settings(todo_entity="todo.einkauf", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'e.db'}",
    )
    with TestClient(app) as c:
        yield c


def einbuchen(c, pid, menge):
    assert c.post("/api/v1/bestand", json={"produkt_id": pid, "menge": menge}).status_code == 201


def test_ersatz_karte_gleiche_einheit():
    produkte = {
        "s": Product(id="s", name="S", unit="g", parent_id="n"),
        "f": Product(id="f", name="F", unit="g", parent_id="n"),
        "l": Product(id="l", name="L", unit="Stück", parent_id="n"),
        "x": Product(id="x", name="X", unit="g"),
    }
    assert kueche.ersatz_karte(produkte) == {"s": ["f"], "f": ["s"]}
    assert kueche.ersatz_karte(produkte, {"n"}) == {}


def test_rezept_kochbar_mit_fusilli(client):
    einbuchen(client, "fusilli", 500)
    einbuchen(client, "tomaten", 400)
    buch = {r["id"]: r for r in client.get("/api/v1/rezeptbuch").json()}
    assert buch["pasta"]["kochbar"] is True
    z = {x["produkt_id"]: x for x in client.get("/api/v1/rezepte/pasta").json()["zutaten"]}
    assert z["spaghetti"]["ampel"] == "ok" and z["spaghetti"]["ersatz"] == ["Fusilli"]
    # Gruppe gesperrt → nicht mehr kochbar
    client.put("/api/v1/einstellungen/ersatz", json={"gesperrt": ["nudeln"]})
    assert {r["id"]: r for r in client.get("/api/v1/rezeptbuch").json()}["pasta"][
        "kochbar"
    ] is False


def test_gekocht_bucht_fusilli_ab(client):
    einbuchen(client, "spaghetti", 100)
    einbuchen(client, "fusilli", 500)
    einbuchen(client, "tomaten", 400)
    r = client.post(
        "/api/v1/gekocht",
        json={"rezept_id": "pasta", "portionen_gekocht": 2, "portionen_gegessen": 2},
    ).json()
    assert r["fehlend"] == []
    ab = {x["name"]: x["menge"] for x in r["abgebucht"]}
    assert ab["Spaghetti"] == 100 and ab["Fusilli (statt Spaghetti)"] == 150
    bestand = {b["name"]: b["menge"] for b in client.get("/api/v1/bestand").json()}
    assert bestand == {"Fusilli": 350}


def test_einkaufsliste_kauft_keine_spaghetti(client, fake_ha):
    einbuchen(client, "fusilli", 500)
    einbuchen(client, "tomaten", 400)
    client.put(f"/api/v1/plan/{HEUTE.isoformat()}", json={"rezept_id": "pasta", "portionen": 2})
    r = client.post("/api/v1/einkaufsliste", json={"tage": 1}).json()
    assert r["positionen"] == []
    # zwei Mal geplant: Fusilli reichen nur einmal (500 g für 2 × 250 g reichen genau)
    client.put(
        f"/api/v1/plan/{(HEUTE + timedelta(days=1)).isoformat()}",
        json={"rezept_id": "pasta", "portionen": 4},
    )
    r = client.post("/api/v1/einkaufsliste", json={"tage": 2}).json()
    pos = {p["name"]: p["menge"] for p in r["positionen"]}
    assert pos.get("Spaghetti") == 500  # 750 g Bedarf − 500 g Fusilli = 250 → 1 Packung


def test_planer_und_spontan_sehen_ersatz(client):
    einbuchen(client, "fusilli", 500)
    einbuchen(client, "tomaten", 400)
    sp = client.get("/api/v1/spontan").json()["rezepte"]
    assert [r["rezept_id"] for r in sp] == ["pasta"] and sp[0]["abdeckung"] == 1.0
    resp = client.post("/api/v1/plan/erzeugen", json={"tage": 1, "ki": False})
    lauf = client.get(f"/api/v1/plan/laeufe/{resp.json()['lauf_id']}").json()
    assert lauf["tage"][0]["rezept_id"] == "pasta"
    assert lauf["einkauf"] == []  # Planer kauft keine Spaghetti, Fusilli reichen


@pytest.fixture
def k_client(fake_ha, tmp_path):
    produkte = [
        Product(id="nudeln", name="Nudeln", unit="g"),
        Product(id="spaghetti", name="Spaghetti", unit="g"),
        Product(id="fusilli", name="Fusilli", unit="g"),
        Product(id="lasagne", name="Lasagneplatten", unit="Stück"),
        Product(id="basmati", name="Basmatireis", unit="g"),
        Product(id="jasmin", name="Jasminreis", unit="g"),
    ]
    b = FakeBackend(produkte, [Location(id="v", name="Vorrat")])
    app = create_app(
        inventory=b,
        ha=fake_ha,
        app_settings=Settings(ai_task_entity="ai_task.test", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'g.db'}",
    )
    with TestClient(app) as c:
        c.backend = b
        yield c


def test_ki_schlaegt_gruppen_vor_review(k_client, fake_ha):
    fake_ha.ki_antwort = {
        "gruppen": [
            # Lasagne hat andere Einheit → fällt raus, Parent "Nudeln" existiert
            {
                "name": "Nudeln",
                "parent_id": "nudeln",
                "mitglieder": ["spaghetti", "fusilli", "lasagne"],
            },
            {"name": "Reis", "parent_id": "", "mitglieder": ["basmati", "jasmin"]},
            {"name": "Unsinn", "mitglieder": ["gibtsnicht"]},
        ]
    }
    r = k_client.post(
        "/api/v1/kategorisieren", json={"produkte": False, "rezepte": False, "gruppen": True}
    )
    assert r.status_code == 202, r.text
    vor = {v["objekt_id"]: v for v in k_client.get("/api/v1/vorschlaege").json()}
    assert set(vor) == {"spaghetti", "fusilli", "basmati", "jasmin"}
    assert vor["spaghetti"]["neu"]["parent_id"] == "nudeln"
    assert vor["basmati"]["neu"] == {"parent_id": None, "name": "Reis", "einheit": "g"}
    assert "Austauschgruppen" in k_client.get("/api/v1/log/ki").json()[0]["aufgabe"]
    # nichts gesetzt, bis übernommen
    assert k_client.backend._products["spaghetti"].parent_id is None
    k_client.post("/api/v1/vorschlaege", json={"uebernehmen": [v["id"] for v in vor.values()]})
    p = k_client.backend._products
    reis = next(x for x in p.values() if x.name == "Reis")
    assert p["spaghetti"].parent_id == p["fusilli"].parent_id == "nudeln"
    assert p["basmati"].parent_id == p["jasmin"].parent_id == reis.id
    assert kueche.ersatz_karte(p)["spaghetti"] == ["fusilli"]
