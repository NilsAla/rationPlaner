"""GrocyBackend – einzige Stelle, die Grocy-Endpunkte, -IDs und Userfields kennt.

Getestet gegen Grocy 4.7 (REST-API unter /api, Header GROCY-API-KEY).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.inventory.base import (
    Batch,
    BatchPatch,
    DueType,
    Ingredient,
    InsufficientStockError,
    Location,
    NotFoundError,
    Product,
    ProductPatch,
    Recipe,
    RecipePatch,
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


# (product_id oder None für global, von Einheit, nach Einheit) → Faktor
Conv = dict[tuple[int | None, int, int], float]


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
            if resp.status_code == 404 or message == "Object not found":
                raise NotFoundError(f"{method} {path}: {message}")
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

    async def _delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    # --- Mapping --------------------------------------------------------------

    async def _unit_context(self) -> tuple[dict[int, str], dict[tuple[int, int], float], Conv]:
        """Einheitennamen, je Produkt der Faktor Bestandseinheit → Gramm, alle Umrechnungen."""
        units = {u["id"]: u["name"] for u in await self._get("/objects/quantity_units")}
        gram_ids = {uid for uid, name in units.items() if name.strip().lower() in GRAM_UNIT_NAMES}
        factors: dict[tuple[int, int], float] = {}
        conversions: Conv = {}
        for conv in await self._get("/objects/quantity_unit_conversions_resolved"):
            key = (conv["product_id"], conv["from_qu_id"], conv["to_qu_id"])
            conversions[key] = float(conv["factor"])
            if conv["to_qu_id"] in gram_ids:
                factors[(conv["product_id"], conv["from_qu_id"])] = float(conv["factor"])
        return units, factors, conversions

    @staticmethod
    def _to_product(
        raw: dict,
        units: dict[int, str],
        factors: dict[tuple[int, int], float],
        conversions: Conv | None = None,
    ) -> Product:
        uf = raw.get("userfields") or {}
        mode = uf.get(UF_TRACKING_MODUS)
        qu_stock = raw["qu_id_stock"]
        grams = factors.get((raw["id"], qu_stock))
        if grams is None and units.get(qu_stock, "").strip().lower() in GRAM_UNIT_NAMES:
            grams = 1.0
        # Packung = Einkaufseinheit, wenn sie von der Bestandseinheit abweicht und umrechenbar ist.
        qu_purchase = raw.get("qu_id_purchase") or qu_stock
        pack_size = None
        if qu_purchase != qu_stock:
            conv = conversions or {}
            pack_size = conv.get((raw["id"], qu_purchase, qu_stock))
            if pack_size is None:
                pack_size = conv.get((None, qu_purchase, qu_stock))
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
            pack_unit=units.get(qu_purchase) if pack_size else None,
            pack_size=pack_size,
            min_stock=float(raw.get("min_stock_amount") or 0),
            category=uf.get(UF_KATEGORIE) or None,
            fill_level=uf.get(UF_FUELLSTAND) or None,
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
        ctx = await self._unit_context()
        raws = await self._get("/objects/products")
        return [self._to_product(r, *ctx) for r in raws if int(r.get("active", 1))]

    async def get_product(self, product_id: str) -> Product:
        ctx = await self._unit_context()
        return self._to_product(await self._get(f"/objects/products/{product_id}"), *ctx)

    async def list_locations(self) -> list[Location]:
        return [
            Location(id=str(r["id"]), name=r["name"], is_freezer=bool(int(r["is_freezer"])))
            for r in await self._get("/objects/locations")
            if int(r.get("active", 1))
        ]

    @staticmethod
    def _to_recipe(raw: dict, positions: list[dict]) -> Recipe:
        uf = raw.get("userfields") or {}
        tags = [t.strip() for t in (uf.get(UF_REZEPT_TAGS) or "").split(",") if t.strip()]
        return Recipe(
            id=str(raw["id"]),
            name=raw["name"],
            servings=int(raw.get("base_servings") or 1),
            description=raw.get("description") or "",
            tags=tuple(tags),
            status=uf.get(UF_REZEPT_STATUS) or None,
            effort=uf.get(UF_REZEPT_AUFWAND) or None,
            leftover_days=_int_or_none(uf.get(UF_REZEPT_REST_HALTBARKEIT)),
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

    async def update_batch(self, batch_id: str, patch: BatchPatch) -> None:
        roh = await self._get(f"/objects/stock/{batch_id}")
        ablauf = roh.get("best_before_date")
        if patch.ohne_ablauf:
            ablauf = NEVER_EXPIRES
        elif patch.best_before is not None:
            ablauf = patch.best_before.isoformat()
        body: dict[str, Any] = {
            # Grocy überschreibt alle Felder, daher die bisherigen Werte mitschicken.
            "amount": patch.amount if patch.amount is not None else float(roh["amount"]),
            "best_before_date": ablauf,
            "location_id": int(patch.location_id or roh["location_id"]),
            "shopping_location_id": roh.get("shopping_location_id"),
            "price": roh.get("price"),
            "open": patch.opened if patch.opened is not None else bool(int(roh.get("open") or 0)),
            "purchased_date": roh.get("purchased_date"),
            "note": roh.get("note"),
        }
        await self._put(f"/stock/entry/{batch_id}", body)

    async def consume_batch(self, batch_id: str, amount: float | None, *, spoiled: bool) -> None:
        roh = await self._get(f"/objects/stock/{batch_id}")
        menge = float(roh["amount"]) if amount is None else amount
        if menge > float(roh["amount"]) + 1e-9:
            raise InsufficientStockError(str(roh["product_id"]))
        await self._post(
            f"/stock/products/{roh['product_id']}/consume",
            {
                "amount": menge,
                "transaction_type": "consume",
                "spoiled": spoiled,
                "stock_entry_id": roh["stock_id"],
                "location_id": int(roh["location_id"]),
            },
        )

    async def delete_product(self, product_id: str) -> None:
        await self._delete(f"/objects/products/{int(product_id)}")

    async def delete_recipe(self, recipe_id: str) -> None:
        # Grocy löscht die Zutatenliste nicht mit.
        for pos in await self._get("/objects/recipes_pos"):
            if str(pos["recipe_id"]) == str(recipe_id):
                await self._delete(f"/objects/recipes_pos/{pos['id']}")
        await self._delete(f"/objects/recipes/{int(recipe_id)}")

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

    async def ensure_pack(
        self, product_id: str, unit_id: str, size: float, *, min_stock: float | None = None
    ) -> None:
        """Setzt Einkaufseinheit, Packungsgröße (in Bestandseinheit) und Mindestbestand.

        Idempotent. Grocy legt beim Wechsel der Einkaufseinheit selbst Umrechnungen mit
        Faktor 1 in beide Richtungen an, deshalb erst die Einheit setzen, dann beide
        Richtungen anlegen oder korrigieren.
        """
        product = await self._get(f"/objects/products/{product_id}")
        pid, qu, stock = int(product_id), int(unit_id), int(product["qu_id_stock"])
        changes: dict[str, Any] = {}
        if int(product["qu_id_purchase"]) != qu:
            changes["qu_id_purchase"] = qu
        if min_stock is not None and float(product.get("min_stock_amount") or 0) != min_stock:
            changes["min_stock_amount"] = min_stock
        if changes:
            await self._put(f"/objects/products/{product_id}", changes)
        if qu != stock:
            await self._ensure_conversion(pid, qu, stock, size)

    async def _ensure_conversion(self, pid: int, von: int, nach: int, faktor: float) -> None:
        """Produktspezifische Umrechnung von → nach (und zurück) anlegen oder korrigieren."""
        for a, b, f in ((von, nach, faktor), (nach, von, 1 / faktor)):
            # Grocy legt zur neuen Umrechnung teils selbst die Gegenrichtung an: neu lesen.
            conversions = [
                c
                for c in await self._get("/objects/quantity_unit_conversions")
                if c.get("product_id") == pid
            ]
            found = next(
                (c for c in conversions if c["from_qu_id"] == a and c["to_qu_id"] == b), None
            )
            if found is None:
                body = {"product_id": pid, "from_qu_id": a, "to_qu_id": b, "factor": f}
                await self._create("quantity_unit_conversions", body)
            elif abs(float(found["factor"]) - f) > 1e-9:
                await self._put(f"/objects/quantity_unit_conversions/{found['id']}", {"factor": f})

    async def set_pack(self, product_id: str, unit: str, size: float) -> None:
        await self.ensure_pack(product_id, await self.ensure_quantity_unit(unit), size)

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
        recipe = await self.create_recipe(
            name,
            servings,
            description,
            [Ingredient(pid, amount) for pid, amount in ingredients],
        )
        if userfields:
            await self._put(f"/userfields/recipes/{recipe.id}", userfields)
        return recipe.id, True

    # --- Anlegen (Import) -----------------------------------------------------

    async def create_product(self, name: str, unit: str) -> Product:
        found = await self._find("products", name=name)
        if found is None:
            unit_id = await self.ensure_quantity_unit(unit)
            locations = await self.list_locations()
            location = next((loc for loc in locations if loc.name == "Vorrat"), locations[0])
            await self.ensure_product(name, unit_id=unit_id, location_id=location.id)
            found = await self._find("products", name=name)
        return await self.get_product(str(found["id"]))

    async def create_recipe(
        self, name: str, servings: int, description: str, ingredients: list[Ingredient]
    ) -> Recipe:
        recipe_id = await self._create(
            "recipes", {"name": name, "base_servings": servings, "description": description}
        )
        await self._add_positions(recipe_id, ingredients)
        return await self.get_recipe(str(recipe_id))

    # --- Bearbeiten (Produktdatenbank, Rezeptbuch, Kategorisierung) -----------

    async def update_product(self, product_id: str, patch: ProductPatch) -> Product:
        raw = await self._get(f"/objects/products/{product_id}")
        body: dict[str, Any] = {}
        simple = {
            "name": patch.name,
            "min_stock_amount": patch.min_stock,
            "due_type": int(patch.due_type) if patch.due_type is not None else None,
            "default_best_before_days": patch.shelf_life_days,
            "default_best_before_days_after_open": patch.shelf_life_opened_days,
            "default_best_before_days_after_freezing": patch.shelf_life_frozen_days,
            "default_best_before_days_after_thawing": patch.shelf_life_thawed_days,
            "location_id": int(patch.default_location_id) if patch.default_location_id else None,
        }
        body = {k: v for k, v in simple.items() if v is not None}
        if patch.parent_id is not None:
            body["parent_product_id"] = int(patch.parent_id) if patch.parent_id else None
        if body:
            await self._put(f"/objects/products/{product_id}", body)
        uf: dict[str, Any] = {}
        if patch.tracking_mode is not None:
            uf[UF_TRACKING_MODUS] = str(patch.tracking_mode)
        if patch.category is not None:
            uf[UF_KATEGORIE] = patch.category
        if patch.tolerance_pct is not None:
            uf[UF_TOLERANZ_PCT] = patch.tolerance_pct
        if patch.fill_level is not None:
            uf[UF_FUELLSTAND] = patch.fill_level
        if uf:
            await self._put(f"/userfields/products/{product_id}", uf)
        if patch.grams_per_unit:
            stock = int(raw["qu_id_stock"])
            gram_id = int(await self.ensure_quantity_unit("g"))
            if stock != gram_id:
                await self._ensure_conversion(int(product_id), stock, gram_id, patch.grams_per_unit)
        return await self.get_product(product_id)

    async def update_recipe(self, recipe_id: str, patch: RecipePatch) -> Recipe:
        await self._get(f"/objects/recipes/{recipe_id}")  # existiert?
        body: dict[str, Any] = {}
        if patch.name is not None:
            body["name"] = patch.name
        if patch.servings is not None:
            body["base_servings"] = patch.servings
            body["desired_servings"] = patch.servings
        if patch.description is not None:
            body["description"] = patch.description
        if body:
            await self._put(f"/objects/recipes/{recipe_id}", body)
        uf: dict[str, Any] = {}
        if patch.tags is not None:
            uf[UF_REZEPT_TAGS] = ", ".join(t.strip() for t in patch.tags if t.strip())
        if patch.status is not None:
            uf[UF_REZEPT_STATUS] = patch.status
        if patch.effort is not None:
            uf[UF_REZEPT_AUFWAND] = patch.effort
        if patch.leftover_days is not None:
            uf[UF_REZEPT_REST_HALTBARKEIT] = patch.leftover_days
        if uf:
            await self._put(f"/userfields/recipes/{recipe_id}", uf)
        if patch.ingredients is not None:
            # Zutatenliste des Rezepts wird als Ganzes ersetzt (Bearbeiten im Rezeptbuch).
            alt = await self._get("/objects/recipes_pos", **{"query[]": f"recipe_id={recipe_id}"})
            for pos in alt:
                await self._delete(f"/objects/recipes_pos/{pos['id']}")
            await self._add_positions(int(recipe_id), patch.ingredients)
        return await self.get_recipe(recipe_id)

    async def _add_positions(self, recipe_id: int, ingredients: list[Ingredient]) -> None:
        products = {p["id"]: p for p in await self._get("/objects/products")}
        for zutat in ingredients:
            pid = int(zutat.product_id)
            if pid not in products:
                raise NotFoundError(f"Produkt {zutat.product_id}")
            body = {
                "recipe_id": recipe_id,
                "product_id": pid,
                "amount": zutat.amount,
                "qu_id": products[pid]["qu_id_stock"],
            }
            if zutat.note:
                body["note"] = zutat.note
            await self._create("recipes_pos", body)

    async def find_by_barcode(self, barcode: str) -> Product | None:
        rows = await self._get("/objects/product_barcodes", **{"query[]": f"barcode={barcode}"})
        return await self.get_product(str(rows[0]["product_id"])) if rows else None

    async def add_barcode(self, product_id: str, barcode: str) -> None:
        rows = await self._get("/objects/product_barcodes", **{"query[]": f"barcode={barcode}"})
        if rows:
            if str(rows[0]["product_id"]) != str(product_id):
                raise ValueError(
                    f"Barcode {barcode} gehört schon zu Produkt {rows[0]['product_id']}"
                )
            return
        await self._post(
            "/objects/product_barcodes", {"product_id": int(product_id), "barcode": barcode}
        )

    async def merge_products(self, keep_id: str, remove_id: str) -> None:
        """Grocy-Merge (/stock/products/{keep}/merge/{remove}) plus, was er auslässt.

        Grocy hängt Bestand, Log, Barcodes, Umrechnungen, Rezeptzutaten und Einkaufsliste um
        und rechnet Mengen über die Umrechnung um, aber nicht die Varianten
        (parent_product_id). Doppelte Umrechnungen (gleiches Einheitenpaar) würden beim
        Umhängen kollidieren und werden vorher am zu löschenden Produkt entfernt, außer die
        für die Mengen-Umrechnung nötige.
        """
        if keep_id == remove_id:
            raise ValueError("keep_id == remove_id")
        keep = await self._get(f"/objects/products/{keep_id}")
        remove = await self._get(f"/objects/products/{remove_id}")
        for child in await self._get(
            "/objects/products", **{"query[]": f"parent_product_id={remove_id}"}
        ):
            await self._put(f"/objects/products/{child['id']}", {"parent_product_id": int(keep_id)})
        conversions = await self._get("/objects/quantity_unit_conversions")
        keep_pairs = {
            (c["from_qu_id"], c["to_qu_id"])
            for c in conversions
            if c.get("product_id") == keep["id"]
        }
        noetig = {
            (remove["qu_id_stock"], keep["qu_id_stock"]),
            (keep["qu_id_stock"], remove["qu_id_stock"]),
        }
        for c in conversions:
            pair = (c["from_qu_id"], c["to_qu_id"])
            if c.get("product_id") == remove["id"] and pair in keep_pairs and pair not in noetig:
                await self._delete(f"/objects/quantity_unit_conversions/{c['id']}")
        await self._post(f"/stock/products/{keep_id}/merge/{remove_id}", {})
