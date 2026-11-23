"""KI-Client: HA- und OpenAI-Modus, Job-Zuordnung, Schema-Umwandlung, Tokens und Kosten."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bon import STRUKTUR as BON_STRUKTUR
from app.ki import schaetzen, schema_aus_struktur
from app.main import create_app
from app.settings import Settings

ANTWORT = {"zutaten": [{"index": 0, "aktion": "zuordnen", "produkt_id": None, "g_je_einheit": 1}]}


class OpenAIFake:
    def __init__(self):
        self.anfragen: list[dict] = []
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.anfragen.append({"body": json.loads(request.content), "headers": request.headers})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "kaputt"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(ANTWORT)}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
            },
        )


@pytest.fixture
def openai_fake():
    return OpenAIFake()


@pytest.fixture
def ki_client(fake_backend, fake_ha, tmp_path, openai_fake):
    (tmp_path / "media" / "ration").mkdir(parents=True)
    (tmp_path / "media" / "ration" / "bild.jpg").write_bytes(b"\xff\xd8bild")
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(
            ai_task_entity="ai_task.standard",
            openai_api_key="sk-geheim",
            data_dir=tmp_path,
            media_dir=tmp_path / "media",
        ),
        db_url=f"sqlite:///{tmp_path / 'ki.db'}",
        ki_http=httpx.AsyncClient(transport=httpx.MockTransport(openai_fake)),
    )
    with TestClient(app) as c:
        c.app = app
        yield c


def _generate(client, job="import", anhaenge=None):
    import anyio

    async def lauf():
        return await client.app.state.ki.generate(
            job, "rationPlaner Test", "Ordne zu.", {"x": {"selector": {"text": {}}}}, anhaenge
        )

    return anyio.run(lauf)


def test_schema_strict():
    s = schema_aus_struktur(BON_STRUKTUR)
    assert s["additionalProperties"] is False and set(s["required"]) == set(BON_STRUKTUR)
    pos = s["properties"]["positionen"]
    assert pos["type"] == "array"
    felder = pos["items"]
    assert felder["additionalProperties"] is False
    assert felder["properties"]["typ"]["enum"][0] == "artikel"
    # optionales Feld: strict verlangt required, also null erlaubt
    assert felder["properties"]["bezug"] == {"anyOf": [{"type": "number"}, {"type": "null"}]}
    assert "bezug" in felder["required"]


def test_ha_modus_schaetzt_tokens_und_kosten(ki_client, fake_ha):
    fake_ha.ki_antwort = ANTWORT
    ki_client.put(
        "/api/v1/einstellungen/ki",
        json={"preise": {"ai_task.standard": {"input": 0.4, "output": 1.6}}},
    )
    assert _generate(ki_client) == ANTWORT
    eintrag = ki_client.get("/api/v1/log/ki").json()[0]
    assert eintrag["backend"] == "ha" and eintrag["job"] == "import"
    assert eintrag["geschaetzt"] is True
    assert eintrag["tokens_in"] == schaetzen('Ordne zu.{"x": {"selector": {"text": {}}}}')
    assert eintrag["kosten_eur"] == pytest.approx(
        eintrag["tokens_in"] * 0.4 / 1e6 + eintrag["tokens_out"] * 1.6 / 1e6
    )


def test_job_eigene_entitaet(ki_client, fake_ha):
    fake_ha.ki_antwort = ANTWORT
    ki_client.put(
        "/api/v1/einstellungen/ki", json={"jobs": {"import": {"entitaet": "ai_task.billig"}}}
    )
    _generate(ki_client)
    assert ki_client.get("/api/v1/log/ki").json()[0]["entitaet"] == "ai_task.billig"


def test_openai_modus_echte_usage(ki_client, openai_fake):
    ki_client.put(
        "/api/v1/einstellungen/ki",
        json={
            "jobs": {"import": {"modus": "openai", "modell": "gpt-test"}},
            "preise": {"gpt-test": {"input": 1, "output": 4}},
        },
    )
    assert _generate(ki_client) == {
        "zutaten": [{"index": 0, "aktion": "zuordnen", "g_je_einheit": 1}]
    }
    body = openai_fake.anfragen[0]["body"]
    assert body["model"] == "gpt-test"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert openai_fake.anfragen[0]["headers"]["authorization"] == "Bearer sk-geheim"
    eintrag = ki_client.get("/api/v1/log/ki").json()[0]
    assert (eintrag["tokens_in"], eintrag["tokens_out"], eintrag["geschaetzt"]) == (
        1200,
        300,
        False,
    )
    assert eintrag["kosten_eur"] == pytest.approx(1200 / 1e6 + 300 * 4 / 1e6)
    # Key steht nirgends im Protokoll oder in den Einstellungen
    detail = ki_client.get(f"/api/v1/log/ki/{eintrag['id']}").text
    assert "sk-geheim" not in detail
    einst = ki_client.get("/api/v1/einstellungen").json()
    assert einst["optionen"]["openai_api_key"] is True and "sk-geheim" not in json.dumps(einst)


def test_openai_bild_als_data_url(ki_client, openai_fake):
    ki_client.put("/api/v1/einstellungen/ki", json={"modus": "openai"})
    _generate(
        ki_client,
        anhaenge=[
            {
                "media_content_id": "media-source://media_source/local/ration/bild.jpg",
                "media_content_type": "image/jpeg",
            }
        ],
    )
    inhalt = openai_fake.anfragen[0]["body"]["messages"][0]["content"]
    assert inhalt[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_openai_fehler_landet_im_protokoll(ki_client, openai_fake):
    ki_client.put("/api/v1/einstellungen/ki", json={"modus": "openai"})
    openai_fake.status = 429
    with pytest.raises(Exception, match="OpenAI 429"):
        _generate(ki_client)
    assert "kaputt" in ki_client.get("/api/v1/log/ki").json()[0]["fehler"]


def test_openai_ohne_key_nicht_verfuegbar(fake_backend, fake_ha, tmp_path):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(ai_task_entity="ai_task.x", data_dir=tmp_path),
        db_url=f"sqlite:///{tmp_path / 'k.db'}",
    )
    with TestClient(app) as c:
        c.put("/api/v1/einstellungen/ki", json={"jobs": {"bon": {"modus": "openai"}}})
        r = c.post("/api/v1/bon/bild", json={"daten": "A" * 200})
        assert r.status_code == 503 and "openai_api_key" in r.json()["detail"]
        assert app.state.ki.verfuegbar("import")  # andere Jobs bleiben bei HA


def test_kosten_summe_je_job(ki_client, fake_ha):
    fake_ha.ki_antwort = ANTWORT
    ki_client.put(
        "/api/v1/einstellungen/ki", json={"preise": {"ai_task.standard": {"input": 1, "output": 1}}}
    )
    _generate(ki_client)
    _generate(ki_client, job="planer")
    k = ki_client.get("/api/v1/log/ki-kosten?zeitraum=tag").json()
    assert set(k["jobs"]) == {"import", "planer"}
    assert k["jobs"]["import"]["aufrufe"] == 1 and k["jobs"]["import"]["geschaetzt"] is True
    assert k["summe_eur"] > 0


def test_einstellung_validiert(ki_client):
    assert ki_client.put("/api/v1/einstellungen/ki", json={"modus": "x"}).status_code == 422
    r = ki_client.put("/api/v1/einstellungen/ki", json={"jobs": {"bon": {"entitaet": "light.x"}}})
    assert r.status_code == 422
    assert ki_client.put("/api/v1/einstellungen/ki", json={"jobs": {"foo": {}}}).status_code == 422


def test_alte_db_bekommt_neue_spalten(tmp_path):
    import sqlite3

    from app.db import make_engine

    db = tmp_path / "alt.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE ki_log (id INTEGER PRIMARY KEY, zeit DATETIME, aufgabe VARCHAR, "
            "entitaet VARCHAR, anweisung TEXT, struktur JSON, anhaenge JSON, antwort JSON, "
            "fehler VARCHAR, dauer_s FLOAT)"
        )
        conn.execute(
            "INSERT INTO ki_log (aufgabe, entitaet, anweisung, dauer_s) VALUES ('a','b','c',1)"
        )
    make_engine(f"sqlite:///{db}")
    with sqlite3.connect(db) as conn:
        spalten = {r[1] for r in conn.execute("PRAGMA table_info(ki_log)")}
        assert {"job", "backend", "tokens_in", "kosten_eur", "geschaetzt"} <= spalten
        assert conn.execute("SELECT count(*) FROM ki_log").fetchone()[0] == 1
