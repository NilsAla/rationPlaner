"""Config Flow: URL der App eintragen, Versions-Handshake prüfen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RationApi, RationApiError
from .const import CONF_URL, DEFAULT_URL, DOMAIN


class RationConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            try:
                await RationApi(async_get_clientsession(self.hass), url).check_version()
            except RationApiError as err:
                errors["base"] = (
                    "incompatible_version" if "API-Version" in str(err) else ("cannot_connect")
                )
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="rationPlaner", data={CONF_URL: url})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str}),
            errors=errors,
        )
