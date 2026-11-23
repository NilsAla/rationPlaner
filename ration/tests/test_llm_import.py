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
