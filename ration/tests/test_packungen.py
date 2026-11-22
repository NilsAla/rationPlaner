"""Packungsgrößen: Standard (Backend) plus weitere Größen (App), Auswahl im Einkauf."""

from datetime import date, timedelta

import pytest

from app.kueche import Groesse, waehle_packungen

HEUTE = date.today().isoformat()
DOSE = Groesse("Dose", 400)
GROSS = Groesse("Dose", 800, "manuell")


@pytest.mark.parametrize(
    ("fehlmenge", "erwartet"),
    [
        (300, [(1, 400)]),
        (400, [(1, 400)]),
        (700, [(1, 800)]),  # gleicher Überhang wie 2 × 400, aber weniger Packungen
        (1000, [(1, 800), (1, 400)]),
        (1700, [(2, 800), (1, 400)]),
    ],
)
def test_waehle_kleinster_ueberhang(fehlmenge, erwartet):
    result = waehle_packungen(fehlmenge, [DOSE, GROSS])
    assert [(p.anzahl, p.menge) for p in result] == erwartet


def test_waehle_angebot_bei_gleichstand():
    angebot = Groesse("Packung", 400, "angebot")
    result = waehle_packungen(350, [DOSE, angebot])
    assert result[0].quelle == "angebot"


def test_waehle_ohne_groessen():
    assert waehle_packungen(300, []) == []


def test_standard_setzen_und_weitere_groessen(client, fake_ha):
    resp = client.put("/api/v1/produkte/nudeln/packung", json={"einheit": "Packung", "menge": 250})
    assert resp.status_code == 200
    neu = client.post(
        "/api/v1/produkte/nudeln/packungen", json={"einheit": "Großpackung", "menge": 1000}
    )
    assert neu.status_code == 201

    nudeln = next(p for p in client.get("/api/v1/packungen").json() if p["produkt_id"] == "nudeln")
    assert nudeln["standard"] == {"einheit": "Packung", "menge": 250}
    assert [(w["einheit"], w["menge"], w["quelle"]) for w in nudeln["weitere"]] == [
        ("Großpackung", 1000, "manuell")
    ]

    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": "paprika-pasta", "portionen": 4})
    client.put(
        f"/api/v1/plan/{(date.today() + timedelta(days=1)).isoformat()}",
        json={"rezept_id": "paprika-pasta", "portionen": 6},
    )
    e = client.post("/api/v1/einkaufsliste", json={"tage": 7}).json()
    # 1000 g Nudeln: eine Großpackung statt 4 × 250
    assert "Nudeln, 1 × Großpackung (1000 g)" in e["hinzugefuegt"]

    client.delete(f"/api/v1/packungen/{neu.json()['id']}")
    nudeln = next(p for p in client.get("/api/v1/packungen").json() if p["produkt_id"] == "nudeln")
    assert nudeln["weitere"] == []


def test_abgelaufenes_angebot_zaehlt_nicht(client, fake_ha):
    gestern = (date.today() - timedelta(days=1)).isoformat()
    client.post(
        "/api/v1/produkte/nudeln/packungen",
        json={"einheit": "Aktion", "menge": 400, "quelle": "angebot", "gueltig_bis": gestern},
    )
    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": "paprika-pasta", "portionen": 4})
    e = client.post("/api/v1/einkaufsliste", json={"tage": 1}).json()
    assert "Nudeln, 1 × Packung (500 g)" in e["hinzugefuegt"]


def test_packung_unbekanntes_produkt(client):
    resp = client.put("/api/v1/produkte/gibtsnicht/packung", json={"einheit": "Dose", "menge": 1})
    assert resp.status_code == 404
