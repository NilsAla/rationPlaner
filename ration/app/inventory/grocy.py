"""GrocyBackend – einzige Stelle mit Grocy-Endpunkten. Implementierung folgt in Phase 0."""

from __future__ import annotations

import httpx


class GrocyBackend:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api",
            headers={"GROCY-API-KEY": api_key},
            timeout=10.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
