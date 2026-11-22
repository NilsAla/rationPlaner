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
    assert r.portionen == 2 and len(r.zutaten) == 11
    assert "Fremdtext" not in ri.beschreibung(r)


def test_kein_rezept():
    with pytest.raises(ri.ImportError_):
        ri.rezept_aus_html("<html></html>", URL)


@pytest.mark.parametrize(
    ("text", "kern"),
    [
        ("vegane weiße Misopaste", "Misopaste"),
        ("rote Zwiebeln", "Zwiebeln"),
        ("Hähnchenbrust (ca. 300 g), gewürfelt", "Hähnchenbrust"),
        ("frische Petersilie", "Petersilie"),
    ],
)
def test_kern_name(text, kern):
    assert ri.kern_name(text) == kern


def _zwiebel(fake_backend):
    from app.inventory.base import Product

    fake_backend.add_product(Product(id="zwiebel", name="Zwiebel", unit="g"))


def test_vorschau_ein_klick(client, fake_backend):
    _zwiebel(fake_backend)
    v = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    z = {x["name"]: x for x in v["zutaten"]}

    def zeile(name):
        x = z[name]
        return x["aktion"], x["produkt"] or x["neu_name"], x["menge_bestand"], x["status"]

    # Ähnlich ist nicht gleich: neues Produkt, das vorhandene nur als Alternative
    assert zeile("Spitzpaprika") == ("neu", "Spitzpaprika", 250, "ok")
    assert "Paprika rot" in [a["name"] for a in z["Spitzpaprika"]["alternativen"]]
    assert zeile("Frühlingszwiebel") == ("neu", "Frühlingszwiebel", 15, "schaetzung")
    assert z["Frühlingszwiebel"]["stueck_g"] == 15
    # Kern-Name trifft vorhandenes Produkt, Stückgewicht aus der Tabelle
    assert zeile("rote Zwiebeln") == ("zuordnen", "Zwiebel", 160, "schaetzung")
    assert zeile("Nudeln") == ("zuordnen", "Nudeln", 200, "ok")
    assert zeile("Olivenöl") == ("zuordnen", "Olivenöl", 1.5, "ok")  # Produkt führt EL
    # Knoblauchzehe → Knoblauch mit 4 g je Zehe, ohne Stückgewicht fürs Produkt
    assert zeile("Knoblauchzehe") == ("neu", "Knoblauch", 4, "schaetzung")
    assert z["Knoblauchzehe"]["stueck_g"] is None
    assert zeile("Sesamöl") == ("neu", "Sesamöl", 9.2, "ok")
    assert zeile("vegane weiße Misopaste") == ("neu", "Misopaste", 25, "ok")
    assert zeile("Salz") == ("neu", "Salz", 0, "ok")
    assert z["Wasser"]["aktion"] == "weglassen"
    assert zeile("Testkäse")[3] == "pruefen"  # Packung ohne bekannte Größe


def test_import_uebernehmen(client, fake_backend):
    v = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    auswahl = [
        {"index": 1, "produkt_id": "nudeln", "menge": 200},
        {"index": 6, "neu_anlegen": "Frühlingszwiebel", "menge": 15, "stueck_g": 15},
        {"index": 4, "neu_anlegen": "Testkäse", "menge": 125},
    ]
    resp = client.post(f"/api/v1/import/{v['import_id']}/uebernehmen", json={"zutaten": auswahl})
    assert resp.status_code == 201, resp.text
    e = resp.json()
    assert e["neue_produkte"] == ["Frühlingszwiebel", "Testkäse"] and e["weggelassen"] == 8
    fz = next(p for p in client.get("/api/v1/packungen").json() if p["name"] == "Frühlingszwiebel")
    assert fz["standard"] == {"einheit": "Stück", "menge": 15}
    rezepte = {r["name"]: r for r in client.get("/api/v1/rezepte").json()}
    assert rezepte["Testpfanne mit Paprika & Nudeln"]["portionen"] == 2

    # Zweite Vorschau: Dublette erkannt, neues Produkt wird jetzt direkt zugeordnet
    v2 = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    assert v2["dublette"] == e["rezept_id"]
    z2 = {x["name"]: x for x in v2["zutaten"]}
    assert z2["Frühlingszwiebel"]["aktion"] == "zuordnen"
    again = client.post(f"/api/v1/import/{v2['import_id']}/uebernehmen", json={"zutaten": auswahl})
    assert again.status_code == 409


def test_uebernehmen_ohne_produkt(client):
    v = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    resp = client.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen", json={"zutaten": [{"index": 0, "menge": 1}]}
    )
    assert resp.status_code == 422
