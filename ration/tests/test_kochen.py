"""Kochmodus: Anleitung (Code-Fallback, KI mit Validierung), Migrator, Timer, Sitzungen."""

import pytest
from fastapi.testclient import TestClient

from app.kochen import anteile_ausgleichen, dauern_s, schritt_pruefen
from app.main import create_app
from app.settings import Settings

R = "paprika-pasta"  # Zutaten: 0 Paprika 250 g, 1 Nudeln 200 g, 2 Olivenöl 2 EL; 2 Portionen

KI_ANTWORT = {
    "schritte": [
        {
            "titel": "Wasser aufsetzen",
            "text": "Großen Topf mit Salzwasser aufsetzen.",
            "parallel": True,
        },
        {"titel": "Paprika schneiden", "text": "Paprika in Streifen schneiden.", "zutaten": ["0"]},
        {
            "titel": "Nudeln ins Wasser",
            "text": "Nudeln 10 Minuten kochen.",
            "zutaten": ["1"],
            "timer_minuten": 10,
            "timer_name": "Nudeln",
            "parallel": True,
        },
        {
            "titel": "Paprika anbraten",
            "text": "Öl erhitzen, Paprika 25 Minuten braten.",
            "zutaten": ["2", "9"],
            "timer_minuten": 25,
        },
    ]
}


@pytest.fixture
def k_client(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(ai_task_entity="ai_task.test", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'k.db'}",
    )
    with TestClient(app) as c:
        yield c


def test_dauern_und_schritt_pruefen():
    assert dauern_s("8–10 Min kochen, dann 1 Std ruhen, 30 Sek rühren") == [600, 3600, 30]
    s = schritt_pruefen(
        {"titel": "X", "text": "y", "zutaten": ["1", "1", "7", "a"], "timer_minuten": 0.1}, 3
    )
    assert s["zutaten"] == [{"zutat": 1, "anteil": 1.0}]  # doppelt, außerhalb, kaputt raus
    assert s["timer_s"] is None  # 6 s ist kein Timer
    assert schritt_pruefen({"titel": "", "text": ""}, 3) is None


def test_anteile_nur_bei_teilweise_geteilt():
    roh = [
        {"titel": "Paprika schneiden", "zutaten": ["0"]},
        {"titel": "Butter in die Pfanne", "zutaten": ["0", "1"], "teilweise": ["1"]},
        {"titel": "Butter in die Soße", "zutaten": ["1"], "teilweise": ["1"]},
    ]
    schritte = [schritt_pruefen(x, 2) for x in roh]
    anteile_ausgleichen(schritte)
    # Paprika zweimal bearbeitet = volle Menge, Butter als Teilmenge halbiert
    assert [[(z["zutat"], z["anteil"]) for z in s["zutaten"]] for s in schritte] == [
        [(0, 1.0)],
        [(0, 1.0), (1, 0.5)],
        [(1, 0.5)],
    ]


def test_ohne_anleitung_code_vorschlag(k_client):
    a = k_client.get(f"/api/v1/kochen/anleitung/{R}?portionen=4").json()
    assert a["gespeichert"] is False and a["quelle"] == "code"
    titel = [s["titel"] for s in a["schritte"]]
    assert titel == ["Nudeln kochen.", "Paprika in Öl anbraten."]
    # Namenstreffer: Nudeln in Schritt 1, Paprika in Schritt 2; Mengen auf 4 Portionen
    assert [(z["name"], z["menge"]) for z in a["schritte"][0]["zutaten"]] == [("Nudeln", 400)]
    # Olivenöl über das Nomen "Öl" im zweiten Schritt, kein "Zutaten bereitstellen" nötig
    assert [z["name"] for z in a["schritte"][1]["zutaten"]] == ["Paprika rot", "Olivenöl"]
    assert a["fehlend"] == []


def test_code_fallback_legt_reste_vorne_an(k_client, fake_backend):
    import asyncio

    from app.inventory.base import RecipePatch

    asyncio.run(
        fake_backend.update_recipe(R, RecipePatch(description="<p>Alles kochen, 8-10 Min.</p>"))
    )
    a = k_client.post(f"/api/v1/kochen/anleitung/{R}/generieren", json={"ki": False}).json()
    assert a["gespeichert"] and a["status"] == "entwurf" and a["quelle"] == "code"
    assert a["schritte"][0]["titel"] == "Zutaten bereitstellen"
    assert len(a["schritte"][0]["zutaten"]) == 3 and a["fehlend"] == []
    assert a["schritte"][1]["timer_s"] == 600


def test_ki_umschreiben_validiert(k_client, fake_ha):
    fake_ha.ki_antwort = KI_ANTWORT
    a = k_client.post(f"/api/v1/kochen/anleitung/{R}/generieren", json={"ki": True}).json()
    assert a["quelle"] == "ki" and a["status"] == "entwurf"
    s = a["schritte"]
    assert [x["titel"] for x in s][:3] == [
        "Wasser aufsetzen",
        "Paprika schneiden",
        "Nudeln ins Wasser",
    ]
    assert s[0]["parallel"] and s[2]["timer_s"] == 600 and s[2]["timer_name"] == "Nudeln"
    assert [z["name"] for z in s[3]["zutaten"]] == ["Olivenöl"]  # Index 9 verworfen
    # Timer stehen nicht im Original → als Schätzung markiert, Hinweis
    assert s[2]["timer_geschaetzt"] and any("≈" in h for h in a["hinweise"])
    assert (
        "Paprika-Pasta" in fake_ha.ki_anfragen[0]
        and "0 | Paprika rot | 250 g" in fake_ha.ki_anfragen[0]
    )
    assert len(fake_ha.ki_anfragen) == 1  # alle Zutaten zugeordnet, keine Reparatur


def test_ki_reparatur_und_code_ergaenzt(k_client, fake_ha):
    ohne_oel = {"schritte": [dict(x) for x in KI_ANTWORT["schritte"][:3]]}
    fake_ha.ki_antwort = [ohne_oel, ohne_oel]
    a = k_client.post(f"/api/v1/kochen/anleitung/{R}/generieren", json={"ki": True}).json()
    assert len(fake_ha.ki_anfragen) == 2 and "Olivenöl" in fake_ha.ki_anfragen[1]
    assert a["fehlend"] == [] and any("Olivenöl" in h for h in a["hinweise"])


def test_ki_fehler_faellt_auf_code_zurueck(k_client, fake_ha):
    fake_ha.ki_antwort = RuntimeError("Timeout")
    a = k_client.post(f"/api/v1/kochen/anleitung/{R}/generieren", json={"ki": True}).json()
    assert a["quelle"] == "code" and "Timeout" in a["hinweise"][0]


def test_bearbeiten_freigeben_veraltet(k_client, fake_backend):
    schritte = [
        {
            "titel": "Alles",
            "text": "Kochen",
            "zutaten": [{"zutat": 0, "anteil": 0.5}, {"zutat": 1}],
        },
        {"titel": "Rest", "text": "Paprika dazu", "zutaten": [{"zutat": 0, "anteil": 0.5}, 2]},
    ]
    a = k_client.put(
        f"/api/v1/kochen/anleitung/{R}", json={"schritte": schritte, "status": "geprueft"}
    )
    assert a.status_code == 200, a.text
    a = a.json()
    assert a["status"] == "geprueft" and a["quelle"] == "manuell"
    assert [z["menge"] for z in a["schritte"][0]["zutaten"]] == [125, 200]
    assert a["schritte"][1]["zutaten"][0]["frueher"] == 1
    liste = k_client.get("/api/v1/kochen/anleitungen").json()
    assert liste[0]["status"] == "geprueft" and not liste[0]["veraltet"]
    import asyncio

    from app.inventory.base import RecipePatch

    asyncio.run(fake_backend.update_recipe(R, RecipePatch(description="<p>Neu</p>")))
    assert k_client.get("/api/v1/kochen/anleitungen").json()[0]["veraltet"]


def test_migrieren_im_hintergrund(k_client, fake_ha):
    fake_ha.ki_antwort = KI_ANTWORT
    r = k_client.post("/api/v1/kochen/migrieren", json={})
    assert r.status_code == 202 and r.json()["rezepte"] == 1
    lauf = k_client.get("/api/v1/kochen/migrieren").json()[0]
    assert lauf["status"] == "fertig" and lauf["fertig"] == 1 and lauf["fehlgeschlagen"] == []
    assert k_client.get("/api/v1/kochen/anleitungen").json()[0]["quelle"] == "ki"
    # nur_fehlende: nichts mehr zu tun
    assert k_client.post("/api/v1/kochen/migrieren", json={}).status_code == 409
    assert fake_ha.hinweise and "1 von 1" in fake_ha.hinweise[-1][1]


def test_migrieren_ki_fehler_speichert_nichts(k_client, fake_ha):
    fake_ha.ki_antwort = RuntimeError("Timeout")
    assert k_client.post("/api/v1/kochen/migrieren", json={}).status_code == 202
    lauf = k_client.get("/api/v1/kochen/migrieren").json()[0]
    assert lauf["status"] == "fertig" and "Timeout" in lauf["fehlgeschlagen"][0]["fehler"]
    # kein Code-Fallback im Massenlauf: Rezept bleibt offen, nächster Lauf versucht es erneut
    assert k_client.get("/api/v1/kochen/anleitungen").json()[0]["status"] == "keine"
    fake_ha.ki_antwort = KI_ANTWORT
    assert k_client.post("/api/v1/kochen/migrieren", json={}).status_code == 202


def test_timer_automatisch_und_starten(k_client, fake_ha):
    fake_ha.zustaende = {
        "timer.kuechenuhr1": {"state": "active", "attributes": {"friendly_name": "Küchenuhr 1"}},
        "timer.kuechenuhr2": {"state": "idle", "attributes": {"friendly_name": "Küchenuhr 2"}},
        "timer.wecker": {"state": "idle", "attributes": {"friendly_name": "Wecker"}},
    }
    t = k_client.get("/api/v1/kochen/timer").json()
    assert t["automatisch"] and [x["entity_id"] for x in t["timer"]] == [
        "timer.kuechenuhr1",
        "timer.kuechenuhr2",
    ]
    sitz = k_client.post("/api/v1/kochen/sitzung", json={"rezept_id": R}).json()
    r = k_client.post(
        "/api/v1/kochen/timer",
        json={"sekunden": 600, "name": "Nudeln", "sitzung_id": sitz["id"], "schritt": 2},
    )
    assert r.status_code == 200 and r.json()["entity_id"] == "timer.kuechenuhr2"  # erster freier
    assert fake_ha.aufrufe[-1] == (
        "timer",
        "start",
        {"entity_id": "timer.kuechenuhr2", "duration": "00:10:00"},
    )
    sitz = k_client.get(f"/api/v1/kochen/sitzung/{sitz['id']}").json()
    assert sitz["timer"][0]["name"] == "Nudeln" and sitz["timer"][0]["schritt"] == 2
    # alle belegt → 409; fremder Timer → 404
    assert k_client.post("/api/v1/kochen/timer", json={"sekunden": 60}).status_code == 409
    assert k_client.post("/api/v1/kochen/timer/timer.wecker/abbrechen").status_code == 404
    k_client.post("/api/v1/kochen/timer/timer.kuechenuhr2/abbrechen")
    assert k_client.get(f"/api/v1/kochen/sitzung/{sitz['id']}").json()["timer"] == []


def test_timer_aus_app_option(fake_backend, fake_ha, tmp_path):
    fake_ha.zustaende = {
        "timer.kuechenuhr1": {"state": "idle", "attributes": {}},
        "timer.eier": {"state": "idle", "attributes": {"friendly_name": "Eieruhr"}},
    }
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(timer_entities=("timer.eier",), data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'o.db'}",
    )
    with TestClient(app) as c:
        t = c.get("/api/v1/kochen/timer").json()
        assert not t["automatisch"] and [x["name"] for x in t["timer"]] == ["Eieruhr"]
        assert c.get("/api/v1/einstellungen").json()["optionen"]["timer_entities"] == ["timer.eier"]


def test_sitzung_fortsetzen_und_neu(k_client):
    a = k_client.post("/api/v1/kochen/sitzung", json={"rezept_id": R}).json()
    assert a["portionen"] == 2 and a["schritt"] == 0
    k_client.patch(
        f"/api/v1/kochen/sitzung/{a['id']}",
        json={"schritt": 2, "abgehakt": ["0:1"], "erledigt": [0, 1]},
    )
    b = k_client.post("/api/v1/kochen/sitzung", json={"rezept_id": R}).json()
    assert b["id"] == a["id"] and b["schritt"] == 2 and b["abgehakt"] == ["0:1"]
    assert [x["id"] for x in k_client.get("/api/v1/kochen/sitzungen").json()] == [a["id"]]
    c = k_client.post("/api/v1/kochen/sitzung", json={"rezept_id": R, "portionen": 4}).json()
    assert c["id"] != a["id"] and c["portionen"] == 4 and c["schritt"] == 0
    k_client.patch(f"/api/v1/kochen/sitzung/{c['id']}", json={"aktiv": False})
    assert k_client.get("/api/v1/kochen/sitzungen").json() == []


def test_mengen_im_text_skalieren():
    from app.kueche import mengen_skalieren as m

    namen = ["Hähnchenbrust", "Zwiebel", "Ei"]
    t = "250 g Hähnchenbrust 10 Minuten anbraten, 2 Zwiebeln, 1 Zehe Knoblauch, 200 °C, 2 cm"
    assert m(t, 1.0, namen) == t
    assert m(t, 0.5, namen) == (
        "125 g Hähnchenbrust 10 Minuten anbraten, 1 Zwiebel, ½ Zehe Knoblauch, 200 °C, 2 cm"
    )
    assert m(t, 2, namen) == (
        "500 g Hähnchenbrust 10 Minuten anbraten, 4 Zwiebeln, 2 Zehen Knoblauch, 200 °C, 2 cm"
    )
    assert m("2-3 EL Öl, 1,5 l Wasser, 2 Eier, 4 Personen", 2, namen) == (
        "4–6 EL Öl, 3 l Wasser, 4 Eier, 4 Personen"
    )
