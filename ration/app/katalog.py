"""Produktdatenbank, Rezeptbuch und Dubletten-Check (Web-App, Bridge, MCP)."""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import dubletten, kueche
from app.db import DubletteEntscheidung, Packung, ProduktMerge, RezeptMeta, ZutatAlias
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
from app.rezept_import import alias_key, quelle_link
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
    result = []
    for r in await b.list_recipes():
        fehlend = [
            produkte[z.product_id].name
            for z in r.ingredients
            if z.product_id in produkte
            and verfuegbar(produkte[z.product_id], bestand.get(z.product_id, 0), z.amount) != "ok"
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
            }
        )
    return sorted(result, key=lambda r: r["name"].lower())


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
    zutaten = []
    for z in r.ingredients:
        p = produkte.get(z.product_id)
        menge = z.amount * faktor
        zutaten.append(
            {
                "produkt_id": z.product_id,
                "name": p.name if p else f"? ({z.product_id})",
                "menge": round(menge, 1),
                "menge_basis": z.amount,
                "einheit": p.unit if p else "",
                "notiz": z.note,
                "bestand": round(bestand.get(z.product_id, 0.0), 1),
                "ampel": verfuegbar(p, bestand.get(z.product_id, 0.0), menge) if p else "fehlt",
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
        "schritte": kueche.schritte(r.description),
        "quelle": m.quelle if m else None,
        "url": m.url if m else None,
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
