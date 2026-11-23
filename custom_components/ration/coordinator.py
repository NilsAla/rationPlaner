"""Pollt Heute, Status und Übersicht der App. Nach jedem Service-Call wird sofort aktualisiert."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RationApi, RationApiError, RationAuthError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class RationCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, api: RationApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = api

    async def _async_update_data(self) -> dict:
        try:
            daten = {"heute": await self.api.heute(), "status": await self.api.status()}
            try:
                daten["uebersicht"] = await self.api.uebersicht()
            except RationAuthError:
                raise
            except RationApiError as err:
                # Ältere App (API < 1.15) kennt /uebersicht nicht: neue Entitäten nicht verfügbar
                _LOGGER.debug("Übersicht nicht abrufbar: %s", err)
                daten["uebersicht"] = None
            return daten
        except RationAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RationApiError as err:
            raise UpdateFailed(str(err)) from err
