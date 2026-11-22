"""FastAPI-Einstieg. Ingress entfernt das Pfad-Präfix, die App läuft daher unter /."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import API_VERSION, __version__, kueche, testdaten
from app.db import PlanEintrag, make_engine
from app.ha import HomeAssistant, SupervisorHomeAssistant
from app.inventory.base import InventoryBackend
from app.inventory.grocy import GrocyBackend
from app.settings import Settings, load_settings

log = logging.getLogger("ration")
INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

api = APIRouter(prefix="/api/v1")


# --- Abhängigkeiten -----------------------------------------------------------


def backend(request: Request) -> InventoryBackend:
    b = request.app.state.backend
    if b is None:
        raise HTTPException(503, "Grocy ist nicht konfiguriert (grocy_url, grocy_api_key)")
    return b


def session(request: Request):
    with Session(request.app.state.engine) as s:
        yield s


def settings(request: Request) -> Settings:
    return request.app.state.settings


def home_assistant(request: Request) -> HomeAssistant:
    ha = request.app.state.ha
    if ha is None:
        raise HTTPException(503, "Kein Zugriff auf Home Assistant (nur innerhalb von HAOS)")
    return ha


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
async def plan(tage: int = 7, s: Session = Depends(session)) -> list[dict]:
    von = date.today()
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
        s.commit()
    log.info("Gekocht: %s, %s Portionen", recipe.name, data.portionen_gekocht)
    return {"rezept": recipe.name, **asdict(ergebnis)}


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
    fehlend = await kueche.bedarf(b, geplant)
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
    return {"text": st.text, "artikel": st.artikel, "laeuft_bald_ab": st.laeuft_bald_ab}


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
        yield
        for client in owned:
            await client.aclose()

    app = FastAPI(title="rationPlaner", version=__version__, lifespan=lifespan)
    app.include_router(api)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return INDEX_HTML.replace("{{version}}", __version__)

    return app


app = create_app()
