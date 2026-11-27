"""Rezepte von der KI erfinden lassen: Wunsch → Entwurf → Überarbeiten → Import-Vorschau.

Die KI liefert Name, Portionen, Zutatenzeilen („200 g Crème fraîche“) und die Kochanleitung
gleich im Format des Kochmodus (Schritte mit Zutaten-Indizes, Timer). Der Code prüft die
Schritte wie bei `kochen.ki_schritte`. Nach Grocy geht nichts direkt: „Übernehmen“ startet den
normalen Import (importlauf.py) mit dem Entwurf als Rohrezept, dort ordnet der Code (und die
Import-KI) die Zutaten Produkten zu und rechnet Mengen in Gramm, der Nutzer prüft wie beim Link.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import importlauf, kochen, rezept_import
from app.db import RezeptEntwurf, RezeptImport, ZutatAlias
from app.deps import session
from app.inventory.base import Product, TrackingMode

log = logging.getLogger("ration.rezept_ki")
router = APIRouter(prefix="/api/v1")

JOB = "rezept"
MAX_ZUTATEN = 40
MAX_VORRAT = 120  # Zeilen Bestand im Prompt, bald Ablaufendes zuerst
AUFWAND = {
    "schnell": "schnell: höchstens 25 Minuten insgesamt, wenige Arbeitsschritte",
    "normal": "normal: etwa 30–45 Minuten, alltagstauglich",
    "aufwaendig": "aufwändig: darf länger dauern und mehr Technik verlangen (Wochenende)",
}

ANWEISUNG = """Du bist Koch und erfindest ein Rezept für einen Haushalt in Deutschland.

Wunsch: {prompt}
Portionen: {portionen}
Aufwand: {aufwand}
{kontext}{basis}
Regeln für die Zutaten:
- Eine Zeile je Zutat: Menge, Einheit, Name, z. B. "200 g Crème fraîche", "2 EL Olivenöl",
  "1 Zwiebel", "1 Dose Kichererbsen (400 g)", "1 Prise Salz". Einheiten: g, kg, ml, l, EL, TL,
  Prise, Bund, Dose, Stück oder keine (bei Stückzutaten). Mengen für {portionen} Portionen.
- Nur der Name des Lebensmittels, keine Zubereitung im Namen ("1 Zwiebel", nicht "1 Zwiebel,
  gewürfelt"). Salz und Pfeffer als eigene Zeilen. Wasser nicht als Zutat.
- Heißt ein Lebensmittel im Katalog schon so, schreibe es genauso.
- Alltagszutaten aus dem deutschen Supermarkt, nichts Exotisches, außer der Wunsch verlangt es.

Regeln für die Anleitung (Kochmodus):
- Ordne die Arbeit so, wie ein erfahrener Koch sie macht: Was lange braucht, zuerst (Wasser
  aufsetzen, Ofen vorheizen), Schneiden während Wasser oder Ofen heiß werden, dann Garen.
- Mengen im Text immer als Zahl mit Einheit für {portionen} Portionen ("250 g Hähnchen",
  "2 EL Öl"), nie "die ganze Packung": Der Code rechnet sie für andere Portionen um.
- Ein Schritt = ein Arbeitsgang, der zusammengehörige Handgriffe bündelt. Ein eigener Schritt
  lohnt sich, wenn danach etwas gart oder wartet, etwas nebenher läuft oder die Arbeit wechselt.
  Bereitstellen, Waschen und Anrichten nie als eigener Schritt. Typisch 6–12 Schritte. Kurzer
  Titel im Imperativ ("Nudeln kochen"), Text 1–4 Sätze mit Temperaturen, Zeiten, Garpunkt.
- zutaten: die Indizes (ab 0, Reihenfolge der Zutatenliste: die erste Zutat ist "0") der
  Zutaten, die in DIESEM Schritt verarbeitet, geschnitten oder in Topf/Pfanne gegeben werden.
  Pflicht: Jede Zutat steht in mindestens einem Schritt, und jeder Schritt, in dem Zutaten
  verarbeitet werden, nennt sie (auch beim Anbraten, wenn sie vorher geschnitten wurden).
  teilweise: Indizes, von denen hier nur ein Teil verwendet wird (Butter halb/halb), sonst leer.
- timer_minuten nur für echtes Warten/Garen (Kochzeit, Ofen, Ruhen). timer_name kurz ("Nudeln").
- parallel: true, wenn der Schritt nebenher weiterläuft (Wasser aufsetzen, Ofen, Soße köcheln).
- Höchstens {max} Schritte.

aenderung: ein kurzer Satz, was du gegenüber der Vorlage geändert hast (beim ersten Entwurf:
leer).
{feedback}"""

BASIS = """
Vorlage (der aktuelle Entwurf):
{rezept}

Änderungswunsch: {wunsch}
Setze den Änderungswunsch um und behalte alles andere bei. Passe Mengen, Zutatenliste,
Schritte und Zutaten-Indizes so an, dass das Rezept stimmig bleibt.
"""

# Indizes als feste Auswahl 0…MAX_ZUTATEN-1 (wie bei der Kochanleitung): als freie Textliste ließ
# gpt-4o-mini über ai_task die Zutaten aller Schritte leer (Live-Test 0.35.0)
_INDIZES = {"select": {"options": [str(i) for i in range(MAX_ZUTATEN)], "multiple": True}}

STRUKTUR = {
    "name": {"required": True, "selector": {"text": {}}},
    "portionen": {"required": True, "selector": {"number": {}}},
    "zutaten": {"required": True, "selector": {"text": {"multiple": True}}},
    "schritte": {
        "required": True,
        "selector": {
            "object": {
                "multiple": True,
                "fields": {
                    "titel": {"required": True, "selector": {"text": {}}},
                    "text": {"required": True, "selector": {"text": {"multiline": True}}},
                    "zutaten": {"required": True, "selector": _INDIZES},
                    "teilweise": {"required": False, "selector": _INDIZES},
                    "timer_minuten": {"required": False, "selector": {"number": {}}},
                    "timer_name": {"required": False, "selector": {"text": {}}},
                    "parallel": {"required": False, "selector": {"boolean": {}}},
                },
            }
        },
    },
    "aenderung": {"required": False, "selector": {"text": {}}},
}


class GenerierenIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    portionen: int = Field(2, ge=1, le=12)
    aufwand: str = Field("normal", pattern="^(schnell|normal|aufwaendig)$")
    vorrat: bool = False  # Bestand mitschicken, bald Ablaufendes bevorzugen


class UeberarbeitenIn(BaseModel):
    wunsch: str = Field(min_length=2, max_length=1000)
    version: int | None = None  # Basis, Standard: die neueste


class EntwurfImportIn(BaseModel):
    version: int | None = None  # Standard: die neueste


# --- Prüfen ---------------------------------------------------------------------------


def pruefen(daten: dict) -> tuple[dict, list[str]]:
    """KI-Antwort → Entwurf. Gibt (Entwurf, Probleme); Probleme gehen in die Reparaturrunde."""
    probleme: list[str] = []
    name = str(daten.get("name") or "").strip()[:120]
    if not name:
        probleme.append("Name fehlt.")
    try:
        portionen = min(12, max(1, round(float(daten.get("portionen") or 0))))
    except (TypeError, ValueError):
        portionen = 2
    zutaten = [t for t in (str(z).strip()[:200] for z in daten.get("zutaten") or []) if t][
        :MAX_ZUTATEN
    ]
    if not zutaten:
        probleme.append("Keine Zutaten geliefert.")
    roh = [s for s in daten.get("schritte") or [] if isinstance(s, dict)]
    schritte = [s for s in (kochen.schritt_pruefen(x, len(zutaten)) for x in roh) if s]
    schritte = schritte[: kochen.MAX_SCHRITTE]
    if not schritte:
        probleme.append("Keine gültigen Schritte geliefert.")
    hinweise: list[str] = []
    if zutaten and schritte and (fehlend := kochen.fehlende_zutaten(schritte, len(zutaten))):
        probleme.append(
            "Diese Zutaten fehlen in allen Schritten: "
            + ", ".join(f"{i} ({zutaten[i]})" for i in fehlend)
        )
        namen = [rezept_import.zutat_parsen(t).name for t in zutaten]
        kochen.zutaten_zuordnen(schritte, fehlend, namen)
        hinweise.append("Zugeordnet vom Code: " + ", ".join(namen[i] for i in fehlend))
    kochen.anteile_ausgleichen(schritte)
    entwurf = {
        "name": name or "Neues Rezept",
        "portionen": portionen,
        "zutaten": zutaten,
        "schritte": schritte,
        "hinweise": hinweise,
        "aenderung": str(daten.get("aenderung") or "").strip()[:300],
    }
    return entwurf, probleme


def als_text(rezept: dict) -> str:
    """Entwurf für den Überarbeiten-Prompt (Indizes wie in der Antwort)."""
    zutaten = "\n".join(f"{i} | {t}" for i, t in enumerate(rezept["zutaten"]))
    schritte = "\n".join(
        f"{n + 1}. {s['titel']}: {s['text']} [Zutaten: "
        + ", ".join(str(z["zutat"]) for z in s["zutaten"])
        + (f"; Timer {round(s['timer_s'] / 60)} min" if s.get("timer_s") else "")
        + "]"
        for n, s in enumerate(rezept["schritte"])
    )
    return (
        f"Name: {rezept['name']}\nPortionen: {rezept['portionen']}\n"
        f"Zutaten (Index | Zeile):\n{zutaten}\nSchritte:\n{schritte}"
    )


# --- Kontext ----------------------------------------------------------------------------


async def vorrat_text(backend) -> str:
    """Bestand je Produkt (Menge, frühestes MHD), bald Ablaufendes zuerst; ohne immer_da."""
    produkte = {p.id: p for p in await backend.list_products()}
    summe: dict[str, float] = {}
    mhd: dict[str, date] = {}
    for b in await backend.list_all_batches():
        if b.product_id not in produkte or b.amount <= 0:
            continue
        summe[b.product_id] = summe.get(b.product_id, 0.0) + b.amount
        if b.best_before and (b.product_id not in mhd or b.best_before < mhd[b.product_id]):
            mhd[b.product_id] = b.best_before
    zeilen = []
    for pid in sorted(summe, key=lambda i: (mhd.get(i) or date.max, produkte[i].name)):
        p = produkte[pid]
        if p.tracking_mode == TrackingMode.IMMER_DA:
            continue
        ablauf = f" | MHD {mhd[pid].isoformat()}" if pid in mhd else ""
        zeilen.append(f"- {p.name} | {round(summe[pid], 1):g} {p.unit}{ablauf}")
    if not zeilen:
        return ""
    return (
        f"\nVorrat (heute {date.today().isoformat()}, bald Ablaufendes zuerst). Nutze bevorzugt "
        "diese Zutaten, besonders die bald ablaufenden, aber nur, wenn sie zum Wunsch passen. "
        "Fehlendes darf dazukommen:\n" + "\n".join(zeilen[:MAX_VORRAT]) + "\n"
    )


async def katalog_text(backend) -> str:
    namen = sorted({p.name for p in await backend.list_products()}, key=str.lower)
    if not namen:
        return ""
    return "\nKatalog (so heißen Lebensmittel bei uns): " + ", ".join(namen) + "\n"


# --- KI ---------------------------------------------------------------------------------


async def erzeugen(
    kc, prompt: str, optionen: dict, kontext: str, basis: dict | None = None, wunsch: str = ""
) -> dict:
    """Ein Entwurf, höchstens eine Reparaturrunde. Fehlende Zutaten ordnet notfalls der Code zu."""
    basis_text = BASIS.format(rezept=als_text(basis), wunsch=wunsch) if basis else ""
    feedback = ""
    entwurf: dict = {}
    for runde in range(2):
        daten = await kc.generate(
            JOB,
            "rationPlaner Rezept generieren",
            ANWEISUNG.format(
                prompt=prompt,
                portionen=basis["portionen"] if basis else optionen["portionen"],
                aufwand=AUFWAND[optionen["aufwand"]],
                kontext=kontext,
                basis=basis_text,
                max=kochen.MAX_SCHRITTE,
                feedback=feedback,
            ).replace("\n\n\n", "\n\n"),
            STRUKTUR,
        )
        log.info("Rezept-KI Rohantwort: %s", json.dumps(daten, ensure_ascii=False))
        entwurf, probleme = pruefen(daten)
        if not probleme or runde == 1:
            break
        feedback = "\nKorrigiere deine letzte Antwort:\n- " + "\n- ".join(probleme)
    if not entwurf["zutaten"] or not entwurf["schritte"]:
        raise HTTPException(502, "KI lieferte kein brauchbares Rezept, bitte nochmal versuchen")
    return entwurf


async def _kontext(request: Request, optionen: dict) -> str:
    backend = request.app.state.backend
    teile = [katalog_text(backend)]
    if optionen.get("vorrat"):
        teile.append(vorrat_text(backend))
    return "".join(await asyncio.gather(*teile))


def _jetzt() -> str:
    return importlauf._jetzt().isoformat(timespec="seconds")


def abgleich(
    rezept: dict,
    produkte: list[Product],
    aliase: dict[str, tuple[str, str]],
    bestand: dict[str, float],
) -> list[dict]:
    """Je Zutat: aus dem Katalog (db: sicher, vorschlag: über den Kern-Namen), neu oder
    weggelassen, dazu der Bestand. Nur der Code-Abgleich; endgültig entscheidet die
    Import-Vorschau (dort prüft zusätzlich die Import-KI)."""
    roh = roh_rezept(0, 0, rezept)
    by_id = {p.id: p for p in produkte}
    ergebnis = []
    for z in rezept_import.zuordnen(roh, produkte, aliase):
        if z.aktion == "zuordnen" and z.produkt_id in by_id:
            p = by_id[z.produkt_id]
            ergebnis.append({
                "status": "db" if z.sicher else "vorschlag",
                "produkt_id": p.id, "produkt": p.name, "einheit": p.unit,
                "bestand": round(bestand.get(p.id, 0.0), 1),
                "immer_da": p.tracking_mode == TrackingMode.IMMER_DA,
            })  # fmt: skip
        elif z.aktion == "weglassen":
            ergebnis.append({"status": "weglassen", "hinweis": z.hinweis})
        else:
            ergebnis.append({"status": "neu", "neu_name": z.neu_name or z.name})
    return ergebnis


async def ausgabe(request: Request, s: Session, e: RezeptEntwurf) -> dict:
    """Entwurf mit allen Versionen, je Version der aktuelle Abgleich mit dem Katalog."""
    backend = request.app.state.backend
    produkte, chargen = await asyncio.gather(backend.list_products(), backend.list_all_batches())
    aliase = {a.text: (a.produkt_id, a.quelle or "nutzer") for a in s.scalars(select(ZutatAlias))}
    bestand: dict[str, float] = {}
    for b in chargen:
        bestand[b.product_id] = bestand.get(b.product_id, 0.0) + b.amount
    return {
        "id": e.id,
        "prompt": e.prompt,
        "optionen": e.optionen,
        "versionen": [
            {**v, "abgleich": abgleich(v["rezept"], produkte, aliase, bestand)} for v in e.versionen
        ],
        "import_id": e.import_id,
        "angelegt": e.angelegt.isoformat() if e.angelegt else None,
    }


def _entwurf(s: Session, entwurf_id: int) -> RezeptEntwurf:
    e = s.get(RezeptEntwurf, entwurf_id)
    if e is None:
        raise HTTPException(404, "Entwurf nicht gefunden")
    return e


def _version(e: RezeptEntwurf, version: int | None) -> dict:
    if version is None:
        version = len(e.versionen) - 1
    if not 0 <= version < len(e.versionen):
        raise HTTPException(422, f"Version {version} gibt es nicht")
    return e.versionen[version]["rezept"]


# --- Endpunkte --------------------------------------------------------------------------


@router.get("/generieren")
async def entwuerfe(request: Request, s: Session = Depends(session)) -> dict:
    """Letzte Entwürfe, erwartete Dauer eines Aufrufs und ob die KI eingerichtet ist."""
    liste = s.scalars(select(RezeptEntwurf).order_by(RezeptEntwurf.id.desc()).limit(20))
    return {
        "entwuerfe": [
            {
                "id": e.id,
                "prompt": e.prompt,
                "name": e.versionen[-1]["rezept"]["name"],
                "versionen": len(e.versionen),
                "import_id": e.import_id,
                "angelegt": e.angelegt.isoformat() if e.angelegt else None,
            }
            for e in liste
        ],
        "erwartet_s": importlauf.erwartete_dauer(s, (JOB,)),
        "ki_fehlt": request.app.state.ki.fehlt(JOB),
    }


@router.get("/generieren/{entwurf_id}")
async def entwurf_lesen(entwurf_id: int, request: Request, s: Session = Depends(session)) -> dict:
    return await ausgabe(request, s, _entwurf(s, entwurf_id))


@router.post("/generieren", status_code=201)
async def generieren(data: GenerierenIn, request: Request, s: Session = Depends(session)) -> dict:
    """Neues Rezept nach Wunsch; landet als Entwurf (Version 0), nichts geht nach Grocy."""
    request.app.state.ki.pruefen(JOB)
    optionen = {"portionen": data.portionen, "aufwand": data.aufwand, "vorrat": data.vorrat}
    rezept = await erzeugen(
        request.app.state.ki, data.prompt.strip(), optionen, await _kontext(request, optionen)
    )
    e = RezeptEntwurf(
        prompt=data.prompt.strip(),
        optionen=optionen,
        versionen=[{"wunsch": None, "rezept": rezept, "angelegt": _jetzt()}],
    )
    s.add(e)
    s.commit()
    log.info("Rezept-Entwurf %s: %s", e.id, rezept["name"])
    return await ausgabe(request, s, e)


@router.post("/generieren/{entwurf_id}/ueberarbeiten")
async def ueberarbeiten(
    entwurf_id: int, data: UeberarbeitenIn, request: Request, s: Session = Depends(session)
) -> dict:
    """Änderungswunsch auf eine Version anwenden; die neue Version kommt ans Ende."""
    request.app.state.ki.pruefen(JOB)
    e = _entwurf(s, entwurf_id)
    basis = _version(e, data.version)
    rezept = await erzeugen(
        request.app.state.ki,
        e.prompt,
        e.optionen,
        await _kontext(request, e.optionen),
        basis=basis,
        wunsch=data.wunsch.strip(),
    )
    e.versionen = [
        *e.versionen,
        {"wunsch": data.wunsch.strip(), "rezept": rezept, "angelegt": _jetzt()},
    ]
    s.commit()
    return await ausgabe(request, s, e)


def roh_rezept(entwurf_id: int, version: int, rezept: dict) -> rezept_import.RohRezept:
    """Entwurf als Rohrezept für den Import. Zutatenzeilen gehen durch denselben Parser wie
    beim Link-Import, damit Mengen und Einheiten der Code rechnet."""
    return rezept_import.RohRezept(
        name=rezept["name"],
        portionen=rezept["portionen"],
        zutaten=[rezept_import.zutat_parsen(t) for t in rezept["zutaten"]],
        url="",
        quelle="ki",
        quelle_id=f"ki-{entwurf_id}-{version}",
        schritte=[s["text"] for s in rezept["schritte"]],
    )


@router.post("/generieren/{entwurf_id}/import", status_code=202)
async def entwurf_import(
    entwurf_id: int, data: EntwurfImportIn, request: Request, s: Session = Depends(session)
) -> dict:
    """Entwurf in die Import-Vorschau geben (Hintergrund wie POST /import/start). Die
    Kochanleitung der KI wird übernommen, die Zuordnung der Zutaten macht der Import."""
    e = _entwurf(s, entwurf_id)
    version = len(e.versionen) - 1 if data.version is None else data.version
    rezept = _version(e, version)
    roh = roh_rezept(e.id, version, rezept)
    anleitung = {
        "quelle": "ki",
        "schritte": rezept["schritte"],
        "hinweise": rezept.get("hinweise") or [],
        "fehler": None,
    }
    eintrag = RezeptImport(
        url="", quelle_id=roh.quelle_id, roh={}, status="laeuft", schritt="Start"
    )
    s.add(eintrag)
    s.commit()
    e.import_id = eintrag.id
    s.commit()
    importlauf.starten(request.app, eintrag.id, None, roh=roh, anleitung=anleitung)
    return importlauf.status(eintrag)
