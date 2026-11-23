"""Rezeptfotos: Hauptbild und Schrittbilder, lokal unter <data_dir>/fotos.

Quellen: Import (HelloFresh liefert Haupt- und Schrittbilder, sonst schema.org `image`) und
Upload (Ergebnis nach dem Kochen, einzelne Schritte). Jedes Bild wird mit Pillow normalisiert
(EXIF-Drehung, JPEG, max. 1600 px) und bekommt ein Vorschaubild (480 px). Die Dateien liegen
im App-Datenverzeichnis und laufen mit dem HA-Backup der App mit.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base, RezeptMeta
from app.deps import backend, session, settings
from app.inventory.base import InventoryBackend
from app.settings import Settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

MAX_BYTES = 15 * 1024 * 1024
GROSS_PX, KLEIN_PX = 1600, 480
MAX_SCHRITTBILDER = 30
HF_BILD_BASIS = "https://img.hellofresh.com/f_auto,fl_lossy,q_auto,w_1200/hellofresh_s3"
USER_AGENT = "Mozilla/5.0 (rationPlaner Rezeptimport)"


class RezeptFoto(Base):
    __tablename__ = "rezept_foto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rezept_id: Mapped[str] = mapped_column(String, index=True)
    schritt: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Original, 0-basiert
    haupt: Mapped[bool] = mapped_column(Boolean, default=False)
    quelle: Mapped[str] = mapped_column(String)  # import / upload
    url: Mapped[str | None] = mapped_column(String, nullable=True)  # Herkunft beim Import
    breite: Mapped[int] = mapped_column(Integer, default=0)
    hoehe: Mapped[int] = mapped_column(Integer, default=0)
    angelegt: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def ordner(st: Settings) -> Path:
    p = st.data_dir / "fotos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def datei(st: Settings, foto_id: int, klein: bool = False) -> Path:
    return ordner(st) / f"{foto_id}{'_k' if klein else ''}.jpg"


def als_dict(f: RezeptFoto) -> dict:
    return {
        "id": f.id,
        "rezept_id": f.rezept_id,
        "schritt": f.schritt,
        "haupt": f.haupt,
        "quelle": f.quelle,
        "breite": f.breite,
        "hoehe": f.hoehe,
        "angelegt": f.angelegt,
    }


def _normalisieren(daten: bytes) -> tuple[bytes, bytes, int, int]:
    """→ (JPEG groß, JPEG klein, Breite, Höhe). ValueError, wenn es kein Bild ist."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        bild = Image.open(io.BytesIO(daten))
        bild.load()
    except (UnidentifiedImageError, OSError) as err:
        raise ValueError("Kein lesbares Bild") from err
    bild = ImageOps.exif_transpose(bild).convert("RGB")
    bild.thumbnail((GROSS_PX, GROSS_PX))
    gross = io.BytesIO()
    bild.save(gross, "JPEG", quality=85, optimize=True)
    breite, hoehe = bild.size
    bild.thumbnail((KLEIN_PX, KLEIN_PX))
    klein = io.BytesIO()
    bild.save(klein, "JPEG", quality=80, optimize=True)
    return gross.getvalue(), klein.getvalue(), breite, hoehe


def speichern(
    s: Session,
    st: Settings,
    rezept_id: str,
    daten: bytes,
    quelle: str,
    schritt: int | None = None,
    haupt: bool = False,
    url: str | None = None,
) -> RezeptFoto:
    if len(daten) > MAX_BYTES:
        raise ValueError("Bild größer als 15 MB")
    gross, klein, breite, hoehe = _normalisieren(daten)
    hat_haupt = s.scalars(
        select(RezeptFoto).where(RezeptFoto.rezept_id == rezept_id, RezeptFoto.haupt)
    ).first()
    if haupt and hat_haupt:
        hat_haupt.haupt = False
    # Das erste Ergebnisfoto (ohne Schritt) wird automatisch Hauptbild
    f = RezeptFoto(
        rezept_id=rezept_id,
        schritt=schritt,
        haupt=haupt or (schritt is None and not hat_haupt),
        quelle=quelle,
        url=url,
        breite=breite,
        hoehe=hoehe,
    )
    s.add(f)
    s.flush()
    datei(st, f.id).write_bytes(gross)
    datei(st, f.id, klein=True).write_bytes(klein)
    return f


def loeschen(s: Session, st: Settings, f: RezeptFoto) -> None:
    for klein in (False, True):
        datei(st, f.id, klein).unlink(missing_ok=True)
    war_haupt, rezept_id = f.haupt, f.rezept_id
    s.delete(f)
    s.flush()
    if war_haupt and (
        nachfolger := s.scalars(
            select(RezeptFoto)
            .where(RezeptFoto.rezept_id == rezept_id, RezeptFoto.schritt.is_(None))
            .order_by(RezeptFoto.id)
        ).first()
    ):
        nachfolger.haupt = True


def rezept_fotos_entfernen(s: Session, st: Settings, rezept_id: str) -> None:
    for f in list(s.scalars(select(RezeptFoto).where(RezeptFoto.rezept_id == rezept_id))):
        loeschen(s, st, f)


def hauptfotos(s: Session) -> dict[str, int]:
    """Rezept → Hauptbild-ID (sonst erstes Foto)."""
    out: dict[str, int] = {}
    for f in s.scalars(select(RezeptFoto).order_by(RezeptFoto.haupt.desc(), RezeptFoto.id)):
        out.setdefault(f.rezept_id, f.id)
    return out


def fotos_von(s: Session, rezept_id: str) -> list[RezeptFoto]:
    return list(
        s.scalars(
            select(RezeptFoto)
            .where(RezeptFoto.rezept_id == rezept_id)
            .order_by(RezeptFoto.haupt.desc(), RezeptFoto.schritt, RezeptFoto.id)
        )
    )


def je_schritt(s: Session, rezept_id: str) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for f in fotos_von(s, rezept_id):
        if f.schritt is not None:
            out.setdefault(f.schritt, []).append(f.id)
    return out


# --- Bilder aus der Rezeptseite --------------------------------------------------------------


def _bild_url(wert: object) -> str | None:
    """schema.org image: String, Liste, ImageObject; HelloFresh: {link} oder {path}."""
    if isinstance(wert, str):
        w = wert.strip()
        if w.startswith("//"):
            return "https:" + w
        if w.startswith("http"):
            return w
        if w.startswith("/") and re.search(r"\.(jpe?g|png|webp)$", w, re.I):
            return HF_BILD_BASIS + w
        return None
    if isinstance(wert, list):
        for x in wert:
            if u := _bild_url(x):
                return u
        return None
    if isinstance(wert, dict):
        for k in ("link", "url", "contentUrl", "path"):
            if u := _bild_url(wert.get(k)):
                return u
    return None


def _hf_schritte(daten: object) -> list[dict] | None:
    """Sucht in __NEXT_DATA__ die Schrittliste mit Bildern (HelloFresh: steps[].images[])."""
    if isinstance(daten, dict):
        steps = daten.get("steps")
        if (
            isinstance(steps, list)
            and steps
            and all(isinstance(x, dict) for x in steps)
            and any("images" in x or "image" in x for x in steps)
        ):
            return steps
        for v in daten.values():
            if (r := _hf_schritte(v)) is not None:
                return r
    elif isinstance(daten, list):
        for v in daten:
            if (r := _hf_schritte(v)) is not None:
                return r
    return None


def bilder_aus_html(html: str, rezept_ld: dict | None = None) -> tuple[str | None, dict[int, str]]:
    """(Hauptbild-URL, {Schrittindex 0-basiert: URL}) aus JSON-LD und __NEXT_DATA__."""
    haupt = _bild_url((rezept_ld or {}).get("image"))
    schritte: dict[int, str] = {}
    anleitung = (rezept_ld or {}).get("recipeInstructions")
    if isinstance(anleitung, list):
        for i, st in enumerate(x for x in anleitung if isinstance(x, dict)):
            if u := _bild_url(st.get("image")):
                schritte[i] = u
    if m := re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            steps = _hf_schritte(json.loads(m.group(1))) or []
        except json.JSONDecodeError:
            steps = []
        for i, st in enumerate(steps):
            u = _bild_url(st.get("images")) or _bild_url(st.get("image"))
            if u:
                schritte.setdefault(i, u)
    og = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    if haupt is None and og:
        haupt = _bild_url(og.group(1))
    return haupt, dict(list(sorted(schritte.items()))[:MAX_SCHRITTBILDER])


async def herunterladen(url: str) -> bytes:
    """Bild laden, mit Größenlimit. Von Tests ersetzbar."""
    async with (
        httpx.AsyncClient(timeout=20, follow_redirects=True) as c,
        c.stream("GET", url, headers={"User-Agent": USER_AGENT}) as r,
    ):
        if r.status_code >= 400:
            raise ValueError(f"Bild antwortet mit {r.status_code}")
        if not r.headers.get("content-type", "image/").startswith("image/"):
            raise ValueError("Kein Bild")
        daten = b""
        async for teil in r.aiter_bytes():
            daten += teil
            if len(daten) > MAX_BYTES:
                raise ValueError("Bild größer als 15 MB")
        return daten


async def importieren(
    engine, st: Settings, rezept_id: str, haupt: str | None, schritte: dict[int, str]
) -> dict:
    """Bilder einer Rezeptseite laden und speichern; vorhandene Import-Bilder werden ersetzt.
    Fehler einzelner Bilder brechen nichts ab (Hintergrundaufgabe nach dem Import)."""
    auftraege = ([(None, haupt)] if haupt else []) + sorted(schritte.items())
    ok, fehler = 0, []
    with Session(engine) as s:
        for f in list(
            s.scalars(
                select(RezeptFoto).where(
                    RezeptFoto.rezept_id == rezept_id, RezeptFoto.quelle == "import"
                )
            )
        ):
            loeschen(s, st, f)
        hat_upload_haupt = s.scalars(
            select(RezeptFoto).where(RezeptFoto.rezept_id == rezept_id, RezeptFoto.haupt)
        ).first()
        for schritt, url in auftraege:
            try:
                daten = await herunterladen(url)
                speichern(
                    s,
                    st,
                    rezept_id,
                    daten,
                    "import",
                    schritt=schritt,
                    haupt=schritt is None and not hat_upload_haupt,
                    url=url,
                )
                ok += 1
            except (ValueError, httpx.HTTPError) as err:
                fehler.append(f"{url}: {err}")
                log.warning("Rezept %s: Bild %s nicht geladen: %s", rezept_id, url, err)
        s.commit()
    log.info("Rezept %s: %s Bilder importiert, %s Fehler", rezept_id, ok, len(fehler))
    return {"geladen": ok, "fehler": fehler}


# --- API -------------------------------------------------------------------------------------


@router.get("/fotos/{foto_id}")
async def foto_datei(
    foto_id: int, klein: bool = False, st: Settings = Depends(settings)
) -> FileResponse:
    p = datei(st, foto_id, klein)
    if not p.exists():
        raise HTTPException(404, "Foto nicht gefunden")
    # IDs werden nie wiederverwendet, Inhalt ändert sich nicht → lange cachen
    return FileResponse(
        p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000"}
    )


@router.get("/rezepte/{rezept_id}/fotos")
async def rezept_fotos(rezept_id: str, s: Session = Depends(session)) -> list[dict]:
    return [als_dict(f) for f in fotos_von(s, rezept_id)]


class FotoIn(BaseModel):
    daten: str = Field(min_length=10)  # Base64, auch als data:-URL
    schritt: int | None = Field(default=None, ge=0, le=99)
    haupt: bool = False


@router.post("/rezepte/{rezept_id}/fotos", status_code=201)
async def foto_hochladen(
    rezept_id: str,
    data: FotoIn,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
    b: InventoryBackend = Depends(backend),
) -> dict:
    await b.get_recipe(rezept_id)
    roh = data.daten.split(",", 1)[1] if data.daten.startswith("data:") else data.daten
    try:
        f = speichern(
            s, st, rezept_id, base64.b64decode(roh, validate=True), "upload", data.schritt,
            data.haupt,
        )  # fmt: skip
    except (binascii.Error, ValueError) as err:
        raise HTTPException(422, str(err) or "Kein gültiges Bild") from err
    s.commit()
    log.info("Rezept %s: Foto %s hochgeladen (Schritt %s)", rezept_id, f.id, data.schritt)
    return als_dict(f)


class FotoPatch(BaseModel):
    haupt: bool | None = None
    schritt: int | None = Field(default=None, ge=-1, le=99)  # -1 = kein Schritt


@router.patch("/fotos/{foto_id}")
async def foto_aendern(foto_id: int, data: FotoPatch, s: Session = Depends(session)) -> dict:
    f = s.get(RezeptFoto, foto_id)
    if f is None:
        raise HTTPException(404, "Foto nicht gefunden")
    if data.schritt is not None:
        f.schritt = None if data.schritt < 0 else data.schritt
    if data.haupt:
        for alt in s.scalars(
            select(RezeptFoto).where(RezeptFoto.rezept_id == f.rezept_id, RezeptFoto.haupt)
        ):
            alt.haupt = False
        f.haupt = True
    s.commit()
    return als_dict(f)


@router.delete("/fotos/{foto_id}")
async def foto_loeschen(
    foto_id: int, s: Session = Depends(session), st: Settings = Depends(settings)
) -> dict:
    f = s.get(RezeptFoto, foto_id)
    if f is None:
        raise HTTPException(404, "Foto nicht gefunden")
    loeschen(s, st, f)
    s.commit()
    return {"geloescht": foto_id}


@router.post("/rezepte/{rezept_id}/fotos/laden", status_code=202)
async def fotos_von_quelle(
    rezept_id: str,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
    st: Settings = Depends(settings),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Bilder von der Originalseite (RezeptMeta.url) nachladen, z. B. für ältere Importe."""
    from app import rezept_import

    await b.get_recipe(rezept_id)
    m = s.get(RezeptMeta, rezept_id)
    if not m or not m.url:
        raise HTTPException(422, "Rezept hat keinen Link zur Quelle")
    try:
        html = await rezept_import.laden(m.url)
        ld = rezept_import.json_ld_rezept(html)
    except rezept_import.ImportError_ as err:
        raise HTTPException(422, str(err)) from err
    haupt, schritte = bilder_aus_html(html, ld)
    if not haupt and not schritte:
        raise HTTPException(422, "Auf der Seite sind keine Bilder zu finden")
    hintergrund.add_task(importieren, request.app.state.engine, st, rezept_id, haupt, schritte)
    return {"hauptbild": bool(haupt), "schrittbilder": len(schritte)}
