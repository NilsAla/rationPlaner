"""Kassenbon-Import: Foto → ai_task (Bild + Katalog) → Regeln, Alias, Validierung → Review → Buchen.

"LLM wählt aus, Code rechnet": Das LLM liest die Zeilen (Text, Anzahl, Preis, aufgedruckte
Packungsgröße) und schlägt Produkt-IDs vor. Mengen in Bestandseinheit rechnet der Code aus
Packungsgröße bzw. Standard-Packung des Produkts. Alias-Treffer (bestätigte Bon-Texte je Markt)
gehen vor. Gebucht wird erst nach Review; Alias-Treffer sind vorausgewählt.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import kueche
from app.db import BonAlias, BonImport, Packung, Preis
from app.deps import backend, session, settings
from app.inventory.base import InventoryBackend, Product, ProductPatch, TrackingMode
from app.rezept_import import _norm
from app.settings import Settings

log = logging.getLogger("ration.bon")
router = APIRouter(prefix="/api/v1")

BILDTYPEN = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_BYTES = 15 * 1024 * 1024
UNTERORDNER = Path("ration") / "bons"
MEDIA_SOURCE = "media-source://media_source/local"

# Regel-Vorverarbeitung: Zeilen, die nie Ware sind (vor jeder Zuordnung).
WEG = re.compile(
    r"(?i)\b(pfand|leergut|einweg|mehrweg|tüte|tuete|tragetasche|beutel\s*\d|rabatt|preisvorteil|"
    r"coupon|gutschein|summe|zwischensumme|gesamt|zu zahlen|bar\b|ec[- ]?karte|kartenzahlung|"
    r"mwst|ust|rückgeld|rueckgeld|payback|punkte)"
)
STUECK = {"stück", "stk", "st", "portion"}
MASSE_G = {"g": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0, "cl": 10.0}

ANWEISUNG = """Lies den Kassenbon auf dem Bild und ordne jede gekaufte Ware einem Produkt zu.

Katalog (id|name|einheit):
{katalog}

Regeln:
- Eine Zeile je Ware, in Bon-Reihenfolge. Pfand, Tüten, Rabatte, Summen und Zahlungsarten weglassen.
- text: der Bon-Text der Zeile, unverändert.
- anzahl: gekaufte Stückzahl (bei "2 x 1,29" → 2; bei Gewichtsware 1).
- preis: Gesamtpreis der Zeile in Euro.
- packung_menge/packung_einheit: nur wenn die Größe auf dem Bon steht ("500G", "1KG", "1,5L",
  Gewichtsware "0,734 kg"), sonst 0 und "".
- produkt_id: nur wenn ein Katalogprodukt genau diese Ware ist, sonst leer.
- neu_name: kurzer deutscher Produktname im Singular ohne Marke und Größe, wenn produkt_id leer.
- konfidenz: 0 bis 1, wie sicher die Zuordnung ist.
markt: Name des Geschäfts. datum: Kaufdatum als JJJJ-MM-TT. summe: Gesamtbetrag."""

STRUKTUR = {
    "markt": {"required": True, "selector": {"text": {}}},
    "datum": {"required": True, "selector": {"text": {}}},
    "summe": {"required": True, "selector": {"number": {}}},
    "positionen": {
        "required": True,
        "selector": {
            "object": {
                "multiple": True,
                "fields": {
                    "text": {"required": True, "selector": {"text": {}}},
                    "anzahl": {"required": True, "selector": {"number": {}}},
                    "preis": {"required": True, "selector": {"number": {}}},
                    "packung_menge": {"required": True, "selector": {"number": {}}},
                    "packung_einheit": {"required": True, "selector": {"text": {}}},
                    "produkt_id": {"selector": {"text": {}}},
                    "neu_name": {"selector": {"text": {}}},
                    "konfidenz": {"required": True, "selector": {"number": {}}},
                },
            }
        },
    },
}


@dataclass
class Position:
    index: int
    text: str
    anzahl: float
    preis: float | None
    packung_menge: float | None = None  # laut Bon, in packung_einheit
    packung_einheit: str | None = None
    aktion: str = "buchen"  # buchen / neu / weglassen
    produkt_id: str | None = None
    produkt: str | None = None
    neu_name: str | None = None
    menge: float | None = None  # in Bestandseinheit, vom Code gerechnet
    einheit: str | None = None
    quelle: str = "ki"  # alias / ki / neu / regel
    konfidenz: float = 0.0
    status: str = "pruefen"  # sicher / vorschlag / pruefen
    hinweis: str = ""
    vorausgewaehlt: bool = False
    packung_abweichend: bool = False  # Größe auf dem Bon ≠ bekannte Packungen
    alternativen: list[dict] = field(default_factory=list)


def norm_markt(markt: str | None) -> str:
    return _norm(markt or "").split(" ")[0] if markt else ""


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w,.%]+", " ", text.lower())).strip()


def _zahl(wert: object) -> float | None:
    try:
        f = float(wert)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if f == f and f >= 0 else None


def menge_berechnen(
    pos: Position, p: Product, weitere: list[kueche.Groesse] | None = None
) -> tuple[float | None, str, bool]:
    """Menge in Bestandseinheit, Hinweis, ob die Bon-Größe von bekannten Packungen abweicht."""
    unit = p.unit.strip().lower()
    anzahl = pos.anzahl or 1
    e = (pos.packung_einheit or "").strip().lower()
    if pos.packung_menge and e in MASSE_G:
        gramm = pos.packung_menge * MASSE_G[e]
        if unit in ("g", "ml"):
            bekannt = [g.menge for g in kueche.groessen(p, weitere)]
            abweichend = bool(bekannt) and all(abs(gramm - b) > kueche.EPS for b in bekannt)
            return anzahl * gramm, f"{anzahl:g} × {gramm:g} {unit} laut Bon", abweichend
        if p.grams_per_unit:
            return anzahl * gramm / p.grams_per_unit, "über g je Einheit umgerechnet", False
    if p.pack_size:
        return (
            anzahl * p.pack_size,
            f"{anzahl:g} × Standard-Packung {p.pack_size:g} {p.unit}",
            False,
        )
    if unit in STUECK:
        return anzahl, "", False
    return None, f"Menge in {p.unit} unbekannt", False


def auswerten(
    daten: dict,
    produkte: list[Product],
    aliase: dict[tuple[str, str], str],
    weitere: dict[str, list[kueche.Groesse]],
    schwelle: float,
) -> tuple[str | None, date | None, float | None, list[Position]]:
    """LLM-Antwort validieren und in Positionen übersetzen. Nichts wird geraten."""
    by_id = {p.id: p for p in produkte}
    markt = str(daten.get("markt") or "").strip() or None
    try:
        datum = date.fromisoformat(str(daten.get("datum") or "")[:10])
    except ValueError:
        datum = None
    summe = _zahl(daten.get("summe"))
    roh = daten.get("positionen") if isinstance(daten.get("positionen"), list) else []
    result = []
    for e in roh:
        if not isinstance(e, dict) or not str(e.get("text") or "").strip():
            continue
        text = str(e["text"]).strip()
        pos = Position(
            index=len(result),
            text=text,
            anzahl=_zahl(e.get("anzahl")) or 1,
            preis=_zahl(e.get("preis")),
            packung_menge=_zahl(e.get("packung_menge")) or None,
            packung_einheit=str(e.get("packung_einheit") or "").strip() or None,
            konfidenz=min(max(_zahl(e.get("konfidenz")) or 0.0, 0.0), 1.0),
        )
        if WEG.search(text):
            pos.aktion, pos.quelle, pos.status, pos.hinweis = (
                "weglassen",
                "regel",
                "sicher",
                "kein Artikel",
            )
            result.append(pos)
            continue
        pid = aliase.get((norm_markt(markt), norm_text(text))) or aliase.get(("", norm_text(text)))
        if pid in by_id:
            pos.quelle, pos.produkt_id = "alias", pid
        elif (llm_id := str(e.get("produkt_id") or "").strip()) in by_id:
            pos.produkt_id = llm_id
        if pos.produkt_id:
            p = by_id[pos.produkt_id]
            pos.produkt, pos.einheit = p.name, p.unit
            pos.menge, pos.hinweis, pos.packung_abweichend = menge_berechnen(
                pos, p, weitere.get(p.id)
            )
            if pos.menge is None:
                pos.status = "pruefen"
            elif pos.quelle == "alias":
                pos.status = "sicher"
            else:
                pos.status = "vorschlag"
            pos.vorausgewaehlt = pos.status == "sicher" or (
                pos.status == "vorschlag" and pos.konfidenz >= schwelle
            )
        else:
            name = str(e.get("neu_name") or "").strip()[:60]
            pos.aktion, pos.quelle, pos.neu_name = "neu", "neu", name or text.title()
            pos.einheit = "g"
            if pos.packung_menge and (pos.packung_einheit or "").lower() in MASSE_G:
                pos.menge = pos.anzahl * pos.packung_menge * MASSE_G[pos.packung_einheit.lower()]
                pos.status = "vorschlag"
            else:
                pos.hinweis = "Menge in g eintragen"
        result.append(pos)
    return markt, datum, summe, result


def anweisung(produkte: list[Product]) -> str:
    katalog = "\n".join(
        f"{p.id}|{p.name}|{p.unit}" for p in produkte if p.category != kueche.REST_KATEGORIE
    )
    return ANWEISUNG.format(katalog=katalog or "(leer)")


# --- Ablauf ---------------------------------------------------------------------------


def bon_dir(st: Settings) -> Path:
    return st.media_dir / UNTERORDNER


async def verarbeiten(app, bon_id: int) -> None:
    """Hintergrund: KI lesen lassen, auswerten, Review bereitstellen, Hinweis schicken."""
    st: Settings = app.state.settings
    ha = app.state.ha
    with Session(app.state.engine) as s:
        bon = s.get(BonImport, bon_id)
        if bon is None:
            return
        try:
            b: InventoryBackend = app.state.backend
            produkte = await b.list_products()
            endung = Path(bon.datei).suffix
            typ = next((t for t, e in BILDTYPEN.items() if e == endung), "image/jpeg")
            daten = await ha.ai_task(
                st.ai_task_entity,
                "rationPlaner Kassenbon",
                anweisung(produkte),
                STRUKTUR,
                attachments=[
                    {
                        "media_content_id": f"{MEDIA_SOURCE}/{UNTERORDNER}/{bon.datei}",
                        "media_content_type": typ,
                    }
                ],
            )
            log.info("Bon %s Rohantwort: %s", bon_id, json.dumps(daten, ensure_ascii=False))
            aliase = {(a.markt, a.text): a.produkt_id for a in s.scalars(select(BonAlias))}
            weitere = _weitere(s)
            markt, datum, summe, pos = auswerten(
                daten, produkte, aliase, weitere, st.bon_konfidenz_schwelle
            )
            bon.roh, bon.markt, bon.datum, bon.summe = daten, markt, datum, summe
            bon.positionen = [asdict(p) for p in pos]
            bon.status = "review"
            s.commit()
            offen = sum(1 for p in pos if p.aktion != "weglassen" and not p.vorausgewaehlt)
            await _hinweis(
                ha,
                st,
                "Kassenbon gelesen",
                f"{markt or 'Bon'}: {len(pos)} Zeilen, {offen} zu prüfen. Im Tab Bon bestätigen.",
            )
        except Exception as err:  # noqa: BLE001 - Fehler landen am Bon, nicht im Nichts
            log.exception("Bon %s fehlgeschlagen", bon_id)
            bon.status, bon.fehler = "fehler", str(err) or type(err).__name__
            s.commit()
            await _hinweis(ha, st, "Kassenbon fehlgeschlagen", bon.fehler)


async def _hinweis(ha, st: Settings, titel: str, text: str) -> None:
    if ha is None:
        return
    try:
        await ha.notify(titel, text, st.notify_service)
    except Exception as err:  # noqa: BLE001 - Hinweis ist optional
        log.warning("Hinweis fehlgeschlagen: %s", err)


def _weitere(s: Session) -> dict[str, list[kueche.Groesse]]:
    result: dict[str, list[kueche.Groesse]] = {}
    for p in s.scalars(select(Packung)):
        result.setdefault(p.produkt_id, []).append(kueche.Groesse(p.einheit, p.menge, p.quelle))
    return result


def _bon_dict(bon: BonImport) -> dict:
    return {
        "id": bon.id,
        "status": bon.status,
        "markt": bon.markt,
        "datum": bon.datum,
        "summe": bon.summe,
        "fehler": bon.fehler,
        "angelegt": bon.angelegt,
        "positionen": bon.positionen or [],
    }


@router.post("/bon", status_code=202)
async def bon_hochladen(
    datei: UploadFile,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
) -> dict:
    """Foto hochladen. Antwortet sofort, Verarbeitung läuft im Hintergrund."""
    if not st.ai_task_entity or request.app.state.ha is None:
        raise HTTPException(503, "Option ai_task_entity fehlt oder kein Zugriff auf HA")
    endung = BILDTYPEN.get(datei.content_type or "")
    if endung is None:
        raise HTTPException(415, "Nur JPEG, PNG oder WebP")
    inhalt = await datei.read(MAX_BYTES + 1)
    if len(inhalt) > MAX_BYTES:
        raise HTTPException(413, "Bild größer als 15 MB")
    return await _anlegen(inhalt, endung, request, hintergrund, s, st)


class BildIn(BaseModel):
    daten: str = Field(
        min_length=100, max_length=MAX_BYTES * 4 // 3 + 8
    )  # Base64, ohne data:-Präfix
    typ: str = "image/jpeg"


@router.post("/bon/bild", status_code=202)
async def bon_aus_base64(
    data: BildIn,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
) -> dict:
    """Bild als Base64 im JSON (Skripte, Automationen, Tests über den Ingress-Proxy)."""
    if not st.ai_task_entity or request.app.state.ha is None:
        raise HTTPException(503, "Option ai_task_entity fehlt oder kein Zugriff auf HA")
    endung = BILDTYPEN.get(data.typ)
    if endung is None:
        raise HTTPException(415, "Nur JPEG, PNG oder WebP")
    roh = data.daten.split(",", 1)[1] if data.daten.startswith("data:") else data.daten
    try:
        inhalt = base64.b64decode(roh, validate=True)
    except (binascii.Error, ValueError) as err:
        raise HTTPException(422, "daten ist kein gültiges Base64") from err
    if len(inhalt) > MAX_BYTES:
        raise HTTPException(413, "Bild größer als 15 MB")
    return await _anlegen(inhalt, endung, request, hintergrund, s, st)


class MediaIn(BaseModel):
    pfad: str = Field(min_length=1, max_length=300)  # relativ zu /media, z. B. "upload/bon.jpg"


@router.post("/bon/media", status_code=202)
async def bon_aus_media(
    data: MediaIn,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
) -> dict:
    """Bon aus einem Bild unter /media verarbeiten (z. B. per Automation aus der Companion-App)."""
    if not st.ai_task_entity or request.app.state.ha is None:
        raise HTTPException(503, "Option ai_task_entity fehlt oder kein Zugriff auf HA")
    quelle = (st.media_dir / data.pfad).resolve()
    if not quelle.is_relative_to(st.media_dir.resolve()) or not quelle.is_file():
        raise HTTPException(404, "Bild nicht gefunden")
    endung = quelle.suffix.lower().replace(".jpeg", ".jpg")
    if endung not in BILDTYPEN.values() or quelle.stat().st_size > MAX_BYTES:
        raise HTTPException(415, "Nur JPEG, PNG oder WebP bis 15 MB")
    return await _anlegen(quelle.read_bytes(), endung, request, hintergrund, s, st)


async def _anlegen(
    inhalt: bytes,
    endung: str,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session,
    st: Settings,
) -> dict:
    ordner = bon_dir(st)
    ordner.mkdir(parents=True, exist_ok=True)
    name = f"{date.today().isoformat()}-{uuid.uuid4().hex[:8]}{endung}"
    (ordner / name).write_bytes(inhalt)
    bon = BonImport(datei=name)
    s.add(bon)
    s.commit()
    hintergrund.add_task(verarbeiten, request.app, bon.id)
    log.info("Bon %s angelegt: %s (%s KB)", bon.id, name, len(inhalt) // 1024)
    return {"id": bon.id, "status": bon.status}


@router.get("/bon")
async def bon_liste(s: Session = Depends(session)) -> list[dict]:
    rows = s.scalars(select(BonImport).order_by(BonImport.id.desc()).limit(30))
    return [{k: v for k, v in _bon_dict(b).items() if k != "positionen"} for b in rows]


@router.get("/bon/{bon_id}")
async def bon_detail(bon_id: int, s: Session = Depends(session)) -> dict:
    bon = s.get(BonImport, bon_id)
    if bon is None:
        raise HTTPException(404, "Bon nicht gefunden")
    return _bon_dict(bon)


class BuchenPosIn(BaseModel):
    index: int = Field(ge=0)
    produkt_id: str | None = None
    neu_anlegen: str | None = Field(default=None, max_length=60)
    menge: float = Field(gt=0)  # Bestandseinheit
    preis: float | None = Field(default=None, ge=0)
    mhd: date | None = None
    lagerort_id: str | None = None


class BuchenIn(BaseModel):
    positionen: list[BuchenPosIn]  # nicht aufgeführte Zeilen werden nicht gebucht


@router.post("/bon/{bon_id}/buchen")
async def bon_buchen(
    bon_id: int,
    data: BuchenIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Bucht die bestätigten Zeilen ein, merkt Aliase, Preise und abweichende Packungsgrößen."""
    bon = s.get(BonImport, bon_id)
    if bon is None:
        raise HTTPException(404, "Bon nicht gefunden")
    if bon.status != "review":
        raise HTTPException(409, f"Bon ist im Status {bon.status}")
    roh = {p["index"]: p for p in bon.positionen or []}
    markt = norm_markt(bon.markt)
    gebucht, neu = [], []
    for z in data.positionen:
        pos = roh.get(z.index)
        if pos is None:
            raise HTTPException(422, f"Zeile {z.index} gibt es nicht")
        if z.neu_anlegen:
            p = await b.create_product(z.neu_anlegen.strip(), "g")
            neu.append(p.name)
        elif z.produkt_id:
            p = await b.get_product(z.produkt_id)
        else:
            raise HTTPException(422, f"Zeile {z.index}: produkt_id oder neu_anlegen fehlt")
        ort = z.lagerort_id or p.default_location_id
        if ort is None:
            orte = await b.list_locations()
            ort = next((o.id for o in orte if o.name == "Vorrat"), orte[0].id)
        einzelpreis = z.preis / z.menge if z.preis else None
        if p.tracking_mode == TrackingMode.FUELLSTAND:
            await b.update_product(p.id, ProductPatch(fill_level="voll"))  # kein Bestand
        elif p.tracking_mode != TrackingMode.IMMER_DA:
            await b.add_stock(p.id, z.menge, z.mhd, ort, einzelpreis)
        anzahl = pos.get("anzahl") or 1
        s.merge(
            BonAlias(
                markt=markt, text=norm_text(pos["text"]), produkt_id=p.id, menge=z.menge / anzahl
            )
        )
        if z.preis is not None:
            s.add(
                Preis(
                    produkt_id=p.id,
                    preis=z.preis,
                    menge=z.menge,
                    markt=bon.markt,
                    datum=bon.datum or date.today(),
                    quelle="bon",
                )
            )
        if pos.get("packung_abweichend") and pos.get("packung_menge"):
            groesse = z.menge / anzahl
            vorhanden = s.scalars(
                select(Packung).where(Packung.produkt_id == p.id, Packung.menge == groesse)
            ).first()
            if vorhanden is None:
                s.add(Packung(produkt_id=p.id, einheit="Packung", menge=groesse, quelle="bon"))
        gebucht.append({"produkt_id": p.id, "name": p.name, "menge": z.menge, "einheit": p.unit})
    bon.status = "gebucht"
    s.commit()
    log.info("Bon %s gebucht: %s Zeilen, %s neue Produkte", bon_id, len(gebucht), len(neu))
    return {"gebucht": gebucht, "neue_produkte": neu}


@router.post("/bon/{bon_id}/verwerfen")
async def bon_verwerfen(bon_id: int, s: Session = Depends(session)) -> dict:
    bon = s.get(BonImport, bon_id)
    if bon is None:
        raise HTTPException(404, "Bon nicht gefunden")
    if bon.status == "gebucht":
        raise HTTPException(409, "Bon ist schon gebucht")
    bon.status = "verworfen"
    s.commit()
    return {"id": bon.id, "status": bon.status}
