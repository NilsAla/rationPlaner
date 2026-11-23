"""Sensoren: Rezept heute, Gericht heute/morgen, Statuszeile, läuft bald ab."""

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
            GerichtSensor(coordinator, entry, "heute"),
            GerichtSensor(coordinator, entry, "morgen"),
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


class GerichtSensor(RationSensor):
    """Gericht aus dem Plan mit Nährwerten, Foto und fehlenden Zutaten (aus /uebersicht)."""

    _attr_icon = "mdi:food"

    def __init__(self, coordinator: RationCoordinator, entry: ConfigEntry, tag: str) -> None:
        self.key = f"gericht_{tag}"
        self.tag = tag
        self._attr_name = f"Gericht {tag}"
        super().__init__(coordinator, entry)

    @property
    def _gericht(self) -> dict | None:
        return (self.coordinator.data.get("uebersicht") or {}).get(self.tag)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("uebersicht") is not None

    @property
    def native_value(self) -> str:
        g = self._gericht
        return g["name"] if g else "nichts geplant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        g = self._gericht or {}
        return {
            "datum": g.get("datum"),
            "rezept_id": g.get("rezept_id"),
            "portionen": g.get("portionen"),
            "status": g.get("status"),
            "kcal": g.get("kcal"),
            "protein": g.get("protein"),
            "nutri": g.get("nutri"),
            "kochbar": g.get("kochbar"),
            # Foto im Dashboard: entity_picture der Bild-Entität
            "foto": f"image.{DOMAIN}_{self.key}" if g.get("foto") else None,
            "zutaten": [
                {k: z[k] for k in ("name", "menge", "einheit", "ampel")}
                for z in g.get("zutaten", [])
            ],
            "fehlend": g.get("fehlend", []),
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
    def _liste(self) -> list[dict]:
        # Übersicht nutzt die App-Einstellung bald_tage, ältere Apps fest 3 Tage
        if u := self.coordinator.data.get("uebersicht"):
            return u["laeuft_ab"]
        return self.coordinator.data["status"]["laeuft_bald_ab"]

    @property
    def native_value(self) -> int:
        return len(self._liste)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        u = self.coordinator.data.get("uebersicht")
        return {
            "tage": u["bald_tage"] if u else 3,
            "liste": [
                {
                    "name": x["name"],
                    "menge": x["menge"],
                    "einheit": x["einheit"],
                    "mhd": x["ablauf"],
                }
                for x in self._liste
            ],
        }
