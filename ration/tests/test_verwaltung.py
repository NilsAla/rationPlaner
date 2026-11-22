"""Inventar, Anlegen/Löschen, Massenaktionen, Bewertungen, Einstellungen, Protokolle."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

HEUTE = date.today()


@pytest.fixture
def v_client(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(ai_task_entity="ai_task.test", api_token="geheim", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'v.db'}",
    )
    with TestClient(app, headers={"Authorization": "Bearer geheim"}) as c:
        yield c


def test_inventar_bearbeiten_und_massenaktion(client):
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 300, "mhd": str(HEUTE)})
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 500})
    inv = client.get("/api/v1/inventar").json()
    assert [x["name"] for x in inv] == ["Paprika rot", "Nudeln"]  # nach Ablauf sortiert
    pap, nud = inv
    assert pap["tage"] == 0 and pap["lagerort"] == "Kühlschrank"
    resp = client.patch(
        f"/api/v1/inventar/{pap['id']}",
        json={"menge": 250, "ablauf": str(HEUTE + timedelta(days=2)), "lagerort_id": "vorrat"},
    )
    assert resp.status_code == 200, resp.text
    pap = next(x for x in client.get("/api/v1/inventar").json() if x["id"] == pap["id"])
    assert (pap["menge"], pap["tage"], pap["lagerort"]) == (250, 2, "Vorrat")
    assert (
        client.patch(f"/api/v1/inventar/{pap['id']}", json={"lagerort_id": "x"}).status_code == 404
    )
    e = client.post(
        "/api/v1/inventar/aktion", json={"ids": [pap["id"], "999"], "aktion": "verdorben"}
    ).json()
    assert e["ok"] == [pap["id"]] and e["fehler"][0]["id"] == "999"
    e = client.post(
        "/api/v1/inventar/aktion", json={"ids": [nud["id"]], "aktion": "ablauf_plus", "tage": 7}
    ).json()
    assert e["ok"] == [nud["id"]]
    assert [x["name"] for x in client.get("/api/v1/inventar").json()] == ["Nudeln"]


def test_produkt_anlegen_loeschen_und_masse(client):
    resp = client.post(
        "/api/v1/produkte",
        json={
            "name": "Linsen",
            "einheit": "g",
            "kategorie": "Hülsenfrüchte",
            "tracking_modus": "menge",
        },
    )
    assert resp.status_code == 201, resp.text
    linsen = resp.json()
    assert (linsen["kategorie"], linsen["tracking_modus"]) == ("Hülsenfrüchte", "menge")
    assert client.post("/api/v1/produkte", json={"name": "linsen"}).status_code == 409
    # Nudeln stecken im Rezept, Paprika hat Bestand → nicht löschbar
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 10})
    e = client.post(
        "/api/v1/produkte/aktion",
        json={"ids": ["nudeln", "paprika", linsen["id"]], "aktion": "loeschen"},
    ).json()
    assert e["ok"] == [linsen["id"]]
    gruende = {f["id"]: f["fehler"] for f in e["fehler"]}
    assert "Rezepten" in gruende["nudeln"] and "Bestand" in gruende["paprika"]
    assert client.delete("/api/v1/produkte/nudeln").status_code == 409
    e = client.post(
        "/api/v1/produkte/aktion",
        json={"ids": ["nudeln", "oel"], "aktion": "setzen", "werte": {"kategorie": "Vorrat"}},
    ).json()
    assert e == {"ok": ["nudeln", "oel"], "fehler": []}
    kat = {p["id"]: p["kategorie"] for p in client.get("/api/v1/katalog/produkte").json()}
    assert kat["nudeln"] == kat["oel"] == "Vorrat"
    bad = {"ids": ["oel"], "aktion": "setzen", "werte": {"name": "x"}}
    assert client.post("/api/v1/produkte/aktion", json=bad).status_code == 422


def test_rezepte_anlegen_masse_und_meinung(client):
    resp = client.post(
        "/api/v1/rezepte",
        json={
            "name": "Nudeln pur",
            "portionen": 1,
            "zutaten": [{"produkt_id": "nudeln", "menge": 125}],
            "schritte": ["Kochen", "Essen"],
            "tags": ["schnell"],
        },
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    r = client.get(f"/api/v1/rezepte/{rid}").json()
    assert r["schritte"] == ["Kochen", "Essen"] and r["status"] == "ungetestet"
    e = client.post(
        "/api/v1/rezepte/aktion",
        json={"ids": [rid, "paprika-pasta"], "aktion": "tag_hinzu", "wert": "Pasta"},
    ).json()
    assert len(e["ok"]) == 2
    client.post("/api/v1/rezepte/aktion", json={"ids": [rid], "aktion": "favorit", "wert": True})
    client.put(f"/api/v1/rezepte/{rid}/meinung", json={"bewertung": 1, "notiz": "lecker"})
    buch = {x["id"]: x for x in client.get("/api/v1/rezeptbuch").json()}
    assert buch[rid]["favorit"] is True and buch[rid]["bewertung"] == 1
    assert buch["paprika-pasta"]["tags"] == ["Pasta"]
    client.put(f"/api/v1/plan/{HEUTE}", json={"rezept_id": rid, "portionen": 1})
    assert client.delete(f"/api/v1/rezepte/{rid}").status_code == 200
    assert client.get("/api/v1/plan").json() == []
    assert client.get(f"/api/v1/rezepte/{rid}").status_code == 404


def test_kochvorgang_und_bewertung(client):
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 300})
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 500})
    e = client.post(
        "/api/v1/gekocht",
        json={"rezept_id": "paprika-pasta", "portionen_gekocht": 2, "portionen_gegessen": 2},
    ).json()
    vid = e["kochvorgang_id"]
    resp = client.put(f"/api/v1/kochvorgaenge/{vid}/bewertung", json={"bewertung": -1})
    assert resp.status_code == 200, resp.text
    r = client.get("/api/v1/rezepte/paprika-pasta").json()
    assert r["bewertung"] == -1 and r["verlauf"][0]["bewertung"] == -1
    buch = client.get("/api/v1/rezeptbuch").json()[0]
    assert buch["gekocht"] == 1 and buch["zuletzt"] == str(HEUTE)


def test_einstellungen(client):
    e = client.get("/api/v1/einstellungen").json()
    assert e["planer"]["portionen"] == 2 and e["optionen"]["todo_entity"] == "todo.einkaufsliste"
    neu = client.put(
        "/api/v1/einstellungen/praeferenzen",
        json={"fleisch": -0.5, "aufwand_je_tag": {"0": "schnell"}, "ausschluss_tags": ["Fisch"]},
    ).json()
    assert neu["fleisch"] == -0.5 and neu["aufwand_je_tag"]["0"] == "schnell"
    assert neu["aufwand_je_tag"]["6"] == "aufwaendig"
    assert client.put("/api/v1/einstellungen/praeferenzen", json={"fleisch": 2}).status_code == 422
    assert client.put("/api/v1/einstellungen/planer", json={"gibts": 1}).status_code == 422
    assert client.delete("/api/v1/einstellungen/praeferenzen").json()["fleisch"] == 0


def test_protokolle(v_client, fake_ha):
    e = v_client.get("/api/v1/einstellungen").json()
    assert e["optionen"]["api_token"] is True  # nur gesetzt/nicht gesetzt
    v_client.put(
        "/api/v1/einstellungen/planer", json={"portionen": 3}, headers={"X-Ration-Client": "ui"}
    )
    fake_ha.ki_antwort = {"produkte": []}
    v_client.post("/api/v1/kategorisieren", json={"rezepte": False, "nur_ohne_attribute": False})
    fake_ha.ki_antwort = RuntimeError("Quota")
    v_client.post("/api/v1/kategorisieren", json={"rezepte": False, "nur_ohne_attribute": False})
    log = v_client.get("/api/v1/log/aenderungen").json()
    assert [x["pfad"] for x in log][-1] == "/api/v1/einstellungen/planer"
    assert log[-1]["quelle"] == "ui" and log[-1]["daten"] == {"portionen": 3}
    assert log[0]["quelle"] == "extern" and log[0]["status"] == 202
    ki = v_client.get("/api/v1/log/ki").json()
    assert [x["fehler"] for x in ki] == ["RuntimeError: Quota", None]
    detail = v_client.get(f"/api/v1/log/ki/{ki[1]['id']}").json()
    assert "Produkte" in detail["anweisung"] and detail["antwort"] == {"produkte": []}
    assert (
        v_client.get("/api/v1/log/aenderungen?pfad=kategorisieren").json()[0]["methode"] == "POST"
    )
