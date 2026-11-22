"""Produktdatenbank, Rezeptbuch und Dubletten-Check gegen FakeBackend."""

from app.inventory.base import Product


def test_katalog_und_detail(client):
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 300})
    client.put("/api/v1/aliase", json={"text": "Spaghetti", "produkt_id": "nudeln"})
    liste = {p["id"]: p for p in client.get("/api/v1/katalog/produkte").json()}
    assert liste["nudeln"]["bestand"] == 300
    assert liste["nudeln"]["aliase"] == 1 and liste["nudeln"]["rezepte"] == 1
    assert liste["nudeln"]["packung"] == {"einheit": "Packung", "menge": 500}
    d = client.get("/api/v1/produkte/nudeln").json()
    assert d["aliase"] == ["spaghetti"]
    assert d["rezepte"] == [{"id": "paprika-pasta", "name": "Paprika-Pasta", "menge": 200}]


def test_produkt_aendern(client):
    resp = client.patch(
        "/api/v1/produkte/nudeln",
        json={"kategorie": "Trockenware", "mindestbestand": 500, "tracking_modus": "menge"},
    )
    assert resp.status_code == 200, resp.text
    p = resp.json()
    assert (p["kategorie"], p["mindestbestand"]) == ("Trockenware", 500)
    assert client.patch("/api/v1/produkte/nudeln", json={"fuellstand": "halb"}).status_code == 422
    assert client.patch("/api/v1/produkte/nudeln", json={"parent_id": "nudeln"}).status_code == 422
    assert client.patch("/api/v1/produkte/gibtsnicht", json={"name": "x"}).status_code == 404


def test_rezeptbuch_und_leser(client):
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 500})
    buch = client.get("/api/v1/rezeptbuch").json()
    assert buch[0]["name"] == "Paprika-Pasta"
    assert buch[0]["fehlend"] == ["Paprika rot"]  # Öl ist Füllstand und gilt als da
    assert buch[0]["kochbar"] is False
    r = client.get("/api/v1/rezepte/paprika-pasta?portionen=4").json()
    z = {x["name"]: x for x in r["zutaten"]}
    assert z["Nudeln"]["menge"] == 400 and z["Nudeln"]["ampel"] == "ok"
    assert z["Paprika rot"]["ampel"] == "fehlt"
    assert r["schritte"] == ["Nudeln kochen.", "Paprika in Öl anbraten."]


def test_rezept_bearbeiten(client):
    resp = client.patch(
        "/api/v1/rezepte/paprika-pasta",
        json={
            "name": "Paprika-Pasta scharf",
            "portionen": 3,
            "schritte": ["Nudeln kochen", "Chili & Paprika braten"],
            "zutaten": [{"produkt_id": "nudeln", "menge": 300}],
            "tags": ["schnell", "vegetarisch"],
            "status": "getestet",
        },
    )
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["name"] == "Paprika-Pasta scharf" and r["portionen"] == 3
    assert [z["name"] for z in r["zutaten"]] == ["Nudeln"]
    assert r["schritte"] == ["Nudeln kochen", "Chili & Paprika braten"]
    assert r["tags"] == ["schnell", "vegetarisch"] and r["status"] == "getestet"
    bad = client.patch("/api/v1/rezepte/paprika-pasta", json={"aufwand": "egal"})
    assert bad.status_code == 422
    bad = client.patch(
        "/api/v1/rezepte/paprika-pasta", json={"zutaten": [{"produkt_id": "x", "menge": 1}]}
    )
    assert bad.status_code == 404


def test_dubletten_mergen(client, fake_backend):
    fake_backend.add_product(Product(id="nudel2", name="Nudel", unit="g", grams_per_unit=1))
    fake_backend.add_product(Product(id="reis", name="Reis", unit="g"))
    client.post(
        "/api/v1/bestand", json={"produkt_id": "nudel2", "menge": 100, "lagerort_id": "vorrat"}
    )
    client.put("/api/v1/aliase", json={"text": "Fusilli", "produkt_id": "nudel2"})
    kandidaten = client.get("/api/v1/dubletten").json()
    assert [(k["a"]["name"], k["b"]["name"]) for k in kandidaten] == [("Nudeln", "Nudel")]
    assert kandidaten[0]["score"] == 1.0
    resp = client.post(
        "/api/v1/dubletten/mergen", json={"behalten": "nudeln", "entfernen": "nudel2"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["aliase_umgehaengt"] == 1
    liste = {p["id"]: p for p in client.get("/api/v1/katalog/produkte").json()}
    assert "nudel2" not in liste and liste["nudeln"]["bestand"] == 100
    assert client.get("/api/v1/dubletten").json() == []


def test_dubletten_ignorieren(client, fake_backend):
    fake_backend.add_product(Product(id="nudel2", name="Nudel", unit="g"))
    assert len(client.get("/api/v1/dubletten").json()) == 1
    client.post("/api/v1/dubletten/ignorieren", json={"behalten": "nudel2", "entfernen": "nudeln"})
    assert client.get("/api/v1/dubletten").json() == []
    # niedrigere Schwelle zeigt mehr, ignoriertes Paar bleibt draußen
    assert client.get("/api/v1/dubletten?schwelle=0.5").json() == []
