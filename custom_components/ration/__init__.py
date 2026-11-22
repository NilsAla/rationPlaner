"""rationPlaner Bridge: Entitäten und Services, die an die App delegieren."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import RationApi, RationApiError
from .const import (
    CONF_URL,
    DOMAIN,
    SERVICE_EINKAUFSLISTE,
    SERVICE_GEKOCHT,
    SERVICE_KATEGORISIEREN,
    SERVICE_PLAN_ERZEUGEN,
)
from .coordinator import RationCoordinator

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

GEKOCHT_SCHEMA = vol.Schema(
    {
        vol.Optional("rezept_id"): cv.string,
        vol.Required("portionen_gekocht"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
        vol.Required("portionen_gegessen"): vol.All(vol.Coerce(int), vol.Range(min=0, max=12)),
    }
)
EINKAUF_SCHEMA = vol.Schema(
    {vol.Optional("tage", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=14))}
)


def _coordinator(hass: HomeAssistant) -> RationCoordinator:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("rationPlaner ist nicht eingerichtet")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def gekocht(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass)
        try:
            result = await coordinator.api.gekocht(dict(call.data))
        except RationApiError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()
        return result

    async def einkaufsliste(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass)
        try:
            return await coordinator.api.einkaufsliste(dict(call.data))
        except RationApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def noch_nicht(call: ServiceCall) -> None:
        raise ServiceValidationError(f"{DOMAIN}.{call.service} ist noch nicht verfügbar")

    hass.services.async_register(
        DOMAIN, SERVICE_GEKOCHT, gekocht, GEKOCHT_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EINKAUFSLISTE, einkaufsliste, EINKAUF_SCHEMA, SupportsResponse.OPTIONAL
    )
    for service in (SERVICE_PLAN_ERZEUGEN, SERVICE_KATEGORISIEREN):
        hass.services.async_register(DOMAIN, service, noch_nicht)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    api = RationApi(async_get_clientsession(hass), entry.data[CONF_URL])
    coordinator = RationCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
