"""FastAPI-Abhängigkeiten, gemeinsam für alle Router."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.ha import HomeAssistant
from app.inventory.base import InventoryBackend
from app.settings import Settings


def backend(request: Request) -> InventoryBackend:
    b = request.app.state.backend
    if b is None:
        raise HTTPException(503, "Grocy ist nicht konfiguriert (grocy_url, grocy_api_key)")
    return b


def session(request: Request):
    with Session(request.app.state.engine) as s:
        yield s


def settings(request: Request) -> Settings:
    return request.app.state.settings


def home_assistant(request: Request) -> HomeAssistant:
    ha = request.app.state.ha
    if ha is None:
        raise HTTPException(503, "Kein Zugriff auf Home Assistant (nur innerhalb von HAOS)")
    return ha
