"""Schlanker Client für die REST-API der App (/api/v1)."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import SUPPORTED_API_MAJOR


class RationApiError(Exception):
    pass


class RationApi:
    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self._base = url.rstrip("/") + "/api/v1"

    async def request(
        self, method: str, path: str, json: dict | None = None, params: dict | None = None
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                json=json,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    detail = body.get("detail") if isinstance(body, dict) else body
                    raise RationApiError(f"{resp.status}: {detail}")
                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise RationApiError(f"App nicht erreichbar: {err}") from err

    async def check_version(self) -> str:
        info = await self.request("GET", "/version")
        version = str(info["api_version"])
        if int(version.split(".")[0]) != SUPPORTED_API_MAJOR:
            raise RationApiError(f"API-Version {version} passt nicht zur Bridge")
        return version

    async def heute(self) -> dict:
        return await self.request("GET", "/heute")

    async def status(self) -> dict:
        return await self.request("GET", "/status")
