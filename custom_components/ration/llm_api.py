"""LLM-API für Assist und die MCP-Server-Integration von HA: jede Aktion wird ein Tool."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from .aktionen import AKTIONEN, Aktion
from .api import RationApiError
from .const import DOMAIN

PROMPT = (
    "Tools für Küchenbestand, Rezepte und Wochenplan (rationPlaner). "
    "IDs immer aus produkte, rezepte oder lagerorte nehmen, nie raten. "
    "Mengen stehen in der Bestandseinheit des Produkts. Datumsangaben als JJJJ-MM-TT."
)


class RationTool(llm.Tool):
    def __init__(self, aktion: Aktion) -> None:
        self.aktion = aktion
        self.name = f"{DOMAIN}_{aktion.name}"
        self.description = aktion.beschreibung
        self.parameters = aktion.schema

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("rationPlaner ist nicht eingerichtet")
        coordinator = entries[0].runtime_data
        try:
            daten = self.parameters(tool_input.tool_args)
            result = await self.aktion.ausfuehren(coordinator.api, daten)
        except (vol.Invalid, RationApiError) as err:
            return {"error": str(err)}
        if self.aktion.schreibend:
            await coordinator.async_request_refresh()
        return result


@dataclass(slots=True, kw_only=True)
class RationLlmApi(llm.API):
    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(
            api=self,
            api_prompt=PROMPT,
            llm_context=llm_context,
            tools=[RationTool(a) for a in AKTIONEN],
        )
