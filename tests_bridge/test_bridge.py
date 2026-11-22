"""Bridge gegen eine gemockte App-API (pytest-homeassistant-custom-component)."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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
