"""Startseite/Sensoren: Übersicht heute/morgen und Verschieben im Plan."""

from datetime import date, timedelta


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_uebersicht_leer(client):
    u = client.get("/api/v1/uebersicht").json()
    assert u["heute"] is None and u["morgen"] is None
    assert len(u["tage"]) == 7
    assert u["bald_tage"] == 3


def test_uebersicht_heute_und_morgen(client):
    client.post("/api/v1/bestand", json={"produkt_id": "nudeln", "menge": 1000})
    client.put(f"/api/v1/plan/{_in(1)}", json={"rezept_id": "paprika-pasta", "portionen": 4})
    u = client.get("/api/v1/uebersicht").json()
    assert u["heute"] is None
    m = u["morgen"]
    assert m["name"] == "Paprika-Pasta" and m["portionen"] == 4 and m["status"] == "geplant"
    paprika = next(z for z in m["zutaten"] if z["produkt_id"] == "paprika")
    assert paprika["menge"] == 500  # 250 g für 2 Portionen → 4 Portionen
    assert "Paprika rot" in m["fehlend"] and "Nudeln" not in m["fehlend"]
    assert m["kochbar"] is False


def test_uebersicht_laeuft_ab_nach_einstellung(client):
    client.post("/api/v1/bestand", json={"produkt_id": "paprika", "menge": 200, "mhd": _in(5)})
    assert client.get("/api/v1/uebersicht").json()["laeuft_ab"] == []
    client.put("/api/v1/einstellungen/anzeige", json={"bald_tage": 7})
    u = client.get("/api/v1/uebersicht").json()
    assert [x["name"] for x in u["laeuft_ab"]] == ["Paprika rot"]
    assert u["laeuft_ab"][0]["ablauf"] == _in(5)


def test_verschieben_auf_freien_tag(client):
    client.put(f"/api/v1/plan/{_in(0)}", json={"rezept_id": "paprika-pasta", "portionen": 3})
    r = client.post(f"/api/v1/plan/{_in(0)}/verschieben", json={"ziel": _in(1)})
    assert r.json()["getauscht"] is False
    plan = client.get("/api/v1/plan").json()
    assert [(p["datum"], p["portionen"]) for p in plan] == [(_in(1), 3)]


def test_verschieben_tauscht(client):
    client.put(f"/api/v1/plan/{_in(0)}", json={"rezept_id": "paprika-pasta", "portionen": 2})
    client.put(f"/api/v1/plan/{_in(1)}", json={"rezept_id": "paprika-pasta", "portionen": 5})
    r = client.post(f"/api/v1/plan/{_in(0)}/verschieben", json={"ziel": _in(1)})
    assert r.json()["getauscht"] is True
    plan = {p["datum"]: p["portionen"] for p in client.get("/api/v1/plan").json()}
    assert plan == {_in(0): 5, _in(1): 2}


def test_verschieben_ohne_eintrag(client):
    assert (
        client.post(f"/api/v1/plan/{_in(0)}/verschieben", json={"ziel": _in(1)}).status_code == 404
    )
