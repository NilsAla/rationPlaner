"""Produktdatenbank, Rezeptbuch und Dubletten-Check (Web-App, Bridge, MCP)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import fields, replace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import dubletten, fotos, kueche, naehrwerte
from app.db import (
    BonAlias,
    DubletteEntscheidung,
    Kochvorgang,
    Packung,
    PlanEintrag,
    Preis,
    ProduktMerge,
    RezeptBewertung,
    RezeptMeta,
    ZutatAlias,
)
from app.deps import backend, session, settings
from app.inventory.base import (
    DueType,
    Ingredient,
    InventoryBackend,
    Product,
    ProductPatch,
    RecipePatch,
    TrackingMode,
)
from app.rezept_import import alias_key, quelle_link, quelle_von
from app.settings import Settings

log = logging.getLogger("ration.katalog")
router = APIRouter(prefix="/api/v1")

FUELLSTAENDE = ("voll", "knapp", "leer")
REZEPT_STATUS = ("getestet", "ungetestet")
AUFWAND = ("schnell", "normal", "aufwaendig")


# --- Hilfen -------------------------------------------------------------------


async def bestand_je_produkt(b: InventoryBackend, produkte: list[Product]) -> dict[str, float]:
    """Bestand je Produkt, übergeordnete Produkte inklusive ihrer Varianten."""
    menge: dict[str, float] = defaultdict(float)
    for batch in await b.list_all_batches():
        menge[batch.product_id] += batch.amount
    eltern = {p.id: p.parent_id for p in produkte}
    for pid, m in list(menge.items()):
        if parent := eltern.get(pid):
            menge[parent] += m
    return dict(menge)


def ersatz(s: Session, produkte: dict[str, Product]) -> dict[str, list[str]]:
    """Austauschbare Geschwister je Produkt (Einstellung „ersatz“ sperrt einzelne Gruppen)."""
    from app import einstellungen

    gesperrt = set(einstellungen.lesen(s)["ersatz"]["gesperrt"])
    return kueche.ersatz_karte(produkte, gesperrt)


def ampel(
    p: Product,
    bestand: dict[str, float],
    bedarf: float,
    karte: dict[str, list[str]],
    produkte: dict[str, Product],
) -> tuple[str, list[str]]:
    """Ampel wie verfuegbar, aber austauschbare Geschwister zählen mit. → (Ampel, Ersatz-Namen)"""
    eigen = verfuegbar(p, bestand.get(p.id, 0.0), bedarf)
    if eigen == "ok" or not karte.get(p.id):
        return eigen, []
    da = [sid for sid in karte[p.id] if bestand.get(sid, 0.0) > kueche.EPS]
    if not da:
        return eigen, []
    gesamt = bestand.get(p.id, 0.0) + sum(bestand[sid] for sid in da)
    mit = verfuegbar(p, gesamt, bedarf)
    if mit == eigen:
        return eigen, []
    return mit, [produkte[sid].name for sid in da]


def verfuegbar(p: Product, bestand: float, bedarf: float) -> str:
    """Ampel je Zutat: ok / knapp / fehlt. Füllstand und Immer-da nach Modus."""
    if p.tracking_mode == TrackingMode.IMMER_DA:
        return "ok"
    if p.tracking_mode == TrackingMode.FUELLSTAND:
        return {"leer": "fehlt", "knapp": "knapp"}.get(p.fill_level or "", "ok")
    if bestand + kueche.EPS >= bedarf:
        return "ok"
    return "knapp" if bestand > 0 else "fehlt"


def produkt_dict(p: Product) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "einheit": p.unit,
        "parent_id": p.parent_id,
        "tracking_modus": p.tracking_mode,
        "kategorie": p.category,
        "fuellstand": p.fill_level,
        "toleranz_pct": p.tolerance_pct,
        "g_je_einheit": p.grams_per_unit,
        "mindestbestand": p.min_stock,
        "ablauf_typ": "verbrauchsdatum" if p.due_type == DueType.VERBRAUCHSDATUM else "mhd",
        "haltbarkeit_tage": p.shelf_life_days,
        "haltbarkeit_offen_tage": p.shelf_life_opened_days,
        "haltbarkeit_gefroren_tage": p.shelf_life_frozen_days,
        "haltbarkeit_aufgetaut_tage": p.shelf_life_thawed_days,
        "lagerort_id": p.default_location_id,
        "packung": {"einheit": p.pack_unit, "menge": p.pack_size} if p.pack_size else None,
    }


# --- Produktdatenbank -----------------------------------------------------------


@router.get("/katalog/produkte")
async def katalog_produkte(
    s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> list[dict]:
    """Alle Produkte mit Attributen, Bestand, Zahl der Aliase, Packungen und Rezepte."""
    produkte = await b.list_products()
    bestand = await bestand_je_produkt(b, produkte)
    aliase: dict[str, int] = defaultdict(int)
    for a in s.scalars(select(ZutatAlias)):
        aliase[a.produkt_id] += 1
    packungen: dict[str, int] = defaultdict(int)
    for pk in s.scalars(select(Packung)):
        packungen[pk.produkt_id] += 1
    rezepte: dict[str, int] = defaultdict(int)
    for r in await b.list_recipes():
        for pid in {z.product_id for z in r.ingredients}:
            rezepte[pid] += 1
    return [
        {
            **produkt_dict(p),
            "bestand": round(bestand.get(p.id, 0.0), 2),
            "aliase": aliase[p.id],
            "weitere_packungen": packungen[p.id],
            "rezepte": rezepte[p.id],
            "attribute_fehlen": p.tracking_mode is None,
        }
        for p in sorted(produkte, key=lambda p: p.name.lower())
    ]


@router.get("/produkte/{produkt_id}")
async def produkt_detail(
    produkt_id: str, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    p = await b.get_product(produkt_id)
    produkte = await b.list_products()
    bestand = await bestand_je_produkt(b, produkte)
    rezepte = [
        {
            "id": r.id,
            "name": r.name,
            "menge": sum(z.amount for z in r.ingredients if z.product_id == p.id),
        }
        for r in await b.list_recipes()
        if any(z.product_id == p.id for z in r.ingredients)
    ]
    return {
        **produkt_dict(p),
        "bestand": round(bestand.get(p.id, 0.0), 2),
        "varianten": [{"id": x.id, "name": x.name} for x in produkte if x.parent_id == p.id],
        "aliase": sorted(
            a.text for a in s.scalars(select(ZutatAlias).where(ZutatAlias.produkt_id == p.id))
        ),
        "weitere_packungen": [
            {
                "id": x.id,
                "einheit": x.einheit,
                "menge": x.menge,
                "quelle": x.quelle,
                "gueltig_bis": x.gueltig_bis,
            }
            for x in s.scalars(select(Packung).where(Packung.produkt_id == p.id))
        ],
        "rezepte": sorted(rezepte, key=lambda r: r["name"].lower()),
    }


class ProduktPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    tracking_modus: TrackingMode | None = None
    kategorie: str | None = Field(default=None, max_length=60)
    fuellstand: str | None = None
    toleranz_pct: int | None = Field(default=None, ge=0, le=100)
    g_je_einheit: float | None = Field(default=None, gt=0, le=100000)
    mindestbestand: float | None = Field(default=None, ge=0)
    ablauf_typ: str | None = None  # mhd / verbrauchsdatum
    haltbarkeit_tage: int | None = Field(default=None, ge=-1, le=3650)
    haltbarkeit_offen_tage: int | None = Field(default=None, ge=0, le=3650)
    haltbarkeit_gefroren_tage: int | None = Field(default=None, ge=0, le=3650)
    haltbarkeit_aufgetaut_tage: int | None = Field(default=None, ge=0, le=3650)
    lagerort_id: str | None = None
    parent_id: str | None = None  # "" = keine Variante mehr

    def patch(self) -> ProductPatch:
        if self.fuellstand is not None and self.fuellstand not in FUELLSTAENDE:
            raise HTTPException(422, f"fuellstand muss {'/'.join(FUELLSTAENDE)} sein")
        if self.ablauf_typ not in (None, "mhd", "verbrauchsdatum"):
            raise HTTPException(422, "ablauf_typ muss mhd oder verbrauchsdatum sein")
        due = None
        if self.ablauf_typ:
            due = DueType.VERBRAUCHSDATUM if self.ablauf_typ == "verbrauchsdatum" else DueType.MHD
        return ProductPatch(
            name=self.name,
            tracking_mode=self.tracking_modus,
            category=self.kategorie,
            fill_level=self.fuellstand,
            tolerance_pct=self.toleranz_pct,
            grams_per_unit=self.g_je_einheit,
            min_stock=self.mindestbestand,
            due_type=due,
            shelf_life_days=self.haltbarkeit_tage,
            shelf_life_opened_days=self.haltbarkeit_offen_tage,
            shelf_life_frozen_days=self.haltbarkeit_gefroren_tage,
            shelf_life_thawed_days=self.haltbarkeit_aufgetaut_tage,
            default_location_id=self.lagerort_id,
            parent_id=self.parent_id,
        )


@router.patch("/produkte/{produkt_id}")
async def produkt_aendern(
    produkt_id: str, data: ProduktPatchIn, b: InventoryBackend = Depends(backend)
) -> dict:
    if data.parent_id == produkt_id:
        raise HTTPException(422, "Produkt kann nicht Variante von sich selbst sein")
    if data.parent_id:
        await b.get_product(data.parent_id)
    p = await b.update_product(produkt_id, data.patch())
    log.info("Produkt %s geändert: %s", produkt_id, data.model_dump(exclude_none=True))
    return produkt_dict(p)


class AliasIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    produkt_id: str


@router.put("/aliase")
async def alias_setzen(
    data: AliasIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Legt einen Zutaten-Alias an oder hängt ihn auf ein anderes Produkt um."""
    await b.get_product(data.produkt_id)
    key = alias_key(data.text)
    s.merge(ZutatAlias(text=key, produkt_id=data.produkt_id))
    s.commit()
    return {"text": key, "produkt_id": data.produkt_id}


@router.delete("/aliase/{text}", status_code=204)
async def alias_loeschen(text: str, s: Session = Depends(session)) -> None:
    if a := s.get(ZutatAlias, text):
        s.delete(a)
        s.commit()


# --- Rezeptbuch -------------------------------------------------------------------


@router.get("/rezeptbuch")
async def rezeptbuch(
    s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> list[dict]:
    """Alle Rezepte mit Tags, Status, Aufwand, Quelle und wie viele Zutaten fehlen."""
    produkte = {p.id: p for p in await b.list_products()}
    bestand = await bestand_je_produkt(b, list(produkte.values()))
    meta = {m.rezept_id: m for m in s.scalars(select(RezeptMeta))}
    verlauf = kochverlauf(s)
    meinung = {x.rezept_id: x for x in s.scalars(select(RezeptBewertung))}
    nw, ziel = naehrwerte.alle(s), naehrwerte.ziel_aus(s)
    bilder = fotos.hauptfotos(s)
    karte = ersatz(s, produkte)
    result = []
    for r in await b.list_recipes():
        n = naehrwerte.rezept_naehrwerte(r, produkte, nw, ziel=ziel)
        fehlend = [
            produkte[z.product_id].name
            for z in r.ingredients
            if z.product_id in produkte
            and ampel(produkte[z.product_id], bestand, z.amount, karte, produkte)[0] != "ok"
        ]
        m = meta.get(r.id)
        result.append(
            {
                "id": r.id,
                "name": r.name,
                "portionen": r.servings,
                "tags": list(r.tags),
                "status": r.status,
                "aufwand": r.effort,
                "zutaten": len(r.ingredients),
                "fehlend": fehlend,
                "kochbar": not fehlend and bool(r.ingredients),
                "quelle": m.quelle if m else None,
                "url": m.url if m else None,
                "foto": bilder.get(r.id),
                "kcal": n.je_portion["kcal"],
                "protein": n.je_portion["protein"],
                "nutri": n.nutri_score["klasse"] if n.nutri_score else None,
                "naehrwert_abdeckung": n.abdeckung,
                **verlauf.get(r.id, {"gekocht": 0, "zuletzt": None}),
                **_meinung(meinung.get(r.id)),
            }
        )
    return sorted(result, key=lambda r: r["name"].lower())


def kochverlauf(s: Session) -> dict[str, dict]:
    """Je Rezept: wie oft gekocht, wann zuletzt."""
    out: dict[str, dict] = {}
    for k in s.scalars(select(Kochvorgang)):
        e = out.setdefault(k.rezept_id, {"gekocht": 0, "zuletzt": None})
        e["gekocht"] += 1
        if e["zuletzt"] is None or k.datum > e["zuletzt"]:
            e["zuletzt"] = k.datum
    return out


def _meinung(m: RezeptBewertung | None) -> dict:
    return {
        "bewertung": m.bewertung if m else 0,
        "favorit": bool(m.favorit) if m else False,
        "notiz": m.notiz if m else None,
    }


@router.get("/rezepte/{rezept_id}")
async def rezept_detail(
    rezept_id: str,
    portionen: int | None = None,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Rezeptleser: Zutaten auf `portionen` skaliert, mit Bestandsampel."""
    r = await b.get_recipe(rezept_id)
    produkte = {p.id: p for p in await b.list_products()}
    bestand = await bestand_je_produkt(b, list(produkte.values()))
    ziel = portionen or r.servings
    faktor = ziel / max(r.servings, 1)
    m = s.get(RezeptMeta, r.id)
    karte = ersatz(s, produkte)
    zutaten = []
    for z in r.ingredients:
        p = produkte.get(z.product_id)
        menge = z.amount * faktor
        a, statt = ampel(p, bestand, menge, karte, produkte) if p else ("fehlt", [])
        zutaten.append(
            {
                "produkt_id": z.product_id,
                "name": p.name if p else f"? ({z.product_id})",
                "menge": round(menge, 1),
                "menge_basis": z.amount,
                "einheit": p.unit if p else "",
                "notiz": z.note,
                "bestand": round(bestand.get(z.product_id, 0.0), 1),
                "ampel": a,
                "ersatz": statt,  # z. B. ["Fusilli"], wenn erst der Ersatz reicht
            }
        )
    return {
        "id": r.id,
        "name": r.name,
        "portionen": ziel,
        "portionen_basis": r.servings,
        "tags": list(r.tags),
        "status": r.status,
        "aufwand": r.effort,
        "rest_haltbarkeit_tage": r.leftover_days,
        "zutaten": zutaten,
        "schritte": [
            kueche.mengen_skalieren(t, faktor, [z["name"] for z in zutaten])
            for t in kueche.schritte(r.description)
        ],
        "quelle": m.quelle if m else None,
        "url": m.url if m else None,
        "naehrwerte": naehrwerte.rezept_dict(r, produkte, s, ziel),
        "fotos": [fotos.als_dict(f) for f in fotos.fotos_von(s, r.id)],
        **_meinung(s.get(RezeptBewertung, r.id)),
        "verlauf": [
            {"id": k.id, "datum": k.datum, "portionen": k.portionen, "bewertung": k.bewertung}
            for k in s.scalars(
                select(Kochvorgang)
                .where(Kochvorgang.rezept_id == r.id)
                .order_by(Kochvorgang.datum.desc())
                .limit(20)
            )
        ],
    }


class RezeptZutatIn(BaseModel):
    produkt_id: str
    menge: float = Field(ge=0)  # in Bestandseinheit, bezogen auf portionen des Rezepts
    notiz: str | None = Field(default=None, max_length=200)


class RezeptPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    portionen: int | None = Field(default=None, ge=1, le=24)
    schritte: list[str] | None = None
    zutaten: list[RezeptZutatIn] | None = None
    tags: list[str] | None = None
    status: str | None = None
    aufwand: str | None = None
    rest_haltbarkeit_tage: int | None = Field(default=None, ge=0, le=30)
    # Herkunft: "" löscht, sonst z. B. manuell/hellofresh/chefkoch.de; url optional
    quelle: str | None = Field(default=None, max_length=60)
    url: str | None = Field(default=None, max_length=500)


def quelle_setzen(s: Session, rezept_id: str, quelle: str | None, url: str | None) -> None:
    """Herkunft in RezeptMeta schreiben; quelle_id (Import-Dubletten) bleibt unberührt."""
    m = s.get(RezeptMeta, rezept_id) or RezeptMeta(rezept_id=rezept_id)
    if quelle is not None:
        m.quelle = quelle.strip().lower() or None
    if url is not None:
        url = url.strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(422, "url muss mit http:// oder https:// beginnen")
        m.url = url or None
        if url and quelle is None and not m.quelle:
            m.quelle = quelle_von(url)[0]
    s.merge(m)
    s.commit()


@router.patch("/rezepte/{rezept_id}")
async def rezept_aendern(
    rezept_id: str,
    data: RezeptPatchIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    if data.status not in (None, *REZEPT_STATUS):
        raise HTTPException(422, f"status muss {'/'.join(REZEPT_STATUS)} sein")
    if data.aufwand not in (None, *AUFWAND):
        raise HTTPException(422, f"aufwand muss {'/'.join(AUFWAND)} sein")
    zutaten = None
    if data.zutaten is not None:
        for z in data.zutaten:
            await b.get_product(z.produkt_id)
        zutaten = [Ingredient(z.produkt_id, z.menge, z.notiz) for z in data.zutaten]
    if data.quelle is not None or data.url is not None:
        await b.get_recipe(rezept_id)
        quelle_setzen(s, rezept_id, data.quelle, data.url)
    beschreibung = None
    if data.schritte is not None:
        beschreibung = kueche.beschreibung(data.schritte)
        if (m := s.get(RezeptMeta, rezept_id)) and m.url:
            beschreibung += quelle_link(m.url, m.quelle or "Quelle")
    r = await b.update_recipe(
        rezept_id,
        RecipePatch(
            name=data.name,
            servings=data.portionen,
            description=beschreibung,
            ingredients=zutaten,
            tags=data.tags,
            status=data.status,
            effort=data.aufwand,
            leftover_days=data.rest_haltbarkeit_tage,
        ),
    )
    log.info("Rezept %s geändert", rezept_id)
    return await rezept_detail(r.id, None, s, b)


# --- Dubletten -----------------------------------------------------------------------


def _ignoriert(s: Session) -> set[tuple[str, str]]:
    return {(d.produkt_a, d.produkt_b) for d in s.scalars(select(DubletteEntscheidung))}


@router.get("/dubletten")
async def dubletten_liste(
    schwelle: float | None = None,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> list[dict]:
    """Produktpaare mit ähnlichem Namen, ohne als „nicht mergen“ markierte."""
    grenze = schwelle if schwelle is not None else st.dubletten_schwelle
    produkte = await b.list_products()
    bestand = await bestand_je_produkt(b, produkte)
    return [
        {
            "a": {**produkt_dict(k.a), "bestand": bestand.get(k.a.id, 0.0)},
            "b": {**produkt_dict(k.b), "bestand": bestand.get(k.b.id, 0.0)},
            "score": k.score,
            "grund": k.grund,
        }
        for k in dubletten.kandidaten(produkte, grenze, _ignoriert(s))
    ]


class MergeIn(BaseModel):
    behalten: str
    entfernen: str


@router.post("/dubletten/mergen")
async def dubletten_mergen(
    data: MergeIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Führt `entfernen` in `behalten` zusammen. Löscht `entfernen` in Grocy.

    Der Aufruf ist die Bestätigung des Nutzers (Button „Mergen“); nie automatisch auslösen.
    """
    if data.behalten == data.entfernen:
        raise HTTPException(422, "behalten und entfernen sind gleich")
    keep = await b.get_product(data.behalten)
    remove = await b.get_product(data.entfernen)
    await b.merge_products(keep.id, remove.id)
    # App-Daten umhängen: Aliase, weitere Packungen (nur bei gleicher Einheit sinnvoll).
    aliase = 0
    for a in s.scalars(select(ZutatAlias).where(ZutatAlias.produkt_id == remove.id)):
        a.produkt_id = keep.id
        aliase += 1
    for pk in s.scalars(select(Packung).where(Packung.produkt_id == remove.id)):
        if keep.unit == remove.unit:
            pk.produkt_id = keep.id
        else:
            s.delete(pk)
    if remove.pack_size and not keep.pack_size and keep.unit == remove.unit and remove.pack_unit:
        await b.set_pack(keep.id, remove.pack_unit, remove.pack_size)
    s.merge(ProduktMerge(alt_id=remove.id, neu_id=keep.id, alt_name=remove.name))
    s.commit()
    log.info("Merge: %s (%s) → %s (%s)", remove.name, remove.id, keep.name, keep.id)
    return {"behalten": keep.name, "entfernt": remove.name, "aliase_umgehaengt": aliase}


@router.post("/dubletten/ignorieren", status_code=201)
async def dubletten_ignorieren(data: MergeIn, s: Session = Depends(session)) -> dict:
    """„Nicht mergen“: Paar wird nie wieder vorgeschlagen."""
    a, b_ = dubletten.paar(data.behalten, data.entfernen)
    s.merge(DubletteEntscheidung(produkt_a=a, produkt_b=b_))
    s.commit()
    return {"produkt_a": a, "produkt_b": b_}


# --- Anlegen, Löschen, Massenaktionen -----------------------------------------------------


class ProduktNeuIn(ProduktPatchIn):
    name: str = Field(min_length=1, max_length=120)
    einheit: str = Field(default="g", min_length=1, max_length=30)


@router.post("/produkte", status_code=201)
async def produkt_anlegen(data: ProduktNeuIn, b: InventoryBackend = Depends(backend)) -> dict:
    """Neues Produkt; existiert der Name schon, kommt 409 statt einer Dublette."""
    name = data.name.strip()
    if any(p.name.lower() == name.lower() for p in await b.list_products()):
        raise HTTPException(409, f"„{name}“ gibt es schon")
    if data.parent_id:
        await b.get_product(data.parent_id)
    p = await b.create_product(name, data.einheit.strip())
    rest = replace(data.patch(), name=None)
    if any(getattr(rest, f.name) is not None for f in fields(rest)):
        p = await b.update_product(p.id, rest)
    return produkt_dict(p)


async def loeschen_pruefen(b: InventoryBackend, produkt_id: str) -> str | None:
    """Grund, warum ein Produkt nicht gelöscht werden darf, sonst None.

    Grocy löscht beim Produkt Bestand und Rezeptzutaten mit, das verhindern wir hier.
    """
    produkte = await b.list_products()
    if not any(p.id == produkt_id for p in produkte):
        return "gibt es nicht"
    if (menge := await b.get_stock_amount(produkt_id)) > 0:
        return f"hat noch Bestand ({kueche.menge(menge)})"
    if kinder := [p.name for p in produkte if p.parent_id == produkt_id]:
        return f"hat Varianten: {', '.join(kinder[:3])}"
    if rezepte := [
        r.name
        for r in await b.list_recipes()
        if any(z.product_id == produkt_id for z in r.ingredients)
    ]:
        return f"steckt in Rezepten: {', '.join(rezepte[:3])}"
    return None


def _app_daten_entfernen(s: Session, produkt_id: str) -> None:
    for modell in (ZutatAlias, BonAlias, Packung, Preis):
        for row in s.scalars(select(modell).where(modell.produkt_id == produkt_id)):
            s.delete(row)


@router.delete("/produkte/{produkt_id}")
async def produkt_loeschen(
    produkt_id: str, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    if grund := await loeschen_pruefen(b, produkt_id):
        raise HTTPException(404 if grund == "gibt es nicht" else 409, f"Nicht löschbar: {grund}")
    await b.delete_product(produkt_id)
    _app_daten_entfernen(s, produkt_id)
    s.commit()
    log.info("Produkt %s gelöscht", produkt_id)
    return {"geloescht": produkt_id}


class ProduktAktionIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    aktion: Literal["loeschen", "setzen"]
    werte: ProduktPatchIn | None = None  # für setzen: nur gesetzte Felder werden geändert


@router.post("/produkte/aktion")
async def produkte_aktion(
    data: ProduktAktionIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """Massenaktion. Jedes Produkt einzeln, Fehler stoppen die anderen nicht."""
    ok, fehler = [], []
    if data.aktion == "setzen":
        if data.werte is None or not data.werte.model_dump(exclude_none=True):
            raise HTTPException(422, "werte fehlen")
        if data.werte.name is not None:
            raise HTTPException(422, "Name lässt sich nicht für mehrere setzen")
        patch = data.werte.patch()
        if data.werte.parent_id:
            await b.get_product(data.werte.parent_id)
    for pid in data.ids:
        try:
            if data.aktion == "loeschen":
                if grund := await loeschen_pruefen(b, pid):
                    fehler.append({"id": pid, "fehler": grund})
                    continue
                await b.delete_product(pid)
                _app_daten_entfernen(s, pid)
            else:
                if data.werte and data.werte.parent_id == pid:
                    fehler.append({"id": pid, "fehler": "kann nicht Variante von sich sein"})
                    continue
                await b.update_product(pid, patch)
            ok.append(pid)
        except Exception as err:  # noqa: BLE001 - je Produkt melden, weitermachen
            fehler.append({"id": pid, "fehler": str(err) or type(err).__name__})
    s.commit()
    return {"ok": ok, "fehler": fehler}


class RezeptNeuIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    portionen: int = Field(default=2, ge=1, le=24)
    schritte: list[str] = Field(default_factory=list)
    zutaten: list[RezeptZutatIn] = Field(default_factory=list)
    tags: list[str] | None = None
    status: str | None = "ungetestet"
    aufwand: str | None = None
    quelle: str | None = Field(default="manuell", max_length=60)
    url: str | None = Field(default=None, max_length=500)


@router.post("/rezepte", status_code=201)
async def rezept_anlegen(
    data: RezeptNeuIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    if data.status not in (None, *REZEPT_STATUS):
        raise HTTPException(422, f"status muss {'/'.join(REZEPT_STATUS)} sein")
    if data.aufwand not in (None, *AUFWAND):
        raise HTTPException(422, f"aufwand muss {'/'.join(AUFWAND)} sein")
    for z in data.zutaten:
        await b.get_product(z.produkt_id)
    r = await b.create_recipe(
        data.name.strip(),
        data.portionen,
        kueche.beschreibung(data.schritte),
        [Ingredient(z.produkt_id, z.menge, z.notiz) for z in data.zutaten],
    )
    if data.tags or data.status or data.aufwand:
        r = await b.update_recipe(
            r.id, RecipePatch(tags=data.tags, status=data.status, effort=data.aufwand)
        )
    quelle_setzen(s, r.id, data.quelle or "manuell", data.url)
    return {"id": r.id, "name": r.name}


def _rezept_app_daten_entfernen(s: Session, rezept_id: str, st: Settings) -> None:
    fotos.rezept_fotos_entfernen(s, st, rezept_id)
    for modell in (RezeptMeta, RezeptBewertung):
        if row := s.get(modell, rezept_id):
            s.delete(row)
    for e in s.scalars(
        select(PlanEintrag).where(
            PlanEintrag.rezept_id == rezept_id, PlanEintrag.status == "geplant"
        )
    ):
        s.delete(e)


@router.delete("/rezepte/{rezept_id}")
async def rezept_loeschen(
    rezept_id: str,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> dict:
    await b.get_recipe(rezept_id)
    await b.delete_recipe(rezept_id)
    _rezept_app_daten_entfernen(s, rezept_id, st)
    s.commit()
    log.info("Rezept %s gelöscht", rezept_id)
    return {"geloescht": rezept_id}


class RezeptAktionIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    aktion: Literal["loeschen", "tag_hinzu", "tag_weg", "status", "aufwand", "favorit"]
    wert: str | bool | None = None


@router.post("/rezepte/aktion")
async def rezepte_aktion(
    data: RezeptAktionIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> dict:
    if data.aktion == "status" and data.wert not in REZEPT_STATUS:
        raise HTTPException(422, f"status muss {'/'.join(REZEPT_STATUS)} sein")
    if data.aktion == "aufwand" and data.wert not in AUFWAND:
        raise HTTPException(422, f"aufwand muss {'/'.join(AUFWAND)} sein")
    if data.aktion in ("tag_hinzu", "tag_weg") and not (
        isinstance(data.wert, str) and data.wert.strip()
    ):
        raise HTTPException(422, "wert (Tag) fehlt")
    ok, fehler = [], []
    for rid in data.ids:
        try:
            r = await b.get_recipe(rid)
            if data.aktion == "loeschen":
                await b.delete_recipe(rid)
                _rezept_app_daten_entfernen(s, rid, st)
            elif data.aktion == "favorit":
                m = s.get(RezeptBewertung, rid) or RezeptBewertung(rezept_id=rid)
                m.favorit = bool(data.wert)
                s.add(m)
            elif data.aktion == "tag_hinzu":
                tag = str(data.wert).strip()
                if tag not in r.tags:
                    await b.update_recipe(rid, RecipePatch(tags=[*r.tags, tag]))
            elif data.aktion == "tag_weg":
                await b.update_recipe(rid, RecipePatch(tags=[t for t in r.tags if t != data.wert]))
            elif data.aktion == "status":
                await b.update_recipe(rid, RecipePatch(status=str(data.wert)))
            else:
                await b.update_recipe(rid, RecipePatch(effort=str(data.wert)))
            ok.append(rid)
        except Exception as err:  # noqa: BLE001 - je Rezept melden, weitermachen
            fehler.append({"id": rid, "fehler": str(err) or type(err).__name__})
    s.commit()
    return {"ok": ok, "fehler": fehler}


class MeinungIn(BaseModel):
    bewertung: Literal[-1, 0, 1] | None = None
    favorit: bool | None = None
    notiz: str | None = Field(default=None, max_length=500)


@router.put("/rezepte/{rezept_id}/meinung")
async def meinung_setzen(
    rezept_id: str,
    data: MeinungIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Daumen, Favorit, Notiz. Fließt in den Planer (Lieblinge ↔ Neues)."""
    await b.get_recipe(rezept_id)
    m = s.get(RezeptBewertung, rezept_id) or RezeptBewertung(rezept_id=rezept_id)
    if data.bewertung is not None:
        m.bewertung = data.bewertung
    if data.favorit is not None:
        m.favorit = data.favorit
    if data.notiz is not None:
        m.notiz = data.notiz.strip() or None
    s.add(m)
    s.commit()
    return _meinung(m)


class KochBewertungIn(BaseModel):
    bewertung: Literal[-1, 0, 1]


@router.put("/kochvorgaenge/{vorgang_id}/bewertung")
async def kochvorgang_bewerten(
    vorgang_id: int, data: KochBewertungIn, s: Session = Depends(session)
) -> dict:
    """Daumen nach dem Kochen; gilt auch als aktuelle Meinung zum Rezept."""
    k = s.get(Kochvorgang, vorgang_id)
    if k is None:
        raise HTTPException(404, "Kochvorgang nicht gefunden")
    k.bewertung = data.bewertung
    m = s.get(RezeptBewertung, k.rezept_id) or RezeptBewertung(rezept_id=k.rezept_id)
    m.bewertung = data.bewertung
    s.add(m)
    s.commit()
    return {"id": k.id, "rezept_id": k.rezept_id, "bewertung": k.bewertung}
