"""Bridge gegen eine gemockte App-API (pytest-homeassistant-custom-component)."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ration.const import DOMAIN

URL = "http://ration.test:8099"
HEUTE = {
    "datum": "2026-11-22",
    "status": "geplant",
    "rezept": {
        "id": "1",
        "name": "Paprika-Pasta",
        "portionen": 2,
        "zutaten": [{"name": "Paprika", "menge": 250, "einheit": "g"}],
        "schritte": ["Nudeln kochen."],
    },
}
STATUS = {
    "text": "3 Artikel · 1 laufen bald ab",
    "artikel": 3,
    "laeuft_bald_ab": [{"name": "Paprika", "menge": 200, "einheit": "g", "ablauf": "2026-11-23"}],
}

GERICHT = {
    "datum": "2026-11-23",
    "rezept_id": "1",
    "name": "Paprika-Pasta",
    "portionen": 2,
    "status": "geplant",
    "foto": 7,
    "kcal": 640.0,
    "protein": 22.5,
    "nutri": "B",
    "aufwand": None,
    "zutaten": [
        {"produkt_id": "p", "name": "Paprika", "menge": 250, "einheit": "g", "ampel": "fehlt"}
    ],
    "fehlend": ["Paprika"],
    "kochbar": False,
}
UEBERSICHT = {
    "datum": "2026-11-22",
    "heute": None,
    "morgen": GERICHT,
    "tage": [],
    "bald_tage": 5,
    "laeuft_ab": [
        {"name": "Paprika", "menge": 200, "einheit": "g", "ablauf": "2026-11-23", "tage": 1},
        {"name": "Milch", "menge": 1, "einheit": "l", "ablauf": "2026-11-26", "tage": 4},
    ],
    "reste": [],
    "artikel": 3,
    "status_text": "3 Artikel · 2 laufen bald ab",
}


def mock_app(aioclient_mock, api_version="1.0", uebersicht=True):
    aioclient_mock.get(f"{URL}/api/v1/version", json={"api_version": api_version})
    aioclient_mock.get(f"{URL}/api/v1/heute", json=HEUTE)
    aioclient_mock.get(f"{URL}/api/v1/status", json=STATUS)
    if uebersicht:
        aioclient_mock.get(f"{URL}/api/v1/uebersicht", json=UEBERSICHT)
    else:
        aioclient_mock.get(f"{URL}/api/v1/uebersicht", status=404, json={"detail": "Not Found"})
    aioclient_mock.get(f"{URL}/api/v1/fotos/7", content=b"\xff\xd8jpeg")


async def test_config_flow(hass: HomeAssistant, aioclient_mock):
    mock_app(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL + "/"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"url": URL, "token": ""}


async def test_config_flow_token(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(f"{URL}/api/v1/version", status=401, json={"detail": "Token"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "token": "falsch"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_token_header_und_reauth(hass: HomeAssistant, aioclient_mock):
    mock_app(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data={"url": URL, "token": "geheim"}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    headers = [call[3] for call in aioclient_mock.mock_calls if str(call[1]).endswith("/heute")]
    assert headers and headers[0]["Authorization"] == "Bearer geheim"
    # App verlangt plötzlich ein anderes Token: Reauth-Flow startet
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/v1/heute", status=401, json={"detail": "Token"})
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_config_flow_falsche_version(hass: HomeAssistant, aioclient_mock):
    mock_app(aioclient_mock, api_version="2.0")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL})
    assert result["errors"] == {"base": "incompatible_version"}


async def _setup(hass, aioclient_mock, **kwargs):
    mock_app(aioclient_mock, **kwargs)
    entry = MockConfigEntry(domain=DOMAIN, data={"url": URL}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensoren(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    heute = hass.states.get("sensor.ration_rezept_heute")
    assert heute.state == "Paprika-Pasta"
    assert heute.attributes["zutaten"][0]["name"] == "Paprika"
    assert hass.states.get("sensor.ration_status").state == "3 Artikel · 1 laufen bald ab"
    # laeuft_ab kommt aus der Übersicht (Einstellung bald_tage), nicht aus /status
    ab = hass.states.get("sensor.ration_laeuft_ab")
    assert ab.state == "2" and ab.attributes["tage"] == 5
    assert ab.attributes["liste"][0] == {
        "name": "Paprika",
        "menge": 200,
        "einheit": "g",
        "mhd": "2026-11-23",
    }
    assert hass.states.get("sensor.ration_gericht_heute").state == "nichts geplant"
    morgen = hass.states.get("sensor.ration_gericht_morgen")
    assert morgen.state == "Paprika-Pasta"
    assert morgen.attributes["kcal"] == 640.0 and morgen.attributes["nutri"] == "B"
    assert morgen.attributes["fehlend"] == ["Paprika"]
    assert morgen.attributes["foto"] == "image.ration_gericht_morgen"
    assert hass.states.get("image.ration_gericht_heute").state == "unavailable"
    assert hass.states.get("image.ration_gericht_morgen").state != "unavailable"


async def test_bild_entitaet(hass: HomeAssistant, aioclient_mock, hass_client):
    await _setup(hass, aioclient_mock)
    client = await hass_client()
    resp = await client.get("/api/image_proxy/image.ration_gericht_morgen")
    assert resp.status == 200
    assert await resp.read() == b"\xff\xd8jpeg"


async def test_aeltere_app_ohne_uebersicht(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock, uebersicht=False)
    assert hass.states.get("sensor.ration_rezept_heute").state == "Paprika-Pasta"
    assert hass.states.get("sensor.ration_laeuft_ab").state == "1"
    assert hass.states.get("sensor.ration_gericht_heute").state == "unavailable"


async def test_service_gekocht(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{URL}/api/v1/gekocht", json={"rezept": "Paprika-Pasta", "fehlend": []})
    result = await hass.services.async_call(
        DOMAIN,
        "gekocht",
        {"portionen_gekocht": 2, "portionen_gegessen": 2},
        blocking=True,
        return_response=True,
    )
    assert result["rezept"] == "Paprika-Pasta"
    posted = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posted[0][2] == {"portionen_gekocht": 2, "portionen_gegessen": 2}


async def test_service_lesend_liefert_liste_als_items(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    aioclient_mock.get(f"{URL}/api/v1/rezepte", json=[{"id": "1", "name": "Paprika-Pasta"}])
    result = await hass.services.async_call(
        DOMAIN, "rezepte", {}, blocking=True, return_response=True
    )
    assert result == {"items": [{"id": "1", "name": "Paprika-Pasta"}]}


async def test_service_plan_setzen(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    aioclient_mock.put(f"{URL}/api/v1/plan/2026-11-23", json={"rezept_id": "1"})
    await hass.services.async_call(
        DOMAIN,
        "plan_setzen",
        {"datum": "2026-11-23", "rezept_id": "1", "portionen": 2},
        blocking=True,
    )
    put = [c for c in aioclient_mock.mock_calls if c[0] == "PUT"]
    assert str(put[0][1]).endswith("/api/v1/plan/2026-11-23")
    assert put[0][2] == {"rezept_id": "1", "portionen": 2}


async def test_llm_api(hass: HomeAssistant, aioclient_mock):
    entry = await _setup(hass, aioclient_mock)
    assert DOMAIN in {api.id for api in llm.async_get_apis(hass)}
    context = llm.LLMContext(
        platform="test", context=None, language="de", assistant=None, device_id=None
    )
    instance = await llm.async_get_api(hass, DOMAIN, context)
    tools = {t.name: t for t in instance.tools}
    assert f"{DOMAIN}_bestand" in tools

    # Direkt am Tool, APIInstance.async_call_tool zieht die conversation-Integration nach.
    async def call(name, args):
        return await tools[f"{DOMAIN}_{name}"].async_call(
            hass, llm.ToolInput(tool_name=name, tool_args=args), context
        )

    aioclient_mock.get(f"{URL}/api/v1/plan", json=[])
    result = await call("plan", {"tage": 3})
    assert result == {"items": []}
    get = [c for c in aioclient_mock.mock_calls if c[0] == "GET" and "/plan" in str(c[1])]
    assert get[0][1].query["tage"] == "3"

    fehler = await call("plan_setzen", {"datum": "morgen"})
    assert "error" in fehler

    await hass.config_entries.async_unload(entry.entry_id)
    assert DOMAIN not in {api.id for api in llm.async_get_apis(hass)}


async def test_service_packung_loeschen_pfad(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    aioclient_mock.delete(f"{URL}/api/v1/packungen/7", status=204)
    await hass.services.async_call(DOMAIN, "packung_loeschen", {"packung_id": 7}, blocking=True)
    assert any(
        c[0] == "DELETE" and str(c[1]).endswith("/packungen/7") for c in aioclient_mock.mock_calls
    )


async def test_tool_import_uebernehmen(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(f"{URL}/api/v1/import/3/uebernehmen", json={"rezept_id": "9"})
    context = llm.LLMContext(
        platform="test", context=None, language="de", assistant=None, device_id=None
    )
    instance = await llm.async_get_api(hass, DOMAIN, context)
    tool = next(t for t in instance.tools if t.name == f"{DOMAIN}_rezept_import_uebernehmen")
    args = {"import_id": 3, "zutaten": [{"index": 0, "produkt_id": "1", "menge": 250}]}
    result = await tool.async_call(
        hass, llm.ToolInput(tool_name=tool.name, tool_args=args), context
    )
    assert result == {"rezept_id": "9"}
    post = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert post[-1][2] == {"zutaten": [{"index": 0, "produkt_id": "1", "menge": 250.0}]}
