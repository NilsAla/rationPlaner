"""FastAPI-Einstieg. Ingress entfernt das Pfad-Präfix, die App läuft daher unter /."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from app import API_VERSION, __version__

api = APIRouter(prefix="/api/v1")


@api.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@api.get("/version")
async def version() -> dict:
    """Versions-Handshake für Bridge und Card (siehe Konzept, API-Vertrag)."""
    return {"app_version": __version__, "api_version": API_VERSION}


def create_app() -> FastAPI:
    app = FastAPI(title="rationPlaner", version=__version__)
    app.include_router(api)
    return app


app = create_app()
