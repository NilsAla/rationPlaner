"""Wochenplaner: Bewertung, Simulation mit Toleranz, KI-Auswahl mit Repair-Loop, Tauschen."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import planer
from app.inventory.base import Batch, Ingredient, Product, Recipe, TrackingMode
from app.main import create_app
from app.settings import Settings

HEUTE = date.today()


def tag(n: int) -> date:
    return HEUTE + timedelta(days=n)


PRODUKTE = [
    Product("paprika", "Paprika rot", "g", tracking_mode=TrackingMode.EXAKT, tolerance_pct=30,
            shelf_life_days=7, pack_unit="Stück", pack_size=160),
    Product("nudeln", "Nudeln", "g", tracking_mode=TrackingMode.MENGE, tolerance_pct=15,
            pack_unit="Packung", pack_size=500),
    Product("oel", "Olivenöl", "EL", tracking_mode=TrackingMode.FUELLSTAND),
    Product("hack", "Hackfleisch", "g", tracking_mode=TrackingMode.EXAKT, tolerance_pct=10,
            shelf_life_days=2, pack_unit="Packung", pack_size=500),
    Product("reis", "Reis", "g", tracking_mode=TrackingMode.MENGE, pack_unit="Packung",
            pack_size=1000),
    Product("sahne", "Sahne", "ml", tracking_mode=TrackingMode.EXAKT, shelf_life_days=7,
            pack_unit="Becher", pack_size=200),
]  # fmt: skip
REZEPTE = [
    Recipe("chili", "Chili", 2, (Ingredient("hack", 250), Ingredient("reis", 150))),
    Recipe("risotto", "Risotto", 2, (Ingredient("reis", 200), Ingredient("sahne", 100)),
           status="ungetestet"),
    Recipe("sahnenudeln", "Sahnenudeln", 2, (Ingredient("nudeln", 200), Ingredient("sahne", 200))),
    Recipe("curry", "Curry", 2, (Ingredient("reis", 150), Ingredient("paprika", 160)),
           status="ungetestet"),
]  # fmt: skip


def kontext(batches=(), **extra) -> planer.Kontext:
    rezepte = {r.id: r for r in REZEPTE}
    rezepte["paprika-pasta"] = Recipe(
        "paprika-pasta",
        "Paprika-Pasta",
        2,
        (Ingredient("paprika", 250), Ingredient("nudeln", 200), Ingredient("oel", 2)),
    )
    return planer.Kontext(
        produkte={p.id: p for p in PRODUKTE},
        rezepte=rezepte,
        batches=list(batches),
        weitere={},
        toleranz_default=10,
        historie=extra.get("historie", {}),
        angebote=set(),
        preise=extra.get("preise", {}),
    )


def charge(pid: str, menge: float, ablauf: date | None, nr: int = 1) -> Batch:
    return Batch(str(nr), pid, menge, ablauf, "kuehlschrank")


def test_simulation_toleranz_und_packungen():
    k = kontext([charge("paprika", 230, tag(3)), charge("sahne", 200, tag(5), 2)],
                preise={"nudeln": 0.002})  # fmt: skip
    plan = [
        planer.PlanTag(tag(0), "paprika-pasta", 2, "code"),  # Paprika 230 statt 250: Toleranz
        planer.PlanTag(tag(1), "sahnenudeln", 2, "code"),  # Nudeln aus dem Rest der Packung
    ]
    sim = planer.simulieren(k, plan, tag(0), tag(6))
    t0, t1 = sim.tage
    assert [(s.name, s.genommen) for s in t0.skaliert] == [("Paprika rot", 230)]
    assert t0.zukauf == ["Nudeln, 1 × Packung (500 g)"] and t1.zukauf == []
    assert [e["name"] for e in sim.einkauf] == ["Nudeln"]
    assert sim.kosten == 1.0 and sim.kosten_unbekannt == 0
    assert sim.verderb == [] and sim.ueberhang == []


def test_simulation_packung_geht_auf_und_verderb():
    k = kontext([charge("paprika", 100, tag(5)), charge("sahne", 150, tag(2), 2)])
    sim = planer.simulieren(k, [planer.PlanTag(tag(0), "paprika-pasta", 2, "code")], tag(0), tag(6))
    # 150 g fehlen → 1 Stück à 160; die übrigen 10 g liegen in der Toleranz und gehen mit rein
    assert sim.tage[0].skaliert[0].genommen == 260
    assert sim.ueberhang == []
    assert [(v.name, v.menge, v.zukauf) for v in sim.verderb] == [("Sahne", 150, False)]
    assert sim.kosten is None and sim.kosten_unbekannt == 2


def test_bewertung_aufbrauch_und_abwechslung():
    k = kontext([charge("hack", 500, tag(1)), charge("reis", 1000, None, 2)])
    lager = planer.Lager(k.produkte, k.batches)
    liste = planer.kandidaten(k, lager, tag(0), 2)
    assert liste[0].rezept_id == "chili" and liste[0].nutzt_bald == ["Hackfleisch"]
    k2 = kontext(k.batches, historie={"chili": tag(-2)})
    liste2 = planer.kandidaten(k2, planer.Lager(k2.produkte, k2.batches), tag(0), 2)
    chili = next(c for c in liste2 if c.rezept_id == "chili")
    assert chili.zuletzt_vor_tagen == 2 and chili.score < liste[0].score


def test_gierig_ohne_doppelte_und_ungetestet_grenze():
    k = kontext([charge("reis", 2000, None)])
    daten = [tag(i) for i in range(5)]
    plan = planer.gierig(k, daten, {}, 2, tag(0), tag(4))
    ids = [t.rezept_id for t in plan]
    assert len(ids) == len(set(ids)) == 5
    k.rezepte["neu"] = Recipe("neu", "Neu", 2, (Ingredient("reis", 100),), status="ungetestet")
    plan = planer.gierig(k, daten, {}, 2, tag(0), tag(4))
    assert sum(k.rezepte[t.rezept_id].status == "ungetestet" for t in plan) <= 2


def test_gekochte_tage_verbrauchen_nichts():
    k = kontext([charge("hack", 250, tag(1)), charge("reis", 150, None, 2)])
    fest = {tag(0): planer.PlanTag(tag(0), "chili", 2, "gekocht")}
    plan = planer.gierig(k, [tag(0), tag(1)], fest, 2, tag(0), tag(1))
    sim = planer.simulieren(k, plan, tag(0), tag(1))
    assert sim.tage[0].quelle == "gekocht" and sim.tage[0].zukauf == []
    assert plan[1].rezept_id != "chili"


def test_pruefen_meldet_konkrete_probleme():
    k = kontext()
    daten = [tag(0), tag(1), tag(2)]
    fest = {tag(3): planer.PlanTag(tag(3), "chili", 2, "fest")}
    antwort = {
        "tage": [
            {"datum": tag(0).isoformat(), "rezept_id": "risotto", "portionen": 2},
            {"datum": tag(1).isoformat(), "rezept_id": "chili", "portionen": 2},
            {"datum": tag(2).isoformat(), "rezept_id": "gibtsnicht", "portionen": 9},
            {"datum": tag(9).isoformat(), "rezept_id": "curry", "portionen": 2},
        ]
    }
    gueltig, probleme = planer.pruefen(antwort, k, daten, fest, set(k.rezepte))
    assert list(gueltig) == [tag(0)]
    assert any("Chili kommt schon vor" in p for p in probleme)
    assert any("'gibtsnicht' ist kein Kandidat" in p for p in probleme)
    assert any("nicht zu planen" in p for p in probleme)


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def p_client(fake_backend, fake_ha, tmp_path):
    for p in PRODUKTE[3:]:
        fake_backend.add_product(p)
    for r in REZEPTE:
        fake_backend._recipes[r.id] = r
    fake_backend._batches.append(Batch("900", "hack", 500, tag(1), "kuehlschrank"))
    fake_backend._batches.append(Batch("901", "reis", 1000, None, "vorrat"))
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(ai_task_entity="ai_task.test", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'p.db'}",
    )
    with TestClient(app) as c:
        yield c


def erzeugen(client, **daten) -> dict:
    resp = client.post("/api/v1/plan/erzeugen", json=daten)
    assert resp.status_code == 202, resp.text
    return client.get(f"/api/v1/plan/laeufe/{resp.json()['lauf_id']}").json()


def test_plan_code_und_uebernehmen(p_client, fake_ha):
    p_client.put(
        f"/api/v1/plan/{tag(2).isoformat()}", json={"rezept_id": "risotto", "portionen": 2}
    )
    lauf = erzeugen(p_client, tage=4, ki=False)
    assert lauf["status"] == "fertig", lauf
    tage = {t["datum"]: t for t in lauf["tage"]}
    assert tage[tag(0).isoformat()]["rezept_id"] == "chili"  # Hack läuft morgen ab
    assert tage[tag(2).isoformat()]["quelle"] == "fest"
    ids = [t["rezept_id"] for t in lauf["tage"]]
    assert len(ids) == len(set(ids)) == 4
    assert lauf["ki"]["aktiv"] is False and fake_ha.ki_anfragen == []
    assert fake_ha.hinweise[-1][0] == "Wochenplan fertig"
    resp = p_client.post(f"/api/v1/plan/laeufe/{lauf['id']}/uebernehmen")
    assert resp.json() == {"uebernommen": 3}
    plan = p_client.get("/api/v1/plan?tage=4").json()
    assert [p["rezept_id"] for p in plan] == ids
    assert p_client.post(f"/api/v1/plan/laeufe/{lauf['id']}/uebernehmen").status_code == 409
    sim = p_client.get("/api/v1/plan/simulation?tage=4").json()
    assert len(sim["tage"]) == 4 and "einkauf" in sim


def test_plan_ki_mit_repair(p_client, fake_ha):
    d = [tag(i).isoformat() for i in range(3)]
    schlecht = {"tage": [{"datum": d[0], "rezept_id": "chili", "portionen": 2},
                         {"datum": d[1], "rezept_id": "chili", "portionen": 2}]}  # fmt: skip
    gut = {
        "tage": [
            {"datum": d[0], "rezept_id": "chili", "portionen": 2, "begruendung": "Hack weg"},
            {"datum": d[1], "rezept_id": "sahnenudeln", "portionen": 2},
            {"datum": d[2], "rezept_id": "paprika-pasta", "portionen": 3},
        ]
    }
    fake_ha.ki_antwort = [schlecht, gut]
    lauf = erzeugen(p_client, tage=3)
    assert lauf["ki"]["runden"] == 2 and lauf["ki"]["probleme"] == []
    assert "Chili kommt schon vor" in fake_ha.ki_anfragen[1]
    assert "Hackfleisch 500 g bis" in fake_ha.ki_anfragen[0]
    assert [(t["rezept_id"], t["quelle"]) for t in lauf["tage"]] == [
        ("chili", "ki"),
        ("sahnenudeln", "ki"),
        ("paprika-pasta", "ki"),
    ]
    assert lauf["tage"][2]["portionen"] == 3


def test_plan_ki_ausfall_code_plant(p_client, fake_ha):
    fake_ha.ki_antwort = RuntimeError("Quota")
    lauf = erzeugen(p_client, tage=2, uebernehmen=True)
    assert lauf["status"] == "uebernommen" and lauf["ki"]["fehler"] == "Quota"
    assert all(t["quelle"] == "code" for t in lauf["tage"])
    assert len(p_client.get("/api/v1/plan?tage=2").json()) == 2


def test_tauschen(p_client):
    heute = tag(0).isoformat()
    p_client.put(f"/api/v1/plan/{heute}", json={"rezept_id": "chili", "portionen": 2})
    resp = p_client.post(f"/api/v1/plan/{heute}/tauschen", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["rezept_id"] != "chili"
    p_client.post("/api/v1/gekocht", json={"portionen_gekocht": 2, "portionen_gegessen": 2})
    assert p_client.post(f"/api/v1/plan/{heute}/tauschen", json={}).status_code == 409
