"""KI-Aufrufe über zwei Backends: HA ai_task (Standard) oder OpenAI direkt (API-Key als Option).

Zuordnung je Job (bon, import, kochanleitung, kategorisierung, naehrwerte, planer) liegt in der
App-DB (Einstellung „ki“). Jeder Aufruf landet im KI-Protokoll mit Tokens und Kosten. Tokens sind
im HA-Modus geschätzt (≈), im OpenAI-Modus echt (usage). Der API-Key wird nie geloggt.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from app import einstellungen
from app.protokoll import KiAnfrage, kuerzen_ki
from app.settings import Settings

log = logging.getLogger("ration.ki")
router = APIRouter(prefix="/api/v1")

JOBS = {
    "bon": "Kassenbon",
    "import": "Rezept-Import",
    "kochanleitung": "Kochanleitung",
    "kategorisierung": "Kategorisierung",
    "naehrwerte": "Nährwerte",
    "planer": "Wochenplan",
    "packungen": "Packungsgrößen",
}
BACKENDS = ("ha", "openai")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ZEICHEN_JE_TOKEN = 3.5  # grobe Schätzung für Deutsch + JSON
TOKENS_JE_BILD = 1100  # Pauschale je Bild im HA-Modus (Detail „high“, ca. 1 MP)
MEDIA_LOCAL = "media-source://media_source/local/"


class KiFehler(Exception):
    pass


def schaetzen(text: str) -> int:
    return max(1, round(len(text) / ZEICHEN_JE_TOKEN))


def kosten(preise: dict, schluessel: str, tokens_in: int, tokens_out: int) -> float | None:
    """€ für einen Aufruf; None, wenn für Modell/Entität kein Preis gepflegt ist."""
    p = preise.get(schluessel)
    if not p:
        return None
    return round(tokens_in * p["input"] / 1e6 + tokens_out * p["output"] / 1e6, 8)


# --- HA-Struktur → JSON Schema (OpenAI Structured Output, strict) -------------------------


def _feld_schema(feld: dict) -> dict:
    sel = feld.get("selector") or {"text": {}}
    art, opt = next(iter(sel.items()))
    opt = opt or {}
    if art == "object":
        if "fields" in opt:
            s: dict = schema_aus_struktur(opt["fields"])
        else:  # freies Objekt: strict erlaubt keine offenen Objekte, also JSON als Text
            s = {"type": "string", "description": "JSON"}
    elif art == "select":
        s = {"type": "string", "enum": [str(o) for o in opt.get("options", [])]}
    elif art == "number":
        s = {"type": "number"}
    elif art == "boolean":
        s = {"type": "boolean"}
    else:
        s = {"type": "string"}
    if opt.get("multiple"):
        s = {"type": "array", "items": s}
    if feld.get("description"):
        s["description"] = feld["description"]
    if not feld.get("required"):
        s = {"anyOf": [s, {"type": "null"}]}  # strict: alle Felder Pflicht, optional = null
    return s


def schema_aus_struktur(struktur: dict) -> dict:
    return {
        "type": "object",
        "properties": {k: _feld_schema(v) for k, v in struktur.items()},
        "required": list(struktur),
        "additionalProperties": False,
    }


def _ohne_null(wert: object) -> object:
    """null-Felder weglassen, damit die Antwort aussieht wie bei ai_task."""
    if isinstance(wert, dict):
        return {k: _ohne_null(v) for k, v in wert.items() if v is not None}
    if isinstance(wert, list):
        return [_ohne_null(v) for v in wert]
    return wert


# --- Client -----------------------------------------------------------------------------


class KiClient:
    def __init__(self, ha, engine, st: Settings, http: httpx.AsyncClient | None = None):
        self._ha = ha
        self._engine = engine
        self._st = st
        self._http = http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    def _einstellung(self) -> dict:
        with Session(self._engine) as s:
            return einstellungen.lesen(s)["ki"]

    def konfig(self, job: str) -> dict:
        """Backend, Entität bzw. Modell für einen Job (Job-Einstellung vor global)."""
        e = self._einstellung()
        j = e["jobs"].get(job) or {}
        backend = j.get("modus") or e["modus"]
        if backend == "openai":
            return {"backend": "openai", "modell": j.get("modell") or e["openai_modell"]}
        return {"backend": "ha", "entitaet": j.get("entitaet") or self._st.ai_task_entity}

    def fehlt(self, job: str) -> str | None:
        k = self.konfig(job)
        if k["backend"] == "openai":
            if not self._st.openai_api_key:
                return "OpenAI-Modus, aber App-Option openai_api_key fehlt"
            if not k["modell"]:
                return "OpenAI-Modus, aber kein Modell gesetzt"
            return None
        if self._ha is None:
            return "Kein Zugriff auf Home Assistant"
        if not k["entitaet"]:
            return "Keine AI-Task-Entität (Option ai_task_entity oder Einstellung KI)"
        return None

    def verfuegbar(self, job: str) -> bool:
        return self.fehlt(job) is None

    def pruefen(self, job: str) -> None:
        """Für Endpunkte: 503 mit Grund, wenn der Job keine KI hat."""
        if grund := self.fehlt(job):
            raise HTTPException(503, grund)

    async def generate(
        self,
        job: str,
        name: str,
        anweisung: str,
        struktur: dict,
        anhaenge: list[dict] | None = None,
    ) -> dict:
        if job not in JOBS:
            raise ValueError(f"Unbekannter KI-Job {job}")
        if grund := self.fehlt(job):
            raise KiFehler(grund)
        k = self.konfig(job)
        start = time.monotonic()
        antwort: dict | None = None
        fehler = None
        tokens_in = tokens_out = 0
        geschaetzt = True
        try:
            if k["backend"] == "openai":
                antwort, tokens_in, tokens_out = await self._openai(
                    k["modell"], name, anweisung, struktur, anhaenge or []
                )
                geschaetzt = False
            else:
                antwort = await self._ha.ai_task(k["entitaet"], name, anweisung, struktur, anhaenge)
                tokens_in = schaetzen(anweisung + json.dumps(struktur, ensure_ascii=False))
                tokens_in += TOKENS_JE_BILD * len(anhaenge or [])
            return antwort
        except Exception as err:
            fehler = f"{type(err).__name__}: {err}"
            raise
        finally:
            if geschaetzt and antwort is not None:
                tokens_out = schaetzen(json.dumps(antwort, ensure_ascii=False))
            self._protokoll(
                job, name, k, anweisung, struktur, anhaenge, antwort, fehler, start,
                tokens_in, tokens_out, geschaetzt,
            )  # fmt: skip

    def _protokoll(
        self, job, name, k, anweisung, struktur, anhaenge, antwort, fehler, start,
        tokens_in, tokens_out, geschaetzt,
    ) -> None:  # fmt: skip
        try:
            schluessel = k.get("modell") or k.get("entitaet") or ""
            preise = self._einstellung()["preise"]
            with Session(self._engine) as s:
                s.add(
                    KiAnfrage(
                        aufgabe=name,
                        job=job,
                        backend=k["backend"],
                        entitaet=schluessel,
                        anweisung=anweisung,
                        struktur=struktur,
                        anhaenge=anhaenge,
                        antwort=antwort if isinstance(antwort, dict) else None,
                        fehler=fehler,
                        dauer_s=round(time.monotonic() - start, 2),
                        tokens_in=tokens_in or None,
                        tokens_out=tokens_out or None,
                        geschaetzt=geschaetzt,
                        kosten_eur=kosten(preise, schluessel, tokens_in, tokens_out),
                    )
                )
                kuerzen_ki(s)
                s.commit()
        except Exception:  # noqa: BLE001 - Protokoll darf die Anfrage nie stören
            log.exception("KI-Log fehlgeschlagen")

    def _bild(self, anhang: dict) -> str:
        """media-source://media_source/local/… → data-URL aus dem gemappten /media."""
        mid = anhang.get("media_content_id", "")
        if not mid.startswith(MEDIA_LOCAL):
            raise KiFehler(f"Anhang nicht lesbar: {mid}")
        basis = self._st.media_dir.resolve()
        pfad = (basis / mid.removeprefix(MEDIA_LOCAL)).resolve()
        if not pfad.is_relative_to(basis) or not pfad.is_file():
            raise KiFehler(f"Anhang nicht gefunden: {mid}")
        typ = anhang.get("media_content_type") or mimetypes.guess_type(pfad.name)[0]
        return f"data:{typ or 'image/jpeg'};base64,{base64.b64encode(pfad.read_bytes()).decode()}"

    async def _openai(
        self, modell: str, name: str, anweisung: str, struktur: dict, anhaenge: list[dict]
    ) -> tuple[dict, int, int]:
        inhalt: list[dict] = [{"type": "text", "text": anweisung}]
        for a in anhaenge:
            inhalt.append({"type": "image_url", "image_url": {"url": self._bild(a)}})
        body = {
            "model": modell,
            "messages": [{"role": "user", "content": inhalt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64] or "antwort",
                    "strict": True,
                    "schema": schema_aus_struktur(struktur),
                },
            },
        }
        self._http = self._http or httpx.AsyncClient(timeout=180.0)
        resp = await self._http.post(
            OPENAI_URL,
            json=body,
            headers={"Authorization": f"Bearer {self._st.openai_api_key}"},
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except ValueError:
                detail = resp.text
            raise KiFehler(f"OpenAI {resp.status_code}: {str(detail)[:300]}")
        daten = resp.json()
        nachricht = daten["choices"][0]["message"]
        if nachricht.get("refusal"):
            raise KiFehler(f"OpenAI lehnt ab: {nachricht['refusal']}")
        usage = daten.get("usage") or {}
        try:
            antwort = json.loads(nachricht.get("content") or "{}")
        except ValueError as err:
            raise KiFehler("OpenAI-Antwort ist kein JSON") from err
        return (
            _ohne_null(antwort),  # type: ignore[return-value]
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )


def ki_client(request: Request) -> KiClient:
    return request.app.state.ki


@router.get("/ki/status")
async def ki_status(request: Request) -> dict:
    """Je Job: Backend, Entität/Modell und ob nutzbar; dazu die AI-Task-Entitäten aus HA."""
    ki: KiClient = request.app.state.ki
    entitaeten: list[str] = []
    if request.app.state.ha is not None:
        try:
            entitaeten = [x["entity_id"] for x in await request.app.state.ha.states("ai_task")]
        except Exception as err:  # noqa: BLE001 - Liste ist nur eine Hilfe
            log.warning("AI-Task-Entitäten nicht lesbar: %s", err)
    return {
        "jobs": [
            {"job": j, "name": n, **ki.konfig(j), "fehlt": ki.fehlt(j)} for j, n in JOBS.items()
        ],
        "entitaeten": entitaeten,
        "openai_key": bool(ki._st.openai_api_key),
    }
