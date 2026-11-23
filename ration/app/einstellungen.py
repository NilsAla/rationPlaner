"""Einstellungen der Web-App: Planer-Vorgaben, Präferenzen, Wochenrhythmus, Anzeige.

Die HA-App-Optionen (Grocy, ai_task, To-do, Token) werden nur angezeigt, nie hier geändert.
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import API_VERSION, __version__
from app.db import Einstellung
from app.deps import session, settings
from app.settings import Settings

router = APIRouter(prefix="/api/v1")

AUFWAND_STUFEN = ("schnell", "normal", "aufwaendig")
STANDARD: dict[str, dict[str, Any]] = {
    "planer": {
        "portionen": 2,
        "kochtage": [0, 1, 2, 3, 4, 5, 6],  # Wochentage 0 = Montag
        "ki": True,
        "max_ungetestet": 2,
    },
    "praeferenzen": {
        # alle Regler -1 … 1, 0 = neutral
        "fleisch": 0.0,  # -1 weniger Fleisch/mehr Gemüse … 1 mehr Fleisch
        "preis": 0.0,  # -1 günstiger … 1 darf teurer sein
        "extravagant": 0.0,  # -1 Vorrat abbauen … 1 extravagant
        "neu": 0.0,  # -1 Lieblinge … 1 lieber Neues
        "gesund": 0.0,  # -1 egal/Genuss … 1 gesund (Nutri-Score)
        "protein": 0.0,  # -1 egal … 1 viel Protein
        "rohkost": 0.0,  # -1 lieber gekocht … 1 viel Rohkost
        "ausschluss_tags": [],  # Rezepte mit diesen Tags nie planen
        "aufwand_je_tag": {str(i): "aufwaendig" for i in range(7)},  # höchster Aufwand je Wochentag
    },
    "anzeige": {"bald_tage": 3},
    # Timer-Entitäten stehen in der App-Option timer_entities (Konfiguration in HA)
    "kochen": {"zutaten_pflicht": True},  # Weiter erst, wenn alle Zutaten abgehakt sind
    # Ziel für EINE Mahlzeit (Abendessen), je Portion. Planer nutzt es nur, wenn im_planer an ist.
    "ernaehrung": {"kcal_mahlzeit": 700, "protein_mahlzeit": 30, "im_planer": False},
    # KI-Backend: ha (ai_task) oder openai (Key als App-Option). Je Job überschreibbar:
    # jobs = {job: {"modus": ""|"ha"|"openai", "entitaet": "ai_task.…", "modell": "…"}}
    # preise = {Modell oder AI-Task-Entität: {"input": €/1 Mio. Tokens, "output": €/1 Mio.}}
    "ki": {"modus": "ha", "openai_modell": "gpt-4.1-mini", "jobs": {}, "preise": {}},
}
KI_JOBS = ("bon", "import", "kochanleitung", "kategorisierung", "naehrwerte", "planer")


def _bereich(wert: object, lo: float, hi: float, name: str) -> float:
    try:
        x = float(wert)  # type: ignore[arg-type]
    except (TypeError, ValueError) as err:
        raise HTTPException(422, f"{name} muss eine Zahl sein") from err
    if not lo <= x <= hi:
        raise HTTPException(422, f"{name} muss zwischen {lo} und {hi} liegen")
    return x


def pruefen(gruppe: str, werte: dict) -> dict:
    """Validiert und ergänzt eine Gruppe; unbekannte Schlüssel → 422."""
    if gruppe not in STANDARD:
        raise HTTPException(404, f"Unbekannte Gruppe {gruppe}")
    unbekannt = set(werte) - set(STANDARD[gruppe])
    if unbekannt:
        raise HTTPException(422, f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")
    out = dict(werte)
    if gruppe == "planer":
        if "portionen" in out:
            out["portionen"] = int(_bereich(out["portionen"], 1, 6, "portionen"))
        if "max_ungetestet" in out:
            out["max_ungetestet"] = int(_bereich(out["max_ungetestet"], 0, 7, "max_ungetestet"))
        if "kochtage" in out:
            tage = sorted({int(_bereich(t, 0, 6, "kochtage")) for t in out["kochtage"]})
            out["kochtage"] = tage
        if "ki" in out:
            out["ki"] = bool(out["ki"])
    elif gruppe == "praeferenzen":
        for k in ("fleisch", "preis", "extravagant", "neu", "gesund", "protein", "rohkost"):
            if k in out:
                out[k] = round(_bereich(out[k], -1, 1, k), 2)
        if "ausschluss_tags" in out:
            out["ausschluss_tags"] = sorted(
                {str(t).strip() for t in out["ausschluss_tags"] if str(t).strip()}
            )
        if "aufwand_je_tag" in out:
            a = out["aufwand_je_tag"]
            if not isinstance(a, dict) or any(
                str(k) not in {str(i) for i in range(7)} or v not in AUFWAND_STUFEN
                for k, v in a.items()
            ):
                raise HTTPException(
                    422, f"aufwand_je_tag: Wochentag 0–6 → {'/'.join(AUFWAND_STUFEN)}"
                )
            out["aufwand_je_tag"] = {
                **STANDARD["praeferenzen"]["aufwand_je_tag"],
                **{str(k): v for k, v in a.items()},
            }
    elif gruppe == "anzeige" and "bald_tage" in out:
        out["bald_tage"] = int(_bereich(out["bald_tage"], 1, 14, "bald_tage"))
    elif gruppe == "kochen" and "zutaten_pflicht" in out:
        out["zutaten_pflicht"] = bool(out["zutaten_pflicht"])
    elif gruppe == "ki":
        out.update(_ki_pruefen(out))
    elif gruppe == "ernaehrung":
        if "kcal_mahlzeit" in out:
            out["kcal_mahlzeit"] = int(_bereich(out["kcal_mahlzeit"], 100, 3000, "kcal_mahlzeit"))
        if "protein_mahlzeit" in out:
            out["protein_mahlzeit"] = int(
                _bereich(out["protein_mahlzeit"], 0, 300, "protein_mahlzeit")
            )
        if "im_planer" in out:
            out["im_planer"] = bool(out["im_planer"])
    return out


def _ki_pruefen(out: dict) -> dict:
    neu: dict = {}
    if "modus" in out:
        if out["modus"] not in ("ha", "openai"):
            raise HTTPException(422, "modus: ha oder openai")
        neu["modus"] = out["modus"]
    if "openai_modell" in out:
        neu["openai_modell"] = str(out["openai_modell"]).strip()[:80]
    if "jobs" in out:
        if not isinstance(out["jobs"], dict) or set(out["jobs"]) - set(KI_JOBS):
            raise HTTPException(422, f"jobs: nur {', '.join(KI_JOBS)}")
        jobs = {}
        for job, j in out["jobs"].items():
            j = j if isinstance(j, dict) else {}
            modus = j.get("modus") or ""
            if modus not in ("", "ha", "openai"):
                raise HTTPException(422, f"{job}: modus leer, ha oder openai")
            entitaet = str(j.get("entitaet") or "").strip()
            if entitaet and not entitaet.startswith("ai_task."):
                raise HTTPException(422, f"{job}: Entität muss ai_task.… sein")
            jobs[job] = {"modus": modus, "entitaet": entitaet,
                         "modell": str(j.get("modell") or "").strip()[:80]}  # fmt: skip
        neu["jobs"] = jobs
    if "preise" in out:
        if not isinstance(out["preise"], dict):
            raise HTTPException(422, "preise: {Modell: {input, output}}")
        neu["preise"] = {
            str(k).strip(): {
                "input": _bereich(v.get("input"), 0, 1000, f"{k} input"),
                "output": _bereich(v.get("output"), 0, 1000, f"{k} output"),
            }
            for k, v in out["preise"].items()
            if str(k).strip() and isinstance(v, dict)
        }
    return neu


def lesen(s: Session) -> dict[str, dict[str, Any]]:
    gespeichert = {e.schluessel: e.wert for e in s.scalars(select(Einstellung))}
    return {g: {**copy.deepcopy(std), **(gespeichert.get(g) or {})} for g, std in STANDARD.items()}


def optionen(st: Settings) -> dict:
    """App-Optionen zur Anzeige, Geheimnisse nur als gesetzt/nicht gesetzt."""
    return {
        "version": __version__,
        "api_version": API_VERSION,
        "grocy_url": st.grocy_url,
        "grocy_api_key": bool(st.grocy_api_key),
        "ai_task_entity": st.ai_task_entity,
        "openai_api_key": bool(st.openai_api_key),
        "todo_entity": st.todo_entity,
        "toleranz_default_pct": st.toleranz_default_pct,
        "bon_konfidenz_schwelle": st.bon_konfidenz_schwelle,
        "dubletten_schwelle": st.dubletten_schwelle,
        "notify_service": st.notify_service,
        "timer_entities": list(st.timer_entities),
        "api_token": bool(st.api_token),
    }


@router.get("/einstellungen")
async def einstellungen(s: Session = Depends(session), st: Settings = Depends(settings)) -> dict:
    return {**lesen(s), "optionen": optionen(st), "aufwand_stufen": list(AUFWAND_STUFEN)}


@router.put("/einstellungen/{gruppe}")
async def einstellungen_setzen(gruppe: str, werte: dict, s: Session = Depends(session)) -> dict:
    neu = pruefen(gruppe, werte)
    alt = lesen(s)[gruppe]
    s.merge(Einstellung(schluessel=gruppe, wert={**alt, **neu}))
    s.commit()
    return lesen(s)[gruppe]


@router.delete("/einstellungen/{gruppe}")
async def einstellungen_zuruecksetzen(gruppe: str, s: Session = Depends(session)) -> dict:
    if gruppe not in STANDARD:
        raise HTTPException(404, f"Unbekannte Gruppe {gruppe}")
    if e := s.get(Einstellung, gruppe):
        s.delete(e)
        s.commit()
    return lesen(s)[gruppe]
