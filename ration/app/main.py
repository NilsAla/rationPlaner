"""FastAPI-Einstieg. Ingress entfernt das Pfad-Präfix, die App läuft daher unter /."""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import (
    API_VERSION,
    __version__,
    bon,
    einstellungen,
    inventar,
    katalog,
    kategorisierung,
    kueche,
    llm_import,
    planer,
    protokoll,
    rezept_import,
    testdaten,
)
from app.db import (
    FuellstandAbfrage,
    Kochvorgang,
    Packung,
    PlanEintrag,
    RezeptImport,
    RezeptMeta,
    ZutatAlias,
    make_engine,
)
from app.deps import backend, home_assistant, session, settings
from app.ha import HomeAssistant, SupervisorHomeAssistant
from app.inventory.base import (
    Ingredient,
    InsufficientStockError,
    InventoryBackend,
    NotFoundError,
    ProductPatch,
    TrackingMode,
)
from app.inventory.grocy import GrocyBackend
from app.settings import Settings, load_settings

log = logging.getLogger("ration")
INGRESS_IP = "172.30.32.2"  # Supervisor-Ingress-Proxy
FUELLSTAND_TAGE = 14  # Füllstand je Produkt höchstens so oft abfragen
STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

api = APIRouter(prefix="/api/v1")


# --- Modelle ------------------------------------------------------------------


class EinbuchenIn(BaseModel):
    produkt_id: str
    menge: float = Field(gt=0)
    mhd: date | None = None
    lagerort_id: str | None = None
    preis: float | None = None


class PlanIn(BaseModel):
    rezept_id: str
    portionen: int = Field(default=1, ge=1, le=12)


class GekochtIn(BaseModel):
    rezept_id: str | None = None  # None = Rezept aus dem Plan für heute
    portionen_gekocht: int = Field(ge=1, le=12)
    portionen_gegessen: int = Field(ge=0, le=12)


class PackungIn(BaseModel):
    einheit: str = Field(min_length=1, max_length=40)
    menge: float = Field(gt=0)


class WeiterePackungIn(PackungIn):
    quelle: Literal["manuell", "bon", "angebot", "llm"] = "manuell"
    gueltig_bis: date | None = None


class ImportIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    html: str | None = None  # Fallback: Seitenquelltext, falls die Seite den Abruf blockt
    ki: bool = True  # offene Zutaten per ai_task zuordnen, falls ai_task_entity gesetzt


class ImportZutatIn(BaseModel):
    index: int = Field(ge=0)
    produkt_id: str | None = None  # bestehendes Produkt
    neu_anlegen: str | None = None  # oder neues Produkt mit diesem Namen (ohne Attribute)
    einheit: str = "g"  # Bestandseinheit für neu_anlegen
    stueck_g: float | None = Field(default=None, gt=0)  # neu_anlegen: Standard-Packung Stück
    menge: float = Field(ge=0)  # in Bestandseinheit des Produkts


class UebernehmenIn(BaseModel):
    name: str | None = None
    portionen: int | None = Field(default=None, ge=1, le=12)
    zutaten: list[ImportZutatIn]  # nicht aufgeführte Zutaten werden weggelassen
    schritte: list[str] | None = None  # bearbeitete Schritte, sonst die aus der Quelle
    trotz_dublette: bool = False


class EinkaufslisteIn(BaseModel):
    tage: int = Field(default=7, ge=1, le=14)


# --- Endpunkte ----------------------------------------------------------------


@api.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@api.get("/version")
async def version() -> dict:
    """Versions-Handshake für Bridge und Card (siehe Konzept, API-Vertrag)."""
    return {"app_version": __version__, "api_version": API_VERSION}


@api.get("/produkte")
async def produkte(b: InventoryBackend = Depends(backend)) -> list[dict]:
    return [
        {"id": p.id, "name": p.name, "einheit": p.unit, "tracking_modus": p.tracking_mode}
        for p in sorted(await b.list_products(), key=lambda p: p.name.lower())
    ]


@api.get("/lagerorte")
async def lagerorte(b: InventoryBackend = Depends(backend)) -> list[dict]:
    return [asdict(loc) for loc in await b.list_locations()]


@api.get("/bestand")
async def bestand(b: InventoryBackend = Depends(backend)) -> list[dict]:
    products = {p.id: p for p in await b.list_products()}
    locations = {loc.id: loc.name for loc in await b.list_locations()}
    return [
        {
            "produkt_id": x.product_id,
            "name": products[x.product_id].name if x.product_id in products else x.product_id,
            "menge": x.amount,
            "einheit": products[x.product_id].unit if x.product_id in products else "",
            "mhd": x.best_before,
            "lagerort": locations.get(x.location_id, x.location_id),
            "geoeffnet": x.opened,
        }
        for x in await b.list_all_batches()
    ]


@api.post("/bestand", status_code=201)
async def einbuchen(data: EinbuchenIn, b: InventoryBackend = Depends(backend)) -> dict:
    product = await b.get_product(data.produkt_id)
    location = data.lagerort_id or product.default_location_id
    if location is None:
        raise HTTPException(422, "Lagerort fehlt und Produkt hat keinen Standard-Lagerort")
    await b.add_stock(product.id, data.menge, data.mhd, location, data.preis)
    return {"produkt_id": product.id, "menge": data.menge}


@api.get("/rezepte")
async def rezepte(b: InventoryBackend = Depends(backend)) -> list[dict]:
    return [
        {"id": r.id, "name": r.name, "portionen": r.servings}
        for r in sorted(await b.list_recipes(), key=lambda r: r.name.lower())
    ]


@api.get("/plan")
async def plan(tage: int = 7, von: date | None = None, s: Session = Depends(session)) -> list[dict]:
    """Planeinträge ab `von` (Standard: heute) für `tage` Tage."""
    von = von or date.today()
    tage = max(1, min(tage, 120))
    rows = s.scalars(
        select(PlanEintrag)
        .where(PlanEintrag.datum >= von, PlanEintrag.datum < von + timedelta(days=tage))
        .order_by(PlanEintrag.datum)
    )
    return [
        {"datum": r.datum, "rezept_id": r.rezept_id, "portionen": r.portionen, "status": r.status}
        for r in rows
    ]


@api.put("/plan/{datum}")
async def plan_setzen(
    datum: date,
    data: PlanIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    await b.get_recipe(data.rezept_id)  # existiert?
    eintrag = s.get(PlanEintrag, datum) or PlanEintrag(datum=datum)
    eintrag.rezept_id, eintrag.portionen, eintrag.status = data.rezept_id, data.portionen, "geplant"
    s.add(eintrag)
    s.commit()
    return {"datum": datum, "rezept_id": data.rezept_id, "portionen": data.portionen}


@api.delete("/plan/{datum}", status_code=204)
async def plan_loeschen(datum: date, s: Session = Depends(session)) -> None:
    if eintrag := s.get(PlanEintrag, datum):
        s.delete(eintrag)
        s.commit()


@api.get("/heute")
async def heute(s: Session = Depends(session), b: InventoryBackend = Depends(backend)) -> dict:
    eintrag = s.get(PlanEintrag, date.today())
    if eintrag is None:
        return {"datum": date.today(), "rezept": None, "status": None}
    recipe = await b.get_recipe(eintrag.rezept_id)
    products = {p.id: p for p in await b.list_products()}
    faktor = eintrag.portionen / max(recipe.servings, 1)
    return {
        "datum": eintrag.datum,
        "status": eintrag.status,
        "rezept": {
            "id": recipe.id,
            "name": recipe.name,
            "portionen": eintrag.portionen,
            "zutaten": [
                {
                    "name": products[z.product_id].name,
                    "menge": round(z.amount * faktor, 1),
                    "einheit": products[z.product_id].unit,
                }
                for z in recipe.ingredients
            ],
            "schritte": kueche.schritte(recipe.description),
        },
    }


@api.post("/gekocht")
async def gekocht(
    data: GekochtIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    eintrag = s.get(PlanEintrag, date.today())
    rezept_id = data.rezept_id or (eintrag.rezept_id if eintrag else None)
    if rezept_id is None:
        raise HTTPException(422, "Kein Rezept angegeben und für heute nichts geplant")
    recipe = await b.get_recipe(rezept_id)
    ergebnis = await kueche.gekocht(b, recipe, data.portionen_gekocht, data.portionen_gegessen)
    if eintrag and eintrag.rezept_id == rezept_id:
        eintrag.status = "gekocht"
    vorgang = Kochvorgang(rezept_id=rezept_id, datum=date.today(), portionen=data.portionen_gekocht)
    s.add(vorgang)
    # Füllstand höchstens alle FUELLSTAND_TAGE je Produkt abfragen.
    heute = date.today()
    fragen = []
    for pos in ergebnis.fuellstand_produkte:
        a = s.get(FuellstandAbfrage, pos.produkt_id)
        if a is None or a.zuletzt <= heute - timedelta(days=FUELLSTAND_TAGE):
            fragen.append({"produkt_id": pos.produkt_id, "name": pos.name})
            s.merge(FuellstandAbfrage(produkt_id=pos.produkt_id, zuletzt=heute))
    s.commit()
    log.info("Gekocht: %s, %s Portionen", recipe.name, data.portionen_gekocht)
    daten = asdict(ergebnis)
    del daten["fuellstand_produkte"]
    return {
        "rezept": recipe.name,
        **daten,
        "fuellstand_fragen": fragen,
        "kochvorgang_id": vorgang.id,
    }


class FuellstandIn(BaseModel):
    stand: Literal["voll", "knapp", "leer"]


@api.get("/fuellstand")
async def fuellstand_liste(b: InventoryBackend = Depends(backend)) -> list[dict]:
    """Alle Produkte im Modus Füllstand mit aktuellem Stand."""
    return [
        {"produkt_id": p.id, "name": p.name, "stand": p.fill_level or "voll"}
        for p in sorted(await b.list_products(), key=lambda p: p.name.lower())
        if p.tracking_mode == TrackingMode.FUELLSTAND
    ]


@api.put("/fuellstand/{produkt_id}")
async def fuellstand_setzen(
    produkt_id: str,
    data: FuellstandIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Setzt voll/knapp/leer. knapp und leer kommen beim nächsten Einkauf auf die Liste."""
    p = await b.get_product(produkt_id)
    if p.tracking_mode != TrackingMode.FUELLSTAND:
        raise HTTPException(422, f"{p.name} hat nicht den Modus Füllstand")
    await b.update_product(produkt_id, ProductPatch(fill_level=data.stand))
    s.merge(FuellstandAbfrage(produkt_id=produkt_id, zuletzt=date.today()))
    s.commit()
    return {"produkt_id": produkt_id, "name": p.name, "stand": data.stand}


class AbbuchenIn(BaseModel):
    produkt_id: str
    menge: float = Field(gt=0)
    verdorben: bool = False


@api.post("/bestand/abbuchen")
async def abbuchen(data: AbbuchenIn, b: InventoryBackend = Depends(backend)) -> dict:
    """Bucht eine Menge ab (FEFO), z. B. Rest gegessen oder verdorben weggeworfen."""
    p = await b.get_product(data.produkt_id)
    try:
        await b.consume(p.id, data.menge, spoiled=data.verdorben)
    except InsufficientStockError as err:
        raise HTTPException(409, f"Nicht genug {p.name} im Bestand") from err
    log.info("Abgebucht: %s %s %s%s", data.menge, p.unit, p.name, " (verdorben)" * data.verdorben)
    return {"produkt_id": p.id, "name": p.name, "menge": data.menge, "verdorben": data.verdorben}


@api.get("/reste")
async def reste(b: InventoryBackend = Depends(backend)) -> list[dict]:
    """Reste im Bestand: Portionen und frühester Ablauf."""
    return (await kueche.status(b)).reste


def weitere_packungen(s: Session) -> dict[str, list[kueche.Groesse]]:
    """Weitere Packungsgrößen je Produkt, abgelaufene Angebote ausgenommen."""
    heute = date.today()
    result: dict[str, list[kueche.Groesse]] = {}
    for p in s.scalars(select(Packung)):
        if p.gueltig_bis is None or p.gueltig_bis >= heute:
            result.setdefault(p.produkt_id, []).append(kueche.Groesse(p.einheit, p.menge, p.quelle))
    return result


@api.get("/packungen")
async def packungen(
    s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> list[dict]:
    """Je Produkt Standard-Packung (Grocy) und weitere Größen (App)."""
    weitere: dict[str, list[dict]] = {}
    for p in s.scalars(select(Packung).order_by(Packung.menge)):
        weitere.setdefault(p.produkt_id, []).append(
            {
                "id": p.id,
                "einheit": p.einheit,
                "menge": p.menge,
                "quelle": p.quelle,
                "gueltig_bis": p.gueltig_bis,
            }
        )
    return [
        {
            "produkt_id": p.id,
            "name": p.name,
            "einheit": p.unit,
            "standard": {"einheit": p.pack_unit, "menge": p.pack_size} if p.pack_size else None,
            "weitere": weitere.get(p.id, []),
        }
        for p in sorted(await b.list_products(), key=lambda p: p.name.lower())
    ]


@api.put("/produkte/{produkt_id}/packung")
async def packung_setzen(
    produkt_id: str, data: PackungIn, b: InventoryBackend = Depends(backend)
) -> dict:
    """Setzt die Standard-Packung (meistverkaufte Größe) in Grocy."""
    await b.get_product(produkt_id)
    await b.set_pack(produkt_id, data.einheit.strip(), data.menge)
    return {"produkt_id": produkt_id, "einheit": data.einheit.strip(), "menge": data.menge}


@api.post("/produkte/{produkt_id}/packungen", status_code=201)
async def packung_hinzufuegen(
    produkt_id: str,
    data: WeiterePackungIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Weitere Packungsgröße, z. B. 800-g-Dose oder Angebotsgröße mit Ablauf."""
    await b.get_product(produkt_id)
    p = Packung(
        produkt_id=produkt_id,
        einheit=data.einheit.strip(),
        menge=data.menge,
        quelle=data.quelle,
        gueltig_bis=data.gueltig_bis,
    )
    s.add(p)
    s.commit()
    return {"id": p.id, "produkt_id": produkt_id, "einheit": p.einheit, "menge": p.menge}


@api.delete("/packungen/{packung_id}", status_code=204)
async def packung_loeschen(packung_id: int, s: Session = Depends(session)) -> None:
    if p := s.get(Packung, packung_id):
        s.delete(p)
        s.commit()


@api.post("/import/vorschau")
async def import_vorschau(
    data: ImportIn,
    request: Request,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> dict:
    """Liest das Rezept hinter einem Link und schlägt je Zutat Produkt und Menge vor."""
    try:
        html = data.html or await rezept_import.laden(data.url)
        roh = rezept_import.rezept_aus_html(html, data.url)
    except rezept_import.ImportError_ as err:
        raise HTTPException(422, str(err)) from err
    aliase = {a.text: a.produkt_id for a in s.scalars(select(ZutatAlias))}
    produkte = await b.list_products()
    zuordnung = rezept_import.zuordnen(roh, produkte, aliase)
    ki = llm_import.KiErgebnis(aktiv=False)
    if data.ki and st.ai_task_entity and request.app.state.ha is not None:
        ki = await llm_import.verfeinern(
            request.app.state.ha, st.ai_task_entity, zuordnung, produkte
        )
    dublette = s.scalars(select(RezeptMeta).where(RezeptMeta.quelle_id == roh.quelle_id)).first()
    eintrag = RezeptImport(url=roh.url, quelle_id=roh.quelle_id, roh=asdict(roh))
    s.add(eintrag)
    s.commit()
    log.info("Import-Vorschau %s: %s (%s Zutaten)", eintrag.id, roh.name, len(roh.zutaten))
    return {
        "import_id": eintrag.id,
        "name": roh.name,
        "portionen": roh.portionen,
        "quelle": roh.quelle,
        "quelle_id": roh.quelle_id,
        "url": roh.url,
        "dublette": dublette.rezept_id if dublette else None,
        "schritte": roh.schritte,
        "zutaten": [asdict(z) for z in zuordnung],
        "ki": asdict(ki),
    }


@api.post("/import/{import_id}/uebernehmen", status_code=201)
async def import_uebernehmen(
    import_id: int,
    data: UebernehmenIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Legt das geprüfte Rezept in Grocy an, merkt Aliase und Herkunft."""
    eintrag = s.get(RezeptImport, import_id)
    if eintrag is None:
        raise HTTPException(404, "Import nicht gefunden")
    if eintrag.rezept_id:
        raise HTTPException(409, f"Schon übernommen als Rezept {eintrag.rezept_id}")
    meta = s.scalars(select(RezeptMeta).where(RezeptMeta.quelle_id == eintrag.quelle_id)).first()
    if meta and not data.trotz_dublette:
        raise HTTPException(409, f"Rezept existiert schon (ID {meta.rezept_id})")
    roh = rezept_import.RohRezept(
        **{**eintrag.roh, "zutaten": [rezept_import.RohZutat(**z) for z in eintrag.roh["zutaten"]]}
    )
    zutaten, neu = [], []
    for z in data.zutaten:
        if z.index >= len(roh.zutaten):
            raise HTTPException(422, f"Zutat {z.index} gibt es nicht")
        original = roh.zutaten[z.index]
        if z.neu_anlegen:
            produkt = await b.create_product(z.neu_anlegen.strip(), z.einheit)
            if z.stueck_g and not produkt.pack_size:
                await b.set_pack(produkt.id, "Stück", z.stueck_g)
            neu.append(produkt.name)
        elif z.produkt_id:
            produkt = await b.get_product(z.produkt_id)
        else:
            raise HTTPException(422, f"Zutat {z.index}: produkt_id oder neu_anlegen fehlt")
        zutaten.append(Ingredient(produkt.id, z.menge, original.text))
        s.merge(ZutatAlias(text=rezept_import.alias_key(original.name), produkt_id=produkt.id))
    if not zutaten:
        raise HTTPException(422, "Keine Zutaten übernommen")
    recipe = await b.create_recipe(
        data.name or roh.name,
        data.portionen or roh.portionen,
        rezept_import.beschreibung(
            roh, None if data.schritte is None else rezept_import.schritte_begrenzen(data.schritte)
        ),
        zutaten,
    )
    eintrag.rezept_id = recipe.id
    s.merge(
        RezeptMeta(rezept_id=recipe.id, quelle=roh.quelle, quelle_id=roh.quelle_id, url=roh.url)
    )
    s.commit()
    log.info("Import %s übernommen: %s → Rezept %s", import_id, recipe.name, recipe.id)
    return {
        "rezept_id": recipe.id,
        "name": recipe.name,
        "zutaten": len(zutaten),
        "neue_produkte": neu,
        "weggelassen": len(roh.zutaten) - len(zutaten),
    }


@api.post("/einkaufsliste")
async def einkaufsliste(
    data: EinkaufslisteIn,
    st: Settings = Depends(settings),
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    ha: HomeAssistant = Depends(home_assistant),
) -> dict:
    """Fehlmengen der geplanten Gerichte in die HA-To-do-Liste. Abhaken bucht nichts ein."""
    todo = st.todo_entity
    if not todo:
        raise HTTPException(503, "Option todo_entity ist nicht gesetzt")
    von = date.today()
    rows = s.scalars(
        select(PlanEintrag).where(
            PlanEintrag.datum >= von,
            PlanEintrag.datum < von + timedelta(days=data.tage),
            PlanEintrag.status == "geplant",
        )
    ).all()
    geplant = [(await b.get_recipe(r.rezept_id), r.portionen) for r in rows]
    fehlend = await kueche.bedarf(b, geplant, weitere_packungen(s))
    vorhanden = [item.lower() for item in await ha.todo_items(todo)]
    neu, schon_da = [], []
    for pos in fehlend:
        text = pos.text
        if any(item.startswith(pos.name.lower()) for item in vorhanden):
            schon_da.append(text)
            continue
        await ha.todo_add(todo, text)
        neu.append(text)
    return {
        "hinzugefuegt": neu,
        "schon_auf_liste": schon_da,
        "gerichte": len(geplant),
        "positionen": [{**asdict(pos), "text": pos.text} for pos in fehlend],
    }


@api.post("/testdaten")
async def testdaten_anlegen(b: InventoryBackend = Depends(backend)) -> dict:
    """Legt 2 Testrezepte samt Produkten in Grocy an (idempotent, löscht nichts)."""
    if not isinstance(b, GrocyBackend):
        raise HTTPException(501, "Nur mit Grocy-Backend")
    return await testdaten.anlegen(b)


@api.get("/status")
async def status(tage: int = 3, b: InventoryBackend = Depends(backend)) -> dict:
    st = await kueche.status(b, tage)
    return {
        "text": st.text,
        "artikel": st.artikel,
        "laeuft_bald_ab": st.laeuft_bald_ab,
        "reste": st.reste,
    }


# --- App ----------------------------------------------------------------------


def create_app(
    *,
    inventory: InventoryBackend | None = None,
    ha: HomeAssistant | None = None,
    app_settings: Settings | None = None,
    db_url: str | None = None,
) -> FastAPI:
    """Ohne Argumente: Konfiguration aus /data/options.json. Tests injizieren Fakes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        st = app_settings or load_settings()
        app.state.settings = st
        app.state.engine = make_engine(db_url or st.db_url)
        app.state.backend = inventory
        app.state.ha = ha
        owned = []
        if inventory is None and st.grocy_url and st.grocy_api_key:
            app.state.backend = GrocyBackend(st.grocy_url, st.grocy_api_key)
            owned.append(app.state.backend)
        if ha is None and st.supervisor_token:
            app.state.ha = SupervisorHomeAssistant(st.supervisor_token)
            owned.append(app.state.ha)
        if app.state.ha is not None:
            app.state.ha = protokoll.ProtokollHA(app.state.ha, app.state.engine)
        yield
        for client in owned:
            await client.aclose()

    app = FastAPI(title="rationPlaner", version=__version__, lifespan=lifespan)
    app.include_router(api)
    app.include_router(katalog.router)
    app.include_router(bon.router)
    app.include_router(kategorisierung.router)
    app.include_router(planer.router)
    app.include_router(protokoll.router)
    app.include_router(einstellungen.router)
    app.include_router(inventar.router)

    @app.middleware("http")
    async def aenderungen_protokollieren(request: Request, call_next):
        return await protokoll.aenderung_protokollieren(request, call_next, INGRESS_IP)

    @app.middleware("http")
    async def token_pruefen(request: Request, call_next):
        """Ingress (Supervisor-Proxy) darf immer, alles andere braucht das Token, falls gesetzt."""
        token = (
            request.app.state.settings.api_token if hasattr(request.app.state, "settings") else ""
        )
        pfad = request.url.path
        if (
            token
            and pfad.startswith("/api/")
            and pfad != "/api/v1/health"
            and (request.client.host if request.client else "") != INGRESS_IP
        ):
            gesendet = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(gesendet.encode(), token.encode()):
                return JSONResponse({"detail": "Token fehlt oder falsch"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"Nicht gefunden: {exc}"}, status_code=404)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def static_nicht_cachen(request: Request, call_next):
        """Nach einem Update sollen Browser die neue Oberfläche laden (ETag-Prüfung)."""
        antwort = await call_next(request)
        if request.url.path.startswith("/static/"):
            antwort.headers["Cache-Control"] = "no-cache"
        return antwort

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return INDEX_HTML.replace("{{version}}", __version__)

    return app


app = create_app()
