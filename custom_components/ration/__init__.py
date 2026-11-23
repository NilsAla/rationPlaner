"""rationPlaner Bridge: Entitäten, Services und LLM-Tools, die an die App delegieren."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .aktionen import AKTIONEN, Aktion
from .api import RationApi, RationApiError
from .const import CONF_TOKEN, CONF_URL, DOMAIN
from .coordinator import RationCoordinator
from .llm_api import RationLlmApi

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _coordinator(hass: HomeAssistant) -> RationCoordinator:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("rationPlaner ist nicht eingerichtet")
    return entries[0].runtime_data


def _register(hass: HomeAssistant, aktion: Aktion) -> None:
    async def handler(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass)
        try:
            result = await aktion.ausfuehren(coordinator.api, dict(call.data))
        except RationApiError as err:
            raise HomeAssistantError(str(err)) from err
        if aktion.schreibend:
            await coordinator.async_request_refresh()
        return result

    # Lesende Services liefern nur eine Antwort, schreibende optional.
    supports = SupportsResponse.OPTIONAL if aktion.schreibend else SupportsResponse.ONLY
    hass.services.async_register(DOMAIN, aktion.name, handler, aktion.schema, supports)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    for aktion in AKTIONEN:
        _register(hass, aktion)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    api = RationApi(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_TOKEN, "")
    )
    coordinator = RationCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(
        llm.async_register_api(hass, RationLlmApi(hass=hass, id=DOMAIN, name="rationPlaner"))
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
