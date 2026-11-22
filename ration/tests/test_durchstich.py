"""Durchstich: Einbuchen → Plan → Heute → Gekocht → Einkaufsliste → Status."""

from datetime import date, timedelta

HEUTE = date.today().isoformat()


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_einbuchen_mit_standard_lagerort(client):
    resp = client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 500})
    assert resp.status_code == 201
    bestand = client.get("/api/v1/bestand").json()
    assert bestand == [
        {
            "produkt_id": "paprika",
            "name": "Paprika rot",
            "menge": 500,
            "einheit": "g",
            "mhd": None,
            "lagerort": "Kühlschrank",
            "geoeffnet": False,
        }
    ]


def test_heute_ohne_plan(client):
    assert client.get("/api/v1/heute").json()["rezept"] is None


def test_heute_skaliert_portionen(client):
    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": "paprika-pasta", "portionen": 1})
    rezept = client.get("/api/v1/heute").json()["rezept"]
    assert rezept["name"] == "Paprika-Pasta"
    assert {z["name"]: z["menge"] for z in rezept["zutaten"]}["Paprika rot"] == 125
    assert rezept["schritte"] == ["Nudeln kochen.", "Paprika in Öl anbraten."]


def test_gekocht_fefo_und_modi(client, fake_backend):
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 300, "mhd": _in(5)})
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 200, "mhd": _in(2)})
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 100})
    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": "paprika-pasta", "portionen": 2})

    e = client.post(
        "/api/v1/gekocht", json={"portionen_gekocht": 2, "portionen_gegessen": 1}
    ).json()

    assert {p["name"]: p["menge"] for p in e["abgebucht"]} == {"Paprika rot": 250, "Nudeln": 100}
    assert [(p["name"], p["menge"]) for p in e["fehlend"]] == [("Nudeln", 100)]
    assert [p["name"] for p in e["nicht_abgebucht"]] == ["Olivenöl"]  # Füllstand
    assert e["rest_portionen"] == 1
    rest = client.get("/api/v1/bestand").json()
    assert [(b["menge"], b["mhd"]) for b in rest] == [(250, _in(5))]  # früheste Charge zuerst weg
    assert client.get("/api/v1/heute").json()["status"] == "gekocht"


def test_gekocht_ohne_plan_und_ohne_rezept(client):
    resp = client.post("/api/v1/gekocht", json={"portionen_gekocht": 1, "portionen_gegessen": 1})
    assert resp.status_code == 422


def test_einkaufsliste_summiert_und_dedupliziert(client, fake_ha):
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 150})
    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": "paprika-pasta", "portionen": 2})
    client.put(f"/api/v1/plan/{_in(2)}", json={"rezept_id": "paprika-pasta", "portionen": 2})
    fake_ha.items.append("Paprika rot 100 g")

    e = client.post("/api/v1/einkaufsliste", json={"tage": 7}).json()

    assert e["gerichte"] == 2
    assert e["hinzugefuegt"] == ["Nudeln 250 g"]  # 400 benötigt, 150 da; Öl nie
    assert e["schon_auf_liste"] == ["Paprika rot 500 g"]
    assert "Nudeln 250 g" in fake_ha.items


def test_status(client):
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 200, "mhd": _in(1)})
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 500, "mhd": _in(90)})
    s = client.get("/api/v1/status").json()
    assert s["artikel"] == 2
    assert [x["name"] for x in s["laeuft_bald_ab"]] == ["Paprika rot"]
    assert s["text"] == "2 Artikel · 1 laufen bald ab"
