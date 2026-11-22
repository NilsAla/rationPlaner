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


def mock_app(aioclient_mock, api_version="1.0"):
    aioclient_mock.get(f"{URL}/api/v1/version", json={"api_version": api_version})
    aioclient_mock.get(f"{URL}/api/v1/heute", json=HEUTE)
    aioclient_mock.get(f"{URL}/api/v1/status", json=STATUS)


async def test_config_flow(hass: HomeAssistant, aioclient_mock):
    mock_app(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL + "/"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"url": URL}


async def test_config_flow_falsche_version(hass: HomeAssistant, aioclient_mock):
    mock_app(aioclient_mock, api_version="2.0")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL})
    assert result["errors"] == {"base": "incompatible_version"}


async def _setup(hass, aioclient_mock):
    mock_app(aioclient_mock)
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
    assert hass.states.get("sensor.ration_laeuft_ab").state == "1"


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
