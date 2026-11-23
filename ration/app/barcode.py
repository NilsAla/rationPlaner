"""Barcode-Scan zum Einbuchen von Vorhandenem: EAN → Produkt (Grocy-Barcodes, Varianten) oder
Open Food Facts → Vorschlag → Review → Einbuchen.

Open Food Facts (OFF) ist frei und ohne Key. Antworten werden in der App-DB gecacht. Packungsgröße
parst der Code aus `quantity` (gleicher Parser wie beim Bon). Nährwerte aus OFF werden nie direkt
gesetzt, sondern als Vorschlag (KI-Vorschläge-Seite) abgelegt.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import __version__, kueche, naehrwerte
from app.bon import groesse_aus_text
from app.db import Base, Packung
from app.deps import backend, session
from app.inventory.base import (
    InventoryBackend,
    NotFoundError,
    Product,
    ProductPatch,
    TrackingMode,
)
from app.rezept_import import _norm

log = logging.getLogger("ration.barcode")
router = APIRouter(prefix="/api/v1")

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{ean}.json"
OFF_FELDER = (
    "product_name,product_name_de,generic_name_de,brands,quantity,categories_tags,nutriments,"
    "image_front_small_url"
)
OFF_CACHE_TAGE = 30
EAN = re.compile(r"^(\d{8}|\d{12,14})$")
# OFF-Nährwerte je 100 g → unsere Felder
OFF_NAEHRWERTE = {
    "kcal": "energy-kcal_100g", "protein": "proteins_100g", "fett": "fat_100g",
    "fett_gesaettigt": "saturated-fat_100g", "kohlenhydrate": "carbohydrates_100g",
    "zucker": "sugars_100g", "ballaststoffe": "fiber_100g", "salz": "salt_100g",
}  # fmt: skip


# OFF-Kategorie (Teilwort des Tags) → Katalog-Kategorie, für „ist das dein …?“
OFF_KATEGORIEN = [
    ("pasta", "Nudeln & Reis"), ("rice", "Nudeln & Reis"), ("legume", "Hülsenfrüchte"),
    ("bean", "Hülsenfrüchte"), ("lentil", "Hülsenfrüchte"), ("chickpea", "Hülsenfrüchte"),
    ("canned", "Konserven"), ("vegetable", "Gemüse"), ("fruit", "Obst"), ("cheese", "Käse"),
    ("milk", "Milchprodukte"), ("dair", "Milchprodukte"), ("yogurt", "Milchprodukte"),
    ("egg", "Eier"), ("bread", "Brot"), ("meat", "Fleisch"), ("fish", "Fisch"),
    ("seafood", "Fisch"), ("flour", "Mehl & Backen"), ("baking", "Mehl & Backen"),
    ("oil", "Öl & Essig"), ("vinegar", "Öl & Essig"), ("spice", "Gewürze"), ("herb", "Kräuter"),
    ("sauce", "Saucen"), ("nut", "Nüsse & Saaten"), ("seed", "Nüsse & Saaten"),
    ("sweet", "Süßes"), ("chocolate", "Süßes"), ("snack", "Süßes"), ("beverage", "Getränke"),
    ("water", "Getränke"), ("frozen", "Tiefkühl"),
]  # fmt: skip


def katalog_kategorien(off_tags: list[str]) -> list[str]:
    """Katalog-Kategorien, spezifischste zuerst (OFF-Tags gehen von allgemein nach speziell)."""
    out: list[str] = []
    for tag in reversed(off_tags):
        for teil, kat in OFF_KATEGORIEN:
            if teil in tag and kat not in out:
                out.append(kat)
    return out


class OffCache(Base):
    __tablename__ = "off_cache"

    ean: Mapped[str] = mapped_column(String, primary_key=True)
    daten: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # None = bei OFF unbekannt
    zeit: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def ean_pruefen(ean: str) -> str:
    ean = re.sub(r"\D", "", ean)
    if not EAN.match(ean):
        raise HTTPException(422, "Barcode: 8, 12, 13 oder 14 Ziffern")
    return ean


def off_auswerten(p: dict) -> dict:
    """OFF-Produkt → Name, Marke, Größe (g/ml/Stück je Packung), Nährwerte je 100 g."""
    name = p.get("product_name_de") or p.get("product_name") or p.get("generic_name_de") or ""
    name = name.strip()
    groesse = groesse_aus_text(str(p.get("quantity") or ""))
    nut = p.get("nutriments") or {}
    werte = {}
    for feld, key in OFF_NAEHRWERTE.items():
        try:
            werte[feld] = round(float(nut[key]), 2) if nut.get(key) not in (None, "") else None
        except (TypeError, ValueError):
            werte[feld] = None
    return {
        "name": name[:80],
        "marke": (str(p.get("brands") or "").split(",")[0].strip() or None),
        "quantity": p.get("quantity"),
        "menge": groesse[0] if groesse else None,
        "einheit": groesse[1] if groesse else None,
        "kategorien": [c.split(":", 1)[-1] for c in (p.get("categories_tags") or [])][:12],
        "naehrwerte": werte if werte.get("kcal") is not None else None,
        "bild": p.get("image_front_small_url"),
    }


async def off_abfragen(request: Request, s: Session, ean: str) -> dict | None:
    c = s.get(OffCache, ean)
    if c and c.zeit > datetime.now() - timedelta(days=OFF_CACHE_TAGE):
        return c.daten
    http: httpx.AsyncClient | None = getattr(request.app.state, "off_http", None)
    eigen = http is None
    http = http or httpx.AsyncClient(timeout=10.0)
    try:
        r = await http.get(
            OFF_URL.format(ean=ean),
            params={"fields": OFF_FELDER},
            headers={"User-Agent": f"rationPlaner/{__version__} (github.com/NilsAla/rationPlaner)"},
        )
        if r.status_code == 404:
            daten = None
        else:
            r.raise_for_status()
            j = r.json()
            daten = off_auswerten(j["product"]) if j.get("status") == 1 else None
    except (httpx.HTTPError, ValueError, KeyError) as err:
        log.warning("Open Food Facts %s: %s", ean, err)
        raise HTTPException(502, f"Open Food Facts nicht erreichbar: {err}") from err
    finally:
        if eigen:
            await http.aclose()
    s.merge(OffCache(ean=ean, daten=daten, zeit=datetime.now()))
    s.commit()
    return daten


def aehnliche(
    name: str, produkte: list[Product], kategorien: list[str] | None = None, n: int = 4
) -> list[dict]:
    """Katalogprodukte mit ähnlichem Namen oder gleicher Kategorie (für „ist das dein …?“)."""
    ziel = _norm(name)
    kategorien = kategorien or []
    wertung = []
    for p in produkte:
        pn = _norm(p.name)
        if not pn:
            continue
        r = SequenceMatcher(None, ziel, pn).ratio() if ziel else 0.0
        if ziel and (pn in ziel or any(w in ziel.split() for w in pn.split() if len(w) >= 4)):
            r = max(r, 0.75)
        if p.category in kategorien:  # spezifischere OFF-Kategorie zählt mehr
            r = max(r, 0.7 - 0.05 * kategorien.index(p.category))
        wertung.append((r, p))
    wertung.sort(key=lambda x: -x[0])
    return [{"produkt_id": p.id, "name": p.name, "einheit": p.unit, "score": round(r, 2)}
            for r, p in wertung[:n] if r >= 0.5]  # fmt: skip


def produkt_dict(p: Product) -> dict:
    packung = {"einheit": p.pack_unit, "menge": p.pack_size} if p.pack_size else None
    return {"produkt_id": p.id, "name": p.name, "einheit": p.unit, "packung": packung}


def in_bestandseinheit(p: Product, menge: float | None, einheit: str | None) -> float | None:
    """Packungsinhalt (g/ml/Stück) in die Bestandseinheit des Produkts."""
    if not menge or not einheit:
        return None
    unit = p.unit.strip().lower()
    if einheit in ("g", "ml"):
        faktor = {"g": 1.0, "ml": 1.0, "kg": 1000.0, "l": 1000.0}.get(unit)
        if faktor:
            return menge / faktor
        return menge / p.grams_per_unit if p.grams_per_unit else None
    if einheit == "Stück" and unit in ("stück", "stk", "st"):
        return menge
    return None


# --- Endpunkte ------------------------------------------------------------------------------


@router.get("/barcode/{ean}")
async def nachschlagen(
    ean: str,
    request: Request,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Bekannt (Grocy-Barcode oder Variante mit EAN)? Sonst Open Food Facts + ähnliche Produkte."""
    ean = ean_pruefen(ean)
    variante = s.scalars(select(Packung).where(Packung.ean == ean)).first()
    p = await b.find_by_barcode(ean)
    if p is None and variante is not None:
        p = await b.get_product(variante.produkt_id)
    off = None
    if p is None or variante is None:
        try:
            off = await off_abfragen(request, s, ean)
        except HTTPException as err:
            if p is None:
                raise
            log.info("OFF für bekanntes Produkt nicht nötig: %s", err.detail)
    groesse = None
    if variante is not None:
        groesse = {"menge": variante.menge, "einheit": p.unit if p else None, "quelle": "variante",
                   "marke": variante.marke}  # fmt: skip
    elif p is not None and off and (m := in_bestandseinheit(p, off["menge"], off["einheit"])):
        groesse = {"menge": round(m, 3), "einheit": p.unit, "quelle": "off"}
    elif p is not None and p.pack_size:
        groesse = {"menge": p.pack_size, "einheit": p.unit, "quelle": "standard"}
    produkte = await b.list_products()
    return {
        "ean": ean,
        "bekannt": produkt_dict(p) if p else None,
        "groesse": groesse,
        "off": off,
        "vorschlaege": []
        if p or not off
        else aehnliche(off["name"], produkte, katalog_kategorien(off["kategorien"])),
    }


class BildIn(BaseModel):
    daten: str = Field(min_length=100, max_length=20_000_000)  # Base64 oder data:-URL


@router.post("/barcode/lesen")
async def bild_lesen(data: BildIn) -> dict:
    """Barcode aus einem Foto lesen (für Geräte ohne BarcodeDetector, z. B. iOS)."""
    try:
        from PIL import Image, ImageOps
        from pyzbar.pyzbar import ZBarSymbol, decode
    except ImportError as err:  # lokal ohne libzbar
        raise HTTPException(501, f"Barcode-Lesen nicht verfügbar: {err}") from err
    roh = data.daten.split(",", 1)[1] if data.daten.startswith("data:") else data.daten
    try:
        img = Image.open(io.BytesIO(base64.b64decode(roh, validate=True)))
        img = ImageOps.exif_transpose(img).convert("L")
    except (binascii.Error, ValueError, OSError) as err:
        raise HTTPException(422, "Kein gültiges Bild") from err
    img.thumbnail((2000, 2000))
    arten = [ZBarSymbol.EAN13, ZBarSymbol.EAN8, ZBarSymbol.UPCA, ZBarSymbol.UPCE]
    codes = [d.data.decode() for d in decode(img, symbols=arten)]
    if not codes:  # zweiter Versuch: kontrastreicher
        codes = [d.data.decode() for d in decode(ImageOps.autocontrast(img), symbols=arten)]
    if not codes:
        raise HTTPException(
            404, "Kein Barcode erkannt. Näher ran, scharf und gerade fotografieren."
        )
    return {"codes": list(dict.fromkeys(codes))}


class EintragIn(BaseModel):
    ean: str
    produkt_id: str | None = None  # vorhandenes Produkt
    neu_name: str | None = Field(default=None, max_length=60)  # oder neu anlegen
    einheit: str = Field(default="g", pattern="^(g|ml|Stück)$")  # Bestandseinheit für neu
    anzahl: float = Field(default=1, gt=0, le=100)
    packung_menge: float | None = Field(default=None, gt=0)  # je Packung, Bestandseinheit
    marke: str | None = Field(default=None, max_length=60)
    mhd: date | None = None
    lagerort_id: str | None = None
    naehrwerte: bool = True  # OFF-Nährwerte als Vorschlag ablegen, falls noch keine da


class EinbuchenIn(BaseModel):
    eintraege: list[EintragIn] = Field(min_length=1, max_length=100)


@router.post("/barcode/einbuchen")
async def einbuchen(
    data: EinbuchenIn,
    request: Request,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Bestätigte Scans einbuchen: Barcode merken, Größe als Variante, Bestand anlegen."""
    from app.kategorisierung import Vorschlag

    gebucht, neu, fehler = [], [], []
    for e in data.eintraege:
        ean = ean_pruefen(e.ean)
        try:
            if e.produkt_id:
                p = await b.get_product(e.produkt_id)
            elif e.neu_name and e.neu_name.strip():
                p = await b.create_product(e.neu_name.strip(), e.einheit)
                neu.append(p.name)
            else:
                raise ValueError("produkt_id oder neu_name fehlt")
            await b.add_barcode(p.id, ean)
            je = e.packung_menge
            if je:
                if not p.pack_size:
                    await b.set_pack(p.id, "Packung", je)
                    p = await b.get_product(p.id)
                v = next(
                    (x for x in s.scalars(select(Packung).where(Packung.produkt_id == p.id))
                     if abs(x.menge - je) < kueche.EPS),
                    None,
                )  # fmt: skip
                if v is None:
                    v = Packung(produkt_id=p.id, einheit="Packung", menge=je, quelle="scan",
                                bestaetigt=True, haeufigkeit=0)  # fmt: skip
                    s.add(v)
                v.ean = v.ean or ean
                v.marke = v.marke or e.marke
            menge = (je or 0) * e.anzahl if je else e.anzahl
            ort = e.lagerort_id or p.default_location_id
            if ort is None:
                orte = await b.list_locations()
                ort = next((o.id for o in orte if o.name == "Vorrat"), orte[0].id)
            if p.tracking_mode == TrackingMode.FUELLSTAND:
                await b.update_product(p.id, ProductPatch(fill_level="voll"))
            elif p.tracking_mode != TrackingMode.IMMER_DA:
                await b.add_stock(p.id, menge, e.mhd, ort, None)
            off = s.get(OffCache, ean)
            nw = off.daten.get("naehrwerte") if off and off.daten else None
            if e.naehrwerte and nw and p.id not in naehrwerte.alle(s):
                s.add(Vorschlag(lauf_id=0, typ="produkt", objekt_id=p.id, name=p.name,
                                feld="naehrwerte", alt=None,
                                neu={**nw, "obst_gemuese": False, "roh": False}))  # fmt: skip
            gebucht.append({"ean": ean, "produkt_id": p.id, "name": p.name, "menge": menge,
                            "einheit": p.unit})  # fmt: skip
        except (ValueError, NotFoundError) as err:
            fehler.append({"ean": ean, "fehler": str(err)})
    s.commit()
    log.info("Scan eingebucht: %s Einträge, %s neu, %s Fehler", len(gebucht), len(neu), len(fehler))
    return {"gebucht": gebucht, "neue_produkte": neu, "fehler": fehler}
