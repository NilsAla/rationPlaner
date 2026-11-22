"""Rezept-Import per Link: schema.org/Recipe (JSON-LD) lesen, Zutaten auf Produkte abbilden.

Übernommen werden Name, Portionen, Zutaten mit Mengen und die Zubereitungsschritte
(recipeInstructions, für die private Datenbank, Entscheidung Nils). Bilder bleiben bei der
Quelle, der Link steht unter den Schritten. Die Zuordnung ist deterministisch (Alias, Name,
Token-Ähnlichkeit) und wird immer im Review bestätigt, bevor etwas in Grocy landet.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from html import escape, unescape
from urllib.parse import urlsplit

import httpx

from app import mengen
from app.inventory.base import Product

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) rationPlaner/1.0"
BRUECHE = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
# Einheiten der Quelle → (Normalform, Faktor zur Basiseinheit g bzw. ml)
MASSE = {"g": ("g", 1), "gr": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000)}
STUECK = {"stück", "stk", "st"}
EINHEITEN = (
    set(MASSE)
    | {"el", "tl", "prise", "msp", "messerspitze", "schuss", "spritzer"}
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


# Ausgeschriebene und abgekürzte Einheiten → Normalform
EINHEIT_ALIAS = {
    "esslöffel": "el",
    "essl": "el",
    "eßl": "el",
    "teelöffel": "tl",
    "teel": "tl",
    "gramm": "g",
    "kilogramm": "kg",
    "kilo": "kg",
    "milliliter": "ml",
    "liter": "l",
    "stk": "stück",
    "stücke": "stück",
    "stück(e)": "stück",
    "prisen": "prise",
    "zentiliter": "cl",
    "deziliter": "dl",
    "bünde": "bund",
    "bund(e)": "bund",
}


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
    schritte: list[str] = field(default_factory=list)


@dataclass
class Zuordnung:
    """Vorschlag je Zutat.

    aktion: zuordnen (produkt_id), neu (Produkt neu_name anlegen) oder weglassen.
    status: ok (sicher), schaetzung (Menge geschätzt) oder pruefen (Menge fehlt).
    """

    index: int
    text: str
    name: str
    menge: float | None
    einheit: str | None
    aktion: str = "neu"
    produkt_id: str | None = None
    produkt: str | None = None
    neu_name: str | None = None
    stueck_g: float | None = None  # Stückgewicht für neue Produkte (Grocy-Umrechnung)
    menge_bestand: float | None = None
    status: str = "ok"
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
        einheit = EINHEIT_ALIAS.get(einheit, einheit)
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


MAX_SCHRITTE = 40
MAX_SCHRITT_ZEICHEN = 1500


def _klartext(text: str) -> str:
    """HTML-Reste und Entities raus, Leerraum je Zeile normalisieren."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", str(text))
    text = unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", z).strip() for z in text.splitlines()).strip()


def schritte_aus_text(text: str) -> list[str]:
    """Fließtext in Schritte: an „1.“, „2)“, „Schritt 3:“ trennen, sonst an Zeilenumbrüchen.

    Nummern mitten im Satz („ca. 2. Hälfte“) zählen nur, wenn sie aufsteigend ab 1 kommen.
    """
    text = _klartext(text)
    if not text:
        return []
    marke = re.compile(r"(?:^|(?<=[\s.!?:]))(?:Schritt\s*)?(\d{1,2})\s*[.):]\s+(?=\S)", re.I)
    treffer, erwartet = [], 1
    for m in marke.finditer(text):
        if int(m.group(1)) == erwartet:
            treffer.append(m)
            erwartet += 1
    if len(treffer) >= 2:
        teile = []
        if vorspann := text[: treffer[0].start()].strip():
            teile.append(vorspann)
        for i, m in enumerate(treffer):
            ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
            teile.append(text[m.end() : ende])
    else:
        teile = re.split(r"\n+", text)
    return [re.sub(r"\s+", " ", t).strip() for t in teile if t.strip()]


def schritte_aus(instructions: object) -> list[str]:
    """recipeInstructions: String, Liste, HowToStep, HowToSection (verschachtelt)."""
    if instructions is None:
        return []
    if isinstance(instructions, str):
        return schritte_aus_text(instructions)
    if isinstance(instructions, list):
        return [s for item in instructions for s in schritte_aus(item)]
    if isinstance(instructions, dict):
        if "itemListElement" in instructions:
            unter = schritte_aus(instructions["itemListElement"])
            titel = _klartext(instructions.get("name") or "")
            if titel and unter and instructions.get("@type") == "HowToSection":
                return [f"{titel}:", *unter]
            return unter
        text = instructions.get("text") or instructions.get("name") or ""
        # HowToStep ist schon ein Schritt, Zeilenumbrüche darin nur glätten
        return [t] if (t := re.sub(r"\s+", " ", _klartext(text)).strip()) else []
    return []


def schritte_begrenzen(schritte: list[str]) -> list[str]:
    return [s[:MAX_SCHRITT_ZEICHEN] for s in schritte if s][:MAX_SCHRITTE]


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
        schritte=schritte_begrenzen(schritte_aus(data.get("recipeInstructions"))),
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


def _staemme(text: str) -> list[str]:
    return [_stamm(w) for w in _norm(text).split() if len(w) >= 2]


def kern_name(name: str) -> str:
    """Produktname aus dem Zutatentext: Klammern, Zusätze nach Komma und vorangestellte
    Adjektive (klein geschrieben) weg. "vegane weiße Misopaste" → "Misopaste"."""
    name = re.sub(r"\(.*?\)", " ", name).split(",")[0]
    woerter = name.split()
    while len(woerter) > 1 and woerter[0][:1].islower():
        woerter.pop(0)
    kern = " ".join(woerter).strip(" .-") or name.strip()
    return kern[:1].upper() + kern[1:]


def _schluessel(text: str) -> frozenset[str]:
    return frozenset(_staemme(text))


def aehnlichkeit(a: str, b: str) -> float:
    """Nur für die Alternativen-Liste, nie für automatische Zuordnung."""
    ta, tb = set(_staemme(a)), set(_staemme(b))
    if not ta or not tb:
        return 0.0
    teil = sum(
        1 for x in ta for y in tb if x != y and (x in y or y in x) and min(len(x), len(y)) > 3
    )
    return (len(ta & tb) + 0.5 * teil) / min(len(ta), len(tb))


def gramm(z: RohZutat, name: str, stueck_g: float | None = None) -> tuple[float | None, str, str]:
    """Menge in g (bzw. ml ≈ g über Dichte). Gibt (Menge, status, hinweis) zurück.

    stueck_g: Stückgewicht des Produkts aus Grocy, falls bekannt. Die Tabelle für den
    konkreten Zutatennamen geht vor (Knoblauchzehe 4 g, nicht Knolle 50 g).
    """
    if z.menge is None:
        return 0.0, "ok", "ohne Mengenangabe"
    staemme = _staemme(name)
    e = z.einheit
    if e in ("cl", "dl"):
        z = RohZutat(z.text, z.menge * (10 if e == "cl" else 100), "ml", z.name)
        e = "ml"
    if e in MASSE:
        basis, faktor = MASSE[e]
        if basis == "g":
            return z.menge * faktor, "ok", ""
        dichte = mengen.nachschlagen(mengen.DICHTE, staemme)
        return (
            z.menge * faktor * (dichte or 1.0),
            "ok" if dichte else "schaetzung",
            f"Dichte {dichte:g}" if dichte else "ml als g gerechnet",
        )
    if e in ("el", "tl"):
        dichte = mengen.nachschlagen(mengen.DICHTE, staemme)
        g = mengen.LOEFFEL_ML[e] * dichte if dichte else mengen.LOEFFEL_FEST_G[e]
        return z.menge * g, "schaetzung", f"1 {e.upper()} ≈ {g:g} g"
    if e in mengen.KLEIN_G:
        return z.menge * mengen.KLEIN_G[e], "schaetzung", f"1 {e} ≈ {mengen.KLEIN_G[e]:g} g"
    if e == "bund":
        if g := mengen.nachschlagen(mengen.BUND_G, staemme):
            return z.menge * g, "schaetzung", f"1 Bund ≈ {g:g} g"
        return None, "pruefen", "Gewicht je Bund unbekannt"
    if e is None or e in STUECK or (e and e.startswith("zehe")):
        g = mengen.nachschlagen(mengen.STUECK_G, staemme) or stueck_g
        if e and e.startswith("zehe"):
            g = mengen.STUECK_G["knoblauchzeh"]
        if g:
            return z.menge * g, "schaetzung", f"1 Stück ≈ {g:g} g"
        return None, "pruefen", "Stückgewicht unbekannt"
    return None, "pruefen", f"Einheit „{e}“ unbekannt"


def zuordnen(rezept: RohRezept, produkte: list[Product], aliase: dict[str, str]) -> list[Zuordnung]:
    """Sicher zuordnen (Alias, gleicher Name, gleicher Kern), sonst neues Produkt vorschlagen.

    Ähnliche Produkte erscheinen nur als Alternative: "Frühlingszwiebel" ist keine "Zwiebel".
    """
    by_id = {p.id: p for p in produkte}
    by_key: dict[frozenset[str], Product] = {}
    for p in produkte:
        by_key.setdefault(_schluessel(p.name), p)
        by_key.setdefault(_schluessel(kern_name(p.name)), p)
    result = []
    for i, z in enumerate(rezept.zutaten):
        zo = Zuordnung(i, z.text, z.name, z.menge, z.einheit)
        kern = kern_name(z.name)
        staemme = _staemme(kern)
        synonym = mengen.SYNONYM.get(staemme[-1]) if staemme else None
        if staemme and staemme[-1] in mengen.WEGLASSEN:
            zo.aktion, zo.hinweis = "weglassen", "wird nicht geführt"
            result.append(zo)
            continue
        treffer = None
        if (pid := aliase.get(alias_key(z.name))) and pid in by_id:
            treffer = by_id[pid]
        else:
            for kandidat in (z.name, kern, synonym):
                if kandidat and (treffer := by_key.get(_schluessel(kandidat))):
                    break
        stueck_g = None
        if treffer:
            zo.aktion, zo.produkt_id, zo.produkt = "zuordnen", treffer.id, treffer.name
            if treffer.pack_unit and treffer.pack_unit.lower() in STUECK:
                stueck_g = treffer.pack_size
        else:
            zo.neu_name = synonym or kern
            zo.alternativen = [
                {"produkt_id": p.id, "name": p.name}
                for s, p in sorted(
                    ((aehnlichkeit(kern, p.name), p) for p in produkte), key=lambda x: -x[0]
                )[:3]
                if s >= 0.3
            ]
        unit = (treffer.unit if treffer else "g").strip().lower()
        if unit in ("g", "ml"):
            menge, zo.status, zo.hinweis = gramm(z, z.name, stueck_g)
        elif z.einheit == unit or (z.einheit is None and unit in STUECK):
            menge, zo.status = z.menge or 0.0, "ok"
        else:
            menge, zo.status, zo.hinweis = None, "pruefen", f"Einheit {z.einheit} → {treffer.unit}"
        zo.menge_bestand = round(menge, 1) if menge is not None else None
        if zo.aktion == "neu" and not synonym and (z.einheit is None or z.einheit in STUECK):
            zo.stueck_g = mengen.nachschlagen(mengen.STUECK_G, _staemme(z.name))
        result.append(zo)
    return result


def beschreibung(rezept: RohRezept, schritte: list[str] | None = None) -> str:
    """Schritte als <p>-Absätze (Küchen-Card zeigt sie einzeln), darunter der Quellenlink."""
    liste = rezept.schritte if schritte is None else schritte
    absaetze = "".join(f"<p>{escape(t.strip())}</p>" for t in liste if t.strip())
    return absaetze + quelle_link(rezept.url, rezept.quelle)


def quelle_link(url: str, quelle: str) -> str:
    return (
        f'<p>Quelle: <a href="{escape(url, quote=True)}">Originalrezept ({escape(quelle)})</a></p>'
    )


def alias_key(text: str) -> str:
    return _norm(text)
