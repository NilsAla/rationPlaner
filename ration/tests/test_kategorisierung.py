"""Kategorisierungslauf: KI schlägt vor, Code validiert, Nutzer übernimmt per Diff."""

import pytest
from fastapi.testclient import TestClient

from app.inventory.base import Product
from app.main import create_app
from app.settings import Settings


@pytest.fixture
def k_client(fake_backend, fake_ha, tmp_path):
    fake_backend.add_product(Product(id="hack", name="Hackfleisch", unit="g"))
    fake_backend.add_product(Product(id="ei", name="Ei", unit="Stück"))
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(ai_task_entity="ai_task.test", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'k.db'}",
    )
    with TestClient(app) as c:
        yield c


class Antworten:
    """Fake-KI: Produkte- und Rezepte-Aufruf unterscheiden."""

    def __init__(self, ha):
        self.ha = ha

    def setzen(self, produkte, rezepte):
        async def ai_task(entity_id, task_name, instructions, structure, attachments=None):
            self.ha.ki_anfragen.append(instructions)
            return {"produkte": produkte} if "produkte" in structure else {"rezepte": rezepte}

        self.ha.ai_task = ai_task


def test_lauf_und_uebernehmen(k_client, fake_ha, fake_backend):
    Antworten(fake_ha).setzen(
        [
            {
                "id": "hack",
                "tracking_modus": "exakt",
                "kategorie": "Fleisch",
                "ablauf_typ": "verbrauchsdatum",
                "haltbarkeit_tage": 2,
                "haltbarkeit_offen_tage": 1,
                "haltbarkeit_gefroren_tage": 90,
                "haltbarkeit_aufgetaut_tage": 1,
                "g_je_einheit": 1,
                "toleranz_pct": 20,
                "packung_einheit": "Packung",
                "packung_menge": 500,
            },
            {
                "id": "ei",
                "tracking_modus": "menge",
                "kategorie": "Erfunden",
                "ablauf_typ": "mhd",
                "haltbarkeit_tage": 28,
                "haltbarkeit_offen_tage": 0,
                "haltbarkeit_gefroren_tage": 0,
                "haltbarkeit_aufgetaut_tage": 0,
                "g_je_einheit": 60,
                "toleranz_pct": 500,
                "packung_einheit": "Packung",
                "packung_menge": 10,
            },
            {"id": "paprika", "tracking_modus": "immer_da"},  # schon kategorisiert: nicht gefragt
        ],
        [
            {
                "id": "paprika-pasta",
                "tags": ["Pasta", "vegetarisch", "Quatsch"],
                "aufwand": "schnell",
                "rest_haltbarkeit_tage": 3,
            }
        ],
    )
    r = k_client.post("/api/v1/kategorisieren", json={})
    assert r.status_code == 202, r.text
    assert k_client.get("/api/v1/kategorisieren").json()[0]["status"] == "fertig"
    assert (
        "hack|Hackfleisch|g" in fake_ha.ki_anfragen[0] and "paprika|" not in fake_ha.ki_anfragen[0]
    )
    v = k_client.get("/api/v1/vorschlaege").json()
    felder = {(x["objekt_id"], x["feld"]): x for x in v}
    assert felder[("hack", "ablauf_typ")]["neu"] == "verbrauchsdatum"
    assert ("hack", "g_je_einheit") not in felder  # Einheit g: kein Vorschlag
    assert ("ei", "kategorie") not in felder and ("ei", "toleranz_pct") not in felder  # ungültig
    assert felder[("ei", "g_je_einheit")]["neu"] == 60
    assert felder[("paprika-pasta", "tags")]["neu"] == ["Pasta", "vegetarisch"]
    assert ("paprika", "tracking_modus") not in felder
    # nichts automatisch gesetzt
    assert (fake_backend._products["hack"].tracking_mode) is None

    uebernehmen = [x["id"] for x in v if x["objekt_id"] in ("hack", "paprika-pasta")]
    ablehnen = [x["id"] for x in v if x["objekt_id"] == "ei"]
    e = k_client.post(
        "/api/v1/vorschlaege", json={"uebernehmen": uebernehmen, "ablehnen": ablehnen}
    ).json()
    assert e["fehler"] == [] and e["uebernommen"] == len(uebernehmen)
    hack = k_client.get("/api/v1/produkte/hack").json()
    assert (hack["tracking_modus"], hack["kategorie"], hack["haltbarkeit_tage"]) == (
        "exakt",
        "Fleisch",
        2,
    )
    assert hack["packung"] == {"einheit": "Packung", "menge": 500}
    rez = k_client.get("/api/v1/rezepte/paprika-pasta").json()
    assert rez["tags"] == ["Pasta", "vegetarisch"] and rez["aufwand"] == "schnell"
    assert k_client.get("/api/v1/vorschlaege").json() == []


def test_lauf_fehler(k_client, fake_ha):
    fake_ha.ki_antwort = RuntimeError("weg")
    k_client.post("/api/v1/kategorisieren", json={"rezepte": False})
    lauf = k_client.get("/api/v1/kategorisieren").json()[0]
    assert lauf["status"] == "fehler" and lauf["fehler"] == "weg"


def test_ohne_ki(client):
    assert client.post("/api/v1/kategorisieren", json={}).status_code == 503
