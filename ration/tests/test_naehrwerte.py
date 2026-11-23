"""Nährwerte: Nutri-Score 2023, Rezeptsummen, API, KI-Vorschlag mit Review, Planer-Score."""

from app import naehrwerte
from app.planer import Kontext, ernaehrung_score

PAPRIKA = {
    "kcal": 31,
    "protein": 1,
    "fett": 0.3,
    "fett_gesaettigt": 0.1,
    "kohlenhydrate": 6,
    "zucker": 4.2,
    "ballaststoffe": 2.1,
    "salz": 0.01,
    "obst_gemuese": True,
    "roh": True,
}
NUDELN = {
    "kcal": 359,
    "protein": 12.5,
    "fett": 1.5,
    "fett_gesaettigt": 0.3,
    "kohlenhydrate": 71,
    "zucker": 3.5,
    "ballaststoffe": 3,
    "salz": 0.01,
}
OEL = {
    "kcal": 884,
    "protein": 0,
    "fett": 100,
    "fett_gesaettigt": 14,
    "kohlenhydrate": 0,
    "zucker": 0,
    "ballaststoffe": 0,
    "salz": 0,
    "obst_gemuese": False,
}


def test_nutri_score_grenzen():
    # Gurke-artig: kaum Energie, viel Gemüse → A
    a = naehrwerte.nutri_score(
        {
            "kcal": 15,
            "zucker": 1.7,
            "fett_gesaettigt": 0,
            "salz": 0,
            "protein": 0.6,
            "ballaststoffe": 0.5,
        },
        100,
    )
    assert a["klasse"] == "A" and a["punkte"] <= 0
    # Schokoriegel-artig → E
    e = naehrwerte.nutri_score(
        {
            "kcal": 520,
            "zucker": 50,
            "fett_gesaettigt": 15,
            "salz": 0.5,
            "protein": 6,
            "ballaststoffe": 2,
        },
        0,
    )
    assert e["klasse"] == "E"
    # Protein zählt bei N ≥ 11 ohne volle Gemüsepunkte nicht
    ohne = naehrwerte.nutri_score(
        {
            "kcal": 400,
            "zucker": 20,
            "fett_gesaettigt": 6,
            "salz": 1.2,
            "protein": 20,
            "ballaststoffe": 0,
        },
        0,
    )
    assert ohne["negativ"] >= 11 and ohne["positiv"] == 0
    assert naehrwerte.gesund_faktor(-5) == 1.0 and naehrwerte.gesund_faktor(25) == 0.0


def test_rezept_naehrwerte_und_api(client):
    r = client.get("/api/v1/rezepte/paprika-pasta/naehrwerte").json()
    assert r["abdeckung"] == 0 and r["nutri_score"] is None
    assert client.put("/api/v1/produkte/paprika/naehrwerte", json={"protein": 1}).status_code == 422
    for pid, werte in (("paprika", PAPRIKA), ("nudeln", NUDELN)):
        assert client.put(f"/api/v1/produkte/{pid}/naehrwerte", json=werte).status_code == 200
    r = client.get("/api/v1/rezepte/paprika-pasta/naehrwerte").json()
    # 250 g Paprika + 200 g Nudeln + 2 EL Öl (20 g, ohne Werte) für 2 Portionen
    assert r["abdeckung"] == round(450 / 470, 3)
    assert r["je_portion"]["kcal"] == round((2.5 * 31 + 2 * 359) / 2, 1)
    assert [f["produkt_id"] for f in r["fehlend"]] == ["oel"]
    assert r["nutri_score"]["klasse"] in "ABC"
    assert r["ziel"]["kcal_ziel"] == 700
    client.put("/api/v1/produkte/oel/naehrwerte", json=OEL)
    buch = {x["id"]: x for x in client.get("/api/v1/rezeptbuch").json()}
    assert buch["paprika-pasta"]["naehrwert_abdeckung"] == 1.0
    assert buch["paprika-pasta"]["nutri"] is not None
    detail = client.get("/api/v1/rezepte/paprika-pasta?portionen=4").json()
    assert detail["naehrwerte"]["je_portion"]["kcal"] == buch["paprika-pasta"]["kcal"]
    assert client.delete("/api/v1/produkte/oel/naehrwerte").status_code == 200
    assert client.get("/api/v1/produkte/oel/naehrwerte").json() is None


def test_ki_naehrwerte_nur_mit_review(fake_backend, fake_ha, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings

    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(ai_task_entity="ai_task.test", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'n.db'}",
    )
    with TestClient(app) as client:
        _ki_review(client, fake_ha)


def _ki_review(client, fake_ha):
    fake_ha.ki_antwort = {
        "produkte": [
            {"id": "paprika", **PAPRIKA},
            {"id": "nudeln", **{**NUDELN, "kcal": None}},  # ohne Energie: verworfen
            {"id": "gibtsnicht", **NUDELN},
        ]
    }
    r = client.post(
        "/api/v1/kategorisieren", json={"produkte": False, "rezepte": False, "naehrwerte": True}
    )
    assert r.status_code == 202, r.text
    v = client.get("/api/v1/vorschlaege").json()
    assert [(x["objekt_id"], x["feld"]) for x in v] == [("paprika", "naehrwerte")]
    assert client.get("/api/v1/produkte/paprika/naehrwerte").json() is None  # nichts automatisch
    e = client.post("/api/v1/vorschlaege", json={"uebernehmen": [v[0]["id"]], "ablehnen": []})
    assert e.json()["fehler"] == []
    n = client.get("/api/v1/produkte/paprika/naehrwerte").json()
    assert n["kcal"] == 31 and n["quelle"] == "ki" and n["obst_gemuese"] is True


def test_ernaehrung_score_neutral_ohne_daten():
    k = Kontext.__new__(Kontext)
    k.praeferenzen = {"gesund": 1.0, "protein": 1.0, "rohkost": 0.0}
    k.ziel_im_planer = False
    assert ernaehrung_score(None, k) == 0.0
    gut = naehrwerte.RezeptNaehrwerte(2, {}, {}, 400, 1.0, gesund=1.0, protein_kcal_pct=30)
    schlecht = naehrwerte.RezeptNaehrwerte(2, {}, {}, 400, 1.0, gesund=0.0, protein_kcal_pct=5)
    luecke = naehrwerte.RezeptNaehrwerte(2, {}, {}, 400, 0.5, gesund=1.0, protein_kcal_pct=30)
    assert ernaehrung_score(gut, k) > 0 > ernaehrung_score(schlecht, k)
    assert ernaehrung_score(luecke, k) == 0.0
