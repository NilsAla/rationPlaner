"""Angebote aus Händler-Prospekten: abrufen, Produkten zuordnen, als Overlay nutzen.

Angebote sind nur ein Overlay (Konzept, „Angebote und Preise“): Normalpreise kommen aus Bons.
Je Händler ein kleiner Abrufer ohne Headless Browser (Recherche „Prospekt-Quellen“, 2026-11-25).
Penny liefert JSON mit Preisen je Verkaufsregion, dort braucht die Extraktion kein LLM.

Zuordnung Angebot → Produkt: gemerkter Titel (Alias, aus bestätigten Zuordnungen) > KI (wählt
nur IDs aus dem Katalog, der Code prüft sie). KI-Treffer zählen als Overlay im Planer, Preise,
Alias und Angebots-Packung entstehen erst nach Bestätigung (Grundregel 4).
"""

from __future__ import annotations

import asyncio
import html as htmllib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import __version__, einstellungen
from app.bon import groesse_aus_text
from app.db import Base, Packung, Preis
from app.deps import backend, session
from app.inventory.base import InventoryBackend, Product
from app.kategorisierung import KATEGORIEN
from app.rezept_import import _norm, _stamm, kern_name

log = logging.getLogger("ration.angebote")
router = APIRouter(prefix="/api/v1")

HAENDLER = {"penny": "Penny", "kaufland": "Kaufland"}
USER_AGENT = f"rationPlaner/{__version__} (privat, github.com/NilsAla/rationPlaner)"
PENNY = "https://www.penny.de"
PENNY_MAERKTE = PENNY + "/.rest/market"
PENNY_SEITE = PENNY + "/angebote"
PENNY_KATEGORIE = PENNY + "/.rest/offers/by-category/{woche}/{kategorie}"
KAUFLAND = "https://filiale.kaufland.de"
KAUFLAND_SEITE = KAUFLAND + "/angebote/uebersicht.html"
KAUFLAND_FILIALEN = KAUFLAND + "/.klstorefinder.json"
# Hauptkategorien mit Lebensmitteln (cat_01 Fleisch … cat_08 Getränke). Themen-Abschnitte
# (Top, K-Card, Regio, Weihnachten …) wiederholen sie fast nur; ab cat_09 Drogerie/Non-Food.
KAUFLAND_FOOD = re.compile(r"^cat_0[1-8][a-z]?_")
# Aktionsbeginn laut Prospekt-Abschnitt („ab-donnerstag--obst-und-gemuese1“), Ende Samstag
PERIODE = {"ab-montag": 0, "ab-dienstag": 1, "ab-mittwoch": 2, "ab-donnerstag": 3,
           "ab-freitag": 4, "ab-samstag": 5}  # fmt: skip
# Penny-Abschnitte ohne Lebensmittel: nonfood laut Seite oder per Name (Drogerie und Pflanzen
# sind dort als „food“ markiert). Gemischte wie „dauerhaft im Preis gesenkt“ (Tierfutter)
# bleiben, die KI ordnet dort nichts zu.
PENNY_OHNE = re.compile(
    r"^(drogerie|haushalt|pflanzen|kinderwelt|weihnachten|black-week|kochen-und)"
)
KI_BATCH = 40  # Angebote je KI-Aufruf
ABRUF_AB_STUNDE = 6
NAECHSTE_WOCHE_AB = 2  # ab Mittwoch nach dem Prospekt der nächsten Woche schauen
ZUGEORDNET = ("alias", "nutzer")  # bestätigte Zuordnung
VORSCHLAG = ("code", "ki")  # unbestätigt: Namensregel im Code oder KI
STUECK = {"stück", "stk", "st", "packung", "pck", "dose", "glas", "flasche", "bund", "becher"}


class Angebot(Base):
    __tablename__ = "angebot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    haendler: Mapped[str] = mapped_column(String, index=True)
    woche: Mapped[str] = mapped_column(String, index=True)  # ISO-Woche "2026-48"
    quelle_id: Mapped[str] = mapped_column(String)  # ID beim Händler
    titel: Mapped[str] = mapped_column(String)
    menge_text: Mapped[str | None] = mapped_column(String, nullable=True)  # „je 150 g“
    menge: Mapped[float | None] = mapped_column(Float, nullable=True)  # g/ml/Stück je Packung
    einheit: Mapped[str | None] = mapped_column(String, nullable=True)  # g, ml, Stück
    lose: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # Preis je kg
    preis: Mapped[float | None] = mapped_column(Float, nullable=True)  # None: „30 % Rabatt“
    preis_text: Mapped[str | None] = mapped_column(String, nullable=True)
    app_preis: Mapped[float | None] = mapped_column(Float, nullable=True)
    streichpreis: Mapped[float | None] = mapped_column(Float, nullable=True)
    grundpreis: Mapped[str | None] = mapped_column(String, nullable=True)
    kategorie: Mapped[str | None] = mapped_column(String, nullable=True)
    typ: Mapped[str] = mapped_column(String, default="food")  # food/nonfood
    gueltig_von: Mapped[date] = mapped_column(Date)
    gueltig_bis: Mapped[date] = mapped_column(Date, index=True)
    bild: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    produkt_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # None offen · ki KI-Treffer · ki_keins KI fand nichts · alias/nutzer bestätigt · ignoriert
    zuordnung: Mapped[str | None] = mapped_column(String, nullable=True)
    ki_info: Mapped[str | None] = mapped_column(String, nullable=True)  # Sorte, verworfener Treffer
    abgerufen: Mapped[datetime] = mapped_column(DateTime)


class AngebotAlias(Base):
    """Angebotstitel je Händler → Produkt; produkt_id None = passt zu keinem Produkt."""

    __tablename__ = "angebot_alias"

    haendler: Mapped[str] = mapped_column(String, primary_key=True)
    text: Mapped[str] = mapped_column(String, primary_key=True)  # normalisiert
    produkt_id: Mapped[str | None] = mapped_column(String, nullable=True)


class AngebotAbruf(Base):
    """Jeder Abruf je Händler: Healthcheck und Zeitplan."""

    __tablename__ = "angebot_abruf"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    haendler: Mapped[str] = mapped_column(String, index=True)
    zeit: Mapped[datetime] = mapped_column(DateTime)
    ok: Mapped[bool] = mapped_column(Boolean)
    wochen: Mapped[str | None] = mapped_column(String, nullable=True)  # "2026-48,2026-49"
    anzahl: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Lebensmittel
    fehler: Mapped[str | None] = mapped_column(String, nullable=True)


class AbrufFehler(Exception):
    pass


# --- Parser (Code, kein LLM) --------------------------------------------------------------


def euro(text: object) -> float | None:
    """„1.49²“, „0,99“ → Betrag; „30% Rabatt“ → None."""
    if m := re.search(r"\d+[.,]\d{2}(?!\d)", str(text or "")):
        return float(m.group(0).replace(",", "."))
    return None


def angebot_menge(text: str | None) -> tuple[float, str, bool] | None:
    """Packungsgröße aus „je 150 g“, „je 0,7 I“, „je 1.000-g-Packung“, „je kg“, „je 8er-Packung“.
    Ergebnis (Menge, g/ml/Stück, lose = Preis je kg)."""
    t = " " + (text or "").strip()
    if re.search(r"(?i)\bje kg\b", t):
        return 1000.0, "g", True
    t = re.sub(r"(\d)\.(\d{3})(?!\d)", r"\1\2", t)  # 1.000 g
    t = re.sub(r"(\d)\s?I\b", r"\1 l", t)  # 0,7 I (großes i statt l)
    t = re.sub(r"(?i)(\d)-(kg|g|ml|cl|l)-[a-zäöüß.]+", r"\1 \2", t)  # 400-g-Packung
    if g := groesse_aus_text(t):
        return g[0], g[1], False
    if m := re.search(r"(\d+)(?:er|[- ]?St(?:ü|ue)ck)\b", t):  # 8er-Packung, 3 Stück
        return float(m.group(1)), "Stück", False
    if re.search(r"(?i)\bje st(ü|ue)ck\b", t):
        return 1.0, "Stück", False
    return None


def titel_sauber(text: str) -> str:
    text = htmllib.unescape(text).replace("\xad", "").replace("\u2028", " ").replace("*", " ")
    return re.sub(r"\s+", " ", text).strip()


def woche_von(tag: date) -> str:
    j, w, _ = tag.isocalendar()
    return f"{j}-{w:02d}"


def wochentag(woche: str, tag: int) -> date:
    j, w = (int(x) for x in woche.split("-"))
    return date.fromisocalendar(j, w, tag + 1)


def penny_kategorien(seite: str) -> dict[str, list[tuple[str, str]]]:
    """Woche → [(Periode, Kategorie-Name)] aus der Angebotsseite, nur Lebensmittel-Abschnitte.
    Abschnitte nach dem Reiter „next-week“ gehören zur nächsten Woche."""
    aktuell = re.search(r'data-current-week="(\d{4}-\d{1,2})"', seite)
    naechste = re.search(r'data-next-week="(\d{4}-\d{1,2})"', seite)
    if not aktuell:
        raise AbrufFehler("Penny: aktuelle Woche nicht auf der Angebotsseite gefunden")
    grenze = seite.find("tabs__content--next-week")
    out: dict[str, list[tuple[str, str]]] = {}
    for m in re.finditer(r"<section\b[^>]*>", seite):
        tag = m.group(0)
        sid = re.search(r'\bid="([^"]+)"', tag)
        name = re.search(r'data-category-name="([^"]+)"', tag)
        if not sid or not name or 'data-period-category="true"' in tag:
            continue
        if 'data-category-type="nonfood"' in tag or PENNY_OHNE.match(name.group(1)):
            continue
        periode = sid.group(1).split("--", 1)[0]
        nae = grenze != -1 and m.start() > grenze and naechste
        woche = (naechste if nae else aktuell).group(1)  # type: ignore[union-attr]
        paar = (periode, htmllib.unescape(name.group(1)))
        if paar not in out.setdefault(woche, []):
            out[woche].append(paar)
    return out


def penny_angebot(t: dict, woche: str, periode: str, kategorie: str) -> dict | None:
    if t.get("primaryType") not in ("offer", "promo", "promoPlus") or not t.get("uuid"):
        return None
    groesse = angebot_menge(t.get("quantity"))
    streich = euro(t.get("crossOutPrice")) or euro(t.get("listPrice"))
    if not streich and t.get("originalPriceType") != "uvp":  # UVP ist kein Penny-Normalpreis
        streich = euro(t.get("originalPrice"))
    bild = (t.get("imageRendition") or {}).get("tileSm")
    return {
        "quelle_id": str(t["uuid"]),
        "titel": titel_sauber(str(t.get("title") or ""))[:200],
        "menge_text": (str(t.get("quantity") or "").strip() or None),
        "menge": groesse[0] if groesse else None,
        "einheit": groesse[1] if groesse else None,
        "lose": groesse[2] if groesse else None,
        "preis": euro(t.get("price")),
        "preis_text": str(t.get("price") or "").replace("²", "").strip() or None,
        "app_preis": euro(t.get("benefitPrice")),
        "streichpreis": streich,
        "grundpreis": (str(t.get("basePrice") or "").strip() or None),
        "kategorie": kategorie,
        "typ": "nonfood" if t.get("type") == "non-food" else "food",
        "gueltig_von": wochentag(woche, PERIODE.get(periode, 0)),
        "gueltig_bis": wochentag(woche, 5),
        "bild": bild,
        "url": PENNY + t["linkHref"] if str(t.get("linkHref") or "").startswith("/") else None,
    }


# --- Abruf ---------------------------------------------------------------------------------


async def _get(
    http: httpx.AsyncClient, url: str, cookies: str | None = None, **params
) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT} | ({"Cookie": cookies} if cookies else {})
    r = await http.get(url, params=params or None, headers=headers)
    if r.status_code >= 400:
        raise AbrufFehler(f"{url}: HTTP {r.status_code}")
    return r


async def penny_markt(http: httpx.AsyncClient, markt: str) -> dict:
    try:
        maerkte = (await _get(http, PENNY_MAERKTE)).json()
    except ValueError as err:
        raise AbrufFehler("Penny: Marktliste ist kein JSON") from err
    for m in maerkte:
        if str(m.get("wwIdent")) == str(markt):
            return m
    raise AbrufFehler(f"Penny: Markt {markt} nicht gefunden")


async def penny_abrufen(http: httpx.AsyncClient, markt: str) -> dict[str, list[dict]]:
    """Woche → Angebote. Regionale Preise über die Verkaufsregion des Markts."""
    m = await penny_markt(http, markt)
    seite = (await _get(http, PENNY_SEITE)).text
    kategorien = penny_kategorien(seite)
    aktuelle = min(kategorien)
    out: dict[str, list[dict]] = {}
    for woche, kats in sorted(kategorien.items()):
        region = m.get("sellingRegion") if woche == aktuelle else m.get("nextWeekSellingRegion")
        if not region:
            continue
        gesehen: dict[str, dict] = {}
        for periode, kategorie in kats:
            url = PENNY_KATEGORIE.format(woche=woche, kategorie=kategorie)
            try:
                kacheln = (await _get(http, url, region=region)).json().get("offerTiles") or []
            except ValueError as err:
                raise AbrufFehler(f"Penny {kategorie}: kein JSON") from err
            for t in kacheln:
                a = penny_angebot(t, woche, periode, kategorie)
                if a and a["quelle_id"] not in gesehen:
                    gesehen[a["quelle_id"]] = a
        out[woche] = list(gesehen.values())
    return out


def kaufland_filiale(wert: str) -> str:
    """„8360“ oder „DE8360“ → „DE8360“; sonst leer."""
    w = wert.strip().upper()
    w = "DE" + w if w.isdigit() else w
    return w if re.fullmatch(r"DE\d{4}", w) else ""


def _datum(text: str, bezug: date) -> date:
    """„23.11.“ → Datum im Jahr, das am nächsten an bezug liegt (Jahreswechsel)."""
    tag, monat = (int(x) for x in text.strip(". ").split(".")[:2])
    return min(
        (date(j, monat, tag) for j in (bezug.year - 1, bezug.year, bezug.year + 1)),
        key=lambda d: abs((d - bezug).days),
    )


def _klasse(html: str, name: str) -> str:
    m = re.search(rf'class="k-product-tile__{name}">([^<]*)', html)
    return titel_sauber(m.group(1)) if m else ""


def kaufland_kachel(t: str, von: date, bis: date, kategorie: str) -> dict | None:
    """Eine Produktkachel der Angebotsseite. Normalpreis-Block vor dem Kaufland-Card-Block."""
    bild = re.search(r'src="(https://kaufland\.media\.schwarz/is/image/schwarz/([^"?]+))', t)
    titel = " ".join(x for x in (_klasse(t, "title"), _klasse(t, "subtitle")) if x)
    if not bild or not titel:
        return None
    normal, _, karte = t.partition("k-product-tile__pricetags-loyalty")
    preise = re.findall(r'k-price-tag__price">([^<]*)', normal)
    alt = re.findall(r'line-through">([^<]*)', normal) or re.findall(
        r'line-through">([^<]*)', karte
    )
    karten = re.findall(r'k-price-tag__price">([^<]*)', karte)
    menge_text = _klasse(t, "unit-price") or None
    groesse = angebot_menge(menge_text)
    return {
        "quelle_id": bild.group(2)[:120],
        "titel": titel[:200],
        "menge_text": menge_text,
        "menge": groesse[0] if groesse else None,
        "einheit": groesse[1] if groesse else None,
        "lose": groesse[2] if groesse else None,
        "preis": euro(preise[0]) if preise else None,
        "preis_text": preise[0].strip() if preise else None,
        "app_preis": euro(karten[0]) if karten else None,
        "streichpreis": euro(alt[0]) if alt else None,
        "grundpreis": _klasse(t, "base-price") or None,
        "kategorie": kategorie,
        "typ": "food",
        "gueltig_von": von,
        "gueltig_bis": bis,
        "bild": bild.group(1),
        "url": None,
    }


def kaufland_seite(seite: str, heute: date | None = None) -> dict[str, list[dict]]:
    """Woche → Angebote aus den Lebensmittel-Hauptkategorien. Gültigkeit je Abschnitt
    („Gültig vom 23.11. bis 28.11.“). Nur „Vorwerbung“ ohne Kategorien → leer."""
    heute = heute or date.today()
    marken = sorted(
        (m.start(), sid.group(1))
        for m in re.finditer(r'<[^>]*class="k-product-section[" ][^>]*>', seite)
        if (sid := re.search(r'\bid="([^"]+)"', m.group(0)))
    )
    out: dict[str, dict[str, dict]] = {}
    for (start, sid), (ende, _) in zip(marken, [*marken[1:], (len(seite), "")], strict=True):
        if not KAUFLAND_FOOD.match(sid):
            continue
        teil = seite[start:ende]
        g = re.search(r"[Gg]ültig vom (\d{1,2}\.\d{1,2}\.) bis (\d{1,2}\.\d{1,2}\.)", teil)
        if not g:
            continue
        von = _datum(g.group(1), heute)
        bis = _datum(g.group(2), von)
        kategorie = re.sub(r"^cat_[0-9a-z]+_", "", sid).replace("__", ", ").replace("_", " ")
        woche = out.setdefault(woche_von(von), {})
        for kachel in teil.split('class="k-product-tile"')[1:]:
            a = kaufland_kachel(kachel, von, bis, kategorie)
            if a and a["quelle_id"] not in woche:
                woche[a["quelle_id"]] = a
    return {w: list(x.values()) for w, x in out.items() if x}


async def kaufland_abrufen(http: httpx.AsyncClient, filiale: str) -> dict[str, list[dict]]:
    """Diese und nächste Woche; die Filiale wählt das Cookie x-aem-variant (Preise regional)."""
    filiale = kaufland_filiale(filiale)
    if not filiale:
        raise AbrufFehler("Kaufland: Filiale muss DE + 4 Ziffern sein (z. B. DE8360)")
    out: dict[str, list[dict]] = {}
    for woche in ("current", "next"):
        r = await _get(http, KAUFLAND_SEITE, cookies=f"x-aem-variant={filiale}",
                       **{"kloffer-week": woche})  # fmt: skip
        for w, liste in kaufland_seite(r.text).items():
            out.setdefault(w, []).extend(liste)
    return out


ABRUFER = {"penny": penny_abrufen, "kaufland": kaufland_abrufen}


def konfig(s: Session) -> dict[str, str]:
    """Händler → Markt-ID, nur aktive (Markt gesetzt)."""
    e = einstellungen.lesen(s)["angebote"]
    return {h: str(e.get(f"{h}_markt") or "") for h in HAENDLER if e.get(f"{h}_markt")}


def speichern(s: Session, haendler: str, wochen: dict[str, list[dict]]) -> int:
    """Upsert je (Händler, Woche, ID). Zuordnung bleibt, Preise werden aktualisiert.
    Angebote der Woche, die nicht mehr im Prospekt stehen, fliegen raus (ohne bestätigte)."""
    jetzt = datetime.now()
    neu = 0
    for woche, liste in wochen.items():
        da = {
            a.quelle_id: a
            for a in s.scalars(
                select(Angebot).where(Angebot.haendler == haendler, Angebot.woche == woche)
            )
        }
        for werte in liste:
            a = da.pop(werte["quelle_id"], None)
            if a is None:
                a = Angebot(haendler=haendler, woche=woche, **werte, abgerufen=jetzt)
                s.add(a)
                neu += 1
            else:
                for k, v in werte.items():
                    setattr(a, k, v)
                a.abgerufen = jetzt
        for alt in da.values():
            if alt.zuordnung not in ZUGEORDNET:
                s.delete(alt)
    return neu


def alias_anwenden(s: Session, haendler: str | None = None) -> int:
    """Offene Angebote über gemerkte Titel zuordnen."""
    aliase = {(a.haendler, a.text): a.produkt_id for a in s.scalars(select(AngebotAlias))}
    n = 0
    q = select(Angebot).where(
        Angebot.zuordnung.is_(None) | Angebot.zuordnung.in_((*VORSCHLAG, "ki_keins"))
    )
    if haendler:
        q = q.where(Angebot.haendler == haendler)
    for a in s.scalars(q):
        key = (a.haendler, _norm(a.titel))
        if key in aliase:
            a.produkt_id = aliase[key]
            a.zuordnung = "alias" if aliase[key] else "ignoriert"
            n += 1
    return n


# --- Namensregel (Code, vor der KI) --------------------------------------------------------


def _woerter(text: str) -> list[tuple[str, bool]]:
    """(Stamm, großgeschrieben) je Wort vor Komma/Zusatz. Bindestrich-Wörter zählen als ein
    Wort (Dessert-Ei, Paprika-Chips), Teile in Großbuchstaben (Marken, TK-) fallen weg."""
    text = re.split(r"[,»«(]", text)[0]
    out = []
    for w in re.findall(r"[A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß]+)*", text):
        teile = [t for t in w.split("-") if not t.isupper()]
        if teile and len(wort := "".join(teile)) >= 2:
            out.append((_stamm(wort.lower()), teile[0][0].isupper()))
    return out


def _kopf(text: str) -> str | None:
    """Kopfwort: letztes großgeschriebenes Wort („MÜHLENHOF Frisches Hackfleisch gemischt“ →
    hackfleisch, „Paprika-Chips“ → paprikachips, „Bio-Spitzpaprika, rot“ → biospitzpaprika)."""
    gross = [w for w, g in _woerter(text) if g]
    return gross[-1] if gross else None


def _passt_wort(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def namensregel(titel: str, produkte: list[Product]) -> tuple[Product, str] | None:
    """Produkt, dessen Kopfwort der Kopf des Titels ist (gleich) oder auf den er endet
    (Variante: Spitzpaprika → Paprika). Weitere Wörter des Produktnamens müssen im Titel
    stehen. Mehrere gleich gute Produkte → None (dann entscheidet die KI)."""
    kopf = _kopf(titel)
    if not kopf:
        return None
    staemme = [w for w, _ in _woerter(titel)]
    kandidaten: list[tuple[int, int, Product, str]] = []
    for p in produkte:
        pk = _kopf(kern_name(p.name))
        if not pk:
            continue
        pw = [w for w, _ in _woerter(kern_name(p.name)) if w != pk]
        if kopf == pk:
            art = "gleich"
        elif len(pk) >= 4 and kopf.endswith(pk):
            art = "variante"
        else:
            continue
        zusatz = pw
        if not all(any(_passt_wort(z, t) for t in staemme) for z in zusatz):
            continue
        kandidaten.append((art == "gleich", len(zusatz), p, art))
    if not kandidaten:
        return None
    kandidaten.sort(key=lambda k: (k[0], k[1]), reverse=True)
    if len(kandidaten) > 1 and kandidaten[0][:2] == kandidaten[1][:2]:
        return None
    return kandidaten[0][2], kandidaten[0][3]


def code_zuordnen(s: Session, produkte: list[Product], haendler: str | None = None) -> int:
    """Offene Lebensmittel-Angebote per Namensregel vorschlagen (ohne KI)."""
    q = select(Angebot).where(Angebot.zuordnung.is_(None), Angebot.typ == "food")
    if haendler:
        q = q.where(Angebot.haendler == haendler)
    n = 0
    for a in s.scalars(q):
        if treffer := namensregel(a.titel, produkte):
            p, art = treffer
            a.produkt_id, a.zuordnung = p.id, "code"
            a.ki_info = f"Name passt ({art})"
            n += 1
    return n


# --- KI-Zuordnung ------------------------------------------------------------------------


def _feld(selector: dict, required: bool = False) -> dict:
    return {"selector": selector, "required": required}


PASSUNG = ["gleich", "variante", "keins"]


def struktur(produkte: list[Product]) -> dict:
    """Sorte und Kategorie zuerst (erst benennen, dann zuordnen), IDs nur als Auswahl."""
    return {
        "zuordnungen": {
            "description": "Genau ein Eintrag je Angebot",
            "required": True,
            "selector": {
                "object": {
                    "multiple": True,
                    "fields": {
                        "index": _feld({"number": {}}, True),
                        "sorte": _feld({"text": {}}, True),
                        "kategorie": _feld({"select": {"options": KATEGORIEN}}, True),
                        "passung": _feld({"select": {"options": PASSUNG}}, True),
                        "produkt_id": _feld({"select": {"options": [p.id for p in produkte]}}),
                    },
                }
            },
        }
    }


ANWEISUNG = """Prüfe Supermarkt-Angebote gegen meinen Küchenkatalog.

Katalog (id|name|kategorie):
{katalog}

Angebote (index|titel|menge):
{angebote}

Vorgehen je Angebot:
1. sorte: was das Angebot ist, 1–3 Wörter ohne Marke („SIMPLY SUNNY Mandeln“ → Mandeln,
   „MÜHLENHOF Rinder-Rouladen“ → Rinderrouladen). kategorie: Kategorie dieser Sorte.
2. Steht genau diese Sorte im Katalog (anderer Name ist ok: Möhren = Karotte, Hirtenkäse =
   Feta), dann passung gleich und ihre produkt_id. Eine Sorte des Katalogprodukts (Romatomaten
   für Tomate, Fusilli für Nudeln, Basmati für Reis): passung variante.
3. Sonst passung keins, keine produkt_id. Das ist der Normalfall, die meisten Angebote passen
   zu nichts. Nie nach Ähnlichkeit raten: Mandeln ≠ Butter, Pistazien ≠ Kürbiskerne,
   Rouladen ≠ Chorizo, Schweineschnitzel ≠ Hähnchenbrust, Fruchtessig ≠ Olivenöl,
   Milch ≠ Sahne, Camembert ≠ Mozzarella, Fruchtjoghurt ≠ Sahne.
4. Verarbeitetes passt nicht zur Zutat: Kartoffelchips ≠ Kartoffeln, Pizza ≠ Mehl,
   Fertiggericht ≠ Zutat. Tierfutter und Drogerie passen nie.
5. produkt_id ist die Zahl/ID vor dem ersten „|“, nie der Name.
"""


def anweisung(angebote: list[Angebot], produkte: list[Product]) -> str:
    katalog = "\n".join(f"{p.id}|{p.name}|{p.category or ''}" for p in produkte)
    zeilen = "\n".join(f"{i}|{a.titel}|{a.menge_text or ''}" for i, a in enumerate(angebote))
    return ANWEISUNG.format(katalog=katalog, angebote=zeilen)


def ki_pruefen(e: dict, produkte: dict[str, Product]) -> tuple[Product | None, str | None]:
    """KI-Treffer prüfen. Live (gpt-4o-mini, 2026-11-25): Namen statt IDs („Gnocchi“) und
    Fehlgriffe quer durch Kategorien (Mandeln → Butter). Ergebnis (Produkt, Hinweis)."""
    sorte = " ".join(str(e.get("sorte") or "").split())[:60]
    if e.get("passung") not in ("gleich", "variante"):
        return None, sorte or None
    wert = str(e.get("produkt_id") or "").strip()
    p = produkte.get(wert)
    if p is None:  # Name statt ID: nur exakter Produktname zählt
        p = next((x for x in produkte.values() if x.name.strip().lower() == wert.lower()), None)
    if p is None:
        return None, f"{sorte} · KI-ID {wert} unbekannt" if wert else sorte or None
    kat = e.get("kategorie")
    if p.category and kat in KATEGORIEN and kat != p.category:
        return None, f"{sorte} · KI meinte {p.name}, aber {kat} ≠ {p.category}"
    return p, sorte or None


def ki_anwenden(angebote: list[Angebot], daten: dict, produkte: dict[str, Product]) -> int:
    """KI-Antwort übernehmen (nur geprüfte Treffer). Gibt die Zahl der Treffer zurück."""
    treffer = 0
    for e in daten.get("zuordnungen") or []:
        try:
            a = angebote[int(e.get("index"))]
        except (TypeError, ValueError, IndexError):
            continue
        p, a.ki_info = ki_pruefen(e, produkte)
        if p:
            a.produkt_id, a.zuordnung = p.id, "ki"
            treffer += 1
        else:
            a.produkt_id, a.zuordnung = None, "ki_keins"
    return treffer


async def ki_zuordnen(s: Session, ki, b: InventoryBackend, haendler: str | None = None) -> dict:
    """Offene Lebensmittel-Angebote in Batches an die KI. Fehler stoppen nur die KI."""
    q = select(Angebot).where(
        Angebot.zuordnung.is_(None), Angebot.typ == "food", Angebot.gueltig_bis >= date.today()
    )
    if haendler:
        q = q.where(Angebot.haendler == haendler)
    offen = list(s.scalars(q.order_by(Angebot.id)))
    if not offen or ki is None or not ki.verfuegbar("angebote"):
        return {"offen": len(offen), "treffer": 0, "ki": False}
    produkte = {p.id: p for p in await b.list_products()}
    katalog = sorted(produkte.values(), key=lambda p: p.name.lower())
    treffer = 0
    for i in range(0, len(offen), KI_BATCH):
        teil = offen[i : i + KI_BATCH]
        try:
            daten = await ki.generate(
                "angebote",
                "rationPlaner Angebote zuordnen",
                anweisung(teil, katalog),
                struktur(katalog),
            )
        except Exception as err:  # noqa: BLE001 - Angebote bleiben offen, Abruf gilt trotzdem
            log.warning("KI-Zuordnung der Angebote fehlgeschlagen: %s", err)
            s.commit()
            return {"offen": len(offen), "treffer": treffer, "ki": True, "fehler": str(err)}
        treffer += ki_anwenden(teil, daten, produkte)
        s.commit()  # je Batch speichern, SQLite nicht lange sperren
    return {"offen": len(offen), "treffer": treffer, "ki": True}


# --- Bestätigen: Preise und Angebots-Packung -----------------------------------------------


def in_bestandseinheit(a: Angebot, p: Product) -> float | None:
    """Angebotsgröße (g/ml/Stück) in der Bestandseinheit des Produkts."""
    if not a.menge or not a.einheit:
        return None
    einheit = p.unit.strip().lower()
    if a.einheit in ("g", "ml"):
        if einheit in ("g", "ml"):
            return a.menge  # ml ≈ g reicht für die Kostenschätzung
        if p.grams_per_unit:
            return round(a.menge / p.grams_per_unit, 3)
        return None
    if einheit in STUECK:
        return a.menge
    if (p.pack_unit or "").strip().lower() in STUECK and p.pack_size:
        return a.menge * p.pack_size  # Paprika in g, Einkauf je Stück à 160 g
    return None


def _folgen_entfernen(s: Session, a: Angebot) -> None:
    s.execute(delete(Preis).where(Preis.angebot_id == a.id))
    if a.produkt_id:
        s.execute(
            delete(Packung).where(
                Packung.produkt_id == a.produkt_id,
                Packung.quelle == "angebot",
                Packung.gueltig_bis == a.gueltig_bis,
                Packung.haendler == HAENDLER.get(a.haendler, a.haendler),
            )
        )


def _folgen_anlegen(s: Session, a: Angebot, p: Product) -> None:
    """Angebots- und Streichpreis in die Preistabelle, abweichende Größe als Angebots-Packung
    bis Aktionsende (Konzept). Lose Ware: Preis je kg."""
    menge = in_bestandseinheit(a, p)
    if not menge:
        return
    markt = HAENDLER.get(a.haendler, a.haendler)
    for quelle, betrag in (("angebot", a.preis), ("streichpreis", a.streichpreis)):
        if betrag:
            s.add(Preis(produkt_id=p.id, preis=betrag, menge=menge, markt=markt,
                        datum=a.gueltig_von, quelle=quelle, angebot_id=a.id))  # fmt: skip
    if a.lose:
        return
    groessen = {round(pk.menge, 3) for pk in s.scalars(select(Packung).where(
        Packung.produkt_id == p.id, Packung.bestaetigt.is_not(False)))}  # fmt: skip
    if p.pack_size:
        groessen.add(round(p.pack_size, 3))
    if round(menge, 3) not in groessen:
        s.add(Packung(produkt_id=p.id, einheit=a.menge_text or "Packung", menge=menge,
                      quelle="angebot", gueltig_bis=a.gueltig_bis, haendler=markt,
                      bestaetigt=True))  # fmt: skip


def zuordnen(s: Session, a: Angebot, p: Product | None) -> None:
    """Bestätigt (p) oder ignoriert (None) ein Angebot und merkt den Titel je Händler."""
    _folgen_entfernen(s, a)
    a.produkt_id = p.id if p else None
    a.zuordnung = "nutzer" if p else "ignoriert"
    s.merge(AngebotAlias(haendler=a.haendler, text=_norm(a.titel), produkt_id=a.produkt_id))
    if p:
        _folgen_anlegen(s, a, p)


def aktiv_je_produkt(s: Session, von: date, bis: date | None = None) -> dict[str, list[Angebot]]:
    """Laufende Angebote im Zeitraum mit Produkt (bestätigt oder KI-Treffer)."""
    bis = bis or von
    out: dict[str, list[Angebot]] = {}
    for a in s.scalars(
        select(Angebot).where(
            Angebot.produkt_id.is_not(None),
            Angebot.zuordnung.in_((*ZUGEORDNET, *VORSCHLAG)),
            Angebot.gueltig_bis >= von,
            Angebot.gueltig_von <= bis,
        )
    ):
        out.setdefault(a.produkt_id or "", []).append(a)
    return out


# --- Lauf und Zeitplan ---------------------------------------------------------------------


@dataclass
class Ergebnis:
    haendler: str
    ok: bool
    wochen: list[str] = field(default_factory=list)
    anzahl: int = 0
    neu: int = 0
    alias: int = 0
    code: int = 0
    ki: dict = field(default_factory=dict)
    fehler: str | None = None


def _status(s: Session, haendler: str) -> AngebotAbruf | None:
    return s.scalars(
        select(AngebotAbruf)
        .where(AngebotAbruf.haendler == haendler)
        .order_by(AngebotAbruf.zeit.desc(), AngebotAbruf.id.desc())
        .limit(1)
    ).first()


async def abrufen(app, haendler: str, markt: str) -> Ergebnis:
    """Abruf + Speichern + Alias + KI. Fehler landen im Healthcheck, kaputt → HA-Hinweis."""
    http: httpx.AsyncClient | None = getattr(app.state, "angebote_http", None)
    eigen = http is None
    http = http or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    erg = Ergebnis(haendler=haendler, ok=False)
    try:
        wochen = await ABRUFER[haendler](http, markt)
        erg.wochen = sorted(wochen)
        erg.anzahl = sum(1 for liste in wochen.values() for a in liste if a["typ"] == "food")
        if not erg.anzahl:
            raise AbrufFehler("keine Lebensmittel-Angebote gefunden")
        with Session(app.state.engine) as s:
            erg.neu = speichern(s, haendler, wochen)
            s.commit()
            erg.alias = alias_anwenden(s, haendler)
            if app.state.backend is not None:
                erg.code = code_zuordnen(s, await app.state.backend.list_products(), haendler)
            s.commit()
        erg.ok = True
    except (AbrufFehler, httpx.HTTPError) as err:
        erg.fehler = str(err) if isinstance(err, AbrufFehler) else f"{type(err).__name__}: {err}"
        log.warning("Angebote %s: %s", haendler, erg.fehler)
    finally:
        if eigen:
            await http.aclose()
    with Session(app.state.engine) as s:
        vorher = _status(s, haendler)
        war_ok = vorher is None or vorher.ok
        s.add(AngebotAbruf(haendler=haendler, zeit=datetime.now(), ok=erg.ok,
                           wochen=",".join(erg.wochen) or None, anzahl=erg.anzahl,
                           fehler=erg.fehler))  # fmt: skip
        s.commit()
        if erg.ok and app.state.backend is not None:
            erg.ki = await ki_zuordnen(s, app.state.ki, app.state.backend, haendler)
    if not erg.ok and war_ok and app.state.ha is not None:
        try:
            await app.state.ha.notify(
                f"Angebote {HAENDLER[haendler]} kaputt",
                f"Abruf fehlgeschlagen: {erg.fehler}. Details in rationPlaner → Angebote.",
                app.state.settings.notify_service,
            )
        except Exception:  # noqa: BLE001 - Hinweis ist Beiwerk
            log.exception("HA-Hinweis für Angebote fehlgeschlagen")
    return erg


def faellig(s: Session, haendler: str, jetzt: datetime) -> bool:
    """Höchstens einmal am Tag. Abrufen, solange die aktuelle Woche fehlt oder (ab Mittwoch)
    die nächste Woche noch nicht da ist."""
    letzter = _status(s, haendler)
    if letzter and letzter.zeit.date() == jetzt.date():
        return False
    gesehen: set[str] = set()
    for w in s.scalars(
        select(AngebotAbruf.wochen).where(
            AngebotAbruf.haendler == haendler,
            AngebotAbruf.ok.is_(True),
            AngebotAbruf.zeit >= jetzt - timedelta(days=14),
        )
    ):
        gesehen.update((w or "").split(","))
    heute = jetzt.date()
    if woche_von(heute) not in gesehen:
        return True
    return heute.weekday() >= NAECHSTE_WOCHE_AB and woche_von(heute + timedelta(7)) not in gesehen


async def schleife(app) -> None:
    """Stündlich prüfen, ob ein Händler fällig ist (nur im Add-on)."""
    while True:
        try:
            jetzt = datetime.now()
            if jetzt.hour >= ABRUF_AB_STUNDE:
                with Session(app.state.engine) as s:
                    aktiv = konfig(s)
                    dran = [h for h in aktiv if faellig(s, h, jetzt)]
                for h in dran:
                    await abrufen(app, h, aktiv[h])
        except Exception:  # noqa: BLE001 - nächster Versuch in einer Stunde
            log.exception("Angebots-Abruf fehlgeschlagen")
        await asyncio.sleep(3600)


# --- Endpunkte ------------------------------------------------------------------------------


class AbrufIn(BaseModel):
    haendler: str | None = None


class ZuordnenIn(BaseModel):
    produkt_id: str | None = None  # None = ignorieren


class BestaetigenIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


def als_dict(a: Angebot, namen: dict[str, str]) -> dict:
    return {
        "id": a.id,
        "haendler": a.haendler,
        "markt": HAENDLER.get(a.haendler, a.haendler),
        "woche": a.woche,
        "titel": a.titel,
        "menge_text": a.menge_text,
        "menge": a.menge,
        "einheit": a.einheit,
        "lose": a.lose,
        "preis": a.preis,
        "preis_text": a.preis_text,
        "app_preis": a.app_preis,
        "streichpreis": a.streichpreis,
        "grundpreis": a.grundpreis,
        "kategorie": a.kategorie,
        "typ": a.typ,
        "gueltig_von": a.gueltig_von,
        "gueltig_bis": a.gueltig_bis,
        "bild": a.bild,
        "url": a.url,
        "produkt_id": a.produkt_id,
        "produkt": namen.get(a.produkt_id or ""),
        "zuordnung": a.zuordnung,
        "ki_info": a.ki_info,
    }


@router.get("/angebote")
async def liste(
    alle: bool = False,
    nonfood: bool = False,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> list[dict]:
    """Laufende und kommende Angebote (alle=true: auch abgelaufene der letzten 4 Wochen)."""
    ab = date.today() - timedelta(days=28 if alle else 0)
    q = select(Angebot).where(Angebot.gueltig_bis >= ab)
    if not nonfood:
        q = q.where(Angebot.typ == "food")
    namen = {p.id: p.name for p in await b.list_products()}
    zeilen = s.scalars(q.order_by(Angebot.gueltig_von, Angebot.haendler, Angebot.titel))
    return [als_dict(a, namen) for a in zeilen]


@router.get("/angebote/status")
async def status(s: Session = Depends(session)) -> list[dict]:
    """Healthcheck je Händler: letzter Abruf, letzter Erfolg, kaputt."""
    aktiv = konfig(s)
    out = []
    for h, name in HAENDLER.items():
        letzter = _status(s, h)
        erfolg = s.scalars(
            select(AngebotAbruf)
            .where(AngebotAbruf.haendler == h, AngebotAbruf.ok.is_(True))
            .order_by(AngebotAbruf.zeit.desc())
            .limit(1)
        ).first()
        out.append({
            "haendler": h, "name": name, "aktiv": h in aktiv, "markt": aktiv.get(h),
            "letzter_abruf": letzter.zeit if letzter else None,
            "ok": letzter.ok if letzter else None,
            "fehler": letzter.fehler if letzter else None,
            "letzter_erfolg": erfolg.zeit if erfolg else None,
            "wochen": (erfolg.wochen or "").split(",") if erfolg and erfolg.wochen else [],
            "anzahl": erfolg.anzahl if erfolg else None,
            "kaputt": bool(letzter and not letzter.ok),
        })  # fmt: skip
    return out


@router.post("/angebote/abrufen")
async def jetzt_abrufen(
    data: AbrufIn, request: Request, tasks: BackgroundTasks, s: Session = Depends(session)
) -> dict:
    aktiv = konfig(s)
    if data.haendler and data.haendler not in HAENDLER:
        raise HTTPException(404, f"Unbekannter Händler {data.haendler}")
    dran = [data.haendler] if data.haendler else list(aktiv)
    if not dran or any(h not in aktiv for h in dran):
        raise HTTPException(422, "Kein Markt eingestellt (Einstellungen → Angebote)")
    for h in dran:
        tasks.add_task(abrufen, request.app, h, aktiv[h])
    return {"gestartet": dran}


@router.post("/angebote/ki")
async def ki_nochmal(
    request: Request,
    neu: bool = False,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    """Offene Angebote der KI geben. neu=true: auch unbestätigte KI-Ergebnisse (Treffer und
    „passt zu nichts“) neu fragen; Bestätigtes und Ignoriertes bleibt."""
    request.app.state.ki.pruefen("angebote")
    if neu:
        for a in s.scalars(select(Angebot).where(Angebot.zuordnung.in_((*VORSCHLAG, "ki_keins")))):
            a.produkt_id, a.zuordnung, a.ki_info = None, None, None
        s.commit()
    code_zuordnen(s, await b.list_products())
    s.commit()
    return await ki_zuordnen(s, request.app.state.ki, b)


@router.post("/angebote/{angebot_id}/zuordnen")
async def zuordnen_api(
    angebot_id: int,
    data: ZuordnenIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    a = s.get(Angebot, angebot_id)
    if a is None:
        raise HTTPException(404, "Angebot nicht gefunden")
    p = await b.get_product(data.produkt_id) if data.produkt_id else None
    zuordnen(s, a, p)
    n = alias_anwenden(s, a.haendler)  # gleicher Titel in anderen Wochen
    s.commit()
    return {**als_dict(a, {p.id: p.name} if p else {}), "weitere": n}


@router.post("/angebote/bestaetigen")
async def bestaetigen(
    data: BestaetigenIn, s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> dict:
    """KI-Treffer übernehmen (Häkchen in der Liste)."""
    produkte = {p.id: p for p in await b.list_products()}
    ok, fehler = [], []
    for aid in data.ids:
        a = s.get(Angebot, aid)
        if a is None or a.zuordnung not in VORSCHLAG or a.produkt_id not in produkte:
            fehler.append({"id": aid, "fehler": "kein offener Vorschlag"})
            continue
        zuordnen(s, a, produkte[a.produkt_id])
        ok.append(aid)
    s.commit()
    return {"ok": ok, "fehler": fehler}


def _markt_treffer(haendler: str, daten: list[dict], q: str) -> list[dict]:
    if haendler == "kaufland":
        return [
            {
                "id": m.get("n"),
                "name": m.get("cn"),
                "adresse": f"{m.get('sn', '')}, {m.get('pc', '')} {m.get('t', '')}",
            }  # fmt: skip
            for m in daten
            if str(m.get("pc", "")).startswith(q) or q in str(m.get("t", "")).lower()
        ]
    return [
        {
            "id": str(m.get("wwIdent")),
            "name": m.get("marketName"),
            "adresse": f"{m.get('streetWithHouseNumber', '')}, {m.get('zipCode', '')} "
            f"{m.get('city', '')}",
        }  # fmt: skip
        for m in daten
        if str(m.get("zipCode", "")).startswith(q) or q in str(m.get("city", "")).lower()
    ]


@router.get("/angebote/maerkte/{haendler}")
async def maerkte_suchen(haendler: str, q: str, request: Request) -> list[dict]:
    """Märkte nach PLZ oder Ort, für die Einstellung."""
    quellen = {"penny": PENNY_MAERKTE, "kaufland": KAUFLAND_FILIALEN}
    if haendler not in quellen:
        raise HTTPException(404, f"Marktsuche für {haendler} gibt es nicht")
    q = q.strip().lower()
    if len(q) < 3:
        raise HTTPException(422, "Mindestens 3 Zeichen (PLZ oder Ort)")
    http: httpx.AsyncClient | None = getattr(request.app.state, "angebote_http", None)
    eigen = http is None
    http = http or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        daten = (await _get(http, quellen[haendler])).json()
    except (AbrufFehler, httpx.HTTPError, ValueError) as err:
        raise HTTPException(502, f"{HAENDLER[haendler]} nicht erreichbar: {err}") from err
    finally:
        if eigen:
            await http.aclose()
    return _markt_treffer(haendler, daten, q)[:30]
