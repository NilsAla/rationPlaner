"""Alle Aktionen der App an einer Stelle: als HA-Service und als LLM-Tool (Assist, MCP Server).

Jede Aktion bildet genau einen Endpunkt der App ab. Platzhalter im Pfad ({datum}) werden aus den
Daten genommen, der Rest geht bei GET als Query, sonst als JSON-Body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .api import RationApi

DATUM = vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}$", msg="Datum als JJJJ-MM-TT"))
PORTIONEN = vol.All(vol.Coerce(int), vol.Range(min=1, max=12))


@dataclass(frozen=True)
class Aktion:
    name: str
    beschreibung: str
    methode: str
    pfad: str
    schema: vol.Schema = field(default_factory=lambda: vol.Schema({}))
    schreibend: bool = False

    async def ausfuehren(self, api: RationApi, daten: dict[str, Any]) -> dict[str, Any]:
        daten = dict(daten)
        pfad = self.pfad.format(**{k: daten.pop(k) for k in list(daten) if f"{{{k}}}" in self.pfad})
        if self.methode == "GET":
            result = await api.request("GET", pfad, params=daten or None)
        else:
            result = await api.request(self.methode, pfad, json=daten or None)
        # Service-Antworten müssen Objekte sein.
        if isinstance(result, dict):
            return result
        return {"items": result} if isinstance(result, list) else {"ok": True}


AKTIONEN: tuple[Aktion, ...] = (
    Aktion(
        "heute",
        "Rezept aus dem Plan für heute mit skalierten Zutaten und Schritten.",
        "GET",
        "/heute",
    ),
    Aktion(
        "status",
        "Statuszeile: Anzahl Artikel und welche Chargen in den nächsten Tagen ablaufen.",
        "GET",
        "/status",
        vol.Schema(
            {vol.Optional("tage", default=3): vol.All(vol.Coerce(int), vol.Range(min=1, max=30))}
        ),
    ),
    Aktion(
        "bestand",
        "Alle Chargen im Bestand mit Menge, Einheit, Lagerort und MHD.",
        "GET",
        "/bestand",
    ),
    Aktion("produkte", "Alle Produkte mit ID, Einheit und Tracking-Modus.", "GET", "/produkte"),
    Aktion("lagerorte", "Alle Lagerorte mit ID und ob es ein Gefrierort ist.", "GET", "/lagerorte"),
    Aktion("rezepte", "Alle Rezepte mit ID und Basis-Portionen.", "GET", "/rezepte"),
    Aktion(
        "plan",
        "Wochenplan ab heute: Datum, Rezept-ID, Portionen, Status.",
        "GET",
        "/plan",
        vol.Schema(
            {vol.Optional("tage", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=14))}
        ),
    ),
    Aktion(
        "plan_setzen",
        "Plant ein Rezept (ID aus rezepte) für ein Datum. Überschreibt den Eintrag des Tages.",
        "PUT",
        "/plan/{datum}",
        vol.Schema(
            {
                vol.Required("datum"): DATUM,
                vol.Required("rezept_id"): cv.string,
                vol.Optional("portionen", default=1): PORTIONEN,
            }
        ),
        schreibend=True,
    ),
    Aktion(
        "plan_loeschen",
        "Entfernt den Planeintrag eines Datums.",
        "DELETE",
        "/plan/{datum}",
        vol.Schema({vol.Required("datum"): DATUM}),
        schreibend=True,
    ),
    Aktion(
        "einbuchen",
        "Bucht eine Menge eines Produkts (ID aus produkte, Menge in dessen Einheit) ein. "
        "Ohne MHD gilt die Standard-Haltbarkeit, ohne Lagerort der Standard des Produkts.",
        "POST",
        "/bestand",
        vol.Schema(
            {
                vol.Required("produkt_id"): cv.string,
                vol.Required("menge"): vol.All(
                    vol.Coerce(float), vol.Range(min=0, min_included=False)
                ),
                vol.Optional("mhd"): DATUM,
                vol.Optional("lagerort_id"): cv.string,
                vol.Optional("preis"): vol.All(vol.Coerce(float), vol.Range(min=0)),
            }
        ),
        schreibend=True,
    ),
    Aktion(
        "gekocht",
        "Bucht ein gekochtes Rezept ab (FEFO). Ohne rezept_id das für heute geplante.",
        "POST",
        "/gekocht",
        vol.Schema(
            {
                vol.Optional("rezept_id"): cv.string,
                vol.Required("portionen_gekocht"): PORTIONEN,
                vol.Required("portionen_gegessen"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=12)
                ),
            }
        ),
        schreibend=True,
    ),
    Aktion(
        "einkaufsliste_erzeugen",
        "Schreibt Fehlmengen der geplanten Gerichte in die Einkaufsliste (To-do).",
        "POST",
        "/einkaufsliste",
        vol.Schema(
            {vol.Optional("tage", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=14))}
        ),
        schreibend=True,
    ),
)
