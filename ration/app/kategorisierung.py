"""Kategorisierungslauf: LLM schlägt Attribute für Produkte und Rezepte vor, Nutzer prüft den Diff.

Nichts wird automatisch übernommen (Konzept, Grundregel 4). Das LLM liefert je Objekt Werte,
der Code validiert Bereiche und Enums und legt nur echte Änderungen als Vorschlag ab.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
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

BATCH = 25
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

ANWEISUNG_PRODUKTE = """Du pflegst die Stammdaten eines Haushaltsvorrats in Deutschland.
Schlage für jedes Produkt Attribute vor. Produkte (id|name|bestandseinheit):
{liste}

Felder je Produkt:
- tracking_modus: "exakt" (verderblich, Ablauf je Charge wichtig: Frischgemüse, Fleisch, Milch),
  "menge" (lange haltbar, Menge zählt: Nudeln, Reis, Dosen, Zwiebeln, Kartoffeln),
  "fuellstand" (angebrochen, nur voll/knapp/leer: Mehl, Zucker, Öl, Essig, Senf, Sojasauce),
  "immer_da" (Kleinkram, nie tracken: Salz, Pfeffer, getrocknete Gewürze, Wasser).
- kategorie: eine aus {kategorien}.
- ablauf_typ: "verbrauchsdatum" nur bei leicht verderblichem Fleisch/Fisch/Hack, sonst "mhd".
- haltbarkeit_tage: typische Haltbarkeit ungeöffnet ab Kauf (-1 = unbegrenzt).
- haltbarkeit_offen_tage: nach dem Öffnen (0 = egal).
- haltbarkeit_gefroren_tage: eingefroren (0 = nicht einfrieren).
- haltbarkeit_aufgetaut_tage: nach dem Auftauen (0 = egal).
- g_je_einheit: Gramm je Bestandseinheit (bei Bestandseinheit g: 1).
- toleranz_pct: wie viel Prozent ein Rezept mehr oder weniger nehmen darf (5 bis 50).
- packung_einheit/packung_menge: übliche Packung im deutschen Supermarkt in Bestandseinheit
  (z. B. Packung 500 für Nudeln in g, Dose 400 für Kidneybohnen in g, Stück 160 für Paprika).
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


def produkt_werte(e: dict, p: Product) -> dict[str, object]:
    """Gültige Vorschläge eines Produkts, je Feld. Ungültiges fällt weg."""
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
    if einheit and menge and menge > 1:
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
    ha = app.state.ha
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
                    daten = await ha.ai_task(
                        st.ai_task_entity,
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
                    for e in daten.get("produkte") or []:
                        p = by_id.pop(str(e.get("id")), None) if isinstance(e, dict) else None
                        if p is None:
                            continue
                        aenderung = diff(
                            produkt_ist(p), produkt_werte(e, p), umfang.get("nur_leere", True)
                        )
                        for feld, (alt, neu) in aenderung.items():
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
                    daten = await ha.ai_task(
                        st.ai_task_entity,
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


class LaufIn(BaseModel):
    produkte: bool = True
    rezepte: bool = True
    nur_ohne_attribute: bool = True  # False: alle (monatlicher Lauf)
    nur_leere: bool = True  # False: auch gesetzte Werte überprüfen lassen
    produkt_ids: list[str] | None = None


@router.post("/kategorisieren", status_code=202)
async def kategorisieren(
    data: LaufIn,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
) -> dict:
    """Startet einen Lauf im Hintergrund. Ergebnis: Vorschläge zur Prüfung, nichts wird gesetzt."""
    if not st.ai_task_entity or request.app.state.ha is None:
        raise HTTPException(503, "Option ai_task_entity fehlt oder kein Zugriff auf HA")
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
    for v in gewaehlt.values():
        je_objekt.setdefault((v.typ, v.objekt_id), {})[v.feld] = v.neu
    fehler = []
    for (typ, oid), felder in je_objekt.items():
        try:
            if typ == "produkt":
                packung = felder.pop("packung", None)
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
            v.status = "abgelehnt"
    s.commit()
    return {
        "uebernommen": sum(1 for v in gewaehlt.values() if v.status == "uebernommen"),
        "abgelehnt": len(data.ablehnen),
        "fehler": fehler,
    }
