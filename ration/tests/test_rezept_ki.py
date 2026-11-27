"""Rezept generieren: Entwurf, Überarbeiten, Übergabe an den Import mit KI-Anleitung."""

import asyncio
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import rezept_ki
from app.main import create_app
from app.settings import Settings

AUFGABE = "rationPlaner Rezept generieren"
ANLEITUNG = "rationPlaner Kochanleitung"
IMPORT = "rationPlaner Rezept-Import"

ENTWURF = {
    "name": "Pilzpasta",
    "portionen": 2,
    "zutaten": ["200 g Spaghetti", "250 g Champignons", "150 g Sahne", "1 Prise Salz"],
    "schritte": [
        {"titel": "Nudeln kochen", "text": "200 g Spaghetti in Salzwasser 9 Minuten kochen.",
         "zutaten": ["0", "3"], "timer_minuten": 9, "timer_name": "Nudeln", "parallel": True},
        {"titel": "Pilze braten", "text": "250 g Champignons anbraten, 150 g Sahne dazu.",
         "zutaten": ["1", "2"]},
    ],
}  # fmt: skip

UEBERARBEITET = {
    **ENTWURF,
    "zutaten": ["200 g Spaghetti", "250 g Champignons", "150 g Crème fraîche", "1 Prise Salz"],
    "aenderung": "Sahne durch Crème fraîche ersetzt",
}


@pytest.fixture
def c(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(
            todo_entity="todo.einkaufsliste", ai_task_entity="ai_task.test", data_dir=tmp_path
        ),
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
    with TestClient(app) as client:
        yield client


def _warten(c, import_id: int) -> dict:
    ende = time.monotonic() + 5
    while time.monotonic() < ende:
        r = c.get(f"/api/v1/import/{import_id}").json()
        if r["status"] != "laeuft":
            return r
        time.sleep(0.05)
    raise AssertionError("Import läuft noch")


def test_pruefen_normalisiert_und_ergaenzt_fehlende_zutaten():
    daten = {**ENTWURF, "schritte": [ENTWURF["schritte"][0]]}  # Pilze und Sahne fehlen
    entwurf, probleme = rezept_ki.pruefen(daten)
    assert probleme and "1 (250 g Champignons)" in probleme[0]
    drin = {z["zutat"] for s in entwurf["schritte"] for z in s["zutaten"]}
    assert drin == {0, 1, 2, 3}
    assert entwurf["schritte"][0]["timer_s"] == 540
    assert entwurf["hinweise"]


def test_generieren_ueberarbeiten_importieren(c, fake_ha, fake_backend):
    fake_ha.ki_antwort_je_aufgabe = {AUFGABE: [ENTWURF, UEBERARBEITET], IMPORT: {"zutaten": []}}
    r = c.post("/api/v1/generieren", json={"prompt": "Pasta mit Pilzen", "aufwand": "schnell"})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["versionen"][0]["rezept"]["name"] == "Pilzpasta"
    assert "höchstens 25 Minuten" in fake_ha.ki_anfragen[-1]
    assert "Änderungswunsch" not in fake_ha.ki_anfragen[-1]

    r = c.post(
        f"/api/v1/generieren/{e['id']}/ueberarbeiten", json={"wunsch": "statt Sahne Crème fraîche"}
    )
    assert r.status_code == 200, r.text
    e = r.json()
    assert len(e["versionen"]) == 2 and e["versionen"][1]["wunsch"] == "statt Sahne Crème fraîche"
    prompt = fake_ha.ki_anfragen[-1]
    assert "Änderungswunsch: statt Sahne Crème fraîche" in prompt and "2 | 150 g Sahne" in prompt

    liste = c.get("/api/v1/generieren").json()
    assert liste["entwuerfe"][0]["versionen"] == 2 and liste["ki_fehlt"] is None

    r = c.post(f"/api/v1/generieren/{e['id']}/import", json={})
    assert r.status_code == 202, r.text
    v = _warten(c, r.json()["import_id"])
    assert v["status"] == "fertig", v
    assert v["quelle"] == "ki" and v["name"] == "Pilzpasta"
    assert [z["text"] for z in v["zutaten"]][2] == "150 g Crème fraîche"
    assert v["anleitung"]["quelle"] == "ki" and len(v["anleitung"]["schritte"]) == 2
    # Anleitung kommt aus dem Entwurf, die Kochanleitungs-KI läuft nicht nochmal
    assert not any(
        "Schritt-für-Schritt-Anleitung für einen Kochmodus" in a for a in fake_ha.ki_anfragen
    )

    neu = [(0, "Spaghetti", 200), (1, "Champignons", 250), (2, "Crème fraîche", 150)]
    zutaten = [{"index": i, "neu_anlegen": n, "menge": m} for i, n, m in neu]
    r = c.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen",
        json={
            "zutaten": zutaten,
            "anleitung": v["anleitung"]["schritte"],
            "anleitung_quelle": "ki",
        },
    )
    assert r.status_code == 201, r.text
    rezept_id = r.json()["rezept_id"]
    assert r.json()["anleitung"] == 2

    rezept = asyncio.run(fake_backend.get_recipe(rezept_id))
    assert "<p>200 g Spaghetti" in rezept.description and "Quelle:" not in rezept.description


def test_vorrat_im_prompt(c, fake_ha, fake_backend):

    p = asyncio.run(fake_backend.list_products())[0]
    loc = asyncio.run(fake_backend.list_locations())[0]
    asyncio.run(fake_backend.add_stock(p.id, 3, date.today() + timedelta(days=2), loc.id))
    fake_ha.ki_antwort_je_aufgabe = {AUFGABE: ENTWURF}
    r = c.post("/api/v1/generieren", json={"prompt": "irgendwas", "vorrat": True})
    assert r.status_code == 201, r.text
    assert "Vorrat (heute" in fake_ha.ki_anfragen[-1] and p.name in fake_ha.ki_anfragen[-1]


def test_ohne_brauchbare_antwort(c, fake_ha):
    fake_ha.ki_antwort_je_aufgabe = {
        AUFGABE: {"name": "x", "portionen": 2, "zutaten": [], "schritte": []}
    }
    r = c.post("/api/v1/generieren", json={"prompt": "irgendwas"})
    assert r.status_code == 502
    assert len(fake_ha.ki_anfragen) == 2  # eine Reparaturrunde
