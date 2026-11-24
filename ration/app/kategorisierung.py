"""Kategorisierungslauf: LLM schlägt Attribute für Produkte und Rezepte vor, Nutzer prüft den Diff.

Nichts wird automatisch übernommen (Konzept, Grundregel 4). Das LLM liefert je Objekt Werte,
der Code validiert Bereiche und Enums und legt nur echte Änderungen als Vorschlag ab.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import haltbarkeit, kueche, naehrwerte, packungen
from app.db import Base, Packung, PackungKauf, Preis
from app.deps import backend, session, settings
from app.inventory.base import (
    DueType,
    InventoryBackend,
    Product,
    ProductPatch,
    Recipe,
    RecipePatch,
    TrackingMode,
)
from app.kueche import REST_KATEGORIE
from app.settings import Settings

log = logging.getLogger("ration.kategorisierung")
router = APIRouter(prefix="/api/v1")

BATCH = 10  # klein halten: 12 Felder je Produkt, Antwort sonst nahe am Token-Limit (3000)
KATEGORIEN = [
    "Gemüse", "Obst", "Kräuter", "Fleisch", "Fisch", "Milchprodukte", "Käse", "Eier", "Brot",
    "Nudeln & Reis", "Hülsenfrüchte", "Konserven", "Mehl & Backen", "Öl & Essig", "Gewürze",
    "Saucen", "Nüsse & Saaten", "Süßes", "Getränke", "Tiefkühl", "Sonstiges",
]  # fmt: skip
TAGS = [
    "vegetarisch", "vegan", "Fleisch", "Fisch", "Pasta", "Reis", "Suppe", "Salat", "Curry",
    "Ofen", "Pfanne", "schnell", "Meal-Prep", "Frühstück", "asiatisch", "italienisch",
    "mexikanisch", "deutsch", "orientalisch",
]  # fmt: skip


class Lauf(Base):
    __tablename__ = "kategorisierung_lauf"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String, default="laeuft")  # laeuft/fertig/fehler
    umfang: Mapped[dict] = mapped_column(JSON)
    fehler: Mapped[str | None] = mapped_column(String, nullable=True)
    angelegt: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Vorschlag(Base):
    """Ein Feld eines Objekts: alter Wert, vorgeschlagener Wert, Entscheidung."""

    __tablename__ = "kategorisierung_vorschlag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lauf_id: Mapped[int] = mapped_column(Integer, index=True)
    typ: Mapped[str] = mapped_column(String)  # produkt / rezept
    objekt_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    feld: Mapped[str] = mapped_column(String)
    alt: Mapped[object] = mapped_column(JSON, nullable=True)
    neu: Mapped[object] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, default="offen")  # offen/uebernommen/abgelehnt


# --- Prompts und Strukturen --------------------------------------------------------------

# Gemeinsam mit dem Import-Prompt (llm_import.py), damit beide gleich entscheiden
REGELN_PRODUKT = """\
- tracking_modus: "exakt" (verderblich, Ablauf je Charge wichtig: Frischgemüse, Fleisch, Milch),
  "menge" (lange haltbar, Menge zählt: Nudeln, Reis, Dosen, Zwiebeln, Kartoffeln),
  "fuellstand" (angebrochen, nur voll/knapp/leer: Mehl, Zucker, Öl, Essig, Senf, Sojasauce),
  "immer_da" (Kleinkram, nie tracken: Salz, Pfeffer, getrocknete Gewürze, Wasser).
- ablauf_typ: "verbrauchsdatum" nur bei leicht verderblichem Fleisch/Fisch/Hack, sonst "mhd".
- haltbarkeit_tage: typische Haltbarkeit ungeöffnet ab Kauf in TAGEN (nicht Monaten!),
  -1 = läuft praktisch nie ab (Salz, Zucker). Nie 0. Richtwerte: Nudeln/Reis 540, Konserven 730,
  getrocknete Gewürze/Pfeffer 730, Brühe (Würfel, Pulver, Glas) 540, Pesto im Glas 365,
  Öl 365, Mehl 270, Joghurt 21, Frischmilch 10, H-Milch 90, Käse 21, Hackfleisch 1,
  Hähnchen 3, Salat 5, Paprika 10, Kartoffeln 60, Zwiebeln 60.
- haltbarkeit_offen_tage: Tage nach dem Öffnen (im Kühlschrank, falls dort gelagert), kleiner
  als ungeöffnet. Richtwerte: Currypaste/Pesto 14, Ketchup/Senf 90, Sojasauce 180, Brühe
  (Glas/Fond) 7, Milch 3, Joghurt 5, Sahne 3, Konserven 3. 0, wenn Öffnen die Haltbarkeit
  nicht verkürzt: trockene Vorräte wie Nudeln, Reis, Couscous, Grieß, Hülsenfrüchte (trocken),
  Salz, Zucker, getrocknete Gewürze."""

ANWEISUNG_PRODUKTE = (
    """Du pflegst die Stammdaten eines Haushaltsvorrats in Deutschland.
Schlage für jedes Produkt Attribute vor. Produkte (id|name|bestandseinheit):
{liste}

Felder je Produkt:
- kategorie: eine aus {kategorien}.
"""
    + REGELN_PRODUKT
    + """
- haltbarkeit_gefroren_tage: eingefroren (0 = nicht einfrieren).
- haltbarkeit_aufgetaut_tage: nach dem Auftauen (0 = egal).
- g_je_einheit: Gramm je Bestandseinheit (bei Bestandseinheit g: 1).
- toleranz_pct: wie viel Prozent ein Rezept mehr oder weniger nehmen darf (5 bis 50).
- packung_einheit/packung_menge: die meistverkaufte Packung im deutschen Supermarkt in
  Bestandseinheit (z. B. Packung 500 für Nudeln in g, Dose 400 für Kidneybohnen in g, Becher
  500 für Joghurt in g, Stück 160 für Paprika). Unsicher → leer lassen.
Gib für jedes Produkt genau einen Eintrag mit seiner id zurück."""
)

ANWEISUNG_NAEHRWERTE = """Schätze typische Nährwerte je 100 g für Lebensmittel aus dem deutschen
Supermarkt, wie in einer Nährwerttabelle (z. B. Bundeslebensmittelschlüssel): roher bzw.
gekaufter Zustand (Nudeln trocken, Hülsenfrüchte aus der Dose abgetropft, Fleisch roh).
Produkte (id|name):
{liste}

Felder je Produkt, alle je 100 g:
- kcal, protein, fett, fett_gesaettigt, kohlenhydrate, zucker, ballaststoffe, salz (Gramm).
- obst_gemuese: true für Obst, Gemüse, Hülsenfrüchte, Nüsse und Kräuter (zählt für den
  Nutri-Score), sonst false. Kartoffeln zählen nicht.
- roh: true, wenn man es üblicherweise roh isst (Obst, Salat, Gurke, Paprika, Nüsse), sonst false.
Gib für jedes Produkt genau einen Eintrag mit seiner id zurück."""

ANWEISUNG_REZEPTE = """Schlage für jedes Rezept Tags, Aufwand und Rest-Haltbarkeit vor.
Rezepte (id|name|zutaten):
{liste}

- tags: 1 bis 4 aus {tags}.
- aufwand: "schnell" (bis 25 min), "normal", "aufwaendig" (über 60 min oder viele Schritte).
- rest_haltbarkeit_tage: wie viele Tage Reste im Kühlschrank halten (1 bis 5).
Gib für jedes Rezept genau einen Eintrag mit seiner id zurück."""


def _feld(selector: dict, required: bool = True) -> dict:
    return {"required": required, "selector": selector}


STRUKTUR_PRODUKTE = {
    "produkte": _feld(
        {
            "object": {
                "multiple": True,
                "fields": {
                    "id": _feld({"text": {}}),
                    "tracking_modus": _feld({"select": {"options": list(TrackingMode)}}),
                    "kategorie": _feld({"select": {"options": KATEGORIEN}}),
                    "ablauf_typ": _feld({"select": {"options": ["mhd", "verbrauchsdatum"]}}),
                    "haltbarkeit_tage": _feld({"number": {}}),
                    "haltbarkeit_offen_tage": _feld({"number": {}}),
                    "haltbarkeit_gefroren_tage": _feld({"number": {}}),
                    "haltbarkeit_aufgetaut_tage": _feld({"number": {}}),
                    "g_je_einheit": _feld({"number": {}}),
                    "toleranz_pct": _feld({"number": {}}),
                    "packung_einheit": _feld({"text": {}}),
                    "packung_menge": _feld({"number": {}}),
                },
            }
        }
    )
}
STRUKTUR_REZEPTE = {
    "rezepte": _feld(
        {
            "object": {
                "multiple": True,
                "fields": {
                    "id": _feld({"text": {}}),
                    "tags": _feld({"select": {"options": TAGS, "multiple": True}}),
                    "aufwand": _feld({"select": {"options": ["schnell", "normal", "aufwaendig"]}}),
                    "rest_haltbarkeit_tage": _feld({"number": {}}),
                },
            }
        }
    )
}


# --- Validierung ------------------------------------------------------------------------


STRUKTUR_NAEHRWERTE = {
    "produkte": _feld(
        {
            "object": {
                "multiple": True,
                "fields": {
                    "id": _feld({"text": {}}),
                    **{f: _feld({"number": {}}) for f in naehrwerte.FELDER},
                    "obst_gemuese": _feld({"boolean": {}}),
                    "roh": _feld({"boolean": {}}),
                },
            }
        }
    )
}


def _int(wert: object, lo: int, hi: int) -> int | None:
    try:
        i = int(round(float(wert)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return i if lo <= i <= hi else None


def _float(wert: object, lo: float, hi: float) -> float | None:
    try:
        f = float(wert)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(f, 3) if lo < f <= hi else None


PLAUSI_FAKTOR = 4  # KI-Haltbarkeit darf höchstens um diesen Faktor vom Richtwert abweichen
RAUSCHEN = 0.3  # gepflegte Haltbarkeit erst ab 30 % Abweichung neu vorschlagen


def haltbarkeit_pruefen(tage: int, p: Product, kategorie: str | None) -> tuple[int | None, str]:
    """Code-Guard für die KI-Haltbarkeit: 0 ist „läuft heute ab“, Monate statt Tage, Ausreißer.
    Gibt (Wert oder None, Grund) zurück."""
    if tage == 0:
        return None, "0 Tage hieße „läuft heute ab“"
    richt = haltbarkeit.tage_schaetzen(replace(p, category=kategorie or p.category))
    if richt is None:
        return tage, ""
    if tage == -1:
        return (tage, "") if richt >= 180 else (None, f"„unbegrenzt“, Richtwert {richt} Tage")
    if tage * PLAUSI_FAKTOR < richt or tage > richt * PLAUSI_FAKTOR:
        monate = " (Monate statt Tage?)" if tage <= 36 and richt >= 180 else ""
        return None, f"{tage} Tage weit weg vom Richtwert {richt}{monate}"
    return tage, ""


def produkt_werte(
    e: dict, p: Product, gekauft: bool = False, verworfen: list[str] | None = None
) -> dict[str, object]:
    """Gültige Vorschläge eines Produkts, je Feld. Ungültiges fällt weg (Grund in verworfen).

    gekauft: Packungsgrößen sind aus Bons bekannt → keine KI-Standardpackung (Daten gehen vor)."""
    verworfen = verworfen if verworfen is not None else []
    w: dict[str, object] = {}
    if e.get("tracking_modus") in list(TrackingMode):
        w["tracking_modus"] = e["tracking_modus"]
    if e.get("kategorie") in KATEGORIEN:
        w["kategorie"] = e["kategorie"]
    if e.get("ablauf_typ") in ("mhd", "verbrauchsdatum"):
        w["ablauf_typ"] = e["ablauf_typ"]
    for feld, lo, hi in (
        ("haltbarkeit_tage", -1, 3650),
        ("haltbarkeit_offen_tage", 0, 3650),
        ("haltbarkeit_gefroren_tage", 0, 3650),
        ("haltbarkeit_aufgetaut_tage", 0, 60),
    ):
        if (v := _int(e.get(feld), lo, hi)) is not None:
            w[feld] = v
    if "haltbarkeit_tage" in w:
        v, grund = haltbarkeit_pruefen(int(w["haltbarkeit_tage"]), p, w.get("kategorie"))  # type: ignore[arg-type]
        alt = p.shelf_life_days
        if v is None:
            del w["haltbarkeit_tage"]
            verworfen.append(f"{p.name}: Haltbarkeit {grund}")
        elif alt > 0 and v > 0 and abs(v - alt) / alt < RAUSCHEN:
            del w["haltbarkeit_tage"]  # nur Rauschen, gepflegter Wert bleibt
        else:
            w["haltbarkeit_tage"] = v
    # Nebenfelder: „leer → 0“ ändert nichts (0 = Standard), nur Rauschen; starke Kürzungen
    # gepflegter Werte (> Faktor 4, z. B. Erdnussbutter geöffnet 180 → 30) nicht vorschlagen.
    for feld, ist in (
        ("haltbarkeit_offen_tage", p.shelf_life_opened_days),
        ("haltbarkeit_gefroren_tage", p.shelf_life_frozen_days),
        ("haltbarkeit_aufgetaut_tage", p.shelf_life_thawed_days),
    ):
        if feld not in w:
            continue
        neu = int(w[feld])  # type: ignore[call-overload]
        leer_zu_null = neu == 0 and not ist
        rauschen = bool(ist and neu and abs(neu - ist) / ist < RAUSCHEN)
        if leer_zu_null or rauschen:
            del w[feld]
        elif ist and neu and neu * PLAUSI_FAKTOR < ist:
            del w[feld]
            verworfen.append(f"{p.name}: {feld} {ist} → {neu} zu starke Kürzung")
    ungeoeffnet = w.get("haltbarkeit_tage", p.shelf_life_days)
    offen = w.get("haltbarkeit_offen_tage")
    if offen and isinstance(ungeoeffnet, int) and ungeoeffnet > 0 and offen > ungeoeffnet:  # type: ignore[operator]
        del w["haltbarkeit_offen_tage"]
        verworfen.append(f"{p.name}: geöffnet länger haltbar als ungeöffnet")
    unit = p.unit.strip().lower()
    g = _float(e.get("g_je_einheit"), 0, 100000)
    # Plausibilität: ml ≈ Dichte 0,5–2; Stück u. Ä. mindestens 2 g (live kam für Ei „1“).
    if g and unit not in ("g", "gramm") and (0.5 <= g <= 2 if unit == "ml" else g >= 2):
        w["g_je_einheit"] = g
    if (t := _int(e.get("toleranz_pct"), 0, 100)) is not None:
        w["toleranz_pct"] = t
    einheit = str(e.get("packung_einheit") or "").strip()[:30]
    menge = _float(e.get("packung_menge"), 0, 100000)
    if einheit.lower() == unit:  # live: "g 500" bzw. "Stück 10" statt "Packung 500"
        einheit = "Packung"
    # Standardpackung nur, wenn noch keine gepflegt ist und keine Käufe laut Bon vorliegen;
    # sonst entscheidet die Analyse „Packungsgrößen“ aus echten Daten.
    if einheit and menge and menge > 1 and not p.pack_size and not gekauft:
        w["packung"] = {"einheit": einheit, "menge": menge}
    return w


def produkt_ist(p: Product) -> dict[str, object]:
    """Aktuelle Werte in derselben Form wie produkt_werte."""
    return {
        "tracking_modus": str(p.tracking_mode) if p.tracking_mode else None,
        "kategorie": p.category,
        # MHD ist Grocy-Standard; bei unkategorisierten Produkten gilt er als „nicht gesetzt“.
        "ablauf_typ": (
            "verbrauchsdatum"
            if p.due_type == DueType.VERBRAUCHSDATUM
            else ("mhd" if p.tracking_mode else None)
        ),
        "haltbarkeit_tage": p.shelf_life_days or None,
        "haltbarkeit_offen_tage": p.shelf_life_opened_days or None,
        "haltbarkeit_gefroren_tage": p.shelf_life_frozen_days or None,
        "haltbarkeit_aufgetaut_tage": p.shelf_life_thawed_days or None,
        "g_je_einheit": p.grams_per_unit,
        "toleranz_pct": p.tolerance_pct,
        "packung": {"einheit": p.pack_unit, "menge": p.pack_size} if p.pack_size else None,
    }


def rezept_werte(e: dict) -> dict[str, object]:
    w: dict[str, object] = {}
    tags = (
        [t for t in (e.get("tags") or []) if t in TAGS] if isinstance(e.get("tags"), list) else []
    )
    if tags:
        w["tags"] = tags[:4]
    if e.get("aufwand") in ("schnell", "normal", "aufwaendig"):
        w["aufwand"] = e["aufwand"]
    if (r := _int(e.get("rest_haltbarkeit_tage"), 1, 7)) is not None:
        w["rest_haltbarkeit_tage"] = r
    return w


def rezept_ist(r: Recipe) -> dict[str, object]:
    return {
        "tags": list(r.tags) or None,
        "aufwand": r.effort,
        "rest_haltbarkeit_tage": r.leftover_days,
    }


def diff(ist: dict, neu: dict, nur_leere: bool) -> dict[str, tuple[object, object]]:
    """Nur echte Änderungen. nur_leere: gesetzte Werte nicht überschreiben (Erstlauf)."""
    result = {}
    for feld, wert in neu.items():
        alt = ist.get(feld)
        if alt == wert or (nur_leere and alt not in (None, "", [])):
            continue
        result[feld] = (alt, wert)
    return result


# --- Lauf ------------------------------------------------------------------------------


async def ausfuehren(app, lauf_id: int) -> None:
    st: Settings = app.state.settings
    ha, ki = app.state.ha, app.state.ki
    b: InventoryBackend = app.state.backend
    with Session(app.state.engine) as s:
        lauf = s.get(Lauf, lauf_id)
        if lauf is None:
            return
        umfang = lauf.umfang
        try:
            anzahl = 0
            if umfang.get("produkte", True):
                produkte = [p for p in await b.list_products() if p.category != REST_KATEGORIE]
                if umfang.get("nur_ohne_attribute", True):
                    produkte = [p for p in produkte if p.tracking_mode is None]
                if ids := umfang.get("produkt_ids"):
                    produkte = [p for p in produkte if p.id in ids]
                for i in range(0, len(produkte), BATCH):
                    teil = produkte[i : i + BATCH]
                    liste = "\n".join(f"{p.id}|{p.name}|{p.unit}" for p in teil)
                    daten = await ki.generate(
                        "kategorisierung",
                        "rationPlaner Kategorisierung Produkte",
                        ANWEISUNG_PRODUKTE.format(liste=liste, kategorien=", ".join(KATEGORIEN)),
                        STRUKTUR_PRODUKTE,
                    )
                    log.info(
                        "Lauf %s Produkte Rohantwort: %s",
                        lauf_id,
                        json.dumps(daten, ensure_ascii=False),
                    )
                    by_id = {p.id: p for p in teil}
                    gekauft = set(s.scalars(select(PackungKauf.produkt_id)))
                    verworfen: list[str] = []
                    for e in daten.get("produkte") or []:
                        p = by_id.pop(str(e.get("id")), None) if isinstance(e, dict) else None
                        if p is None:
                            continue
                        werte = produkt_werte(e, p, p.id in gekauft, verworfen)
                        aenderung = diff(produkt_ist(p), werte, umfang.get("nur_leere", True))
                        for feld, (alt, neu) in aenderung.items():
                            # ältere offene Vorschläge desselben Felds ersetzt der neue
                            for v in s.scalars(
                                select(Vorschlag).where(
                                    Vorschlag.objekt_id == p.id,
                                    Vorschlag.feld == feld,
                                    Vorschlag.status == "offen",
                                )
                            ):
                                v.status = "ersetzt"
                            s.add(
                                Vorschlag(
                                    lauf_id=lauf_id,
                                    typ="produkt",
                                    objekt_id=p.id,
                                    name=p.name,
                                    feld=feld,
                                    alt=alt,
                                    neu=neu,
                                )
                            )
                            anzahl += 1
                    if verworfen:
                        log.info("Lauf %s verworfen (Code-Guard): %s", lauf_id, verworfen)
            if umfang.get("naehrwerte", False):
                anzahl += await _naehrwerte_lauf(s, ki, b, lauf_id, umfang)
            if umfang.get("packungen", False):
                anzahl += await _packungen_lauf(s, ki, b, lauf_id, umfang)
            if umfang.get("gruppen", False):
                anzahl += await _gruppen_lauf(s, ki, b, lauf_id, umfang)
            if umfang.get("rezepte", True):
                rezepte = await b.list_recipes()
                if umfang.get("nur_ohne_attribute", True):
                    rezepte = [r for r in rezepte if not r.tags or not r.effort]
                namen = {p.id: p.name for p in await b.list_products()}
                for i in range(0, len(rezepte), BATCH):
                    teil = rezepte[i : i + BATCH]
                    liste = "\n".join(
                        f"{r.id}|{r.name}|"
                        + ", ".join(namen.get(z.product_id, "?") for z in r.ingredients)
                        for r in teil
                    )
                    daten = await ki.generate(
                        "kategorisierung",
                        "rationPlaner Kategorisierung Rezepte",
                        ANWEISUNG_REZEPTE.format(liste=liste, tags=", ".join(TAGS)),
                        STRUKTUR_REZEPTE,
                    )
                    log.info(
                        "Lauf %s Rezepte Rohantwort: %s",
                        lauf_id,
                        json.dumps(daten, ensure_ascii=False),
                    )
                    by_id = {r.id: r for r in teil}
                    for e in daten.get("rezepte") or []:
                        r = by_id.pop(str(e.get("id")), None) if isinstance(e, dict) else None
                        if r is None:
                            continue
                        aenderung = diff(
                            rezept_ist(r), rezept_werte(e), umfang.get("nur_leere", True)
                        )
                        for feld, (alt, neu) in aenderung.items():
                            s.add(
                                Vorschlag(
                                    lauf_id=lauf_id,
                                    typ="rezept",
                                    objekt_id=r.id,
                                    name=r.name,
                                    feld=feld,
                                    alt=alt,
                                    neu=neu,
                                )
                            )
                            anzahl += 1
            lauf.status = "fertig"
            s.commit()
            if ha is not None and anzahl:
                try:
                    await ha.notify(
                        "Kategorisierung fertig",
                        f"{anzahl} Vorschläge warten auf Prüfung (Tab Produkte → Vorschläge).",
                        st.notify_service,
                    )
                except Exception as err:  # noqa: BLE001
                    log.warning("Hinweis fehlgeschlagen: %s", err)
        except Exception as err:  # noqa: BLE001 - Fehler am Lauf festhalten
            log.exception("Kategorisierung %s fehlgeschlagen", lauf_id)
            s.rollback()
            lauf = s.get(Lauf, lauf_id)
            lauf.status, lauf.fehler = "fehler", str(err) or type(err).__name__
            s.commit()


async def _naehrwerte_lauf(s: Session, ki, b, lauf_id: int, umfang: dict) -> int:
    """KI schätzt Nährwerte je 100 g; nur als Vorschlag (Feld `naehrwerte`) mit Review."""
    vorhanden = naehrwerte.alle(s)
    produkte = [p for p in await b.list_products() if p.category != REST_KATEGORIE]
    if ids := umfang.get("produkt_ids"):
        produkte = [p for p in produkte if p.id in ids]
    if umfang.get("nur_leere", True):
        produkte = [p for p in produkte if p.id not in vorhanden]
    anzahl = 0
    for i in range(0, len(produkte), BATCH):
        teil = produkte[i : i + BATCH]
        daten = await ki.generate(
            "naehrwerte",
            "rationPlaner Nährwerte",
            ANWEISUNG_NAEHRWERTE.format(liste="\n".join(f"{p.id}|{p.name}" for p in teil)),
            STRUKTUR_NAEHRWERTE,
        )
        log.info("Lauf %s Nährwerte Rohantwort: %s", lauf_id, json.dumps(daten, ensure_ascii=False))
        by_id = {p.id: p for p in teil}
        for e in daten.get("produkte") or []:
            p = by_id.pop(str(e.get("id")), None) if isinstance(e, dict) else None
            if p is None:
                continue
            neu = naehrwerte.werte_pruefen(e)
            if neu["kcal"] is None:
                continue  # ohne Energie unbrauchbar
            alt = naehrwerte.als_dict(vorhanden.get(p.id))
            if alt:
                alt.pop("quelle", None)
            if alt == neu:
                continue
            s.add(
                Vorschlag(
                    lauf_id=lauf_id,
                    typ="produkt",
                    objekt_id=p.id,
                    name=p.name,
                    feld="naehrwerte",
                    alt=alt,
                    neu=neu,
                )
            )
            anzahl += 1
    return anzahl


class LaufIn(BaseModel):
    produkte: bool = True
    rezepte: bool = True
    naehrwerte: bool = False  # Nährwerte je 100 g schätzen lassen (eigener KI-Aufruf je Batch)
    nur_ohne_attribute: bool = True  # False: alle (monatlicher Lauf)
    nur_leere: bool = True  # False: auch gesetzte Werte überprüfen lassen
    produkt_ids: list[str] | None = None
    packungen: bool = False  # Packungsgrößen analysieren (Bon-Historie, Angebote, KI-Wissen)
    gruppen: bool = False  # austauschbare Varianten gruppieren (Fusilli ↔ Spaghetti unter Nudeln)
    monate: int = Field(default=6, ge=1, le=36)  # Zeitraum für den Standard (häufigste Größe)


@router.post("/kategorisieren", status_code=202)
async def kategorisieren(
    data: LaufIn,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
) -> dict:
    """Startet einen Lauf im Hintergrund. Ergebnis: Vorschläge zur Prüfung, nichts wird gesetzt."""
    if data.produkte or data.rezepte or data.gruppen:
        request.app.state.ki.pruefen("kategorisierung")
    if data.naehrwerte:
        request.app.state.ki.pruefen("naehrwerte")
    if data.packungen:
        request.app.state.ki.pruefen("packungen")
    if s.scalars(select(Lauf).where(Lauf.status == "laeuft")).first():
        raise HTTPException(409, "Es läuft schon eine Kategorisierung")
    lauf = Lauf(umfang=data.model_dump())
    s.add(lauf)
    s.commit()
    hintergrund.add_task(ausfuehren, request.app, lauf.id)
    return {"lauf_id": lauf.id, "status": lauf.status}


@router.get("/kategorisieren")
async def laeufe(s: Session = Depends(session)) -> list[dict]:
    rows = s.scalars(select(Lauf).order_by(Lauf.id.desc()).limit(10))
    return [
        {
            "id": r.id,
            "status": r.status,
            "fehler": r.fehler,
            "angelegt": r.angelegt,
            "umfang": r.umfang,
        }
        for r in rows
    ]


@router.get("/vorschlaege")
async def vorschlaege(status: str = "offen", s: Session = Depends(session)) -> list[dict]:
    rows = s.scalars(
        select(Vorschlag).where(Vorschlag.status == status).order_by(Vorschlag.typ, Vorschlag.name)
    )
    return [
        {
            "id": v.id,
            "lauf_id": v.lauf_id,
            "typ": v.typ,
            "objekt_id": v.objekt_id,
            "name": v.name,
            "feld": v.feld,
            "alt": v.alt,
            "neu": v.neu,
        }
        for v in rows
    ]


class EntscheidungIn(BaseModel):
    uebernehmen: list[int] = Field(default_factory=list)
    ablehnen: list[int] = Field(default_factory=list)


def _produkt_patch(felder: dict[str, object]) -> ProductPatch:
    due = None
    if "ablauf_typ" in felder:
        due = DueType.VERBRAUCHSDATUM if felder["ablauf_typ"] == "verbrauchsdatum" else DueType.MHD
    return ProductPatch(
        tracking_mode=TrackingMode(felder["tracking_modus"])
        if "tracking_modus" in felder
        else None,
        category=felder.get("kategorie"),  # type: ignore[arg-type]
        due_type=due,
        shelf_life_days=felder.get("haltbarkeit_tage"),  # type: ignore[arg-type]
        shelf_life_opened_days=felder.get("haltbarkeit_offen_tage"),  # type: ignore[arg-type]
        shelf_life_frozen_days=felder.get("haltbarkeit_gefroren_tage"),  # type: ignore[arg-type]
        shelf_life_thawed_days=felder.get("haltbarkeit_aufgetaut_tage"),  # type: ignore[arg-type]
        grams_per_unit=felder.get("g_je_einheit"),  # type: ignore[arg-type]
        tolerance_pct=felder.get("toleranz_pct"),  # type: ignore[arg-type]
    )


@router.post("/vorschlaege")
async def entscheiden(
    data: EntscheidungIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Übernimmt bzw. verwirft Vorschläge. Je Objekt ein Schreibvorgang."""
    gewaehlt = {
        v.id: v
        for v in s.scalars(select(Vorschlag).where(Vorschlag.id.in_(data.uebernehmen)))
        if v.status == "offen"
    }
    je_objekt: dict[tuple[str, str], dict[str, object]] = {}
    fehler = []
    neue_eltern: dict[str, str] = {}  # Name → angelegtes Parent-Produkt (einmal je Gruppe)
    for v in sorted(gewaehlt.values(), key=lambda v: v.feld != "variante_von"):
        if v.feld == "variante_von":
            try:
                await _variante_von_uebernehmen(b, v, neue_eltern)
            except Exception as err:  # noqa: BLE001
                fehler.append(f"{v.name}: {err}")
                v.status = "fehler"
            continue
        if v.feld == "variante":  # je Größe ein Vorschlag, eigener Schreibvorgang in der App-DB
            try:
                _variante_uebernehmen(s, v)
            except Exception as err:  # noqa: BLE001
                fehler.append(f"{v.name}: {err}")
                v.status = "fehler"
            continue
        je_objekt.setdefault((v.typ, v.objekt_id), {})[v.feld] = v.neu
    for (typ, oid), felder in je_objekt.items():
        try:
            if typ == "produkt":
                packung = felder.pop("packung", None)
                nw = felder.pop("naehrwerte", None)
                if isinstance(nw, dict):
                    naehrwerte.speichern(s, oid, naehrwerte.werte_pruefen(nw), "ki")
                if felder:
                    await b.update_product(oid, _produkt_patch(felder))
                if isinstance(packung, dict):
                    await b.set_pack(oid, str(packung["einheit"]), float(packung["menge"]))
            else:
                await b.update_recipe(
                    oid,
                    RecipePatch(
                        tags=felder.get("tags"),  # type: ignore[arg-type]
                        effort=felder.get("aufwand"),  # type: ignore[arg-type]
                        leftover_days=felder.get("rest_haltbarkeit_tage"),  # type: ignore[arg-type]
                    ),
                )
        except Exception as err:  # noqa: BLE001 - je Objekt weitermachen, Fehler melden
            fehler.append(f"{typ} {oid}: {err}")
            for v in gewaehlt.values():
                if (v.typ, v.objekt_id) == (typ, oid):
                    v.status = "fehler"
    for v in gewaehlt.values():
        if v.status == "offen":
            v.status = "uebernommen"
    for v in s.scalars(select(Vorschlag).where(Vorschlag.id.in_(data.ablehnen))):
        if v.status == "offen":
            v.status = "abgelehnt"  # unbestätigte Bon-Variante bleibt unbestätigt (zählt weiter)
    s.commit()
    return {
        "uebernommen": sum(1 for v in gewaehlt.values() if v.status == "uebernommen"),
        "abgelehnt": len(data.ablehnen),
        "fehler": fehler,
    }


# --- Packungsgrößen ---------------------------------------------------------------------------

ANWEISUNG_PACKUNGEN = """Du kennst das Sortiment deutscher Supermärkte (Rewe, Edeka, Aldi, Lidl,
Kaufland, dm). Nenne je Produkt die Packungsgrößen, die es dort üblicherweise gibt.
Produkte (id|name|bestandseinheit|gekauft laut Bons: Größe×Anzahl|bekannte Größen|Angebote):
{liste}

Felder je Produkt:
- groessen: 1 bis 4 gängige Größen. einheit = Packungsart (Packung, Dose, Glas, Flasche, Netz,
  Becher, Stück), menge = Inhalt in der Bestandseinheit des Produkts (z. B. 500 für 500 g),
  marke nur, wenn die Größe typisch für eine bestimmte Marke ist, sonst leer.
- typisch: die Größe, die ein Zwei-Personen-Haushalt am häufigsten kauft (Menge in
  Bestandseinheit).
Gekaufte Größen aus den Bons sind Fakten, nimm sie auf. Gib für jedes Produkt genau einen Eintrag
mit seiner id zurück."""

STRUKTUR_PACKUNGEN = {
    "produkte": _feld(
        {
            "object": {
                "multiple": True,
                "fields": {
                    "id": _feld({"text": {}}),
                    "groessen": _feld(
                        {
                            "object": {
                                "multiple": True,
                                "fields": {
                                    "einheit": _feld({"text": {}}),
                                    "menge": _feld({"number": {}}),
                                    "marke": _feld({"text": {}}, required=False),
                                },
                            }
                        }
                    ),
                    "typisch": _feld({"number": {}}),
                },
            }
        }
    )
}


def _variante_uebernehmen(s: Session, v: Vorschlag) -> None:
    neu = v.neu if isinstance(v.neu, dict) else {}
    pid = neu.get("packung_id")
    p = s.get(Packung, int(pid)) if pid else None
    if p is None:
        menge = float(neu["menge"])
        p = next(
            (
                x
                for x in s.scalars(select(Packung).where(Packung.produkt_id == v.objekt_id))
                if abs(x.menge - menge) < kueche.EPS
            ),
            None,
        ) or Packung(produkt_id=v.objekt_id, menge=menge, quelle=str(neu.get("quelle") or "llm"))
        s.add(p)
    p.einheit = str(neu.get("einheit") or p.einheit or "Packung")[:40]
    p.marke = (str(neu.get("marke") or "").strip() or p.marke) or None
    p.bestaetigt = True


async def _packungen_lauf(s: Session, ki, b, lauf_id: int, umfang: dict) -> int:
    """Standard = häufigste Größe laut Bons im Zeitraum (Code). KI ergänzt gängige Größen.
    Alles landet als Vorschlag (Diff-Review), nichts wird gesetzt."""
    monate = int(umfang.get("monate") or 6)
    kaeufe = packungen.kaeufe_je_groesse(s, packungen.seit_monaten(monate))
    varianten: dict[str, list[Packung]] = {}
    for pk in s.scalars(select(Packung)):
        varianten.setdefault(pk.produkt_id, []).append(pk)
    angebote: dict[str, set[float]] = {}
    for pr in s.scalars(select(Preis).where(Preis.quelle == "angebot")):
        if pr.menge:
            angebote.setdefault(pr.produkt_id, set()).add(round(pr.menge, 3))
    produkte = [p for p in await b.list_products() if p.category != REST_KATEGORIE]
    if ids := umfang.get("produkt_ids"):
        produkte = [p for p in produkte if p.id in ids]
    offen = {
        (v.objekt_id, v.feld, json.dumps(v.neu, sort_keys=True))
        for v in s.scalars(select(Vorschlag).where(Vorschlag.status == "offen"))
    }
    anzahl = 0

    def vorschlagen(p: Product, feld: str, alt: object, neu: dict) -> None:
        nonlocal anzahl
        key = (p.id, feld, json.dumps(neu, sort_keys=True))
        if key in offen:
            return
        offen.add(key)
        s.add(Vorschlag(lauf_id=lauf_id, typ="produkt", objekt_id=p.id, name=p.name, feld=feld,
                        alt=alt, neu=neu))  # fmt: skip
        anzahl += 1

    def fmt(x: float) -> str:
        return f"{x:g}"

    for i in range(0, len(produkte), BATCH):
        teil = produkte[i : i + BATCH]
        zeilen = []
        for p in teil:
            gekauft = ", ".join(f"{fmt(g)}×{fmt(n)}" for g, n in kaeufe.get(p.id, {}).items())
            bekannt = [fmt(p.pack_size)] if p.pack_size else []
            bekannt += [fmt(v.menge) for v in varianten.get(p.id, []) if v.bestaetigt is not False]
            zeilen.append(
                f"{p.id}|{p.name}|{p.unit}|{gekauft or '-'}|{', '.join(bekannt) or '-'}|"
                + (", ".join(fmt(x) for x in sorted(angebote.get(p.id, ()))) or "-")
            )
        daten = await ki.generate(
            "packungen",
            "rationPlaner Packungsgrößen",
            ANWEISUNG_PACKUNGEN.format(liste="\n".join(zeilen)),
            STRUKTUR_PACKUNGEN,
        )
        log.info("Lauf %s Packungen Rohantwort: %s", lauf_id, json.dumps(daten, ensure_ascii=False))
        by_id = {p.id: p for p in teil}
        ki_je: dict[str, dict] = {}
        for e in daten.get("produkte") or []:
            if isinstance(e, dict) and str(e.get("id")) in by_id:
                ki_je[str(e["id"])] = e
        for p in teil:
            e = ki_je.get(p.id, {})
            bekannt = [p.pack_size] if p.pack_size else []
            bekannt += [v.menge for v in varianten.get(p.id, [])]
            # 1. Standard: häufigste gekaufte Größe (Code), sonst KI-typisch nur ohne Standard
            standard = packungen.haeufigste(kaeufe.get(p.id, {}))
            grund = f"häufigste Größe der letzten {monate} Monate laut Bons"
            if standard is None and not p.pack_size:
                standard, grund = _float(e.get("typisch"), 0.001, 100000), "KI: typische Größe"
            if standard and not (p.pack_size and abs(p.pack_size - standard) < kueche.EPS):
                einheit = next(
                    (v.einheit for v in varianten.get(p.id, [])
                     if abs(v.menge - standard) < kueche.EPS and v.einheit != "Packung"),
                    p.pack_unit or "Packung",
                )  # fmt: skip
                alt = {"einheit": p.pack_unit, "menge": p.pack_size} if p.pack_size else None
                vorschlagen(p, "packung", alt, {"einheit": einheit, "menge": standard,
                                                "grund": grund})  # fmt: skip
            # 2. gängige Größen, die noch nicht bekannt sind → neue Variante
            for g in (e.get("groessen") or [])[:4]:
                if not isinstance(g, dict):
                    continue
                menge = _float(g.get("menge"), 0.001, 100000)
                if menge is None or any(abs(menge - x) < kueche.EPS for x in bekannt):
                    continue
                bekannt.append(menge)
                vorschlagen(
                    p,
                    "variante",
                    None,
                    {
                        "einheit": str(g.get("einheit") or "Packung").strip()[:40] or "Packung",
                        "menge": menge,
                        "marke": str(g.get("marke") or "").strip()[:60] or None,
                        "quelle": "llm",
                    },
                )
        s.commit()
    return anzahl


# --- Austauschgruppen (Variante von) ------------------------------------------------------

ANWEISUNG_GRUPPEN = """Du ordnest einen Haushaltsvorrat. Finde Gruppen von Produkten, die sich in
Rezepten gegenseitig ersetzen können, ohne dass das Gericht ein anderes wird:
z. B. Spaghetti, Fusilli, Penne → "Nudeln"; Basmati- und Jasminreis → "Reis";
rote und gelbe Paprika → "Paprika"; Gouda und Emmentaler gerieben → "Reibekäse".
Nicht gruppieren, was sich im Rezept anders verhält (Lasagneplatten ≠ Spaghetti,
Vollmilch ≠ Sahne, Weißmehl ≠ Speisestärke) oder eine andere Einheit hat.

Produkte (id|name|einheit|kategorie|gehört schon zu):
{liste}

Felder je Gruppe:
- name: kurzer Oberbegriff im Singular oder üblichen Plural ("Nudeln", "Reis").
- parent_id: id eines Produkts aus der Liste, das genau der Oberbegriff ist (z. B. ein Produkt
  "Nudeln"), sonst leer, dann wird ein neues Oberprodukt angelegt.
- mitglieder: ids der austauschbaren Produkte (mindestens 2, ohne parent_id).
Nur Gruppen, bei denen du sicher bist. Keine Gruppe ist auch eine gültige Antwort."""

STRUKTUR_GRUPPEN = {
    "gruppen": _feld(
        {
            "object": {
                "multiple": True,
                "fields": {
                    "name": _feld({"text": {}}),
                    "parent_id": _feld({"text": {}}, required=False),
                    "mitglieder": _feld({"text": {"multiple": True}}),
                },
            }
        }
    )
}
GRUPPEN_BATCH = 80


async def _gruppen_lauf(s: Session, ki, b, lauf_id: int, umfang: dict) -> int:
    """KI schlägt Austauschgruppen vor; Code prüft Einheit, Ebenen und Doppelungen.
    Ergebnis: Vorschläge „variante_von“ je Produkt (Review), nichts wird gesetzt."""
    produkte = {p.id: p for p in await b.list_products() if p.category != REST_KATEGORIE}
    if ids := umfang.get("produkt_ids"):
        auswahl = [p for p in produkte.values() if p.id in ids or p.parent_id in ids]
    else:
        auswahl = list(produkte.values())
    eltern = {p.parent_id for p in produkte.values() if p.parent_id}
    offen = {
        v.objekt_id
        for v in s.scalars(
            select(Vorschlag).where(Vorschlag.status == "offen", Vorschlag.feld == "variante_von")
        )
    }
    auswahl.sort(key=lambda p: ((p.category or "~"), p.name.lower()))
    anzahl = 0
    for i in range(0, len(auswahl), GRUPPEN_BATCH):
        teil = auswahl[i : i + GRUPPEN_BATCH]
        liste = "\n".join(
            f"{p.id}|{p.name}|{p.unit}|{p.category or '-'}|"
            f"{produkte[p.parent_id].name if p.parent_id in produkte else '-'}"
            for p in teil
        )
        daten = await ki.generate(
            "kategorisierung",
            "rationPlaner Austauschgruppen",
            ANWEISUNG_GRUPPEN.format(liste=liste),
            STRUKTUR_GRUPPEN,
        )
        log.info("Lauf %s Gruppen Rohantwort: %s", lauf_id, json.dumps(daten, ensure_ascii=False))
        for g in daten.get("gruppen") or []:
            if not isinstance(g, dict):
                continue
            name = str(g.get("name") or "").strip()[:60]
            parent_id = str(g.get("parent_id") or "").strip() or None
            parent = produkte.get(parent_id) if parent_id else None
            if parent is not None and parent.parent_id:  # Grocy kennt nur eine Ebene
                parent = None
            if parent is None and not name:
                continue
            mitglieder = [
                produkte[m]
                for m in dict.fromkeys(str(x) for x in (g.get("mitglieder") or []))
                if m in produkte and m != (parent.id if parent else None)
            ]
            einheit = (parent.unit if parent else None) or (
                mitglieder[0].unit if mitglieder else ""
            )
            mitglieder = [
                m for m in mitglieder
                if m.unit.strip().lower() == einheit.strip().lower()
                and m.id not in eltern  # hat selbst Varianten → bleibt Oberprodukt
                and m.id not in offen
            ]  # fmt: skip
            if len(mitglieder) + (1 if parent else 0) < 2 or not mitglieder:
                continue
            for m in mitglieder:
                if parent and m.parent_id == parent.id:
                    continue  # schon richtig zugeordnet
                alt = produkte[m.parent_id].name if m.parent_id in produkte else None
                neu = {"parent_id": parent.id, "name": parent.name} if parent else {
                    "parent_id": None, "name": name, "einheit": einheit}  # fmt: skip
                s.add(Vorschlag(lauf_id=lauf_id, typ="produkt", objekt_id=m.id, name=m.name,
                                feld="variante_von", alt=alt, neu=neu))  # fmt: skip
                offen.add(m.id)
                anzahl += 1
        s.commit()
    return anzahl


async def _variante_von_uebernehmen(b, v: Vorschlag, neue_eltern: dict[str, str]) -> None:
    neu = v.neu if isinstance(v.neu, dict) else {}
    pid = neu.get("parent_id")
    if not pid:
        name = str(neu.get("name") or "").strip()
        if not name:
            raise ValueError("Gruppe ohne Namen")
        if name not in neue_eltern:
            neue_eltern[name] = (await b.create_product(name, str(neu.get("einheit") or "g"))).id
        pid = neue_eltern[name]
    if pid == v.objekt_id:
        return
    await b.update_product(v.objekt_id, ProductPatch(parent_id=str(pid)))
