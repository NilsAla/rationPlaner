"""Ausgaben aus Bons: alle Zeilen (auch ungebuchte), Kategorie, geplant/spontan, Monats-Recap."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import ausgaben
from app.main import create_app
from app.settings import Settings

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f"
    b"\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)
HEUTE = date.today()


def z(text, preis, typ="artikel", **x):
    return {"text": text, "typ": typ, "anzahl": 1, "preis": preis, "packung_menge": 0,
            "packung_einheit": "", "konfidenz": 0.9, **x}  # fmt: skip


BON = {
    "markt": "Frische Ecke",
    "datum": HEUTE.isoformat(),
    "summe": 12.96,
    "positionen": [
        z("PAPRIKA ROT", 1.99, produkt_id="paprika", kategorie="Gemüse"),
        z("NUDELN 500G", 0.99, produkt_id="nudeln", kategorie="Vorrat"),
        z("CHIPS PAPRIKA", 1.79, neu_name="Chips", kategorie="Snacks & Süßes"),
        z("RIESLING TROCKEN", 5.99, neu_name="Riesling", kategorie="Alkohol"),
        z("ZAHNPASTA", 1.95, typ="non_food", neu_name="Zahnpasta"),
        z("PFAND", 0.25, typ="pfand"),
        z("STORNO CHIPS PAPRIKA", -1.79, typ="storno"),
        z("CHIPS PAPRIKA", 1.79, neu_name="Chips"),
    ],
}


@pytest.fixture
def a_client(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(
            ai_task_entity="ai_task.test",
            todo_entity="todo.einkauf",
            data_dir=tmp_path,
            media_dir=tmp_path / "media",
        ),
        db_url=f"sqlite:///{tmp_path / 'a.db'}",
    )
    with TestClient(app) as c:
        c.app = app
        yield c


def buchen(c, fake_ha, antwort=BON, kategorien=None):
    fake_ha.ki_antwort = antwort
    r = c.post("/api/v1/bon", files={"datei": ("bon.png", PNG, "image/png")})
    b = c.get(f"/api/v1/bon/{r.json()['id']}").json()
    by = {x["text"]: x for x in b["positionen"]}
    # nur Paprika wird eingebucht, alles andere nicht ins Inventar
    r = c.post(
        f"/api/v1/bon/{b['id']}/buchen",
        json={
            "positionen": [
                {"index": by["PAPRIKA ROT"]["index"], "produkt_id": "paprika", "menge": 250}
            ],
            "kategorien": kategorien or {},
        },
    )
    assert r.status_code == 200, r.text
    return b


def test_alle_zeilen_landen_in_den_ausgaben(a_client, fake_ha):
    b = buchen(a_client, fake_ha, kategorien={})
    zeilen = {x["text"]: x for x in a_client.get("/api/v1/ausgaben/zeilen").json()}
    assert set(zeilen) == {"PAPRIKA ROT", "NUDELN 500G", "CHIPS PAPRIKA", "RIESLING TROCKEN",
                           "ZAHNPASTA", "PFAND"}  # fmt: skip
    assert zeilen["RIESLING TROCKEN"]["kategorie"] == "Alkohol"
    assert zeilen["ZAHNPASTA"]["kategorie"] == "Drogerie"  # non_food ohne KI-Vorschlag
    assert zeilen["PFAND"]["kategorie"] == "Pfand"
    # nur die gebuchte Paprika ist im Inventar
    assert {x["name"] for x in a_client.get("/api/v1/bestand").json()} == {"Paprika rot"}
    r = a_client.get("/api/v1/ausgaben").json()
    assert r["summe"] == 12.96 and r["bons"] == 1
    assert r["pfand"] == 0.25 and round(r["geplant"] + r["spontan"], 2) == 12.71
    assert b["id"]


def test_geplant_vs_spontan(a_client, fake_ha):
    # Paprika-Pasta steht morgen im Plan → Paprika und Nudeln gelten als geplant
    a_client.put(
        f"/api/v1/plan/{(HEUTE + timedelta(days=1)).isoformat()}",
        json={"rezept_id": "paprika-pasta", "portionen": 2},
    )
    buchen(a_client, fake_ha)
    zeilen = {x["text"]: x for x in a_client.get("/api/v1/ausgaben/zeilen").json()}
    assert zeilen["PAPRIKA ROT"]["geplant"] is True
    assert zeilen["NUDELN 500G"]["geplant"] is True  # Produkt per KI erkannt, auch ungebucht
    assert zeilen["CHIPS PAPRIKA"]["geplant"] is False
    r = a_client.get("/api/v1/ausgaben").json()
    assert r["geplant"] == 2.98 and r["spontan"] == round(1.79 + 5.99 + 1.95, 2)
    assert r["top_spontan"][0]["text"] == "RIESLING TROCKEN"


def test_einkaufsliste_macht_geplant(a_client, fake_ha):
    a_client.put(
        f"/api/v1/plan/{HEUTE.isoformat()}", json={"rezept_id": "paprika-pasta", "portionen": 2}
    )
    a_client.post("/api/v1/einkaufsliste", json={"tage": 1})
    a_client.delete(f"/api/v1/plan/{HEUTE.isoformat()}")  # Plan weg, Einkaufsliste zählt
    buchen(a_client, fake_ha)
    zeilen = {x["text"]: x for x in a_client.get("/api/v1/ausgaben/zeilen").json()}
    assert zeilen["PAPRIKA ROT"]["geplant"] is True


def test_kategorie_wird_gemerkt(a_client, fake_ha):
    b = buchen(a_client, fake_ha)
    chips = next(x for x in b["positionen"] if x["text"] == "CHIPS PAPRIKA")
    zeile = next(
        x for x in a_client.get("/api/v1/ausgaben/zeilen").json() if x["text"] == "ZAHNPASTA"
    )
    a_client.patch(f"/api/v1/ausgaben/{zeile['id']}", json={"kategorie": "Haushalt"})
    # zweiter Bon: Zahnpasta ohne KI-Kategorie → gemerkte Kategorie; Review-Wahl für Chips
    buchen(a_client, fake_ha, kategorien={chips["index"]: "Sonstiges"})
    neu = [x for x in a_client.get("/api/v1/ausgaben/zeilen").json() if x["bon_id"] != b["id"]]
    by = {x["text"]: x["kategorie"] for x in neu}
    assert by["ZAHNPASTA"] == "Haushalt" and by["CHIPS PAPRIKA"] == "Sonstiges"
    assert (
        a_client.patch(f"/api/v1/ausgaben/{zeile['id']}", json={"kategorie": "x"}).status_code
        == 422
    )


def test_recap_vormonat_und_text(a_client, fake_ha):
    buchen(a_client, fake_ha)
    with Session(a_client.app.state.engine) as s:
        vor = HEUTE.replace(day=1) - timedelta(days=3)
        s.add(ausgaben.Ausgabe(bon_id=99, index=0, datum=vor, markt="X", text="A", betrag=10.0,
                               typ="artikel", kategorie="Obst"))  # fmt: skip
        s.commit()
        r = ausgaben.recap(s)
        assert r["vormonat"] == 10.0 and r["verlauf"][-1]["summe"] == 12.96
        assert r["verlauf"][-2] == {"monat": vor.isoformat()[:7], "summe": 10.0}
        text = ausgaben.recap_text(r)
        assert "12,96 €" in text and "+2,96 € zum Vormonat" in text


def test_monat_ungueltig(a_client):
    assert a_client.get("/api/v1/ausgaben?monat=2026-13").status_code == 422
