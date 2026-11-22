"""Sensoren: Rezept heute, Statuszeile, läuft bald ab."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RationCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RationCoordinator = entry.runtime_data
    async_add_entities(
        [
            RezeptHeuteSensor(coordinator, entry),
            StatusSensor(coordinator, entry),
            LaeuftAbSensor(coordinator, entry),
        ]
    )


class RationSensor(CoordinatorEntity[RationCoordinator], SensorEntity):
    _attr_has_entity_name = True
    key: str

    def __init__(self, coordinator: RationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{self.key}"
        # Feste entity_id laut Konzept (sensor.ration_<key>), unabhängig vom Gerätenamen.
        self.entity_id = f"sensor.{DOMAIN}_{self.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="rationPlaner",
            entry_type=DeviceEntryType.SERVICE,
        )


class RezeptHeuteSensor(RationSensor):
    key = "rezept_heute"
    _attr_name = "Rezept heute"
    _attr_icon = "mdi:silverware-fork-knife"

    @property
    def native_value(self) -> str:
        rezept = self.coordinator.data["heute"]["rezept"]
        return rezept["name"] if rezept else "nichts geplant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        heute = self.coordinator.data["heute"]
        rezept = heute["rezept"] or {}
        return {
            "rezept_id": rezept.get("id"),
            "portionen": rezept.get("portionen"),
            "zutaten": rezept.get("zutaten", []),
            "schritte": rezept.get("schritte", []),
            "status": heute.get("status"),
        }


class StatusSensor(RationSensor):
    key = "status"
    _attr_name = "Status"
    _attr_icon = "mdi:fridge-outline"

    @property
    def native_value(self) -> str:
        return self.coordinator.data["status"]["text"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.coordinator.data["status"]
        return {"artikel": st["artikel"], "laeuft_bald_ab": len(st["laeuft_bald_ab"])}


class LaeuftAbSensor(RationSensor):
    key = "laeuft_ab"
    _attr_name = "Läuft bald ab"
    _attr_icon = "mdi:clock-alert-outline"
    _attr_native_unit_of_measurement = "Chargen"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data["status"]["laeuft_bald_ab"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"liste": self.coordinator.data["status"]["laeuft_bald_ab"]}
