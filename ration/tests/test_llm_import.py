"""KI-Zuordnung beim Import: LLM wählt aus, Code rechnet und validiert."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

HTML = (Path(__file__).parent / "fixtures" / "rezept_jsonld.html").read_text(encoding="utf-8")
URL = "https://www.hellofresh.de/recipes/test-rezept-0123456789abcdef01234567"


@pytest.fixture
def ki_client(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(
            todo_entity="todo.einkaufsliste", ai_task_entity="ai_task.test", data_dir=tmp_path
        ),
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
    with TestClient(app) as c:
        yield c


def _vorschau(client, **extra):
    resp = client.post("/api/v1/import/vorschau", json={"url": URL, "html": HTML, **extra})
    assert resp.status_code == 200, resp.text
    v = resp.json()
    return v, {x["name"]: x for x in v["zutaten"]}


def _index(z, name):
    """nr im Prompt: Position unter den Zutaten, die an die KI gehen (1…n)."""
    offen = [
        x["name"]
        for x in sorted(z.values(), key=lambda x: x["index"])
        if x["aktion"] != "weglassen" and (not x["sicher"] or x["status"] == "pruefen")
    ]
    return offen.index(name) + 1 if name in offen else 99


def test_ki_fuellt_luecken(ki_client, fake_ha):
    _, z0 = _vorschau(ki_client, ki=False)
    fake_ha.ki_antwort = {
        "zutaten": [
            # gültige Zuordnung eines offenen Eintrags auf vorhandenes Produkt
            {"index": _index(z0, "Spitzpaprika"), "aktion": "zuordnen", "produkt_id": "paprika"},
            # erfundene ID wird verworfen, neu_name bleibt Code-Vorschlag
            {"index": _index(z0, "Sesamöl"), "aktion": "zuordnen", "produkt_id": "gibtsnicht"},
            # Packung ohne Größe: g je Einheit rechnet der Code
            {
                "index": _index(z0, "Testkäse"),
                "aktion": "neu",
                "neu_name": "Käse",
                "g_je_einheit": 150,
            },
            # sichere Code-Treffer bleiben unangetastet
            {"index": _index(z0, "Nudeln"), "aktion": "neu", "neu_name": "Spaghetti"},
            # Unsinn: falscher Index, negative Menge
            {"index": 99, "aktion": "neu", "neu_name": "X"},
            {
                "index": _index(z0, "Salz"),
                "aktion": "neu",
                "neu_name": "Speisesalz",
                "g_je_einheit": -5,
            },
        ]
    }
    v, z = _vorschau(ki_client)
    assert len(fake_ha.ki_anfragen) == 1
    assert "paprika|Paprika rot|g" in fake_ha.ki_anfragen[0]
    assert (z["Spitzpaprika"]["aktion"], z["Spitzpaprika"]["produkt_id"]) == ("zuordnen", "paprika")
    assert z["Spitzpaprika"]["menge_bestand"] == 250
    assert z["Spitzpaprika"]["hinweis"].startswith("KI")
    assert (z["Sesamöl"]["aktion"], z["Sesamöl"]["neu_name"]) == ("neu", "Sesamöl")
    assert z["Testkäse"]["neu_name"] == "Testkäse"  # Name bildet der Code
    assert (z["Testkäse"]["menge_bestand"], z["Testkäse"]["status"]) == (150, "schaetzung")
    assert z["Nudeln"]["produkt_id"] == "nudeln"
    assert z["Salz"]["neu_name"] == "Salz" and z["Salz"]["menge_bestand"] == 0
    assert z["Wasser"]["aktion"] == "weglassen"
    assert v["ki"] == {"aktiv": True, "geaendert": 2, "fehler": None}


def test_ki_fehler_faellt_zurueck(ki_client, fake_ha):
    fake_ha.ki_antwort = httpx.ConnectError("weg")
    v, z = _vorschau(ki_client)
    assert v["ki"]["fehler"] == "weg"
    assert z["Spitzpaprika"]["aktion"] == "neu"


def test_ki_abschaltbar_und_ohne_entitaet(ki_client, client, fake_ha):
    v, _ = _vorschau(ki_client, ki=False)
    assert v["ki"]["aktiv"] is False
    v, _ = _vorschau(client)
    assert v["ki"]["aktiv"] is False
    assert fake_ha.ki_anfragen == []


def test_ki_antwort_muell(ki_client, fake_ha):
    fake_ha.ki_antwort = {"zutaten": "kaputt"}
    v, _ = _vorschau(ki_client)
    assert v["ki"]["geaendert"] == 0


def test_ki_fehlgriff_nur_als_alternative(ki_client, fake_ha):
    _, z0 = _vorschau(ki_client, ki=False)
    # echte Antwort aus dem Live-Test: Salz → Olivenöl
    fake_ha.ki_antwort = {
        "zutaten": [{"index": _index(z0, "Salz"), "aktion": "zuordnen", "produkt_id": "oel"}]
    }
    _, z = _vorschau(ki_client)
    assert z["Salz"]["aktion"] == "neu"
    assert z["Salz"]["alternativen"][0]["produkt_id"] == "oel"
    assert "KI meint: Olivenöl?" in z["Salz"]["hinweis"]


# --- KI-Schritt mit Kategorie, Attributen und Varianten ---------------------------------------

import asyncio  # noqa: E402
import json  # noqa: E402

from app.inventory.base import ProductPatch  # noqa: E402

URL2 = "https://www.chefkoch.de/rezepte/1/Kategorietest.html"


def _seite(zutaten: list[str]) -> str:
    ld = {
        "@type": "Recipe",
        "name": "Kategorietest",
        "recipeYield": "2",
        "recipeIngredient": zutaten,
    }
    return f'<script type="application/ld+json">{json.dumps(ld)}</script>'


def _produkt(fake_backend, name: str, kategorie: str | None = None, unit: str = "g") -> str:
    async def anlegen():
        p = await fake_backend.create_product(name, unit)
        if kategorie:
            await fake_backend.update_product(p.id, ProductPatch(category=kategorie))
        return p.id

    return asyncio.run(anlegen())


def _vorschau2(client, zutaten, **extra):
    resp = client.post(
        "/api/v1/import/vorschau", json={"url": URL2, "html": _seite(zutaten), **extra}
    )
    assert resp.status_code == 200, resp.text
    v = resp.json()
    return v, {x["text"]: x for x in v["zutaten"]}


def test_kategorie_prueft_ki_treffer(ki_client, fake_ha, fake_backend):
    fz = _produkt(fake_backend, "Frühlingszwiebel", "Gemüse")
    paprika = _produkt(fake_backend, "Paprika", "Gemüse")
    zutaten = ["4 Lauchzwiebeln", "1 g Paprika, edelsüß", "2 Stück Paprika"]
    _, z0 = _vorschau2(ki_client, zutaten, ki=False)
    # Kern-Treffer ist nur ein Vorschlag, voller Name gleich ist sicher
    assert z0["1 g Paprika, edelsüß"]["produkt_id"] == paprika
    assert z0["1 g Paprika, edelsüß"]["sicher"] is False
    assert z0["2 Stück Paprika"]["sicher"] is True
    fake_ha.ki_antwort = {
        "zutaten": [
            # Synonym mit gleicher Kategorie: wird übernommen (früher am Namen gescheitert)
            {"index": 1, "aktion": "zuordnen", "produkt_id": fz, "kategorie": "Gemüse"},
            # Gewürz ≠ Gemüse: KI lehnt den Kern-Treffer ab, neues Produkt mit vollem Namen
            {"index": 2, "aktion": "neu", "kategorie": "Gewürze", "tracking_modus": "immer_da",
             "haltbarkeit_tage": 730},
            # sicherer Treffer: KI darf nichts ändern
            {"index": 3, "aktion": "neu", "kategorie": "Gemüse"},
        ]
    }  # fmt: skip
    v, z = _vorschau2(ki_client, zutaten)
    assert "4 Lauchzwiebeln" in fake_ha.ki_anfragen[-1]
    assert "2 Stück Paprika" not in fake_ha.ki_anfragen[-1]
    assert (z["4 Lauchzwiebeln"]["aktion"], z["4 Lauchzwiebeln"]["produkt_id"]) == ("zuordnen", fz)
    edel = z["1 g Paprika, edelsüß"]
    assert (edel["aktion"], edel["neu_name"]) == ("neu", "Paprika edelsüß")
    assert edel["attribute"]["kategorie"] == "Gewürze"
    assert edel["alternativen"][0]["produkt_id"] == paprika
    assert z["2 Stück Paprika"]["produkt_id"] == paprika

    # Fehlgriff über Kategoriegrenze: nur Alternative
    fake_ha.ki_antwort = {
        "zutaten": [
            {"index": 1, "aktion": "zuordnen", "produkt_id": paprika, "kategorie": "Kräuter"}
        ]
    }
    _, z = _vorschau2(ki_client, ["4 Lauchzwiebeln"])
    assert z["4 Lauchzwiebeln"]["aktion"] == "neu"
    assert "KI meint: Paprika?" in z["4 Lauchzwiebeln"]["hinweis"]


def test_neues_produkt_mit_attributen_und_variante(ki_client, fake_ha, fake_backend):
    penne = _produkt(fake_backend, "Penne", "Nudeln & Reis")
    fake_ha.ki_antwort = {
        "zutaten": [
            {"index": 1, "aktion": "neu", "kategorie": "Nudeln & Reis", "tracking_modus": "menge",
             "ablauf_typ": "mhd", "haltbarkeit_tage": 540, "haltbarkeit_offen_tage": 0,
             "variante_von": penne, "g_je_einheit": 0, "stueck_g": 0},
        ]
    }  # fmt: skip
    v, z = _vorschau2(ki_client, ["250 g Vollkorn-Penne"])
    vp = z["250 g Vollkorn-Penne"]
    assert vp["attribute"] == {
        "tracking_modus": "menge",
        "kategorie": "Nudeln & Reis",
        "ablauf_typ": "mhd",
        "haltbarkeit_tage": 540,
    }
    assert vp["variante_von"] == {"produkt_id": penne, "name": "Penne"}
    e = ki_client.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen",
        json={"zutaten": [{"index": 0, "neu_anlegen": "Vollkorn-Penne", "menge": 250, "ki": True,
                           "attribute": vp["attribute"], "variante_von": penne}]},
    )  # fmt: skip
    assert e.status_code == 201, e.text
    p = next(p for p in asyncio.run(fake_backend.list_products()) if p.name == "Vollkorn-Penne")
    assert (p.category, str(p.tracking_mode), p.shelf_life_days) == ("Nudeln & Reis", "menge", 540)
    assert p.parent_id == penne


def test_ki_alias_gilt_nicht_als_sicher(ki_client, fake_ha, fake_backend):
    fz = _produkt(fake_backend, "Frühlingszwiebel", "Gemüse")
    fake_ha.ki_antwort = {
        "zutaten": [{"index": 1, "aktion": "zuordnen", "produkt_id": fz, "kategorie": "Gemüse"}]
    }
    v, z = _vorschau2(ki_client, ["4 Lauchzwiebeln"])
    ki_client.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen",
        json={"zutaten": [{"index": 0, "produkt_id": fz, "menge": 60, "ki": True}]},
    )
    _, z = _vorschau2(ki_client, ["4 Lauchzwiebeln"], ki=False)
    assert z["4 Lauchzwiebeln"]["produkt_id"] == fz  # Alias greift …
    assert z["4 Lauchzwiebeln"]["sicher"] is False  # … geht aber wieder an die KI


def test_ki_nummern_statt_rezept_index(ki_client, fake_ha, fake_backend):
    # Live: Zutaten 3 und 7 offen, die KI antwortete mit 1 und 2 → jetzt so gewollt
    zitrone = _produkt(fake_backend, "Zitrone", "Obst")
    for name in ("Paprika", "Salz", "Pfeffer", "Zucker"):
        _produkt(fake_backend, name)
    zutaten = [
        "Salz",
        "Pfeffer",
        "40 g Zucker",
        "½ Stück Zitrone, gewachst",
        "1 g Paprika, edelsüß",
    ]
    fake_ha.ki_antwort = {
        "zutaten": [
            {"index": 1, "kategorie": "Obst", "aktion": "zuordnen", "produkt_id": zitrone,
             "stueck_g": 100},
            {"index": 2, "kategorie": "Gewürze", "aktion": "neu"},
        ]
    }  # fmt: skip
    _, z = _vorschau2(ki_client, zutaten)
    zeilen = fake_ha.ki_anfragen[-1].split("\n")
    assert any(x.startswith("1: ½ Stück Zitrone") for x in zeilen)
    assert any(x.startswith("2: 1 g Paprika, edelsüß") for x in zeilen)
    assert z["½ Stück Zitrone, gewachst"]["produkt_id"] == zitrone
    assert z["½ Stück Zitrone, gewachst"]["menge_bestand"] == 50  # stueck_g als Ersatz
    assert z["1 g Paprika, edelsüß"]["aktion"] == "neu"
    assert z["1 g Paprika, edelsüß"]["kategorie"] == "Gewürze"


def test_aehnliche_kandidaten():
    from app.inventory.base import Product
    from app.llm_import import aehnliche
    from app.rezept_import import Zuordnung

    namen = ["Paprika", "Paprikapulver", "Salz", "Frühlingszwiebel", "Zwiebel"]
    ps = [Product(str(i), n, "g") for i, n in enumerate(namen)]
    edel = aehnliche(Zuordnung(0, "1 g Paprika, edelsüß", "Paprika, edelsüß", 1, "g"), ps)
    assert "1 Paprikapulver" in edel and "Salz" not in edel
    lauch = aehnliche(Zuordnung(0, "4 Lauchzwiebeln", "Lauchzwiebeln", 4, None), ps)
    assert "3 Frühlingszwiebel" in lauch


def test_ki_name_nur_aus_zutatentext(ki_client, fake_ha):
    fake_ha.ki_antwort = {
        "zutaten": [
            {"index": 1, "kategorie": "Konserven", "aktion": "neu", "name": "Stückige Tomaten"},
            {"index": 2, "kategorie": "Milchprodukte", "aktion": "neu", "name": "Schmand"},
            {"index": 3, "kategorie": "Gemüse", "aktion": "neu", "name": "Knoblauchzehe"},
        ]
    }
    zutaten = ["1 Packung stückige Tomaten", "2 EL saure Sahne", "2 Knoblauchzehe(n)"]
    _, z = _vorschau2(ki_client, zutaten)
    assert z["1 Packung stückige Tomaten"]["neu_name"] == "Stückige Tomaten"
    assert z["2 EL saure Sahne"]["neu_name"] == "Sahne"  # erfundener Name verworfen → Code
    assert z["2 Knoblauchzehe(n)"]["neu_name"] == "Knoblauch"  # Code-Synonym bleibt


def test_variante_nur_gleiche_kategorie(ki_client, fake_ha, fake_backend):
    paprika = _produkt(fake_backend, "Paprika", "Gemüse")
    fake_ha.ki_antwort = {
        "zutaten": [
            {"index": 1, "kategorie": "Gewürze", "aktion": "neu", "variante_von": paprika},
            {"index": 2, "kategorie": "Gemüse", "aktion": "neu", "variante_von": paprika},
        ]
    }
    _, z = _vorschau2(ki_client, ["1 TL Paprikapulver", "2 Stück Spitzpaprika"])
    assert z["1 TL Paprikapulver"]["variante_von"] is None
    assert z["2 Stück Spitzpaprika"]["variante_von"]["produkt_id"] == paprika


def test_vorschau_wieder_lesen(ki_client, fake_ha):
    fake_ha.ki_antwort = {"zutaten": []}
    v, _ = _vorschau2(ki_client, ["1 Ei"])
    assert ki_client.get(f"/api/v1/import/{v['import_id']}").json() == v
    assert ki_client.get("/api/v1/import").json()[0]["import_id"] == v["import_id"]
    assert ki_client.get("/api/v1/import/99999").status_code == 404


def _warten(client, import_id, sekunden=5.0):
    import time

    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        r = client.get(f"/api/v1/import/{import_id}").json()
        if r["status"] != "laeuft":
            return r
        time.sleep(0.05)
    raise AssertionError("Import läuft noch")


def test_import_im_hintergrund(ki_client, fake_ha):
    fake_ha.ki_antwort = {"zutaten": []}
    r = ki_client.post("/api/v1/import/start", json={"url": URL2, "html": _seite(["1 Ei"])})
    assert r.status_code == 202
    start = r.json()
    assert start["status"] == "laeuft" and start["fortschritt"] == 2
    fertig = _warten(ki_client, start["import_id"])
    assert fertig["status"] == "fertig" and fertig["name"] == "Kategorietest"
    assert fertig["zutaten"][0]["text"] == "1 Ei"
    liste = ki_client.get("/api/v1/import").json()
    assert liste[0]["import_id"] == start["import_id"] and liste[0]["status"] == "fertig"
    # übernehmen wie nach der synchronen Vorschau
    e = ki_client.post(
        f"/api/v1/import/{start['import_id']}/uebernehmen",
        json={"zutaten": [{"index": 0, "neu_anlegen": "Ei", "menge": 60}]},
    )
    assert e.status_code == 201, e.text


def test_import_im_hintergrund_fehler(ki_client):
    r = ki_client.post("/api/v1/import/start", json={"url": URL2, "html": "<html></html>"})
    fertig = _warten(ki_client, r.json()["import_id"])
    assert fertig["status"] == "fehler" and "Recipe" in fertig["fehler"]


def test_import_fortschritt_und_veraltet():
    from datetime import timedelta

    from app import importlauf
    from app.db import RezeptImport

    jetzt = importlauf._jetzt()
    vor20 = jetzt - timedelta(seconds=20)
    e = RezeptImport(id=1, url="u", quelle_id="", roh={}, status="laeuft", schritt="KI",
                     erwartet_s=40.0, ki_start=vor20, angelegt=jetzt)  # fmt: skip
    st = importlauf.status(e)
    assert 50 <= st["fortschritt"] <= 60 and 19 <= st["rest_s"] <= 21
    e.ki_start = jetzt - timedelta(seconds=400)
    assert importlauf.status(e)["fortschritt"] == 95  # wartet bei 95 %, bis fertig
    e.angelegt = jetzt - timedelta(hours=1)
    assert importlauf.status(e)["status"] == "fehler"
