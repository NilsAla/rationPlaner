"""Config Flow: URL der App eintragen, Versions-Handshake prüfen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_URL, DEFAULT_URL, DOMAIN, SUPPORTED_API_MAJOR


class RationConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            try:
                session = async_get_clientsession(self.hass)
                async with session.get(f"{url}/api/v1/version", timeout=10) as resp:
                    info = await resp.json()
                major = int(str(info["api_version"]).split(".")[0])
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                if major != SUPPORTED_API_MAJOR:
                    errors["base"] = "incompatible_version"
                else:
                    await self.async_set_unique_id(DOMAIN)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title="rationPlaner", data={CONF_URL: url})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str}),
            errors=errors,
        )
