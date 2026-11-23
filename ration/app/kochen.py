"""Kochmodus: Schritt-für-Schritt-Anleitungen, Zutaten je Schritt, HA-Timer, Kochsitzungen.

Die Anleitung liegt in der App-DB (`koch_anleitung`), das Rezept in Grocy bleibt unverändert.
Das LLM ordnet die Originalschritte neu (Wasser zuerst aufsetzen, parallel schneiden …), teilt
sie in einzelne Handgriffe und verweist auf Zutaten nur per Index. Mengen rechnet immer der Code
aus Rezept × Portionen. Ohne KI oder bei Fehlern erzeugt der Code eine Anleitung aus den
Originalschritten (Zutaten per Namenstreffer, Timer per Regex).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import fotos, kueche
from app.db import Base
from app.deps import backend, session, settings
from app.inventory.base import InventoryBackend, Product, Recipe
from app.settings import Settings

log = logging.getLogger("ration.kochen")
router = APIRouter(prefix="/api/v1")

MAX_SCHRITTE = 30
MAX_TIMER_S = 6 * 3600
MAX_ORIGINAL = 100
KUECHEN_MUSTER = re.compile(r"k(ü|ue)che|kitchen|koch", re.I)


# --- Datenmodell ---------------------------------------------------------------------------


class KochAnleitung(Base):
    """Strukturierte Anleitung je Rezept. schritte: siehe `schritt_pruefen`."""

    __tablename__ = "koch_anleitung"

    rezept_id: Mapped[str] = mapped_column(String, primary_key=True)
    schritte: Mapped[list] = mapped_column(JSON)
    quelle: Mapped[str] = mapped_column(String)  # ki / code / manuell
    status: Mapped[str] = mapped_column(String, default="entwurf")  # entwurf / geprueft
    basis: Mapped[str] = mapped_column(String)  # Hash von Schritten + Zutaten bei Erstellung
    hinweise: Mapped[list | None] = mapped_column(JSON, nullable=True)
    geaendert: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AnleitungLauf(Base):
    __tablename__ = "koch_anleitung_lauf"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String, default="laeuft")  # laeuft / fertig / fehler
    rezept_ids: Mapped[list] = mapped_column(JSON)
    fertig: Mapped[int] = mapped_column(Integer, default=0)
    fehlgeschlagen: Mapped[list] = mapped_column(JSON, default=list)
    fehler: Mapped[str | None] = mapped_column(String, nullable=True)
    angelegt: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KochSitzung(Base):
    """Laufender Kochvorgang, geräteübergreifend (Tablet und Handy sehen denselben Stand)."""

    __tablename__ = "koch_sitzung"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rezept_id: Mapped[str] = mapped_column(String, index=True)
    portionen: Mapped[int] = mapped_column(Integer)
    schritt: Mapped[int] = mapped_column(Integer, default=0)
    erledigt: Mapped[list] = mapped_column(JSON, default=list)  # Schritt-Indizes
    abgehakt: Mapped[list] = mapped_column(JSON, default=list)  # "schritt:zutat"
    timer: Mapped[list] = mapped_column(JSON, default=list)  # [{entity_id, name, ende, schritt}]
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    gestartet: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- Schritte: Prüfen, Fallback, Darstellung -------------------------------------------------


def basis_hash(r: Recipe) -> str:
    roh = json.dumps(
        [r.description, [(z.product_id, z.amount) for z in r.ingredients]], ensure_ascii=False
    )
    return hashlib.sha1(roh.encode()).hexdigest()[:12]


DAUER = re.compile(
    r"(\d+(?:[,.]\d+)?)\s*(?:[-–]|bis)?\s*(\d+(?:[,.]\d+)?)?\s*"
    r"(Std\.?|Stunden?|h\b|Min\.?|Minuten?|min\b|Sek\.?|Sekunden?|s\b)",
    re.I,
)


def dauern_s(text: str) -> list[int]:
    """Alle Zeitangaben im Text in Sekunden; bei Spannen ('8–10 Min') der größere Wert."""
    out = []
    for m in DAUER.finditer(text):
        wert = float((m.group(2) or m.group(1)).replace(",", "."))
        einheit = m.group(3).lower()
        faktor = (
            3600
            if einheit.startswith(("std", "stu", "h"))
            else 1
            if einheit.startswith("s")
            else 60
        )
        out.append(round(wert * faktor))
    return out


def nennt(text: str, name: str) -> bool:
    """Erwähnt der Schritt das Produkt? Ganzer Name, Wörter ab 4 Buchstaben oder ein
    großgeschriebenes Wort, auf das der Name endet (Öl → Olivenöl, Zwiebeln → Rote Zwiebeln)."""
    t, n = text.lower(), name.lower()
    teile = [w for w in re.split(r"[\s,/()-]+", n) if len(w) >= 4]
    if n in t or any(w in t for w in teile):
        return True
    nomen = re.findall(r"\b[A-ZÄÖÜ][\wäöüß]+", text)
    return any(len(w) >= 2 and n.endswith(w.lower()) for w in nomen)


def schritt_pruefen(roh: dict, n_zutaten: int) -> dict | None:
    """Normalisiert einen Schritt; None, wenn er unbrauchbar ist."""
    if not isinstance(roh, dict):
        return None
    text = str(roh.get("text") or "").strip()[:800]
    titel = str(roh.get("titel") or "").strip()[:80]
    if not text and not titel:
        return None
    zutaten = []
    teilweise = {str(x).strip() for x in (roh.get("teilweise") or [])}
    for z in roh.get("zutaten") or []:
        if isinstance(z, dict):
            idx, anteil = z.get("zutat"), z.get("anteil", 1)
        else:
            idx, anteil = z, 1
        try:
            idx, anteil = int(str(idx).strip()), float(anteil)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n_zutaten and 0 < anteil <= 1 and idx not in [x["zutat"] for x in zutaten]:
            eintrag = {"zutat": idx, "anteil": round(anteil, 3)}
            if str(idx) in teilweise:
                eintrag["teil"] = True  # nur für anteile_ausgleichen, wird dort entfernt
            zutaten.append(eintrag)
    timer_s = roh.get("timer_s")
    if timer_s is None and roh.get("timer_minuten") not in (None, ""):
        try:
            timer_s = float(roh["timer_minuten"]) * 60
        except (TypeError, ValueError):
            timer_s = None
    try:
        timer_s = int(round(float(timer_s))) if timer_s not in (None, "") else None
    except (TypeError, ValueError):
        timer_s = None
    if timer_s is not None and not 10 <= timer_s <= MAX_TIMER_S:
        timer_s = None
    original = []
    for o in roh.get("original") or []:
        try:
            o = int(str(o).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= o < MAX_ORIGINAL and o not in original:
            original.append(o)
    try:
        foto = int(roh["foto"]) if roh.get("foto") not in (None, "") else None
    except (TypeError, ValueError):
        foto = None
    return {
        "titel": titel or text[:60],
        "text": text,
        "zutaten": zutaten,
        "original": original,  # Originalschritte (0-basiert), für Schrittbilder
        "foto": foto,  # ausdrücklich zugeordnetes Foto, sonst die der Originalschritte
        "timer_s": timer_s,
        "timer_name": (str(roh.get("timer_name") or "").strip()[:40] or None) if timer_s else None,
        "parallel": bool(roh.get("parallel")),
        "timer_geschaetzt": bool(roh.get("timer_geschaetzt")),
    }


def anteile_ausgleichen(schritte: list[dict]) -> None:
    """Teilmengen aus der KI-Markierung `teilweise` gleichmäßig aufteilen.

    Mehrfachnennung ohne Markierung heißt: dieselbe Zutat in einem späteren Arbeitsgang
    (schneiden, dann anbraten), also volle Menge. Geteilt wird nur, was die KI als Teilmenge
    markiert (z. B. Butter halb in die Pfanne, halb in die Soße)."""
    teile: dict[int, list[dict]] = {}
    for s in schritte:
        for z in s["zutaten"]:
            if z.pop("teil", False):
                teile.setdefault(z["zutat"], []).append(z)
    for liste in teile.values():
        for z in liste:
            z["anteil"] = round(1 / len(liste), 3) if len(liste) > 1 else 1.0


def fehlende_zutaten(schritte: list[dict], n_zutaten: int) -> list[int]:
    drin = {z["zutat"] for s in schritte for z in s["zutaten"]}
    return [i for i in range(n_zutaten) if i not in drin]


def zutaten_zuordnen(schritte: list[dict], fehlend: list[int], namen: list[str]) -> None:
    """Fehlende Zutaten in den ersten Schritt, der sie nennt, sonst in den ersten Schritt."""
    for i in fehlend:
        ziel = next(
            (s for s in schritte if nennt(s["titel"] + " " + s["text"], namen[i])),
            schritte[0],
        )
        ziel["zutaten"].append({"zutat": i, "anteil": 1.0})


def aus_originalschritten(r: Recipe, produkte: dict[str, Product]) -> list[dict]:
    """Code-Fallback: je Originalschritt ein Schritt, Zutaten per Namenstreffer, Timer per Regex."""
    namen = [
        produkte[z.product_id].name if z.product_id in produkte else "?" for z in r.ingredients
    ]
    original = kueche.schritte(r.description) or ["Alle Zutaten nach Rezept verarbeiten."]
    schritte = []
    for nr, text in enumerate(original[:MAX_SCHRITTE]):
        d = dauern_s(text)
        titel = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0][:60]
        schritte.append(
            {
                "titel": titel,
                "text": text,
                "zutaten": [],
                "original": [nr],
                "timer_s": max(d) if d else None,
                "timer_name": titel[:40] if d else None,
                "parallel": False,
                "timer_geschaetzt": False,
            }
        )
    vergeben: set[int] = set()
    for s in schritte:
        t = s["titel"] + " " + s["text"]
        for i, n in enumerate(namen):
            if i not in vergeben and nennt(t, n):
                s["zutaten"].append({"zutat": i, "anteil": 1.0})
                vergeben.add(i)
    rest = [i for i in range(len(namen)) if i not in vergeben]
    if rest:
        schritte.insert(
            0,
            {
                "titel": "Zutaten bereitstellen",
                "text": "Diese Zutaten bereitlegen.",
                "zutaten": [{"zutat": i, "anteil": 1.0} for i in rest],
                "timer_s": None,
                "timer_name": None,
                "parallel": False,
                "timer_geschaetzt": False,
            },
        )
    return [s for s in (schritt_pruefen(x, len(namen)) for x in schritte) if s]


def darstellen(
    a: KochAnleitung | None,
    schritte: list[dict],
    r: Recipe,
    produkte: dict[str, Product],
    portionen: int,
    quelle: str,
    fotos_je: dict[int, list[int]] | None = None,
) -> dict:
    """Schritte mit gerechneten Mengen (Rezept × Portionen × Anteil) für UI und Card."""
    fotos_je = fotos_je or {}
    faktor = portionen / max(r.servings, 1)
    zutaten = []
    for i, z in enumerate(r.ingredients):
        p = produkte.get(z.product_id)
        zutaten.append(
            {
                "index": i,
                "produkt_id": z.product_id,
                "name": p.name if p else f"? ({z.product_id})",
                "einheit": p.unit if p else "",
                "menge": round(z.amount * faktor, 1),
                "notiz": z.note,
            }
        )
    out = []
    namen = [z["name"] for z in zutaten]
    erstmals: dict[int, int] = {}  # Zutat → erster Schritt (1-basiert), für „aus Schritt n“
    for nr, s in enumerate(schritte, 1):
        for z in s["zutaten"]:
            erstmals.setdefault(z["zutat"], nr)
    for nr, s in enumerate(schritte, 1):
        out.append(
            {
                **s,
                # Text steht für die Grundportionen, Mengen darin rechnet der Code um
                "text": kueche.mengen_skalieren(s["text"], faktor, namen),
                "fotos": [s["foto"]]
                if s.get("foto")
                else [f for o in s.get("original") or [] for f in fotos_je.get(o, [])][:3],
                "zutaten": [
                    {
                        **zutaten[z["zutat"]],
                        "anteil": z["anteil"],
                        "menge": round(zutaten[z["zutat"]]["menge"] * z["anteil"], 1),
                        "frueher": erstmals[z["zutat"]] if erstmals[z["zutat"]] < nr else None,
                    }
                    for z in s["zutaten"]
                    if z["zutat"] < len(zutaten)
                ],
            }
        )
    return {
        "rezept_id": r.id,
        "name": r.name,
        "portionen": portionen,
        "portionen_basis": r.servings,
        "quelle": quelle,
        "status": a.status if a else "keine",
        "gespeichert": a is not None,
        "veraltet": bool(a and a.basis != basis_hash(r)),
        "hinweise": (a.hinweise if a else None) or [],
        "zutaten": zutaten,
        "schritte": out,
        "fehlend": [zutaten[i]["name"] for i in fehlende_zutaten(schritte, len(zutaten))],
    }


# --- KI-Migrator -----------------------------------------------------------------------------


ANWEISUNG = """Schreibe das Rezept in eine Schritt-für-Schritt-Anleitung für einen Kochmodus um.

Rezept: {name} ({portionen} Portionen)
Zutaten (Index | Name | Menge):
{zutaten}

Originale Zubereitung:
{schritte}

Regeln:
- Ordne die Arbeit so, wie ein erfahrener Koch sie macht: Was lange braucht, zuerst
  (Wasser aufsetzen, Ofen vorheizen, Reis aufsetzen), Schneiden und Vorbereiten während Wasser
  oder Ofen heiß werden, dann Garen in sinnvoller Reihenfolge.
- Mengen im Text immer als Zahl mit Einheit für {portionen} Portionen schreiben (z. B. "250 g
  Hähnchen", "2 EL Öl", "1 Zwiebel"), nie "die ganze Packung" o. Ä.: Der Code rechnet sie
  für andere Portionen um.
- Ein Schritt = ein Handgriff bzw. eine Phase, kurzer Titel im Imperativ (z. B. "Wasser aufsetzen",
  "Zwiebeln würfeln", "Nudeln ins Wasser"), Text 1–3 Sätze mit den Details aus dem Original.
- zutaten: die Indizes der Zutaten, die in DIESEM Schritt verarbeitet oder in Topf/Pfanne gegeben
  werden. Jede Zutat muss mindestens einmal vorkommen. Wird eine Zutat in mehreren Schritten
  bearbeitet (Paprika schneiden, später anbraten), nenne sie in jedem davon. teilweise: Indizes,
  von denen in diesem Schritt nur ein Teil verwendet wird (z. B. Butter halb/halb), sonst leer.
  Keine erfundenen Zutaten; Wasser, Salz zum Kochen u. Ä. ohne Index nur im Text erwähnen.
- timer_minuten nur für echtes Warten/Garen (Kochzeit, Ofen, Ruhen), Wert aus dem Original
  übernehmen; bei Spannen den größeren Wert. timer_name kurz, z. B. "Nudeln".
- original: Nummern der Originalschritte, aus denen dieser Schritt stammt (für Schrittbilder).
- parallel: true, wenn der Schritt nebenher weiterläuft, während schon der nächste beginnt
  (z. B. Wasser aufsetzen, Ofen vorheizen, Soße köcheln), nicht bei reinen Handgriffen.
- Nichts weglassen, was im Original steht. Höchstens {max} Schritte.
{feedback}"""


def struktur(n_zutaten: int, n_original: int = 0) -> dict:
    optionen = [str(i) for i in range(n_zutaten)] or ["0"]
    return {
        "schritte": {
            "required": True,
            "selector": {
                "object": {
                    "multiple": True,
                    "fields": {
                        "titel": {"required": True, "selector": {"text": {}}},
                        "text": {"required": True, "selector": {"text": {"multiline": True}}},
                        "zutaten": {
                            "required": False,
                            "selector": {"select": {"options": optionen, "multiple": True}},
                        },
                        "teilweise": {
                            "required": False,
                            "selector": {"select": {"options": optionen, "multiple": True}},
                        },
                        "timer_minuten": {"required": False, "selector": {"number": {}}},
                        "timer_name": {"required": False, "selector": {"text": {}}},
                        "parallel": {"required": False, "selector": {"boolean": {}}},
                        "original": {
                            "required": False,
                            "selector": {
                                "select": {
                                    "options": [str(i + 1) for i in range(n_original)] or ["1"],
                                    "multiple": True,
                                }
                            },
                        },
                    },
                }
            },
        }
    }


async def mit_ki(
    ha, st: Settings, r: Recipe, produkte: dict[str, Product]
) -> tuple[list[dict], list[str]]:
    """LLM schreibt um, Code validiert, höchstens eine Reparaturrunde. → (Schritte, Hinweise)"""
    namen = [
        produkte[z.product_id].name if z.product_id in produkte else "?" for z in r.ingredients
    ]
    einheiten = [
        produkte[z.product_id].unit if z.product_id in produkte else "" for z in r.ingredients
    ]
    zutaten_text = "\n".join(
        f"{i} | {n} | {kueche.menge(z.amount)} {einheiten[i]}"
        for i, (n, z) in enumerate(zip(namen, r.ingredients, strict=True))
    )
    original = kueche.schritte(r.description)
    original_text = (
        "\n".join(f"{i + 1}. {t}" for i, t in enumerate(original)) or "(keine Schritte hinterlegt)"
    )
    zeiten = set(dauern_s(" ".join(original)))
    feedback, hinweise = "", []
    schritte: list[dict] = []
    for runde in range(2):
        daten = await ha.ai_task(
            st.ai_task_entity,
            "rationPlaner Kochanleitung",
            ANWEISUNG.format(
                name=r.name,
                portionen=r.servings,
                zutaten=zutaten_text,
                schritte=original_text,
                max=MAX_SCHRITTE,
                feedback=feedback,
            ),
            struktur(len(namen), len(original)),
        )
        log.info("Kochanleitung %s Rohantwort: %s", r.id, json.dumps(daten, ensure_ascii=False))
        roh_schritte = [
            {**x, "original": [int(o) - 1 for o in x.get("original") or [] if str(o).isdigit()]}
            for x in (daten.get("schritte") or [])
            if isinstance(x, dict)
        ]  # KI zählt ab 1, gespeichert wird 0-basiert
        schritte = [s for s in (schritt_pruefen(x, len(namen)) for x in roh_schritte) if s][
            :MAX_SCHRITTE
        ]
        probleme = []
        if not schritte:
            probleme.append("Keine gültigen Schritte geliefert.")
        if fehlend := fehlende_zutaten(schritte, len(namen)):
            probleme.append(
                "Diese Zutaten fehlen in allen Schritten: "
                + ", ".join(f"{i} ({namen[i]})" for i in fehlend)
            )
        if not probleme or runde == 1:
            break
        feedback = "\nKorrigiere deine letzte Antwort:\n- " + "\n- ".join(probleme)
    if not schritte:
        raise ValueError("KI lieferte keine gültigen Schritte")
    if fehlend := fehlende_zutaten(schritte, len(namen)):
        zutaten_zuordnen(schritte, fehlend, namen)
        hinweise.append("Zugeordnet vom Code: " + ", ".join(namen[i] for i in fehlend))
    anteile_ausgleichen(schritte)
    for s in schritte:
        # Timer, deren Dauer nicht im Original steht, als Schätzung markieren
        if s["timer_s"] and not any(abs(s["timer_s"] - z) <= 60 for z in zeiten):
            s["timer_geschaetzt"] = True
    if any(s["timer_geschaetzt"] for s in schritte):
        hinweise.append("Timer mit ≈ stehen so nicht im Original, bitte prüfen.")
    return schritte, hinweise


async def erzeugen(
    s: Session,
    b: InventoryBackend,
    ha,
    st: Settings,
    rezept_id: str,
    ki: bool,
    fallback: bool = True,
) -> KochAnleitung:
    """Schreibt ein Rezept um und speichert es als Entwurf.

    fallback=False (Massenlauf): scheitert die KI, bleibt die bisherige Anleitung unverändert
    und der Fehler geht nach oben, damit ein späterer Lauf das Rezept erneut versucht."""
    r = await b.get_recipe(rezept_id)
    produkte = {p.id: p for p in await b.list_products()}
    hinweise: list[str] = []
    quelle = "code"
    schritte = aus_originalschritten(r, produkte)
    if ki and r.ingredients:
        if not st.ai_task_entity or ha is None:
            raise HTTPException(503, "Option ai_task_entity fehlt oder kein Zugriff auf HA")
        try:
            schritte, hinweise = await mit_ki(ha, st, r, produkte)
            quelle = "ki"
        except Exception as err:  # noqa: BLE001 - Fallback statt Abbruch
            log.warning("KI-Kochanleitung %s fehlgeschlagen: %s", rezept_id, err)
            if not fallback:
                raise
            hinweise = [
                f"KI nicht erreichbar ({err}), Anleitung vom Code aus den Originalschritten."
            ]
    a = s.get(KochAnleitung, rezept_id) or KochAnleitung(rezept_id=rezept_id)
    a.schritte, a.quelle, a.status, a.basis, a.hinweise = (
        schritte,
        quelle,
        "entwurf",
        basis_hash(r),
        hinweise,
    )
    s.add(a)
    s.commit()
    return a


ABBRUCH_NACH = 3


async def migrieren(app, lauf_id: int) -> None:
    """Hintergrundlauf: ein LLM-Aufruf je Rezept, Fehler je Rezept festhalten und weitermachen."""
    with Session(app.state.engine) as s:
        lauf = s.get(AnleitungLauf, lauf_id)
        if lauf is None:
            return
        fehlgeschlagen = []
        try:
            for rid in lauf.rezept_ids:
                try:
                    await erzeugen(
                        s, app.state.backend, app.state.ha, app.state.settings, rid, True, False
                    )
                except Exception as err:  # noqa: BLE001
                    s.rollback()
                    fehlgeschlagen.append({"id": rid, "fehler": str(err) or type(err).__name__})
                lauf = s.get(AnleitungLauf, lauf_id)
                lauf.fertig += 1
                lauf.fehlgeschlagen = list(fehlgeschlagen)
                s.commit()
                if lauf.fertig == len(fehlgeschlagen) == ABBRUCH_NACH:
                    # Die ersten Aufrufe scheitern alle: KI vermutlich weg, keine Aufrufe verbrennen
                    raise RuntimeError(
                        f"KI {ABBRUCH_NACH}× hintereinander gescheitert, Lauf abgebrochen"
                    )
            lauf.status = "fertig"
            s.commit()
            ha, st = app.state.ha, app.state.settings
            if ha is not None:
                try:
                    await ha.notify(
                        "Kochanleitungen fertig",
                        f"{lauf.fertig - len(fehlgeschlagen)} von {lauf.fertig} Rezepten fertig."
                        " Prüfen unter Rezepte → Kochanleitungen.",
                        st.notify_service,
                    )
                except Exception as err:  # noqa: BLE001
                    log.warning("Hinweis fehlgeschlagen: %s", err)
        except Exception as err:  # noqa: BLE001
            log.exception("Anleitungslauf %s fehlgeschlagen", lauf_id)
            s.rollback()
            lauf = s.get(AnleitungLauf, lauf_id)
            lauf.status, lauf.fehler = "fehler", str(err) or type(err).__name__
            s.commit()


# --- Endpunkte: Anleitungen ------------------------------------------------------------------


@router.get("/kochen/anleitungen")
async def anleitungen(
    s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> list[dict]:
    """Je Rezept: Status der Anleitung (keine / entwurf / geprueft), veraltet, Schrittzahl."""
    vorhanden = {a.rezept_id: a for a in s.scalars(select(KochAnleitung))}
    out = []
    for r in sorted(await b.list_recipes(), key=lambda r: r.name.lower()):
        a = vorhanden.get(r.id)
        out.append(
            {
                "rezept_id": r.id,
                "name": r.name,
                "status": a.status if a else "keine",
                "quelle": a.quelle if a else None,
                "schritte": len(a.schritte) if a else 0,
                "original_schritte": len(kueche.schritte(r.description)),
                "veraltet": bool(a and a.basis != basis_hash(r)),
                "geaendert": a.geaendert if a else None,
                "hinweise": (a.hinweise or []) if a else [],
                "zutaten": len(r.ingredients),
            }
        )
    return out


@router.get("/kochen/anleitung/{rezept_id}")
async def anleitung(
    rezept_id: str,
    portionen: int | None = None,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Gespeicherte Anleitung oder (ohne) der Code-Vorschlag aus den Originalschritten."""
    r = await b.get_recipe(rezept_id)
    produkte = {p.id: p for p in await b.list_products()}
    a = s.get(KochAnleitung, rezept_id)
    schritte = a.schritte if a else aus_originalschritten(r, produkte)
    ziel = max(1, min(portionen or r.servings, 24))
    return darstellen(
        a, schritte, r, produkte, ziel, a.quelle if a else "code", fotos.je_schritt(s, r.id)
    )


class GenerierenIn(BaseModel):
    ki: bool = True


@router.post("/kochen/anleitung/{rezept_id}/generieren")
async def anleitung_generieren(
    rezept_id: str,
    data: GenerierenIn,
    request: Request,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> dict:
    """Ein Rezept umschreiben (KI oder Code), Ergebnis als Entwurf speichern."""
    a = await erzeugen(s, b, request.app.state.ha, st, rezept_id, data.ki)
    r = await b.get_recipe(rezept_id)
    produkte = {p.id: p for p in await b.list_products()}
    return darstellen(a, a.schritte, r, produkte, r.servings, a.quelle, fotos.je_schritt(s, r.id))


class AnleitungIn(BaseModel):
    schritte: list[dict] = Field(min_length=1, max_length=MAX_SCHRITTE)
    status: str = "entwurf"


@router.put("/kochen/anleitung/{rezept_id}")
async def anleitung_speichern(
    rezept_id: str,
    data: AnleitungIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Von Hand bearbeitet oder freigegeben. Zutaten als Index ins Rezept, Anteil 0–1."""
    if data.status not in ("entwurf", "geprueft"):
        raise HTTPException(422, "status muss entwurf oder geprueft sein")
    r = await b.get_recipe(rezept_id)
    produkte = {p.id: p for p in await b.list_products()}
    schritte = [x for x in (schritt_pruefen(roh, len(r.ingredients)) for roh in data.schritte) if x]
    if not schritte:
        raise HTTPException(422, "Keine gültigen Schritte")
    anteile_ausgleichen(schritte)
    alt = s.get(KochAnleitung, rezept_id)
    a = alt or KochAnleitung(rezept_id=rezept_id)
    geaendert = alt is None or alt.schritte != schritte
    a.schritte, a.status, a.basis = schritte, data.status, basis_hash(r)
    if geaendert:
        a.quelle, a.hinweise = "manuell" if alt is None or alt.quelle != "ki" else "ki", []
    s.add(a)
    s.commit()
    return darstellen(a, schritte, r, produkte, r.servings, a.quelle, fotos.je_schritt(s, r.id))


@router.delete("/kochen/anleitung/{rezept_id}")
async def anleitung_loeschen(rezept_id: str, s: Session = Depends(session)) -> dict:
    if a := s.get(KochAnleitung, rezept_id):
        s.delete(a)
        s.commit()
    return {"geloescht": rezept_id}


class FreigebenIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/kochen/anleitungen/freigeben")
async def anleitungen_freigeben(data: FreigebenIn, s: Session = Depends(session)) -> dict:
    ok = []
    for rid in data.ids:
        if a := s.get(KochAnleitung, rid):
            a.status = "geprueft"
            ok.append(rid)
    s.commit()
    return {
        "ok": ok,
        "fehler": [{"id": i, "fehler": "keine Anleitung"} for i in data.ids if i not in ok],
    }


class MigrierenIn(BaseModel):
    rezept_ids: list[str] | None = None  # None = alle
    nur_fehlende: bool = True  # Rezepte mit Anleitung überspringen (außer veraltet)


@router.post("/kochen/migrieren", status_code=202)
async def anleitungen_migrieren(
    data: MigrierenIn,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> dict:
    """Mehrere Rezepte per KI umschreiben, im Hintergrund. Ein LLM-Aufruf je Rezept."""
    if not st.ai_task_entity or request.app.state.ha is None:
        raise HTTPException(503, "Option ai_task_entity fehlt oder kein Zugriff auf HA")
    if s.scalars(select(AnleitungLauf).where(AnleitungLauf.status == "laeuft")).first():
        raise HTTPException(409, "Es läuft schon ein Umschreib-Lauf")
    rezepte = [r for r in await b.list_recipes() if r.ingredients]
    if data.rezept_ids is not None:
        rezepte = [r for r in rezepte if r.id in set(data.rezept_ids)]
    if data.nur_fehlende:
        vorhanden = {a.rezept_id: a for a in s.scalars(select(KochAnleitung))}
        rezepte = [
            r for r in rezepte if r.id not in vorhanden or vorhanden[r.id].basis != basis_hash(r)
        ]
    if not rezepte:
        raise HTTPException(409, "Nichts zu tun: alle gewählten Rezepte haben schon eine Anleitung")
    lauf = AnleitungLauf(rezept_ids=[r.id for r in rezepte], fehlgeschlagen=[])
    s.add(lauf)
    s.commit()
    hintergrund.add_task(migrieren, request.app, lauf.id)
    return {"lauf_id": lauf.id, "rezepte": len(rezepte)}


@router.get("/kochen/migrieren")
async def migrier_laeufe(s: Session = Depends(session)) -> list[dict]:
    return [
        {
            "id": x.id,
            "status": x.status,
            "gesamt": len(x.rezept_ids),
            "fertig": x.fertig,
            "fehlgeschlagen": x.fehlgeschlagen,
            "fehler": x.fehler,
            "angelegt": x.angelegt,
        }
        for x in s.scalars(select(AnleitungLauf).order_by(AnleitungLauf.id.desc()).limit(10))
    ]


# --- Timer (HA timer.*) ---------------------------------------------------------------------


def _iso(x: datetime) -> str:
    return x.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _hhmmss(sek: int) -> str:
    return f"{sek // 3600:02d}:{sek % 3600 // 60:02d}:{sek % 60:02d}"


def _sek(text: str | None) -> int | None:
    if not text:
        return None
    try:
        h, m, s = (float(x) for x in str(text).split(":"))
        return int(h * 3600 + m * 60 + s)
    except ValueError:
        return None


async def timer_liste(ha, st: Settings) -> tuple[list[dict], bool]:
    """Kochtimer mit Zustand aus der App-Option timer_entities.

    Ohne Option: alle timer.* mit Küche/kitchen/koch in ID oder Namen (automatisch)."""
    if ha is None:
        return [], False
    alle = await ha.states("timer")
    gewaehlt = list(st.timer_entities)
    if gewaehlt:
        alle = [x for x in alle if x["entity_id"] in gewaehlt]
    else:
        alle = [
            x
            for x in alle
            if KUECHEN_MUSTER.search(
                x["entity_id"] + " " + str(x.get("attributes", {}).get("friendly_name", ""))
            )
        ]
    out = []
    for x in sorted(alle, key=lambda x: x["entity_id"]):
        a = x.get("attributes", {})
        out.append(
            {
                "entity_id": x["entity_id"],
                "name": a.get("friendly_name") or x["entity_id"],
                "state": x.get("state"),
                "ende": a.get("finishes_at"),
                "rest_s": _sek(a.get("remaining")),
                "dauer_s": _sek(a.get("duration")),
            }
        )
    return out, not gewaehlt


@router.get("/kochen/timer")
async def timer(request: Request, st: Settings = Depends(settings)) -> dict:
    """Kochtimer aus HA; `automatisch`: ohne Option timer_entities per Namen gefunden."""
    try:
        liste, auto = await timer_liste(request.app.state.ha, st)
    except Exception as err:  # noqa: BLE001 - UI fällt auf Browser-Timer zurück
        return {"timer": [], "automatisch": True, "fehler": str(err)}
    return {"timer": liste, "automatisch": auto, "fehler": None}


@router.get("/kochen/timer/verfuegbar")
async def timer_verfuegbar(request: Request) -> list[dict]:
    """Alle timer.* in HA zur Auswahl in den Einstellungen."""
    ha = request.app.state.ha
    if ha is None:
        return []
    return [
        {
            "entity_id": x["entity_id"],
            "name": x.get("attributes", {}).get("friendly_name") or x["entity_id"],
            "state": x.get("state"),
        }
        for x in await ha.states("timer")
    ]


class TimerIn(BaseModel):
    sekunden: int = Field(ge=5, le=MAX_TIMER_S)
    name: str = Field(default="Timer", max_length=40)
    sitzung_id: int | None = None
    schritt: int | None = None
    entity_id: str | None = None  # sonst der erste freie Kochtimer


@router.post("/kochen/timer")
async def timer_starten(
    data: TimerIn,
    request: Request,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
) -> dict:
    """Startet einen freien HA-Timer. 409, wenn keiner frei ist (UI nimmt dann den Browser)."""
    ha = request.app.state.ha
    liste, _ = await timer_liste(ha, st)
    if not liste:
        raise HTTPException(409, "Keine Kochtimer in HA gefunden (App-Option timer_entities)")
    if data.entity_id:
        t = next((x for x in liste if x["entity_id"] == data.entity_id), None)
        if t is None:
            raise HTTPException(404, f"{data.entity_id} ist kein Kochtimer")
    else:
        t = next((x for x in liste if x["state"] == "idle"), None)
        if t is None:
            raise HTTPException(409, "Alle Kochtimer laufen gerade")
    await ha.call_service(
        "timer", "start", {"entity_id": t["entity_id"], "duration": _hhmmss(data.sekunden)}
    )
    ende = _iso(datetime.now(UTC) + timedelta(seconds=data.sekunden))
    if data.sitzung_id and (k := s.get(KochSitzung, data.sitzung_id)):
        k.timer = [x for x in k.timer if x["entity_id"] != t["entity_id"]] + [
            {
                "entity_id": t["entity_id"],
                "name": data.name,
                "ende": ende,
                "schritt": data.schritt,
                "dauer_s": data.sekunden,
            }
        ]
        s.commit()
    return {"entity_id": t["entity_id"], "timer_name": t["name"], "name": data.name, "ende": ende}


@router.post("/kochen/timer/{entity_id}/{aktion}")
async def timer_aktion(
    entity_id: str,
    aktion: str,
    request: Request,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
) -> dict:
    """abbrechen / pausieren / fortsetzen / fertig; nur für Kochtimer."""
    dienst = {
        "abbrechen": "cancel",
        "pausieren": "pause",
        "fortsetzen": "start",
        "fertig": "finish",
    }
    if aktion not in dienst:
        raise HTTPException(404, "Aktion unbekannt")
    liste, _ = await timer_liste(request.app.state.ha, st)
    if entity_id not in {x["entity_id"] for x in liste}:
        raise HTTPException(404, f"{entity_id} ist kein Kochtimer")
    await request.app.state.ha.call_service("timer", dienst[aktion], {"entity_id": entity_id})
    if aktion in ("abbrechen", "fertig"):
        for k in s.scalars(select(KochSitzung).where(KochSitzung.aktiv.is_(True))):
            if any(x["entity_id"] == entity_id for x in k.timer):
                k.timer = [x for x in k.timer if x["entity_id"] != entity_id]
        s.commit()
    return {"entity_id": entity_id, "aktion": aktion}


# --- Kochsitzung -----------------------------------------------------------------------------


def sitzung_dict(k: KochSitzung) -> dict:
    return {
        "id": k.id,
        "rezept_id": k.rezept_id,
        "portionen": k.portionen,
        "schritt": k.schritt,
        "erledigt": k.erledigt,
        "abgehakt": k.abgehakt,
        "timer": k.timer,
        "aktiv": k.aktiv,
        "gestartet": k.gestartet,
    }


class SitzungIn(BaseModel):
    rezept_id: str
    portionen: int | None = Field(default=None, ge=1, le=24)
    neu: bool = False  # True: laufende Sitzung des Rezepts verwerfen


@router.post("/kochen/sitzung")
async def sitzung_starten(
    data: SitzungIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Laufende Sitzung des Rezepts fortsetzen oder neu anfangen. Andere Rezepte laufen weiter."""
    r = await b.get_recipe(data.rezept_id)
    k = s.scalars(
        select(KochSitzung).where(KochSitzung.rezept_id == r.id, KochSitzung.aktiv.is_(True))
    ).first()
    if k and (data.neu or (data.portionen and data.portionen != k.portionen)):
        k.aktiv = False
        k = None
    if k is None:
        k = KochSitzung(
            rezept_id=r.id,
            portionen=data.portionen or r.servings,
            erledigt=[],
            abgehakt=[],
            timer=[],
        )
        s.add(k)
    s.commit()
    return sitzung_dict(k)


@router.get("/kochen/sitzungen")
async def sitzungen(s: Session = Depends(session)) -> list[dict]:
    """Laufende Sitzungen der letzten 24 h (für „weiterkochen“)."""
    grenze = datetime.now() - timedelta(hours=24)
    rows = s.scalars(
        select(KochSitzung)
        .where(KochSitzung.aktiv.is_(True), KochSitzung.gestartet >= grenze)
        .order_by(KochSitzung.id.desc())
    )
    return [sitzung_dict(k) for k in rows]


@router.get("/kochen/sitzung/{sitzung_id}")
async def sitzung(sitzung_id: int, s: Session = Depends(session)) -> dict:
    k = s.get(KochSitzung, sitzung_id)
    if k is None:
        raise HTTPException(404, "Sitzung nicht gefunden")
    return sitzung_dict(k)


class SitzungPatch(BaseModel):
    schritt: int | None = Field(default=None, ge=0, le=MAX_SCHRITTE)
    erledigt: list[int] | None = None
    abgehakt: list[str] | None = None
    timer: list[dict] | None = None  # Browser-Timer ohne HA
    aktiv: bool | None = None


@router.patch("/kochen/sitzung/{sitzung_id}")
async def sitzung_aendern(
    sitzung_id: int, data: SitzungPatch, s: Session = Depends(session)
) -> dict:
    k = s.get(KochSitzung, sitzung_id)
    if k is None:
        raise HTTPException(404, "Sitzung nicht gefunden")
    for feld, wert in data.model_dump(exclude_none=True).items():
        setattr(k, feld, wert)
    s.commit()
    return sitzung_dict(k)
