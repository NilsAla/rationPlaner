"""Kassenbon: Upload, KI-Auswertung (gefaked), Regeln, Alias, Review, Buchen."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100

ANTWORT = {
    "markt": "REWE Markt GmbH",
    "datum": "2026-11-21",
    "summe": 9.07,
    "positionen": [
        {
            "text": "BARILLA SPAGHETTI 500G",
            "anzahl": 2,
            "preis": 3.58,
            "packung_menge": 500,
            "packung_einheit": "g",
            "produkt_id": "nudeln",
            "konfidenz": 0.95,
        },
        {
            "text": "PAPRIKA ROT",
            "anzahl": 3,
            "preis": 2.97,
            "packung_menge": 0,
            "packung_einheit": "",
            "produkt_id": "paprika",
            "konfidenz": 0.6,
        },
        {
            "text": "OLIVENOEL 0,75L",
            "anzahl": 1,
            "preis": 6.99,
            "packung_menge": 0.75,
            "packung_einheit": "l",
            "produkt_id": "oel",
            "konfidenz": 0.9,
        },
        {
            "text": "PFAND 0,25",
            "anzahl": 1,
            "preis": 0.25,
            "packung_menge": 0,
            "packung_einheit": "",
            "produkt_id": "",
            "konfidenz": 1,
        },
        {
            "text": "KICHERERBSEN 400G",
            "anzahl": 1,
            "preis": 0.89,
            "packung_menge": 400,
            "packung_einheit": "g",
            "produkt_id": "gibtsnicht",
            "neu_name": "Kichererbsen",
            "konfidenz": 0.8,
        },
    ],
}


@pytest.fixture
def bon_client(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(
            ai_task_entity="ai_task.test", data_dir=tmp_path, media_dir=tmp_path / "media"
        ),
        db_url=f"sqlite:///{tmp_path / 'b.db'}",
    )
    with TestClient(app) as c:
        yield c


def _hochladen(c):
    r = c.post("/api/v1/bon", files={"datei": ("bon.png", PNG, "image/png")})
    assert r.status_code == 202, r.text
    return c.get(f"/api/v1/bon/{r.json()['id']}").json()


def test_bon_auswerten_und_buchen(bon_client, fake_ha, tmp_path):
    fake_ha.ki_antwort = ANTWORT
    bon = _hochladen(bon_client)
    assert bon["status"] == "review" and bon["markt"] == "REWE Markt GmbH"
    anhang = fake_ha.ki_anhaenge[0][0]
    assert anhang["media_content_id"].startswith("media-source://media_source/local/ration/bons/")
    assert (tmp_path / "media" / "ration" / "bons").iterdir()
    assert "nudeln|Nudeln|g" in fake_ha.ki_anfragen[0]
    p = {x["text"]: x for x in bon["positionen"]}
    # Code rechnet: 2 × 500 g; Paprika über Standard-Packung 160 g
    assert (
        p["BARILLA SPAGHETTI 500G"]["menge"],
        p["BARILLA SPAGHETTI 500G"]["vorausgewaehlt"],
    ) == (1000, True)
    assert p["PAPRIKA ROT"]["menge"] == 480 and p["PAPRIKA ROT"]["vorausgewaehlt"] is False
    # Produkt führt EL mit 10 g je EL: 750 ml ≈ 750 g → 75 EL, nur Vorschlag
    assert (p["OLIVENOEL 0,75L"]["menge"], p["OLIVENOEL 0,75L"]["status"]) == (75, "vorschlag")
    assert p["PFAND 0,25"]["aktion"] == "weglassen"
    assert (p["KICHERERBSEN 400G"]["aktion"], p["KICHERERBSEN 400G"]["menge"]) == ("neu", 400)
    assert fake_ha.hinweise and fake_ha.hinweise[0][0] == "Kassenbon gelesen"

    r = bon_client.post(
        f"/api/v1/bon/{bon['id']}/buchen",
        json={
            "positionen": [
                {"index": 0, "produkt_id": "nudeln", "menge": 1000, "preis": 3.58},
                {"index": 1, "produkt_id": "paprika", "menge": 480, "preis": 2.97},
                {"index": 4, "neu_anlegen": "Kichererbsen", "menge": 400, "preis": 0.89},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["neue_produkte"] == ["Kichererbsen"]
    bestand = {b["name"]: b["menge"] for b in bon_client.get("/api/v1/bestand").json()}
    assert bestand == {"Nudeln": 1000, "Paprika rot": 480, "Kichererbsen": 400}
    # zweiter Bon desselben Markts: Alias greift ohne KI-Zuordnung
    fake_ha.ki_antwort = {**ANTWORT, "positionen": [{**ANTWORT["positionen"][1], "produkt_id": ""}]}
    bon2 = _hochladen(bon_client)
    pos = bon2["positionen"][0]
    assert (pos["quelle"], pos["produkt_id"], pos["vorausgewaehlt"]) == ("alias", "paprika", True)
    again = bon_client.post(f"/api/v1/bon/{bon['id']}/buchen", json={"positionen": []})
    assert again.status_code == 409


def test_bon_ki_fehler(bon_client, fake_ha):
    fake_ha.ki_antwort = RuntimeError("Kontingent erschöpft")
    bon = _hochladen(bon_client)
    assert bon["status"] == "fehler" and "Kontingent" in bon["fehler"]
    assert fake_ha.hinweise[-1][0] == "Kassenbon fehlgeschlagen"


def test_bon_nur_bilder(bon_client):
    r = bon_client.post("/api/v1/bon", files={"datei": ("x.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 415


def test_bon_ohne_ki(client):
    r = client.post("/api/v1/bon", files={"datei": ("bon.png", PNG, "image/png")})
    assert r.status_code == 503


def test_bon_aus_media(bon_client, fake_ha, tmp_path):
    fake_ha.ki_antwort = ANTWORT
    (tmp_path / "media" / "upload").mkdir(parents=True)
    (tmp_path / "media" / "upload" / "bon.jpg").write_bytes(b"\xff\xd8" + b"0" * 50)
    r = bon_client.post("/api/v1/bon/media", json={"pfad": "upload/bon.jpg"})
    assert r.status_code == 202, r.text
    assert bon_client.get(f"/api/v1/bon/{r.json()['id']}").json()["status"] == "review"
    raus = bon_client.post("/api/v1/bon/media", json={"pfad": "../b.db"})
    assert raus.status_code == 404


def test_bon_als_base64(bon_client, fake_ha):
    import base64

    fake_ha.ki_antwort = {"markt": "X", "datum": "2026-11-21", "summe": 0, "positionen": []}
    daten = base64.b64encode(PNG).decode()
    r = bon_client.post("/api/v1/bon/bild", json={"daten": daten, "typ": "image/png"})
    assert r.status_code == 202, r.text
    assert bon_client.get(f"/api/v1/bon/{r.json()['id']}").json()["status"] == "review"
    kaputt = {"daten": "!" * 200, "typ": "image/png"}
    assert bon_client.post("/api/v1/bon/bild", json=kaputt).status_code == 422
    assert (
        bon_client.post("/api/v1/bon/bild", json={"daten": daten, "typ": "image/gif"}).status_code
        == 415
    )


HUERDEN = {
    "markt": "Frische Ecke",
    "datum": "2026-11-21",
    "summe": 13.35,
    "positionen": [
        # 0: Gewichtsware
        {
            "text": "PAPRIKA ROT LOSE",
            "typ": "artikel",
            "anzahl": 1,
            "einzelpreis": 3.99,
            "preis": 1.94,
            "packung_menge": 0.486,
            "packung_einheit": "kg",
            "produkt_id": "paprika",
            "konfidenz": 0.9,
        },
        # 1: KI liest Anzahl 1, Code rechnet 2,97 ÷ 0,99 = 3
        {
            "text": "SPAGHETTI NR.5 500G",
            "typ": "artikel",
            "anzahl": 1,
            "einzelpreis": 0.99,
            "preis": 2.97,
            "packung_menge": 500,
            "packung_einheit": "g",
            "produkt_id": "nudeln",
            "konfidenz": 0.95,
        },
        {
            "text": "OLIVENOEL NAT.EXTRA 0,75L",
            "typ": "artikel",
            "anzahl": 1,
            "preis": 6.49,
            "packung_menge": 0.75,
            "packung_einheit": "l",
            "produkt_id": "oel",
            "konfidenz": 0.9,
        },
        {"text": "RABATT 20%", "typ": "rabatt", "bezug": 2, "anzahl": 1, "preis": -1.30},
        {"text": "PFAND 12 x 0,25", "typ": "pfand", "anzahl": 1, "preis": 3.00},
        {
            "text": "SPAGHETTI NR.5 500G",
            "typ": "artikel",
            "anzahl": 1,
            "preis": 0.99,
            "packung_menge": 500,
            "packung_einheit": "g",
            "produkt_id": "nudeln",
            "konfidenz": 0.9,
        },
        # Storno ohne bezug: Code findet die gleichnamige Zeile davor
        {"text": "STORNO SPAGHETTI NR.5 500G", "typ": "artikel", "anzahl": 1, "preis": -0.99},
        {
            "text": "TUETENSUPPE TOMATE",
            "typ": "artikel",
            "anzahl": 1,
            "preis": 0.89,
            "neu_name": "Tütensuppe Tomate",
            "konfidenz": 0.7,
        },
        {"text": "LEERGUT", "typ": "leergut", "anzahl": 1, "preis": -2.25},
        {
            "text": "EINWEGHANDSCHUHE 100ST",
            "typ": "non_food",
            "anzahl": 1,
            "preis": 2.49,
            "neu_name": "Einweghandschuhe",
            "konfidenz": 0.8,
        },
        {"text": "SUMME", "typ": "artikel", "anzahl": 1, "preis": 13.35},
    ],
}


def test_bon_huerden(bon_client, fake_ha):
    fake_ha.ki_antwort = HUERDEN
    bon = _hochladen(bon_client)
    p = {(x["text"], x["index"]): x for x in bon["positionen"]}
    by = {}
    for x in bon["positionen"]:
        by.setdefault(x["text"], []).append(x)
    assert "SUMME" not in by  # Summenzeile fliegt raus
    assert by["PAPRIKA ROT LOSE"][0]["menge"] == 486
    nudeln, storniert = by["SPAGHETTI NR.5 500G"]
    assert (nudeln["anzahl"], nudeln["menge"], nudeln["aktion"]) == (3, 1500, "buchen")
    assert "Preis ÷ Einzelpreis" in nudeln["hinweis"]
    assert (storniert["aktion"], storniert["hinweis"]) == ("weglassen", "storniert")
    assert by["STORNO SPAGHETTI NR.5 500G"][0]["typ"] == "storno"  # Code-Guard schlägt KI-Typ
    oel = by["OLIVENOEL NAT.EXTRA 0,75L"][0]
    assert (oel["preis"], oel["rabatt"]) == (5.19, 1.3) and "Rabatt" in oel["hinweis"]
    assert by["RABATT 20%"][0]["bezug"] == oel["index"]
    assert by["PFAND 12 x 0,25"][0]["aktion"] == by["LEERGUT"][0]["aktion"] == "weglassen"
    suppe = by["TUETENSUPPE TOMATE"][0]
    assert (suppe["aktion"], suppe["neu_name"]) == ("neu", "Tütensuppe Tomate")
    hand = by["EINWEGHANDSCHUHE 100ST"][0]
    assert (hand["quelle"], hand["vorausgewaehlt"]) == ("non_food", False)
    # Summenprüfung: 1,94 + 2,97 + 5,19 + 3,00 + 0,89 − 2,25 + 2,49 = 14,23
    assert bon["pruefung"] == {"berechnet": 14.23, "abweichung": -0.88}
    assert p  # Indizes durchgehend
    assert [x["index"] for x in bon["positionen"]] == list(range(len(bon["positionen"])))
