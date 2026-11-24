"""Rezept-Import: JSON-LD lesen, Zutaten parsen, zuordnen, Review übernehmen."""

import json
from pathlib import Path

import pytest

from app import kueche
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
        ("1 Packung(en) Mozzarella", 1, "packung", "Mozzarella"),
        ("1 Prise(n) Zucker", 1, "prise", "Zucker"),
        ("8 Scheibe/n Parmaschinken", 8, "scheibe", "Parmaschinken"),
        ("1 Pck. Pizzateig (260 g)", 1, "packung", "Pizzateig (260 g)"),
        ("3 EL Olivenöl (plus etwas)", 3, "el", "Olivenöl"),
        ("Salz für das Kochwasser", None, None, "Salz"),
        ("0,5 kg Kartoffeln", 0.5, "kg", "Kartoffeln"),
        ("Salz", None, None, "Salz"),
        ("1 Esslöffel Öl", 1, "el", "Öl"),
        ("2 Teelöffel Zucker", 2, "tl", "Zucker"),
        ("1 Stk. Ei", 1, "stück", "Ei"),
        ("nach Geschmack Salz", None, None, "Salz"),
        ("Salz nach Geschmack", None, None, "Salz"),
        ("Salz, nach Belieben", None, None, "Salz"),
        ("Pfeffer (frisch gemahlen)", None, None, "Pfeffer"),
        ("etwas Olivenöl zum Anbraten", None, None, "Olivenöl"),
        ("1 EL Butter zum Einfetten", 1, "el", "Butter"),
        ("Prise Salz", 1, "prise", "Salz"),
        ("1 Bund Petersilie, optional", 1, "bund", "Petersilie"),
        ("Pfeffer aus der Mühle", None, None, "Pfeffer"),
        ("nach Geschmack", None, None, "nach Geschmack"),  # nur Füllwort: Text bleibt
    ],
)
def test_zutat_parsen(text, menge, einheit, name):
    z = ri.zutat_parsen(text)
    assert (z.menge, z.einheit, z.name) == (menge, einheit, name)


@pytest.mark.parametrize(
    ("text", "namen"),
    [
        ("Salz, Pfeffer, Paprika (edelsüß)", ["Salz", "Pfeffer", "Paprika (edelsüß)"]),
        ("Salz, schwarzer Pfeffer", ["Salz", "schwarzer Pfeffer"]),
        ("Weißwein, trockener", ["Weißwein, trockener"]),
        ("Mandeln, (gemahlen)", ["Mandeln, (gemahlen)"]),
    ],
)
def test_gewuerzliste_getrennt(text, namen):
    assert [z.name for z in ri.zutaten_parsen(text)] == namen


def test_hellofresh_varianten_aus_schritten():
    schritt = {
        "@type": "HowToStep",
        "text": "Erhitze 300 ml [450 ml | 600 ml] Wasser\\* und 1 EL [1,5 EL I 2 EL] Öl*.",
    }
    assert ri.schritte_aus([schritt]) == ["Erhitze 300 ml Wasser und 1 EL Öl."]
    assert ri.schritte_aus(["Die Hälfte [drei Viertel | den ganzen] Ingwer."]) == [
        "Die Hälfte Ingwer."
    ]
    assert ri.schritte_aus(["Topf [groß] nehmen."]) == ["Topf [groß] nehmen."]


def test_hellofresh_name_ohne_werbung():
    def seite(name, headline):
        ld = {"@type": "Recipe", "name": f"{name} {headline}", "recipeIngredient": ["1 Ei"]}
        nd = {
            "props": {"pageProps": {"ssrPayload": {"recipe": {"name": name, "headline": headline}}}}
        }
        return (
            f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
        )

    r = ri.rezept_aus_html(seite("Flammkuchen", "Nur 10 Minuten aktive Kochzeit"), URL)
    assert r.name == "Flammkuchen"
    r = ri.rezept_aus_html(seite("Curry mit Naan", "dazu Reis"), URL)
    assert r.name == "Curry mit Naan, dazu Reis"
    r = ri.rezept_aus_html(seite("Grillplatte", "Bifteki und Zaziki"), URL)
    assert r.name == "Grillplatte, Bifteki und Zaziki"


KARTE = """<h1>Burger aus dem Video</h1><h2>Das Rezept aus dem Video</h2>
<div id="list-recipes0"><div class="ds-list-view-item ds-list-view-item--recipe">
<a href="https://www.chefkoch.de/rezepte/42/Test-Burger.html">Test-Burger</a></div></div>
<div id="gallery-recipeGallery0"><div class="ds-slider__item">
<a href="https://www.chefkoch.de/rezepte/7/Anderes.html">x</a></div></div>"""


def test_rezept_karte_im_artikel(monkeypatch):
    assert ri.rezept_karte(KARTE) == "https://www.chefkoch.de/rezepte/42/Test-Burger.html"
    assert ri.rezept_karte(ARTIKEL) is None  # Rezept steht im Artikel selbst

    ld = {"@type": "Recipe", "name": "Test-Burger von Grillfan", "recipeIngredient": ["1 Brötchen"]}
    seiten = {
        "https://www.chefkoch.de/rezepte/42/Test-Burger.html": (
            f'<script type="application/ld+json">{json.dumps(ld)}</script>'
        )
    }

    async def laden(url, client=None):
        return seiten[url]

    monkeypatch.setattr(ri, "laden", laden)
    import asyncio

    r = asyncio.run(ri.rezept_laden("https://www.chefkoch.de/magazin/artikel/1-burger", KARTE))
    assert r.name == "Test-Burger" and r.url.endswith("/rezepte/42/Test-Burger.html")


def test_salz_und_pfeffer_getrennt():
    assert [z.name for z in ri.zutaten_parsen("Salz und Pfeffer")] == ["Salz", "Pfeffer"]
    assert [z.name for z in ri.zutaten_parsen("Salz & Pfeffer nach Geschmack")] == [
        "Salz",
        "Pfeffer",
    ]
    # mit Menge oder mehrteiligem Namen nicht trennen
    assert len(ri.zutaten_parsen("200 g Erbsen und Möhren")) == 1
    assert ri.zutat_parsen("nach Geschmack Salz").text == "nach Geschmack Salz"  # Original bleibt


def test_quelle_hellofresh_id():
    assert ri.quelle_von(URL) == ("hellofresh", "0123456789abcdef01234567")
    assert ri.quelle_von("https://example.org/rezept/x/")[0] == "example.org"


def test_rezept_aus_html():
    r = ri.rezept_aus_html(HTML, URL)
    assert r.name == "Testpfanne mit Paprika & Nudeln"
    assert r.portionen == 2 and len(r.zutaten) == 11
    assert r.schritte == [
        "Vorbereitung:",
        "Paprika & Zwiebeln in Streifen schneiden.",
        "Nudeln kochen. Abgießen.",
    ]
    html = ri.beschreibung(r)
    assert html.startswith("<p>Vorbereitung:</p><p>Paprika &amp; Zwiebeln")
    assert html.endswith("Originalrezept (hellofresh)</a></p>")
    # Küchen-Card zerlegt die Beschreibung wieder, der Quellenlink ist kein Schritt
    assert kueche.schritte(html) == r.schritte


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [
        (
            "1. Wasser kochen. 2. Nudeln rein. 3. Abgießen.",
            ["Wasser kochen.", "Nudeln rein.", "Abgießen."],
        ),
        ("Schritt 1: Ofen an\nSchritt 2: Backen", ["Ofen an", "Backen"]),
        ("Zeile eins\n\nZeile zwei", ["Zeile eins", "Zeile zwei"]),
        ("Ca. 2. Hälfte nehmen und 200 g Mehl.", ["Ca. 2. Hälfte nehmen und 200 g Mehl."]),
        ("<p>1) Schneiden</p><p>2) Braten</p>", ["Schneiden", "Braten"]),
        (
            "Vorab alles bereitstellen. 1. Schneiden 2. Braten",
            ["Vorab alles bereitstellen.", "Schneiden", "Braten"],
        ),
        ("", []),
    ],
)
def test_schritte_aus_text(eingabe, erwartet):
    assert ri.schritte_aus(eingabe) == erwartet


def test_schritte_formen():
    assert ri.schritte_aus(["Eins", "Zwei"]) == ["Eins", "Zwei"]
    assert ri.schritte_aus([{"@type": "HowToStep", "name": "Nur Name"}]) == ["Nur Name"]
    assert ri.schritte_aus(None) == [] and ri.schritte_aus(42) == []
    lang = ri.schritte_begrenzen(["x" * 5000] * 100)
    assert len(lang) == ri.MAX_SCHRITTE and len(lang[0]) == ri.MAX_SCHRITT_ZEICHEN


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
    assert len(v["schritte"]) == 3
    leser = client.get(f"/api/v1/rezepte/{e['rezept_id']}").json()
    assert leser["schritte"] == v["schritte"]

    # Zweite Vorschau: Dublette erkannt, neues Produkt wird jetzt direkt zugeordnet
    v2 = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    assert v2["dublette"] == e["rezept_id"]
    z2 = {x["name"]: x for x in v2["zutaten"]}
    assert z2["Frühlingszwiebel"]["aktion"] == "zuordnen"
    again = client.post(f"/api/v1/import/{v2['import_id']}/uebernehmen", json={"zutaten": auswahl})
    assert again.status_code == 409
    # Bewusst doppelt anlegen, mit eigenen Schritten statt denen der Quelle
    eigen = client.post(
        f"/api/v1/import/{v2['import_id']}/uebernehmen",
        json={"zutaten": auswahl, "trotz_dublette": True, "schritte": ["Alles in eine Pfanne."]},
    ).json()
    assert client.get(f"/api/v1/rezepte/{eigen['rezept_id']}").json()["schritte"] == [
        "Alles in eine Pfanne."
    ]


def test_uebernehmen_ohne_produkt(client):
    v = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML}).json()
    resp = client.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen", json={"zutaten": [{"index": 0, "menge": 1}]}
    )
    assert resp.status_code == 422


def test_gramm_esslöffel_oel():
    z = ri.zutat_parsen("1 Esslöffel Öl")
    assert ri.gramm(z, z.name)[0] == pytest.approx(13.8)
    z = ri.zutat_parsen("2 Teelöffel Zucker")
    assert ri.gramm(z, z.name)[:2] == (8.0, "schaetzung")


# Aufbau wie ein Chefkoch-Magazinartikel (kein JSON-LD-Rezept), Inhalt ausgedacht
ARTIKEL = """<html><head>
<meta property="og:image" content="https://img.example.org/artikel/haupt.jpg">
<script type="application/ld+json">{"@type": "NewsArticle", "headline": "x"}</script>
</head><body><h1>Testauflauf: Schnell gemacht</h1>
<h2>So gelingt er</h2><p>Text</p>
<h2 id="t">Testauflauf mit Brokkoli</h2>
<ul><li><strong>Arbeitszeit</strong>: ca. 10 Min.</li></ul>
<h3>Zutaten für 3 Portionen:</h3>
<ul><!--[--><li>300 g Brokkoli</li><li>Salz, Pfeffer</li><!--]--></ul>
<h3>Zubereitung </h3>
<ol><li>Ofen auf 200 Grad vorheizen.</li><li>Brokkoli <b>backen</b>.</li></ol>
<p>Fazit</p><h2>Tipps der Redaktion</h2><ul><li>kein Schritt</li></ul>
<h2>Weitere Rezepte</h2><h3>Zutaten für 2 Portionen:</h3><ul><li>1 Ei</li></ul>
</body></html>"""
ARTIKEL_URL = "https://www.chefkoch.de/magazin/artikel/123-testauflauf#gallery-recipeGallery0-4"


def test_rezept_aus_ueberschriften():
    r = ri.rezept_aus_html(ARTIKEL, ARTIKEL_URL)
    assert r.name == "Testauflauf mit Brokkoli" and r.portionen == 3
    assert r.url == "https://www.chefkoch.de/magazin/artikel/123-testauflauf"
    assert [(z.menge, z.einheit, z.name) for z in r.zutaten] == [
        (300, "g", "Brokkoli"),
        (None, None, "Salz"),
        (None, None, "Pfeffer"),
    ]
    assert r.schritte == ["Ofen auf 200 Grad vorheizen.", "Brokkoli backen."]
    assert r.bild == "https://img.example.org/artikel/haupt.jpg"


def test_chefkoch_rezeptseite_aufgeraeumt():
    ld = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Recipe",
                "name": "Testnudeln von Koch123",
                "recipeYield": ["2", "2 Portionen"],
                "recipeIngredient": ["200 g Nudeln"],
                "recipeInstructions": [
                    {
                        "@type": "HowToSection",
                        "name": "Zubereitung",
                        "itemListElement": [{"@type": "HowToStep", "text": "Kochen."}],
                    }
                ],
            }
        ],
    }
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    r = ri.rezept_aus_html(html, "https://www.chefkoch.de/rezepte/1/Testnudeln.html")
    assert r.name == "Testnudeln" and r.portionen == 2
    assert r.schritte == ["Kochen."]
