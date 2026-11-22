"""Durchstich: Einbuchen → Plan → Heute → Gekocht → Einkaufsliste → Status."""

from dataclasses import replace
from datetime import date, timedelta

HEUTE = date.today().isoformat()


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _produkt(backend, product_id):
    return backend._products[product_id]


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
    paprika = [b for b in client.get("/api/v1/bestand").json() if b["produkt_id"] == "paprika"]
    assert [(b["menge"], b["mhd"]) for b in paprika] == [(250, _in(5))]  # früheste Charge weg
    assert client.get("/api/v1/heute").json()["status"] == "gekocht"
    # Rest: 1 Portion als „Rest: Paprika-Pasta“ im Kühlschrank, 3 Tage haltbar
    assert e["rest"]["name"] == "Rest: Paprika-Pasta" and e["rest"]["menge"] == 1
    reste = client.get("/api/v1/reste").json()
    assert [(r["name"], r["portionen"], r["ablauf"]) for r in reste] == [
        ("Rest: Paprika-Pasta", 1, _in(3))
    ]
    assert client.get("/api/v1/status").json()["text"].endswith("· 1 Rest")
    # Füllstand wird beim ersten Mal abgefragt, danach 14 Tage nicht
    assert [f["name"] for f in e["fuellstand_fragen"]] == ["Olivenöl"]
    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": "paprika-pasta", "portionen": 2})
    e2 = client.post(
        "/api/v1/gekocht", json={"portionen_gekocht": 2, "portionen_gegessen": 2}
    ).json()
    assert e2["fuellstand_fragen"] == [] and e2["rest"] is None
    # Rest gegessen
    rest_id = reste[0]["produkt_id"]
    r = client.post("/api/v1/bestand/abbuchen", json={"produkt_id": rest_id, "menge": 1})
    assert r.status_code == 200 and client.get("/api/v1/reste").json() == []
    zu_viel = client.post("/api/v1/bestand/abbuchen", json={"produkt_id": rest_id, "menge": 1})
    assert zu_viel.status_code == 409


def test_fuellstand_einkauf(client, fake_ha):
    assert client.get("/api/v1/fuellstand").json() == [
        {"produkt_id": "oel", "name": "Olivenöl", "stand": "voll"}
    ]
    assert client.put("/api/v1/fuellstand/nudeln", json={"stand": "leer"}).status_code == 422
    client.put("/api/v1/fuellstand/oel", json={"stand": "knapp"})
    e = client.post("/api/v1/einkaufsliste", json={"tage": 1}).json()
    assert "Olivenöl" in e["hinzugefuegt"]  # ohne Packungsgröße nur der Name


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
    # 400 g Nudeln benötigt, 150 da → 250 g fehlen → eine ganze Packung. Öl nie.
    assert e["hinzugefuegt"] == ["Nudeln, 1 × Packung (500 g)"]
    # 500 g Paprika à 160 g → 4 Stück
    assert e["schon_auf_liste"] == ["Paprika rot, 4 × Stück (160 g)"]
    assert "Nudeln, 1 × Packung (500 g)" in fake_ha.items
    nudeln = next(p for p in e["positionen"] if p["name"] == "Nudeln")
    assert nudeln["packungen"][0]["anzahl"] == 1 and nudeln["menge"] == 500


def test_einkaufsliste_ohne_packung_ganze_einheiten(client, fake_backend, fake_ha):
    fake_backend.add_product(replace(_produkt(fake_backend, "paprika"), pack_size=None))
    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": "paprika-pasta", "portionen": 1})
    e = client.post("/api/v1/einkaufsliste", json={"tage": 1}).json()
    assert "Paprika rot 125 g" in e["hinzugefuegt"]


def test_einkaufsliste_mindestbestand_auch_ungeplant(client, fake_backend, fake_ha):
    fake_backend.add_product(replace(_produkt(fake_backend, "nudeln"), min_stock=500))
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 300})
    e = client.post("/api/v1/einkaufsliste", json={"tage": 7}).json()
    assert e["gerichte"] == 0
    assert e["hinzugefuegt"] == ["Nudeln, 1 × Packung (500 g)"]


def test_status(client):
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 200, "mhd": _in(1)})
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 500, "mhd": _in(90)})
    s = client.get("/api/v1/status").json()
    assert s["artikel"] == 2
    assert [x["name"] for x in s["laeuft_bald_ab"]] == ["Paprika rot"]
    assert s["text"] == "2 Artikel · 1 laufen bald ab"
