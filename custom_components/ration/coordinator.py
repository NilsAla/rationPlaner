"""Pollt Heute und Status der App. Nach jedem Service-Call wird sofort aktualisiert."""

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
            return {"heute": await self.api.heute(), "status": await self.api.status()}
        except RationAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RationApiError as err:
            raise UpdateFailed(str(err)) from err
