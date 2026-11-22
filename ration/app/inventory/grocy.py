"""GrocyBackend – einzige Stelle, die Grocy-Endpunkte, -IDs und Userfields kennt.

Getestet gegen Grocy 4.7 (REST-API unter /api, Header GROCY-API-KEY).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.inventory.base import (
    Batch,
    DueType,
    Ingredient,
    InsufficientStockError,
    Location,
    Product,
    Recipe,
    TrackingMode,
    consume_order,
)

# Grocy speichert "läuft nie ab" als dieses Datum.
NEVER_EXPIRES = "2999-12-31"
GRAM_UNIT_NAMES = {"g", "gramm", "gram"}

# Userfield-Namen in Grocy. Nur hier definiert, siehe scripts/grocy_setup.py.
UF_TRACKING_MODUS = "tracking_modus"
UF_KATEGORIE = "kategorie"
UF_FUELLSTAND = "fuellstand"
UF_TOLERANZ_PCT = "toleranz_pct"
UF_REZEPT_STATUS = "status"
UF_REZEPT_AUFWAND = "aufwand"
UF_REZEPT_TAGS = "tags"
UF_REZEPT_REST_HALTBARKEIT = "rest_haltbarkeit_tage"


# Was scripts/grocy_setup.py anlegt: Lagerorte (Name, Gefrierort) und Userfields
# (Entität, Name, Beschriftung, Typ, Optionen).
LOCATIONS = [("Kühlschrank", False), ("Vorrat", False), ("Gefrierschrank", True)]
USERFIELDS: list[tuple[str, str, str, str, list[str] | None]] = [
    ("products", UF_TRACKING_MODUS, "Tracking-Modus", "preset-list", list(TrackingMode)),
    ("products", UF_KATEGORIE, "Kategorie", "text-single-line", None),
    ("products", UF_FUELLSTAND, "Füllstand", "preset-list", ["voll", "knapp", "leer"]),
    ("products", UF_TOLERANZ_PCT, "Toleranz %", "number-integral", None),
    ("recipes", UF_REZEPT_STATUS, "Status", "preset-list", ["getestet", "ungetestet"]),
    ("recipes", UF_REZEPT_AUFWAND, "Aufwand", "preset-list", ["schnell", "normal", "aufwaendig"]),
    ("recipes", UF_REZEPT_TAGS, "Tags", "text-single-line", None),
    ("recipes", UF_REZEPT_REST_HALTBARKEIT, "Rest-Haltbarkeit (Tage)", "number-integral", None),
]


class GrocyError(Exception):
    pass


def _parse_date(value: str | None) -> date | None:
    if not value or value.startswith(NEVER_EXPIRES):
        return None
    return date.fromisoformat(value[:10])


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


class GrocyBackend:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api",
            headers={"GROCY-API-KEY": api_key},
            timeout=15.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GrocyBackend:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- HTTP -----------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                message = resp.json().get("error_message", resp.text)
            except ValueError:
                message = resp.text
            if "cannot be >" in message or "not enough" in message.lower():
                raise InsufficientStockError(message)
            raise GrocyError(f"{method} {path}: {resp.status_code} {message}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def _get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, params=params or None)

    async def _post(self, path: str, body: dict) -> Any:
        return await self._request("POST", path, json=body)

    async def _put(self, path: str, body: dict) -> Any:
        return await self._request("PUT", path, json=body)

    # --- Mapping --------------------------------------------------------------

    async def _unit_context(self) -> tuple[dict[int, str], dict[tuple[int, int], float]]:
        """Einheitennamen und je Produkt der Faktor Bestandseinheit → Gramm."""
        units = {u["id"]: u["name"] for u in await self._get("/objects/quantity_units")}
        gram_ids = {uid for uid, name in units.items() if name.strip().lower() in GRAM_UNIT_NAMES}
        factors: dict[tuple[int, int], float] = {}
        for conv in await self._get("/objects/quantity_unit_conversions_resolved"):
            if conv["to_qu_id"] in gram_ids:
                factors[(conv["product_id"], conv["from_qu_id"])] = float(conv["factor"])
        return units, factors

    @staticmethod
    def _to_product(
        raw: dict, units: dict[int, str], factors: dict[tuple[int, int], float]
    ) -> Product:
        uf = raw.get("userfields") or {}
        mode = uf.get(UF_TRACKING_MODUS)
        qu_stock = raw["qu_id_stock"]
        grams = factors.get((raw["id"], qu_stock))
        if grams is None and units.get(qu_stock, "").strip().lower() in GRAM_UNIT_NAMES:
            grams = 1.0
        return Product(
            id=str(raw["id"]),
            name=raw["name"],
            unit=units.get(qu_stock, ""),
            parent_id=str(raw["parent_product_id"]) if raw.get("parent_product_id") else None,
            tracking_mode=TrackingMode(mode) if mode else None,
            grams_per_unit=grams,
            tolerance_pct=_int_or_none(uf.get(UF_TOLERANZ_PCT)),
            due_type=DueType(int(raw.get("due_type") or 1)),
            shelf_life_days=max(int(raw.get("default_best_before_days") or 0), 0),
            shelf_life_opened_days=int(raw.get("default_best_before_days_after_open") or 0),
            shelf_life_frozen_days=int(raw.get("default_best_before_days_after_freezing") or 0),
            shelf_life_thawed_days=int(raw.get("default_best_before_days_after_thawing") or 0),
            default_location_id=str(raw["location_id"]) if raw.get("location_id") else None,
        )

    @staticmethod
    def _to_batch(raw: dict) -> Batch:
        return Batch(
            id=str(raw["id"]),
            product_id=str(raw["product_id"]),
            amount=float(raw["amount"]),
            best_before=_parse_date(raw.get("best_before_date")),
            location_id=str(raw["location_id"]),
            opened=bool(int(raw.get("open") or 0)),
        )

    # --- InventoryBackend -----------------------------------------------------

    async def list_products(self) -> list[Product]:
        units, factors = await self._unit_context()
        raws = await self._get("/objects/products")
        return [self._to_product(r, units, factors) for r in raws if int(r.get("active", 1))]

    async def get_product(self, product_id: str) -> Product:
        units, factors = await self._unit_context()
        return self._to_product(await self._get(f"/objects/products/{product_id}"), units, factors)

    async def list_locations(self) -> list[Location]:
        return [
            Location(id=str(r["id"]), name=r["name"], is_freezer=bool(int(r["is_freezer"])))
            for r in await self._get("/objects/locations")
            if int(r.get("active", 1))
        ]

    @staticmethod
    def _to_recipe(raw: dict, positions: list[dict]) -> Recipe:
        return Recipe(
            id=str(raw["id"]),
            name=raw["name"],
            servings=int(raw.get("base_servings") or 1),
            description=raw.get("description") or "",
            ingredients=tuple(
                Ingredient(
                    product_id=str(pos["product_id"]),
                    amount=float(pos["amount"]),
                    note=pos.get("note"),
                )
                for pos in positions
                if pos["recipe_id"] == raw["id"]
            ),
        )

    async def list_recipes(self) -> list[Recipe]:
        # Grocy legt intern auch Rezepte für Essensplan-Tage an (type != normal).
        raws = [r for r in await self._get("/objects/recipes") if r.get("type") == "normal"]
        positions = await self._get("/objects/recipes_pos")
        return [self._to_recipe(r, positions) for r in raws]

    async def get_recipe(self, recipe_id: str) -> Recipe:
        raw = await self._get(f"/objects/recipes/{recipe_id}")
        positions = await self._get("/objects/recipes_pos", **{"query[]": f"recipe_id={recipe_id}"})
        return self._to_recipe(raw, positions)

    async def list_all_batches(self) -> list[Batch]:
        raws = await self._get("/objects/stock")
        return sorted((self._to_batch(r) for r in raws), key=consume_order)

    async def get_batches(self, product_id: str, *, include_children: bool = False) -> list[Batch]:
        params = {"include_sub_products": "true"} if include_children else {}
        raws = await self._get(f"/stock/products/{product_id}/entries", **params)
        return sorted((self._to_batch(r) for r in raws), key=consume_order)

    async def get_stock_amount(self, product_id: str) -> float:
        details = await self._get(f"/stock/products/{product_id}")
        return float(details.get("stock_amount_aggregated") or details.get("stock_amount") or 0)

    async def add_stock(
        self,
        product_id: str,
        amount: float,
        best_before: date | None,
        location_id: str,
        price: float | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "amount": amount,
            "location_id": int(location_id),
            "transaction_type": "purchase",
        }
        if best_before is not None:
            body["best_before_date"] = best_before.isoformat()
        if price is not None:
            body["price"] = price
        await self._post(f"/stock/products/{product_id}/add", body)

    async def consume(self, product_id: str, amount: float, *, spoiled: bool = False) -> None:
        await self._post(
            f"/stock/products/{product_id}/consume",
            {
                "amount": amount,
                "transaction_type": "consume",
                "spoiled": spoiled,
                "allow_subproduct_substitution": True,
            },
        )

    async def transfer(
        self, product_id: str, amount: float, from_location_id: str, to_location_id: str
    ) -> None:
        await self._post(
            f"/stock/products/{product_id}/transfer",
            {
                "amount": amount,
                "location_id_from": int(from_location_id),
                "location_id_to": int(to_location_id),
            },
        )

    # --- Verwaltung (Setup-Skript, Contract-Tests) ----------------------------
    # Nicht Teil von InventoryBackend. Alle Methoden sind idempotent und löschen nichts.

    async def _find(self, object_type: str, **match: Any) -> dict | None:
        for obj in await self._get(f"/objects/{object_type}"):
            if all(obj.get(k) == v for k, v in match.items()):
                return obj
        return None

    async def _create(self, entity: str, body: dict) -> int:
        created = await self._post(f"/objects/{entity}", body)
        return int(created["created_object_id"])

    async def ensure_quantity_unit(self, name: str) -> str:
        found = await self._find("quantity_units", name=name)
        if found:
            return str(found["id"])
        return str(await self._create("quantity_units", {"name": name, "name_plural": name}))

    async def ensure_location(self, name: str, *, is_freezer: bool = False) -> tuple[str, bool]:
        """Gibt (id, geändert) zurück. Korrigiert is_freezer bei bestehenden Lagerorten."""
        found = await self._find("locations", name=name)
        if found is None:
            body = {"name": name, "is_freezer": int(is_freezer)}
            return str(await self._create("locations", body)), True
        if bool(int(found["is_freezer"])) != is_freezer:
            await self._put(f"/objects/locations/{found['id']}", {"is_freezer": int(is_freezer)})
            return str(found["id"]), True
        return str(found["id"]), False

    async def ensure_userfield(
        self,
        entity: str,
        name: str,
        caption: str,
        field_type: str,
        options: list[str] | None = None,
    ) -> bool:
        """Legt ein Userfield an, falls es fehlt. Gibt True zurück, wenn angelegt."""
        if await self._find("userfields", entity=entity, name=name):
            return False
        body = {
            "entity": entity,
            "name": name,
            "caption": caption,
            "type": field_type,
            "show_as_column_in_tables": 0,
        }
        if options:
            body["config"] = "\n".join(options)
        await self._create("userfields", body)
        return True

    async def ensure_product(
        self,
        name: str,
        *,
        unit_id: str,
        location_id: str,
        parent_id: str | None = None,
        shelf_life_days: int = 0,
        shelf_life_frozen_days: int = 0,
        shelf_life_thawed_days: int = 0,
    ) -> str:
        found = await self._find("products", name=name)
        if found:
            return str(found["id"])
        qu = int(unit_id)
        body = {
            "name": name,
            "location_id": int(location_id),
            "qu_id_purchase": qu,
            "qu_id_stock": qu,
            "qu_id_consume": qu,
            "qu_id_price": qu,
            "default_best_before_days": shelf_life_days,
            "default_best_before_days_after_freezing": shelf_life_frozen_days,
            "default_best_before_days_after_thawing": shelf_life_thawed_days,
        }
        if parent_id:
            body["parent_product_id"] = int(parent_id)
        return str(await self._create("products", body))

    async def ensure_structure(self) -> list[tuple[str, str, str]]:
        """Einheit g, Lagerorte und Userfields. Gibt (Art, Name, Status) je Objekt zurück."""
        rows = [("Einheit", "g", "ok")]
        await self.ensure_quantity_unit("g")
        for name, is_freezer in LOCATIONS:
            _, changed = await self.ensure_location(name, is_freezer=is_freezer)
            rows.append(("Lagerort", name, "angelegt/korrigiert" if changed else "vorhanden"))
        for entity, name, caption, field_type, options in USERFIELDS:
            created = await self.ensure_userfield(entity, name, caption, field_type, options)
            rows.append((f"Userfield {entity}", name, "angelegt" if created else "vorhanden"))
        return rows

    async def set_product_userfields(self, product_id: str, values: dict[str, Any]) -> None:
        await self._put(f"/userfields/products/{product_id}", values)

    async def ensure_recipe(
        self,
        name: str,
        *,
        servings: int,
        description: str,
        ingredients: list[tuple[str, float]],
        userfields: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """Legt ein Rezept mit Zutaten (Produkt-ID, Menge in Bestandseinheit) an, falls es fehlt."""
        found = await self._find("recipes", name=name)
        if found:
            return str(found["id"]), False
        recipe_id = await self._create(
            "recipes", {"name": name, "base_servings": servings, "description": description}
        )
        products = {p["id"]: p for p in await self._get("/objects/products")}
        for product_id, amount in ingredients:
            await self._create(
                "recipes_pos",
                {
                    "recipe_id": recipe_id,
                    "product_id": int(product_id),
                    "amount": amount,
                    "qu_id": products[int(product_id)]["qu_id_stock"],
                },
            )
        if userfields:
            await self._put(f"/userfields/recipes/{recipe_id}", userfields)
        return str(recipe_id), True
