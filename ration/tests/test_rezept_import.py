"""Rezept-Import: JSON-LD lesen, Zutaten parsen, zuordnen, Review übernehmen."""

from pathlib import Path

import pytest

from app import rezept_import as ri

HTML = (Path(__file__).parent / "fixtures" / "rezept_jsonld.html").read_text(encoding="utf-8")
URL = "https://www.hellofresh.de/recipes/testpfanne-mit-paprika-0123456789abcdef01234567"


@pytest.mark.parametrize(
    ("text", "menge", "einheit", "name"),
    [
        ("250 g Spitzpaprika", 250, "g", "Spitzpaprika"),
        ("200g Nudeln", 200, "g", "Nudeln"),
        ("1 ½ EL Olivenöl", 1.5, "el", "Olivenöl"),
        ("½ Stück Zwiebel", 0.5, "stück", "Zwiebel"),
        ("2 rote Zwiebeln", 2, None, "rote Zwiebeln"),
        ("1 Packung(en) Mozzarella", 1, "packung(en)", "Mozzarella"),
        ("0,5 kg Kartoffeln", 0.5, "kg", "Kartoffeln"),
        ("Salz", None, None, "Salz"),
    ],
)
def test_zutat_parsen(text, menge, einheit, name):
    z = ri.zutat_parsen(text)
    assert (z.menge, z.einheit, z.name) == (menge, einheit, name)


def test_quelle_hellofresh_id():
    assert ri.quelle_von(URL) == ("hellofresh", "0123456789abcdef01234567")
    assert ri.quelle_von("https://example.org/rezept/x/")[0] == "example.org"


def test_rezept_aus_html():
    r = ri.rezept_aus_html(HTML, URL)
    assert r.name == "Testpfanne mit Paprika & Nudeln"
    assert r.portionen == 2 and len(r.zutaten) == 6
    assert "Fremdtext" not in ri.beschreibung(r)


def test_kein_rezept():
    with pytest.raises(ri.ImportError_):
        ri.rezept_aus_html("<html></html>", URL)


def test_import_vorschau_und_uebernehmen(client):
    v = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    assert v["quelle"] == "hellofresh" and v["dublette"] is None
    z = {x["name"]: x for x in v["zutaten"]}
    # Teilwort-Treffer, Stück-Packung 160 g wird nicht genutzt, weil g angegeben ist
    assert z["Spitzpaprika"]["produkt"] == "Paprika rot"
    assert z["Spitzpaprika"]["status"] == "vorschlag" and z["Spitzpaprika"]["menge_bestand"] == 250
    assert z["Nudeln"]["status"] == "ok" and z["Nudeln"]["menge_bestand"] == 200
    assert z["Olivenöl"]["menge_bestand"] == 1.5  # Produkt führt EL
    assert z["Salz"]["status"] == "unbekannt"

    auswahl = [
        {"index": 0, "produkt_id": "paprika", "menge": 250},
        {"index": 1, "produkt_id": "nudeln", "menge": 200},
        {"index": 4, "neu_anlegen": "Testkäse", "menge": 125},
    ]
    resp = client.post(f"/api/v1/import/{v['import_id']}/uebernehmen", json={"zutaten": auswahl})
    assert resp.status_code == 201, resp.text
    e = resp.json()
    assert e["neue_produkte"] == ["Testkäse"] and e["weggelassen"] == 3
    rezepte = {r["name"]: r for r in client.get("/api/v1/rezepte").json()}
    assert rezepte["Testpfanne mit Paprika & Nudeln"]["portionen"] == 2

    # Zweite Vorschau: Dublette erkannt, bestätigte Zuordnung als Alias gelernt
    v2 = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    assert v2["dublette"] == e["rezept_id"]
    assert {x["name"]: x for x in v2["zutaten"]}["Spitzpaprika"]["status"] == "ok"
    again = client.post(f"/api/v1/import/{v2['import_id']}/uebernehmen", json={"zutaten": auswahl})
    assert again.status_code == 409


def test_uebernehmen_ohne_produkt(client):
    v = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    resp = client.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen", json={"zutaten": [{"index": 0, "menge": 1}]}
    )
    assert resp.status_code == 422
