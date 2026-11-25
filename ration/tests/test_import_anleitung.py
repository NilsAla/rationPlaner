"""Kochanleitung im Import: parallel zur Zuordnung, Prüfung in der Vorschau, Übernahme."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

URL = "https://www.chefkoch.de/rezepte/1/Anleitungstest.html"
ZUTATEN = ["2 Zwiebeln", "200 g Nudeln", "1 Prise Salz"]
SCHRITTE = ["Nudeln kochen.", "Zwiebeln würfeln und anbraten, salzen."]
IMPORT = "rationPlaner Rezept-Import"
ANLEITUNG = "rationPlaner Kochanleitung"


def _seite() -> str:
    ld = {
        "@type": "Recipe",
        "name": "Anleitungstest",
        "recipeYield": "2",
        "recipeIngredient": ZUTATEN,
        "recipeInstructions": [{"@type": "HowToStep", "text": t} for t in SCHRITTE],
    }
    return f'<script type="application/ld+json">{json.dumps(ld)}</script>'


KI_SCHRITTE = {
    "schritte": [
        {"titel": "Wasser aufsetzen", "text": "Wasser für 200 g Nudeln aufsetzen.",
         "zutaten": ["1"], "timer_minuten": 10, "timer_name": "Nudeln", "original": ["1"]},
        {"titel": "Zwiebeln würfeln", "text": "2 Zwiebeln würfeln und anbraten.",
         "zutaten": ["0", "2"], "original": ["2"]},
    ]
}  # fmt: skip


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


def _vorschau(c) -> dict:
    r = c.post("/api/v1/import/vorschau", json={"url": URL, "html": _seite()})
    assert r.status_code == 200, r.text
    return r.json()


def test_anleitung_parallel_zur_zuordnung(c, fake_ha):
    fake_ha.ki_antwort_je_aufgabe = {IMPORT: {"zutaten": []}, ANLEITUNG: KI_SCHRITTE}
    v = _vorschau(c)
    a = v["anleitung"]
    assert a["quelle"] == "ki" and a["fehler"] is None
    assert [s["titel"] for s in a["schritte"]] == ["Wasser aufsetzen", "Zwiebeln würfeln"]
    # Zutaten als Index der Import-Zeilen, Originalschritte 0-basiert
    assert a["schritte"][1]["zutaten"] == [{"zutat": 0, "anteil": 1.0}, {"zutat": 2, "anteil": 1.0}]
    assert a["schritte"][0]["timer_s"] == 600 and a["schritte"][0]["original"] == [0]
    # beide Jobs gefragt, die Anleitung mit den Zutatentexten statt Produkten
    prompt = next(p for p in fake_ha.ki_anfragen if p.startswith("Schreibe"))
    assert "0 | Zwiebeln | 2" in prompt and "1 | Nudeln | 200 g" in prompt
    assert any(p.startswith("Du ordnest") for p in fake_ha.ki_anfragen)


def test_anleitung_fallback_code(c, fake_ha):
    fake_ha.ki_antwort_je_aufgabe = {IMPORT: {"zutaten": []}, ANLEITUNG: RuntimeError("weg")}
    a = _vorschau(c)["anleitung"]
    assert a["quelle"] == "code" and "weg" in a["fehler"]
    assert [s["text"] for s in a["schritte"]] == SCHRITTE


def test_uebernehmen_speichert_geprueft(c, fake_ha):
    fake_ha.ki_antwort_je_aufgabe = {IMPORT: {"zutaten": []}, ANLEITUNG: KI_SCHRITTE}
    v = _vorschau(c)
    # Zeile 0 (Zwiebeln) weglassen: Nudeln → Position 0, Salz → Position 1
    zutaten = [
        {"index": 1, "neu_anlegen": "Nudeln", "menge": 200},
        {"index": 2, "neu_anlegen": "Salz", "menge": 1},
    ]
    r = c.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen",
        json={"zutaten": zutaten, "fotos": False, "anleitung": v["anleitung"]["schritte"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["anleitung"] == 2
    a = c.get(f"/api/v1/kochen/anleitung/{r.json()['rezept_id']}").json()
    assert a["status"] == "geprueft" and a["quelle"] == "ki" and not a["veraltet"]
    assert [[z["name"] for z in s["zutaten"]] for s in a["schritte"]] == [["Nudeln"], ["Salz"]]


def test_uebernehmen_ohne_anleitung(c, fake_ha):
    fake_ha.ki_antwort_je_aufgabe = {IMPORT: {"zutaten": []}, ANLEITUNG: KI_SCHRITTE}
    v = _vorschau(c)
    r = c.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen",
        json={"zutaten": [{"index": 1, "neu_anlegen": "Nudeln", "menge": 200}], "fotos": False},
    )
    assert r.json()["anleitung"] == 0
    assert c.get(f"/api/v1/kochen/anleitung/{r.json()['rezept_id']}").json()["status"] == "keine"


def test_produkte_nutzung(c, fake_ha):
    fake_ha.ki_antwort_je_aufgabe = {IMPORT: {"zutaten": []}, ANLEITUNG: KI_SCHRITTE}

    def summe() -> int:
        return sum(x["rezepte"] for x in c.get("/api/v1/produkte?nutzung=true").json())

    vorher = summe()
    v = _vorschau(c)
    zutaten = [
        {"index": 1, "neu_anlegen": "Testnudeln", "menge": 200},
        {"index": 2, "neu_anlegen": "Testsalz", "menge": 1},
    ]
    c.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen", json={"zutaten": zutaten, "fotos": False}
    )
    assert summe() == vorher + 2
    p = {x["name"]: x for x in c.get("/api/v1/produkte?nutzung=true").json()}
    assert p["Testnudeln"]["rezepte"] == 1 and "kategorie" in p["Testnudeln"]
    assert "rezepte" not in c.get("/api/v1/produkte").json()[0]
