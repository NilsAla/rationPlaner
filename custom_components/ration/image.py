"""Fotos der Gerichte für heute und morgen als Bild-Entitäten (entity_picture fürs Dashboard)."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import RationApiError
from .const import DOMAIN
from .coordinator import RationCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RationCoordinator = entry.runtime_data
    async_add_entities([GerichtBild(coordinator, entry, tag) for tag in ("heute", "morgen")])


class GerichtBild(CoordinatorEntity[RationCoordinator], ImageEntity):
    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: RationCoordinator, entry: ConfigEntry, tag: str) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self.tag = tag
        key = f"gericht_{tag}"
        self._attr_name = f"Gericht {tag}"
        self._attr_unique_id = f"{entry.entry_id}_{key}_bild"
        self.entity_id = f"image.{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="rationPlaner",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._foto_id = self._aktuell()
        self._attr_image_last_updated = dt_util.utcnow()

    def _aktuell(self) -> int | None:
        g = ((self.coordinator.data or {}).get("uebersicht") or {}).get(self.tag)
        return g.get("foto") if g else None

    @property
    def available(self) -> bool:
        return super().available and self._foto_id is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        if (neu := self._aktuell()) != self._foto_id:
            self._foto_id = neu
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if self._foto_id is None:
            return None
        try:
            return await self.coordinator.api.foto(self._foto_id)
        except RationApiError:
            return None
