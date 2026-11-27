"""Rezept-Import per Link: schema.org/Recipe (JSON-LD) lesen, Zutaten auf Produkte abbilden.

Übernommen werden Name, Portionen, Zutaten mit Mengen und die Zubereitungsschritte
(recipeInstructions, für die private Datenbank, Entscheidung Nils). Haupt- und Schrittbilder
werden nach der Übernahme lokal gespeichert (`fotos.py`), der Link steht unter den Schritten.
Die Zuordnung ist deterministisch (Alias, Name, Token-Ähnlichkeit) und wird immer im Review
bestätigt, bevor etwas in Grocy landet.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from html import escape, unescape
from urllib.parse import urldefrag, urlsplit

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
        "tube",
        "kugel",
        "zweig",
        "stiel",
        "stängel",
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
    "pck": "packung",
    "pkg": "packung",
    "päckchen": "packung",
    "kopf": "stück",  # "1 Kopf Brokkoli"
    "paket": "packung",
    "tuben": "tube",
    "kugeln": "kugel",
    "zweige": "zweig",
    "stiele": "stiel",
    "köpfe": "stück",
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
    bild: str | None = None  # Hauptbild-URL
    schritt_bilder: dict[str, str] = field(default_factory=dict)  # "0" → URL (JSON-Schlüssel)


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
    sicher: bool = False  # Code-Treffer ohne Zweifel (bestätigter Alias, gleicher voller Name)
    ki: bool = False  # Vorschlag stammt von der KI
    kategorie: str | None = None  # Kategorie laut KI (Prüfung von KI-Treffern)
    pruefung: str = ""  # Begründung der KI (Vorschau)
    treffer: str | None = None  # KI-Treffer: gleich / oberbegriff / austauschbar (Vorschau-Farbe)
    attribute: dict = field(default_factory=dict)  # neue Produkte: geprüfte KI-Werte
    variante_von: dict | None = None  # neue Produkte: {"produkt_id", "name"} des Oberprodukts


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


_BLOCK = re.compile(r"<(h[1-4]|ul|ol)\b[^>]*>(.*?)</\1>", re.S | re.I)
_ZUTATEN = re.compile(r"(?i)^zutaten\b")
_ZUBEREITUNG = re.compile(r"(?i)^(zubereitung|anleitung|so geht'?s)\b")


def rezept_aus_ueberschriften(html: str) -> dict:
    """Rezept ohne JSON-LD (z. B. Chefkoch-Magazinartikel) als schema.org/Recipe-Dict.

    Gesucht wird eine Überschrift „Zutaten (für N Portionen)“, danach Listen als Zutaten bis
    „Zubereitung“, danach Listen als Schritte bis zur nächsten h2 oder dem nächsten Rezept.
    Name ist die Überschrift direkt davor, sonst die h1 bis zum Doppelpunkt. Nur das erste
    Rezept der Seite; weitere (Galerie, „Das passt dazu“) bleiben eigene Links.
    """
    if "ds-subscription-card--plus" in html:
        raise ImportError_("Chefkoch-PLUS-Rezept: nur mit Abo sichtbar, Import nicht möglich")
    bloecke = [(m.group(1).lower(), m.group(2)) for m in _BLOCK.finditer(html)]
    start = next(
        (
            i
            for i, (tag, inhalt) in enumerate(bloecke)
            if tag.startswith("h") and _ZUTATEN.match(_klartext(inhalt))
        ),
        None,
    )
    if start is None:
        raise ImportError_("Kein schema.org/Recipe und keine Überschrift „Zutaten“ gefunden")
    kopf = _klartext(bloecke[start][1])
    name = next((_klartext(x) for tag, x in reversed(bloecke[:start]) if tag in ("h2", "h3")), "")
    if not name and (m := re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)):
        name = _klartext(m.group(1)).split(":")[0]
    zutaten: list[str] = []
    schritte: list[dict] = []
    in_schritten = False
    for tag, inhalt in bloecke[start + 1 :]:
        text = re.sub(r"\s+", " ", _klartext(inhalt)).strip()
        if tag.startswith("h"):
            if _ZUBEREITUNG.match(text):
                in_schritten = True
            elif _ZUTATEN.match(text) or (in_schritten and (tag in ("h1", "h2") or not text)):
                break  # nächstes Rezept oder nächster Abschnitt
            elif in_schritten and schritte:
                schritte.append({"@type": "HowToStep", "text": f"{text.rstrip(':')}:"})
            continue
        punkte = [
            t
            for li in re.findall(r"<li\b[^>]*>(.*?)</li>", inhalt, re.S | re.I)
            if (t := re.sub(r"\s+", " ", _klartext(li)).strip())
        ]
        if in_schritten:
            schritte += [{"@type": "HowToStep", "text": t} for t in punkte]
        else:
            zutaten += punkte
    if not schritte:  # ohne Zubereitung ist es kein Rezept (Footer-Listen, Paywall)
        raise ImportError_("Kein schema.org/Recipe und keine Zubereitungsschritte gefunden")
    return {
        "@type": "Recipe",
        "name": name,
        "recipeYield": kopf,
        "recipeIngredient": zutaten,
        "recipeInstructions": schritte,
    }


def rezept_daten(html: str) -> dict:
    """schema.org/Recipe aus JSON-LD, sonst aus den Überschriften der Seite."""
    try:
        return json_ld_rezept(html)
    except ImportError_:
        return rezept_aus_ueberschriften(html)


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


# Füllwörter, die nicht zum Zutatennamen gehören ("nach Geschmack Salz" → "Salz")
ZUSATZ = re.compile(
    r"(?i)(?<![\wäöüß])(?:je\s+)?(?:nach\s+(?:geschmack|belieben|bedarf)|bei\s+bedarf|"
    r"zum\s+(?:ab)?schmecken|zum\s+(?:be)?streuen|zum\s+garnieren|zum\s+servieren|"
    r"zum\s+(?:an)?braten|für\s+(?:das|den|die)\s+(?:koch|nudel)?wasser|zum\s+frittieren|zum\s+würzen|zum\s+einfetten|für\s+die\s+form|"
    r"optional|evtl\.?|eventuell|ggf\.?|n\.\s?b\.|etwas|wenig|reichlich|"
    r"frisch\s+gemahlen(?:er|es|e)?|aus\s+der\s+mühle)(?![\wäöüß])"
)
# "Salz und Pfeffer", "Salz, Pfeffer, Muskat" ohne Menge: je eine Zutat
_TEIL = r"(?:[a-zäöüß]+\s+)?[A-ZÄÖÜ][\wäöüß-]*(?:\s*\([^()]*\))?"
_TRENNER = r"\s*,\s*|\s+(?:und|&|\+|sowie)\s+"
LISTE = re.compile(rf"^{_TEIL}(?:(?:{_TRENNER}){_TEIL})+$")


def _ohne_zusatz(text: str) -> str:
    text = ZUSATZ.sub(" ", text)
    text = re.sub(r"\(\s*(?:plus|und|oder|sowie)?\s*\)", " ", text)  # "(plus etwas)"
    return re.sub(r"\s+", " ", text).strip(" ,;:-")


def zutaten_parsen(text: str) -> list[RohZutat]:
    """Wie zutat_parsen, trennt aber "Salz und Pfeffer" (ohne Menge) in einzelne Zutaten."""
    z = zutat_parsen(text)
    # "Salz und Pfeffer, schwarzer": Zusatz hinter dem Komma stört die Liste
    liste = re.sub(r",\s*[a-zäöüß][a-zäöüß ]*$", "", z.name)
    if z.menge is None and LISTE.match(liste):
        return [RohZutat(z.text, None, None, n) for n in re.split(_TRENNER, liste)]
    return [z]


def _einheit(text: str) -> str:
    """Normalform: "Prise(n)" → prise, "Scheibe/n" → scheibe, "Pck." → packung."""
    e = re.sub(r"(?:\(e?n?\)|/e?n?)$", "", text.lower().rstrip("."))
    return EINHEIT_ALIAS.get(e, e)


def _ist_einheit(text: str) -> bool:
    return _einheit(text) in EINHEITEN


def zutat_parsen(text: str) -> RohZutat:
    """'250 g Spitzpaprika', '½ Stück Zwiebel', '2 EL Olivenöl', 'Salz', 'Salz nach Geschmack'."""
    original = re.sub(r"\s+", " ", unescape(text)).strip()
    clean = _ohne_zusatz(original) or original
    clean = re.sub(r"(?<=[\wäöüß.)])\*", "", clean)  # HelloFresh-Vorrat: "Essig*"
    clean = re.sub(r"(?i)^(?:ca\.?|circa|etwa|ungefähr|rund)\s+(?=[\d½¼¾⅓⅔⅛])", "", clean)
    # "1 TL, gehäuft Speisestärke" (Chefkoch): Zusatz hinter der Einheit fällt weg
    clean = re.sub(
        r"(?i)^([\d.,/½¼¾⅓⅔⅛\s]+[^\d\s,]+?)\s*,?\s*(?:gehäuft|gestrichen|gestr\.|knapp|voll)\s+",
        lambda m: m.group(1) + " " if _ist_einheit(m.group(1).split()[-1]) else m.group(0),
        clean,
    )
    # "1 kl. Glas Kapern", "1 gehäufter TL Zucker": Größe vor der Einheit fällt weg
    clean = re.sub(
        r"(?i)^([\d.,/½¼¾⅓⅔⅛\s]+)(?:kl\.|gr\.|kleine[snr]?|große[snr]?|gehäufte[rn]?|"
        r"gestrichene[rn]?|knappe[rn]?)\s+(?=([^\d\s]+)\s)",
        lambda m: m.group(1) if _ist_einheit(m.group(2)) else m.group(0),
        clean,
    )
    # Einheit ohne Zahl: "Prise Salz", "Schuss Essig" → 1
    if m := re.match(r"^([^\d\s]+)\s+(.+)$", clean):
        e = EINHEIT_ALIAS.get(m.group(1).lower().rstrip("."), m.group(1).lower().rstrip("."))
        if e in {"prise", "msp", "messerspitze", "schuss", "spritzer", "handvoll", "bund"}:
            return RohZutat(original, 1.0, e, m.group(2).strip())
        if e in STUECK:  # "Stück Salz" (ältere HelloFresh-Rezepte): keine Menge
            return RohZutat(original, None, None, m.group(2).strip())
    zahl = r"(\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?(?:\s*[½¼¾⅓⅔⅛])?|[½¼¾⅓⅔⅛])"
    m = re.match(rf"^{zahl}\s*(?:-\s*[\d.,]+\s*)?([^\d\s]+\.?)?\s+(.+)$", clean)
    if m and (menge := _zahl(m.group(1))) is not None:
        einheit = _einheit(m.group(2) or "")
        name = m.group(3)
        if einheit and einheit not in EINHEITEN:  # "2 Zwiebeln" → keine Einheit
            name, einheit = f"{m.group(2)} {name}", ""
        return RohZutat(original, menge, einheit or None, name.strip())
    if m := re.match(r"^([\d.,½¼¾]+)\s*(g|kg|ml|l)\s+(.+)$", clean, re.I):
        return RohZutat(original, _zahl(m.group(1)), m.group(2).lower(), m.group(3).strip())
    return RohZutat(original, None, None, clean)


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
    text = _ohne_varianten(_klartext(text))
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


def _ohne_varianten(text: str) -> str:
    """HelloFresh: „300 ml [450 ml | 600 ml] Wasser\\*“ → „300 ml Wasser“.

    In eckigen Klammern stehen die Mengen für 3 und 4 Personen (Trenner „|“ oder „ I “,
    auch als Wort: „Die Hälfte [drei Viertel | den ganzen]“),
    das Sternchen markiert Vorratszutaten. Skaliert wird bei uns per Code.
    """
    text = re.sub(r"\s*\[[^\[\]]*(?:\||\d[^\[\]]*\sI\s)[^\[\]]*\]", "", text)
    return re.sub(r"(?<=[\wäöüß.)])\\?\*", "", text)


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
            # „Zubereitung:“ als einziger Abschnitt (Chefkoch) ist kein Schritt
            if _ZUBEREITUNG.match(titel):
                titel = ""
            if titel and unter and instructions.get("@type") == "HowToSection":
                return [f"{titel}:", *unter]
            return unter
        text = _klartext(instructions.get("text") or instructions.get("name") or "")
        text = _ohne_varianten(text)
        # HowToStep ist schon ein Schritt, Zeilenumbrüche darin nur glätten
        return [t] if (t := re.sub(r"\s+", " ", text).strip()) else []
    return []


def schritte_begrenzen(schritte: list[str]) -> list[str]:
    return [s[:MAX_SCHRITT_ZEICHEN] for s in schritte if s][:MAX_SCHRITTE]


def _hf_rezept(html: str) -> dict:
    """HelloFresh: Rezeptobjekt aus __NEXT_DATA__ (props.pageProps.ssrPayload.recipe)."""
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S | re.I)
    try:
        r = json.loads(m.group(1))["props"]["pageProps"]["ssrPayload"]["recipe"] if m else {}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
    return r if isinstance(r, dict) else {}


def _name(data: dict, quelle: str, html: str) -> str:
    name = re.sub(r"\s+", " ", unescape(str(data.get("name") or "Importiertes Rezept"))).strip()
    if "chefkoch" in quelle:  # „… von Mirko1710“: Chefkoch hängt den Nutzernamen an
        name = re.sub(r"\s+von\s+\S+$", "", name)
    if quelle == "hellofresh":
        # JSON-LD-Name = Name + Untertitel ohne Trenner. Beschreibender Untertitel bleibt,
        # Werbung mit Zahlen („Nur 10 Minuten aktive Kochzeit“, „50 % weniger CO2e“) fällt weg.
        hf = _hf_rezept(html)
        kurz, unter = str(hf.get("name") or "").strip(), str(hf.get("headline") or "").strip()
        if kurz and name.startswith(kurz):
            name = f"{kurz}, {unter}" if unter and not re.search(r"\d", unter) else kurz
    return name


# Chefkoch-Magazin: Artikel mit eingebetteter Rezeptkarte statt Rezept („Das Rezept aus dem Video“)
_KARTE = re.compile(
    r'ds-list-view-item--recipe[^>]*>(?:(?!</a>).)*?href="(https://www\.chefkoch\.de/rezepte/\d+/[^"#?]+)"'
    r'|href="(https://www\.chefkoch\.de/rezepte/\d+/[^"#?]+)"[^>]*class="[^"]*ds-list-view-item--recipe',
    re.S,
)


def rezept_karte(html: str) -> str | None:
    """Link der einzigen eingebetteten Rezeptkarte, wenn die Seite selbst kein Rezept hat.
    Galerie-Links („weitere Rezepte“) zählen nicht."""
    links = {a or b for a, b in _KARTE.findall(html)}
    return links.pop() if len(links) == 1 else None


async def rezept_laden(url: str, html: str | None = None) -> RohRezept:
    """Seite lesen. Zeigt ein Artikel nur auf ein Rezept, dieses laden (Quelle wird das Rezept)."""
    html = html or await laden(url)
    try:
        return rezept_aus_html(html, url)
    except ImportError_:
        if not (ziel := rezept_karte(html)):
            # Sammelseite („Das perfekte Dinner“): welches Rezept gemeint ist, weiß nur der Nutzer
            n = len(set(re.findall(r'href="(https://www\.chefkoch\.de/rezepte/\d+/)', html)))
            if n >= 2:
                raise ImportError_(
                    f"Die Seite enthält kein einzelnes Rezept, sondern verlinkt {n} Rezepte. "
                    "Bitte den Link eines dieser Rezepte einfügen."
                ) from None
            raise
    return rezept_aus_html(await laden(ziel), ziel)


def rezept_aus_html(html: str, url: str) -> RohRezept:
    url = urldefrag(url.strip()).url  # „#gallery-…“ gehört nicht zum Rezept
    data = rezept_daten(html)
    quelle, quelle_id = quelle_von(url)
    from app.fotos import bilder_aus_html

    bild, schritt_bilder = bilder_aus_html(html, data)
    zutaten = [x for z in data.get("recipeIngredient") or [] for x in zutaten_parsen(str(z))]
    if not zutaten:
        raise ImportError_("Rezept ohne Zutatenliste")
    return RohRezept(
        name=_name(data, quelle, html),
        portionen=_portionen(data.get("recipeYield")),
        zutaten=zutaten,
        url=url,
        quelle=quelle,
        quelle_id=quelle_id,
        schritte=schritte_begrenzen(schritte_aus(data.get("recipeInstructions"))),
        bild=bild,
        schritt_bilder={str(k): v for k, v in schritt_bilder.items()},
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


# Zusätze hinter dem Produktnamen: "Paprika multicolor", "Hartkäse ital. Art"
NACHGESTELLT = re.compile(
    r"(?i)(?:\s+(?:multicolor|glatt|kraus|geraspelt|gerieben|gehackt|getrocknet|frisch|"
    r"ital\.\s*art|italienischer\s+art|natur|bio))+\s*$"
)


def kern_name(name: str) -> str:
    """Produktname aus dem Zutatentext: Klammern, Zusätze nach Komma und vorangestellte
    Adjektive (klein geschrieben) weg. "vegane weiße Misopaste" → "Misopaste"."""
    name = re.sub(r"\(.*?\)", " ", name).split(",")[0]
    name = NACHGESTELLT.sub("", name)
    name = re.sub(r"(?<=[a-zäöüß])\s+vom\s+\S+\s*$", "", name)  # "Hackfleisch… vom Weiderind"
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
    if e is None or e in STUECK or e == "kugel" or (e and e.startswith("zehe")):
        g = mengen.nachschlagen(mengen.STUECK_G, staemme) or stueck_g
        if e and e.startswith("zehe"):
            g = mengen.STUECK_G["knoblauchzeh"]
        if g:
            return z.menge * g, "schaetzung", f"1 Stück ≈ {g:g} g"
        return None, "pruefen", "Stückgewicht unbekannt"
    return None, "pruefen", f"Einheit „{e}“ unbekannt"


def zuordnen(
    rezept: RohRezept, produkte: list[Product], aliase: dict[str, tuple[str, str]]
) -> list[Zuordnung]:
    """Code-Vorschlag je Zutat. Sicher (``sicher=True``) nur über bestätigten Alias oder gleichen
    vollen Namen; Treffer über den Kern-Namen („rote Zwiebel“ → Zwiebel) sind ein Vorschlag, den
    die KI prüft. aliase: Text → (produkt_id, quelle); Quelle „ki“ gilt nicht als sicher.

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
        pid, quelle = aliase.get(alias_key(z.name), (None, ""))
        if pid in by_id:
            treffer = by_id[pid]
            zo.sicher = quelle != "ki"
        else:
            for kandidat in (z.name, kern, synonym):
                if kandidat and (treffer := by_key.get(_schluessel(kandidat))):
                    break
            zo.sicher = bool(treffer) and _voll(z.name) == _voll(treffer.name)
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
    return absaetze + (quelle_link(rezept.url, rezept.quelle) if rezept.url else "")


def quelle_link(url: str, quelle: str) -> str:
    return (
        f'<p>Quelle: <a href="{escape(url, quote=True)}">Originalrezept ({escape(quelle)})</a></p>'
    )


def alias_key(text: str) -> str:
    """Normalisierter Zutatentext mit Klammerinhalt: „Tomate(n) (geschälte)“ ≠ „Tomate(n)“.
    Nur Plural-Marker wie „(n)“, „/n“ fallen weg."""
    text = re.sub(r"\((?:e|n|en|er|s)\)|/(?:e|n|en)\b", "", text)
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-zäöüß ]+", " ", text)).strip()


def _voll(text: str) -> frozenset[str]:
    """Alle Wortstämme inklusive Klammern und Zusätzen: Grundlage für „sicherer Treffer“."""
    return frozenset(_stamm(w) for w in alias_key(text).split() if len(w) >= 2)
