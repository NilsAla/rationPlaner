"""Zugriff auf die HA-Core-API über den Supervisor (homeassistant_api: true)."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

SUPERVISOR_CORE_API = "http://supervisor/core/api"


class HomeAssistant(Protocol):
    async def todo_items(self, entity_id: str) -> list[str]:
        """Offene Einträge einer To-do-Liste (summary)."""
        ...

    async def todo_add(self, entity_id: str, item: str) -> None: ...

    async def ai_task(
        self,
        entity_id: str,
        task_name: str,
        instructions: str,
        structure: dict,
        attachments: list[dict] | None = None,
    ) -> dict:
        """ai_task.generate_data mit Structured Output, liefert das data-Objekt.

        attachments: [{"media_content_id": "media-source://…", "media_content_type": "image/jpeg"}]
        """
        ...

    async def notify(self, title: str, message: str, service: str = "") -> None:
        """Hinweis an den Nutzer: persistent_notification, zusätzlich notify.<service>."""
        ...


class SupervisorHomeAssistant:
    def __init__(self, token: str, base_url: str = SUPERVISOR_CORE_API):
        self._client = httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=120.0
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _service(
        self, domain: str, service: str, data: dict[str, Any], *, response: bool = False
    ) -> Any:
        path = f"/services/{domain}/{service}" + ("?return_response" if response else "")
        resp = await self._client.post(path, json=data)
        resp.raise_for_status()
        return resp.json()

    async def todo_items(self, entity_id: str) -> list[str]:
        result = await self._service(
            "todo", "get_items", {"entity_id": entity_id, "status": "needs_action"}, response=True
        )
        items = result.get("service_response", {}).get(entity_id, {}).get("items", [])
        return [item["summary"] for item in items]

    async def todo_add(self, entity_id: str, item: str) -> None:
        await self._service("todo", "add_item", {"entity_id": entity_id, "item": item})

    async def ai_task(
        self,
        entity_id: str,
        task_name: str,
        instructions: str,
        structure: dict,
        attachments: list[dict] | None = None,
    ) -> dict:
        data: dict[str, Any] = {
            "entity_id": entity_id,
            "task_name": task_name,
            "instructions": instructions,
            "structure": structure,
        }
        if attachments:
            data["attachments"] = attachments
        result = await self._service("ai_task", "generate_data", data, response=True)
        return result.get("service_response", {}).get("data") or {}

    async def notify(self, title: str, message: str, service: str = "") -> None:
        await self._service(
            "persistent_notification", "create", {"title": title, "message": message}
        )
        if service:
            domain, _, name = service.partition(".")
            await self._service(domain, name, {"title": title, "message": message})
