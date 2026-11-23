"""Config Flow: URL und Token der App eintragen, Versions-Handshake prüfen. Reauth bei 401."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import RationApi, RationApiError, RationAuthError
from .const import CONF_TOKEN, CONF_URL, DEFAULT_URL, DOMAIN

PASSWORT = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class RationConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _pruefen(self, url: str, token: str) -> str | None:
        try:
            await RationApi(async_get_clientsession(self.hass), url, token).check_version()
        except RationAuthError:
            return "invalid_auth"
        except RationApiError as err:
            return "incompatible_version" if "API-Version" in str(err) else "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            token = user_input.get(CONF_TOKEN, "").strip()
            if not (fehler := await self._pruefen(url, token)):
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="rationPlaner", data={CONF_URL: url, CONF_TOKEN: token}
                )
            errors["base"] = fehler

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=DEFAULT_URL): str,
                    vol.Optional(CONF_TOKEN, default=""): PASSWORT,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            token = user_input.get(CONF_TOKEN, "").strip()
            if not (fehler := await self._pruefen(entry.data[CONF_URL], token)):
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
            errors["base"] = fehler
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): PASSWORT}),
            errors=errors,
        )
