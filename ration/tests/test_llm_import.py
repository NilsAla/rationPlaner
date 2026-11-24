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
    return z[name]["index"]


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
            {"index": 0, "aktion": "zuordnen", "produkt_id": fz, "kategorie": "Gemüse"},
            # Gewürz ≠ Gemüse: KI lehnt den Kern-Treffer ab, neues Produkt mit vollem Namen
            {"index": 1, "aktion": "neu", "kategorie": "Gewürze", "tracking_modus": "immer_da",
             "haltbarkeit_tage": 730},
            # sicherer Treffer: KI darf nichts ändern
            {"index": 2, "aktion": "neu", "kategorie": "Gemüse"},
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
            {"index": 0, "aktion": "zuordnen", "produkt_id": paprika, "kategorie": "Kräuter"}
        ]
    }
    _, z = _vorschau2(ki_client, ["4 Lauchzwiebeln"])
    assert z["4 Lauchzwiebeln"]["aktion"] == "neu"
    assert "KI meint: Paprika?" in z["4 Lauchzwiebeln"]["hinweis"]


def test_neues_produkt_mit_attributen_und_variante(ki_client, fake_ha, fake_backend):
    penne = _produkt(fake_backend, "Penne", "Nudeln & Reis")
    fake_ha.ki_antwort = {
        "zutaten": [
            {"index": 0, "aktion": "neu", "kategorie": "Nudeln & Reis", "tracking_modus": "menge",
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
        "zutaten": [{"index": 0, "aktion": "zuordnen", "produkt_id": fz, "kategorie": "Gemüse"}]
    }
    v, z = _vorschau2(ki_client, ["4 Lauchzwiebeln"])
    ki_client.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen",
        json={"zutaten": [{"index": 0, "produkt_id": fz, "menge": 60, "ki": True}]},
    )
    _, z = _vorschau2(ki_client, ["4 Lauchzwiebeln"], ki=False)
    assert z["4 Lauchzwiebeln"]["produkt_id"] == fz  # Alias greift …
    assert z["4 Lauchzwiebeln"]["sicher"] is False  # … geht aber wieder an die KI
