"""Rezept-Import per Link: schema.org/Recipe (JSON-LD) lesen, Zutaten auf Produkte abbilden.

Übernommen werden nur Name, Portionen und Zutaten mit Mengen. Zubereitungstexte und Bilder
bleiben bei der Quelle (Link in der Beschreibung), eine eigene Kurzfassung kommt später per
ai_task. Die Zuordnung ist deterministisch (Alias, Name, Token-Ähnlichkeit) und wird immer
im Review bestätigt, bevor etwas in Grocy landet.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from html import escape, unescape
from urllib.parse import urlsplit

import httpx

from app.inventory.base import Product

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) rationPlaner/1.0"
BRUECHE = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
# Einheiten der Quelle → (Normalform, Faktor zur Basiseinheit g bzw. ml)
MASSE = {"g": ("g", 1), "gr": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000)}
SCHAETZUNG_G = {"el": 10.0, "tl": 5.0, "prise": 0.5, "msp": 0.5}  # grob, im Review prüfen
STUECK = {"stück", "stk", "st"}
EINHEITEN = (
    set(MASSE)
    | set(SCHAETZUNG_G)
    | STUECK
    | {
        "packung",
        "packungen",
        "packung(en)",
        "dose",
        "dosen",
        "dose(n)",
        "bund",
        "becher",
        "zehe",
        "zehen",
        "zehe(n)",
        "scheibe",
        "scheiben",
        "scheibe(n)",
        "beutel",
        "glas",
        "flasche",
        "netz",
        "handvoll",
        "cl",
        "dl",
    }
)


class ImportError_(Exception):
    """Quelle nicht lesbar oder kein Rezept gefunden."""


@dataclass
class RohZutat:
    text: str
    menge: float | None
    einheit: str | None  # normalisiert, klein
    name: str


@dataclass
class RohRezept:
    name: str
    portionen: int
    zutaten: list[RohZutat]
    url: str
    quelle: str
    quelle_id: str


@dataclass
class Zuordnung:
    """Vorschlag je Zutat. status: ok, vorschlag, einheit, schaetzung, unbekannt."""

    index: int
    text: str
    name: str
    menge: float | None
    einheit: str | None
    produkt_id: str | None = None
    produkt: str | None = None
    menge_bestand: float | None = None
    status: str = "unbekannt"
    hinweis: str = ""
    alternativen: list[dict] = field(default_factory=list)


# --- Quelle ----------------------------------------------------------------------


def quelle_von(url: str) -> tuple[str, str]:
    """(quelle, quelle_id). HelloFresh: 24-stellige Hex-ID am Ende des Slugs."""
    teile = urlsplit(url.strip())
    host = teile.netloc.lower().removeprefix("www.")
    m = re.search(r"-([0-9a-f]{24})/?$", teile.path)
    if "hellofresh" in host and m:
        return "hellofresh", m.group(1)
    return host or "unbekannt", f"{host}{teile.path.rstrip('/')}"


async def laden(url: str, client: httpx.AsyncClient | None = None) -> str:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "de"})
    except httpx.HTTPError as err:
        raise ImportError_(f"Seite nicht erreichbar: {err}") from err
    finally:
        if own:
            await client.aclose()
    if resp.status_code >= 400:
        raise ImportError_(f"Seite antwortet mit {resp.status_code}")
    return resp.text


def _recipes_in(data: object) -> list[dict]:
    if isinstance(data, list):
        return [r for item in data for r in _recipes_in(item)]
    if not isinstance(data, dict):
        return []
    typ = data.get("@type")
    if typ == "Recipe" or (isinstance(typ, list) and "Recipe" in typ):
        return [data]
    return _recipes_in(data.get("@graph", []))


def json_ld_rezept(html: str) -> dict:
    """Erstes schema.org/Recipe aus den JSON-LD-Blöcken der Seite."""
    blocks = re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.S | re.I
    )
    for block in blocks:
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if found := _recipes_in(data):
            return found[0]
    raise ImportError_("Kein schema.org/Recipe auf der Seite gefunden")


def _zahl(text: str) -> float | None:
    text = text.strip()
    for zeichen, wert in BRUECHE.items():
        if text.endswith(zeichen):
            ganz = text[: -len(zeichen)].strip()
            return (float(ganz) if ganz else 0) + wert
    if m := re.fullmatch(r"(\d+)\s*/\s*(\d+)", text):
        return int(m.group(1)) / int(m.group(2))
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def zutat_parsen(text: str) -> RohZutat:
    """'250 g Spitzpaprika', '½ Stück Zwiebel', '2 EL Olivenöl', 'Salz'."""
    clean = re.sub(r"\s+", " ", unescape(text)).strip()
    zahl = r"(\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?(?:\s*[½¼¾⅓⅔⅛])?|[½¼¾⅓⅔⅛])"
    m = re.match(rf"^{zahl}\s*(?:-\s*[\d.,]+\s*)?([^\d\s]+\.?)?\s+(.+)$", clean)
    if m and (menge := _zahl(m.group(1))) is not None:
        einheit = (m.group(2) or "").lower().rstrip(".")
        name = m.group(3)
        if einheit and einheit not in EINHEITEN:  # "2 Zwiebeln" → keine Einheit
            name, einheit = f"{m.group(2)} {name}", ""
        return RohZutat(clean, menge, einheit or None, name.strip())
    if m := re.match(r"^([\d.,½¼¾]+)\s*(g|kg|ml|l)\s+(.+)$", clean, re.I):
        return RohZutat(clean, _zahl(m.group(1)), m.group(2).lower(), m.group(3).strip())
    return RohZutat(clean, None, None, clean)


def _portionen(value: object) -> int:
    if isinstance(value, list):
        value = value[0] if value else 1
    if m := re.search(r"\d+", str(value)):
        return max(int(m.group()), 1)
    return 1


def rezept_aus_html(html: str, url: str) -> RohRezept:
    data = json_ld_rezept(html)
    quelle, quelle_id = quelle_von(url)
    zutaten = [zutat_parsen(str(z)) for z in data.get("recipeIngredient") or []]
    if not zutaten:
        raise ImportError_("Rezept ohne Zutatenliste")
    return RohRezept(
        name=unescape(str(data.get("name") or "Importiertes Rezept")).strip(),
        portionen=_portionen(data.get("recipeYield")),
        zutaten=zutaten,
        url=url.strip(),
        quelle=quelle,
        quelle_id=quelle_id,
    )


# --- Zuordnung ---------------------------------------------------------------------


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"\(.*?\)", " ", text)
    return re.sub(r"[^a-zäöüß ]+", " ", text).strip()


def _stamm(wort: str) -> str:
    for endung in ("en", "n", "e", "s"):
        if len(wort) > 4 and wort.endswith(endung):
            return wort[: -len(endung)]
    return wort


def _tokens(text: str) -> set[str]:
    return {_stamm(w) for w in _norm(text).split() if len(w) > 2}


def aehnlichkeit(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    gemeinsam = len(ta & tb)
    # Teilwort: "Spitzpaprika" enthält "paprika"
    teil = sum(
        1 for x in ta for y in tb if x != y and (x in y or y in x) and min(len(x), len(y)) > 4
    )
    # Bezug: Tokens der Zutat. Zusätze am Produktnamen ("Paprika rot") kosten wenig.
    return (gemeinsam + 0.8 * teil) / len(ta) - 0.05 * max(len(tb) - len(ta), 0)


def menge_in_bestand(z: RohZutat, p: Product) -> tuple[float | None, str, str]:
    """(Menge in Bestandseinheit, status, hinweis)."""
    if z.menge is None:
        return 0.0, "ok", "ohne Mengenangabe"
    einheit, ziel = z.einheit, p.unit.strip().lower()
    if einheit == ziel:
        return z.menge, "ok", ""
    if einheit in MASSE:
        basis, faktor = MASSE[einheit]
        if ziel == basis or (ziel == "g" and basis == "ml"):
            hinweis = "ml als g gerechnet" if basis != ziel else ""
            return z.menge * faktor, "ok" if not hinweis else "schaetzung", hinweis
    if einheit is None or einheit in STUECK:
        if ziel in STUECK:
            return z.menge, "ok", ""
        if p.pack_unit and p.pack_unit.lower() in STUECK and p.pack_size:
            return z.menge * p.pack_size, "ok", f"1 Stück = {p.pack_size:g} {p.unit}"
    if einheit and p.pack_unit and einheit.startswith(p.pack_unit.lower()[:4]) and p.pack_size:
        return z.menge * p.pack_size, "ok", f"1 {p.pack_unit} = {p.pack_size:g} {p.unit}"
    if einheit in SCHAETZUNG_G and ziel == "g":
        return (
            z.menge * SCHAETZUNG_G[einheit],
            "schaetzung",
            f"1 {einheit.upper()} ≈ {SCHAETZUNG_G[einheit]:g} g",
        )
    return None, "einheit", f"{z.einheit or 'Stück'} → {p.unit} unbekannt"


def zuordnen(rezept: RohRezept, produkte: list[Product], aliase: dict[str, str]) -> list[Zuordnung]:
    by_id = {p.id: p for p in produkte}
    by_name = {_norm(p.name): p for p in produkte}
    result = []
    for i, z in enumerate(rezept.zutaten):
        zo = Zuordnung(i, z.text, z.name, z.menge, z.einheit)
        key = _norm(z.name)
        treffer: Product | None = None
        if (pid := aliase.get(key)) and pid in by_id:
            treffer, zo.status = by_id[pid], "ok"
        elif key in by_name:
            treffer, zo.status = by_name[key], "ok"
        else:
            ranking = sorted(
                ((aehnlichkeit(z.name, p.name), p) for p in produkte), key=lambda x: -x[0]
            )
            zo.alternativen = [
                {"produkt_id": p.id, "name": p.name, "score": round(s, 2)}
                for s, p in ranking[:3]
                if s > 0
            ]
            if ranking and ranking[0][0] >= 0.5:
                treffer, zo.status = ranking[0][1], "vorschlag"
        if treffer:
            zo.produkt_id, zo.produkt = treffer.id, treffer.name
            menge, status, hinweis = menge_in_bestand(z, treffer)
            zo.menge_bestand, zo.hinweis = (round(menge, 1) if menge is not None else None), hinweis
            if menge is None or (status != "ok" and zo.status == "ok"):
                zo.status = status
        result.append(zo)
    return result


def beschreibung(rezept: RohRezept) -> str:
    """Kein Fremdtext: nur Verweis auf die Quelle. Kurzfassung kommt später per ai_task."""
    url = escape(rezept.url, quote=True)
    return (
        f'<p>Zubereitung: siehe <a href="{url}">Originalrezept ({escape(rezept.quelle)})</a>.</p>'
    )


def alias_key(text: str) -> str:
    return _norm(text)
