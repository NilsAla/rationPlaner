"""rationPlaner Bridge: Entitäten und Services, die an die App delegieren.

Gerüst. Coordinator, Sensoren, Card-Auslieferung und Service-Logik folgen in Phase 1.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, SERVICE_GEKOCHT, SERVICE_KATEGORISIEREN, SERVICE_PLAN_ERZEUGEN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data

    async def _not_implemented(call: ServiceCall) -> None:
        raise NotImplementedError(f"{DOMAIN}.{call.service} ist noch nicht implementiert")

    for service in (SERVICE_GEKOCHT, SERVICE_PLAN_ERZEUGEN, SERVICE_KATEGORISIEREN):
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, _not_implemented)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        for service in (SERVICE_GEKOCHT, SERVICE_PLAN_ERZEUGEN, SERVICE_KATEGORISIEREN):
            hass.services.async_remove(DOMAIN, service)
    return True
