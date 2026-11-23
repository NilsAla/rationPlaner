"""Packungsgrößen clever: Käufe zählen, neue Größen als Vorschlag, KI-Analyse, Einkauf bevorzugt
tatsächlich gekaufte Größen."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import kueche, packungen
from app.db import Packung
from app.inventory.base import Product
from app.main import create_app
from app.settings import Settings

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f"
    b"\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


@pytest.fixture
def app_client(fake_backend, fake_ha, tmp_path):
    fake_backend.add_product(
        Product(id="kidney", name="Kidneybohnen", unit="g", pack_unit="Dose", pack_size=400)
    )
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(
            ai_task_entity="ai_task.test", data_dir=tmp_path, media_dir=tmp_path / "media"
        ),
        db_url=f"sqlite:///{tmp_path / 'p.db'}",
    )
    with TestClient(app) as c:
        c.app = app
        yield c


def bon(text, preis, **x):
    return {"markt": "Frische Ecke", "datum": date.today().isoformat(), "summe": preis,
            "positionen": [{"text": text, "typ": "artikel", "anzahl": 1, "preis": preis,
                            "packung_menge": 0, "packung_einheit": "", "produkt_id": "kidney",
                            "konfidenz": 0.95, **x}]}  # fmt: skip


def bon_buchen(c, fake_ha, antwort):
    fake_ha.ki_antwort = antwort
    r = c.post("/api/v1/bon", files={"datei": ("bon.png", PNG, "image/png")})
    b = c.get(f"/api/v1/bon/{r.json()['id']}").json()
    z = b["positionen"][0]
    r = c.post(
        f"/api/v1/bon/{b['id']}/buchen",
        json={"positionen": [{"index": 0, "produkt_id": "kidney", "menge": z["menge"]}]},
    )
    assert r.status_code == 200, r.text
    return z


def varianten(c):
    return next(p for p in c.get("/api/v1/packungen").json() if p["produkt_id"] == "kidney")


def test_bekannte_groesse_zaehlt_neue_wird_vorschlag(app_client, fake_ha):
    bon_buchen(app_client, fake_ha, bon("KIDNEYB. 400G", 0.79))
    bon_buchen(app_client, fake_ha, bon("KIDNEYB. 400G", 1.58, anzahl=2, einzelpreis=0.79))
    v = varianten(app_client)["weitere"]
    std = next(x for x in v if x["menge"] == 400)
    assert (std["haeufigkeit"], std["bestaetigt"], std["haendler"]) == (3, True, "Frische Ecke")
    assert std["zuletzt"] == date.today().isoformat()
    # neue Größe aus dem Kürzel "2x400g": unbestätigt + Vorschlag, Einkauf nutzt sie noch nicht
    z = bon_buchen(app_client, fake_ha, bon("KIDNEYB. 2x400G", 1.39))
    assert z["menge"] == 800 and z["packung_abweichend"]
    neu = next(x for x in varianten(app_client)["weitere"] if x["menge"] == 800)
    assert neu["bestaetigt"] is False and neu["haeufigkeit"] == 1
    vor = [x for x in app_client.get("/api/v1/vorschlaege").json() if x["feld"] == "variante"]
    assert len(vor) == 1 and vor[0]["neu"]["menge"] == 800
    with Session(app_client.app.state.engine) as s:
        assert all(g.menge != 800 for g in packungen.weitere(s).get("kidney", []))
    # Review: übernehmen → gilt
    app_client.post("/api/v1/vorschlaege", json={"uebernehmen": [vor[0]["id"]]})
    neu = next(x for x in varianten(app_client)["weitere"] if x["menge"] == 800)
    assert neu["bestaetigt"] is True
    # erneuter Kauf derselben neuen Größe: kein zweiter Vorschlag, Häufigkeit hoch
    bon_buchen(app_client, fake_ha, bon("KIDNEYB. 2x400G", 1.39))
    assert not [x for x in app_client.get("/api/v1/vorschlaege").json() if x["feld"] == "variante"]
    assert (
        next(x for x in varianten(app_client)["weitere"] if x["menge"] == 800)["haeufigkeit"] == 2
    )


def test_variante_pflegen(app_client):
    r = app_client.post(
        "/api/v1/produkte/kidney/packungen", json={"einheit": "Dose", "menge": 250}
    ).json()
    r = app_client.patch(
        f"/api/v1/packungen/{r['id']}",
        json={"marke": "Ja!", "haendler": "Rewe", "ean": "4001234567890"},
    )
    assert (r.json()["marke"], r.json()["ean"]) == ("Ja!", "4001234567890")
    assert (
        app_client.patch(f"/api/v1/packungen/{r.json()['id']}", json={"ean": "12"}).status_code
        == 422
    )


def test_analyse_standard_aus_bons_und_ki_groessen(app_client, fake_ha):
    with Session(app_client.app.state.engine) as s:
        prod = Product(id="kidney", name="Kidneybohnen", unit="g", pack_unit="Dose", pack_size=400)
        for _ in range(3):
            packungen.kauf_merken(s, prod, 800, 1, date.today() - timedelta(days=10), "Rewe")
        packungen.kauf_merken(s, prod, 400, 1, date.today() - timedelta(days=5), "Rewe")
        # alter Kauf außerhalb des Zeitraums zählt nicht für den Standard
        packungen.kauf_merken(s, prod, 400, 9, date.today() - timedelta(days=400), "Rewe")
        s.commit()
    fake_ha.ki_antwort = {
        "produkte": [
            {
                "id": "kidney",
                "groessen": [
                    {"einheit": "Dose", "menge": 400},
                    {"einheit": "Dose", "menge": 255, "marke": "Bonduelle"},
                    {"einheit": "Glas", "menge": -1},  # ungültig, fällt weg
                ],
                "typisch": 400,
            }
        ]
    }
    r = app_client.post(
        "/api/v1/kategorisieren",
        json={"produkte": False, "rezepte": False, "packungen": True, "monate": 6,
              "produkt_ids": ["kidney"]},
    )  # fmt: skip
    assert r.status_code == 202, r.text
    vor = {
        (v["feld"], v["neu"]["menge"]): v
        for v in app_client.get("/api/v1/vorschlaege").json()
        if v["objekt_id"] == "kidney"
    }
    std = vor[("packung", 800)]
    assert std["alt"] == {"einheit": "Dose", "menge": 400} and "häufigste" in std["neu"]["grund"]
    assert vor[("variante", 255)]["neu"]["marke"] == "Bonduelle"
    assert ("variante", 400) not in vor
    log = app_client.get("/api/v1/log/ki").json()[0]
    assert (
        log["job"] == "packungen"
        and "800×3" in app_client.get(f"/api/v1/log/ki/{log['id']}").json()["anweisung"]
    )
    # übernehmen: Standard in Grocy, Variante in der App-DB
    app_client.post("/api/v1/vorschlaege", json={"uebernehmen": [v["id"] for v in vor.values()]})
    k = varianten(app_client)
    assert k["standard"]["menge"] == 800
    assert any(
        x["menge"] == 255 and x["marke"] == "Bonduelle" and x["bestaetigt"] for x in k["weitere"]
    )


def test_einkauf_bevorzugt_gekaufte_groesse():
    # 800 g fehlen: 2 × 400 und 1 × 800 haben beide keinen Überhang
    std = kueche.Groesse("Dose", 400)
    gross = kueche.Groesse("Dose", 800, "manuell")
    assert [(p.anzahl, p.menge) for p in kueche.waehle_packungen(800, [std, gross])] == [(1, 800)]
    gekauft = kueche.Groesse("Dose", 400, "kauf", haeufigkeit=5)
    prod = Product(id="k", name="K", unit="g", pack_unit="Dose", pack_size=400)
    auswahl = kueche.groessen(prod, [gekauft, gross])
    assert auswahl[0].haeufigkeit == 5  # Käufe zählen für die Standard-Packung
    assert [(p.anzahl, p.menge) for p in kueche.waehle_packungen(800, auswahl)] == [(2, 400)]
    # Überhang geht weiter vor: 500 fehlen → 1 × 800 (300 Überhang) schlägt 2 × 400 (300) nicht,
    # bei Gleichstand gewinnt die gekaufte Größe
    assert [(p.anzahl, p.menge) for p in kueche.waehle_packungen(500, auswahl)] == [(2, 400)]


def test_unbestaetigte_variante_nicht_im_einkauf(app_client):
    with Session(app_client.app.state.engine) as s:
        s.add(Packung(produkt_id="kidney", einheit="Dose", menge=800, bestaetigt=False))
        s.commit()
        assert packungen.weitere(s) == {}
        assert packungen.weitere(s, alle=True)["kidney"][0].menge == 800
