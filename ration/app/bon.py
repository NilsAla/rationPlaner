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
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import kueche, packungen
from app.db import BonAlias, BonImport, Preis
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

# Zeilentypen. Die KI typisiert, eindeutige Bon-Wörter am Zeilenanfang gehen vor (_typ).
TYPEN = ("artikel", "non_food", "pfand", "leergut", "rabatt", "storno", "mengenzeile")
# Zeilen, die keine Bon-Position sind (falls die KI sie trotzdem liefert). Bewusst am
# Zeilenanfang verankert: "Tütensuppe" oder "Einweghandschuhe" sind Ware.
SONST = re.compile(
    r"(?i)^\s*(summe|zwischensumme|gesamt|zu zahlen|bar\b|geg(eben)?\.?\s|ec[- ]?karte|"
    r"kartenzahlung|mwst|ust\b|steuer|netto|brutto|rückgeld|rueckgeld|punkte|treuepunkte)"
)
STUECK = {"stück", "stk", "st", "portion"}
MASSE_G = {"g": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0, "cl": 10.0}
# Einheit laut Bon → Basiseinheit (g, ml, Stück) und Faktor
BASIS = {
    "g": ("g", 1.0), "kg": ("g", 1000.0), "ml": ("ml", 1.0), "cl": ("ml", 10.0),
    "l": ("ml", 1000.0), "st": ("Stück", 1.0), "stk": ("Stück", 1.0), "stück": ("Stück", 1.0),
}  # fmt: skip
_ZAHL = r"(\d+(?:[.,]\d+)?)"
_EINHEIT = r"(kg|g|ml|cl|l|stk|st)"
# Gebinde im Bon-Text: "12X0,7L", "2x250g"; einfache Größe: "500G", "0,75L", "100ST"
GEBINDE = re.compile(rf"(?i)(?<![\d,.])(\d{{1,3}})\s*[x×*]\s*{_ZAHL}\s?{_EINHEIT}\b")
GROESSE = re.compile(rf"(?i)(?<![\d,.]){_ZAHL}\s?{_EINHEIT}\b")
# Mengenzeile über/unter der Ware: "3 x 0,99", "2 Stk x 0,69", "0,486 kg x 3,99 EUR/kg"
MENGE_STUECK = re.compile(
    rf"(?i)^\s*(\d{{1,3}})\s*(?:stk|st|stück)?\.?\s*[x×*]\s*{_ZAHL}\s*(?:eur|€)?\s*$"
)
MENGE_GEWICHT = re.compile(
    rf"(?i)^\s*{_ZAHL}\s*kg\s*[x×*]\s*{_ZAHL}\s*(?:eur|€)?\s*(?:/\s*kg)?\s*$"
)


def _f(text: str) -> float:
    return float(text.replace(",", "."))


def groesse_aus_text(text: str) -> tuple[float, str] | None:
    """Packungsgröße aus dem Bon-Text in g, ml oder Stück (Code, nicht KI)."""
    if m := GEBINDE.search(text):
        basis, faktor = BASIS[m.group(3).lower()]
        return round(int(m.group(1)) * _f(m.group(2)) * faktor, 3), basis
    if m := GROESSE.search(text):
        basis, faktor = BASIS[m.group(2).lower()]
        return round(_f(m.group(1)) * faktor, 3), basis
    return None


def mengenzeile(text: str) -> tuple[str, float, float] | None:
    """("stueck", Anzahl, Einzelpreis) oder ("gewicht", kg, €/kg)."""
    if m := MENGE_GEWICHT.match(text):
        return "gewicht", _f(m.group(1)), _f(m.group(2))
    if m := MENGE_STUECK.match(text):
        return "stueck", float(m.group(1)), _f(m.group(2))
    return None


def name_passt(name: str, text: str) -> bool:
    """Namensvorschlag nur aus dem Bon-Text: Jedes Wort ab 4 Buchstaben muss zu mindestens
    der Hälfte (und mindestens 4 Zeichen am Stück) im Bon-Text stehen."""

    def n(x: str) -> str:
        x = x.lower()
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            x = x.replace(a, b)
        return x

    bon = re.sub(r"[^a-z]", "", n(text))
    for wort in re.findall(r"[a-z]+", n(name)):
        if len(wort) < 4:
            continue
        m = SequenceMatcher(None, wort, bon).find_longest_match(0, len(wort), 0, len(bon))
        if m.size < max(4, len(wort) / 2):
            return False
    return True


ANWEISUNG = """Lies den Kassenbon auf dem Bild und ordne jede gekaufte Ware einem Produkt zu.

Katalog (id|name|einheit):
{katalog}

Regeln:
- Jede Zeile mit Betrag in Bon-Reihenfolge, auch Pfand, Leergut, Rabatte, Coupons und Stornos.
  Nicht aufnehmen: Summe, Zahlungsart, MwSt-Tabelle, Kopf- und Fußtexte.
- Zeilen ohne eigenen Artikelnamen wie "3 x 0,99", "2 Stk x 0,69" oder "0,486 kg x 3,99 EUR/kg"
  gibst du als eigene Position mit typ mengenzeile aus, text genau wie gedruckt, preis 0. Der
  Code ordnet sie der passenden Ware zu.
- typ: artikel (Lebensmittel, Getränke), non_food (Drogerie, Haushalt), pfand, leergut,
  rabatt (auch Coupon, Preisvorteil), storno, mengenzeile.
- bezug: bei rabatt und storno die Nummer (ab 0) der Zeile in deiner Liste, auf die sie sich
  beziehen; sonst leer.
- text: der Bon-Text der Zeile Zeichen für Zeichen wie gedruckt, auch Abkürzungen. Keine Wörter
  trennen, zusammenziehen, korrigieren oder ergänzen ("EINWEGHANDSCHUHE" bleibt so).
- anzahl: gekaufte Stückzahl (bei "2 x 1,29" → 2; bei Gewichtsware 1).
- einzelpreis: Preis je Stück bzw. je kg, wenn er auf dem Bon steht, sonst 0.
- preis: Betrag der Zeile in Euro wie gedruckt (Rabatt, Storno, Leergut negativ).
- packung_menge/packung_einheit: Größe EINER Packung, nur wenn sie auf dem Bon steht ("500G",
  "1KG", "1,5L"; Gebinde "12X0,7L" → 8,4 und "l"), sonst 0 und "". Nie schätzen.
- produkt_id: nur wenn ein Katalogprodukt genau diese Ware ist, sonst leer.
- neu_name: kurzer deutscher Produktname im Singular ohne Marke und Größe, wenn produkt_id leer.
  Nur aus den Wörtern des Bon-Texts ableiten (Abkürzungen ausschreiben ist erlaubt), nichts
  dazuerfinden. Unklar → Bon-Text übernehmen.
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
                    "typ": {
                        "required": True,
                        "selector": {"select": {"options": list(TYPEN)}},
                    },
                    "bezug": {"selector": {"number": {}}},
                    "anzahl": {"required": True, "selector": {"number": {}}},
                    "einzelpreis": {"selector": {"number": {}}},
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
    preis: float | None  # Zeilenpreis nach Rabatt/Storno, ohne Vorzeichen
    typ: str = "artikel"  # artikel / non_food / pfand / leergut / rabatt / storno
    ki_index: int | None = None  # Position in der KI-Antwort (für bezug)
    bezug: int | None = None  # Rabatt/Storno: index der betroffenen Zeile
    einzelpreis: float | None = None
    rabatt: float = 0.0  # verrechnete Rabatte
    storniert: bool = False
    betrag: float | None = None  # Beitrag zur Bon-Summe (Pfand +, Leergut −)
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
    gewicht: bool = False  # Gewichtsware: packung_menge ist das gewogene Gewicht
    bon_menge: float | None = None  # Gesamtmenge laut Bon (Anzahl × Packung) in bon_einheit
    bon_einheit: str | None = None  # g / ml / Stück
    annahme: bool = False  # Menge beruht auf einer Annahme (z. B. Standard-Packung)
    zweifel: bool = False  # Code konnte Anzahl/Gewicht nicht bestätigen → Review


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
    """Menge in Bestandseinheit, Hinweis, ob die Bon-Größe von bekannten Packungen abweicht.

    packung_menge/packung_einheit stehen schon in Basiseinheit (g, ml, Stück)."""
    unit = p.unit.strip().lower()
    anzahl = pos.anzahl or 1
    groesse, basis = pos.packung_menge, pos.packung_einheit
    if groesse and basis in ("g", "ml"):
        faktor = {"g": 1.0, "ml": 1.0, "kg": 1000.0, "l": 1000.0}.get(unit)
        if faktor:
            je = groesse / faktor
            bekannt = [g.menge for g in kueche.groessen(p, weitere)]
            abweichend = (
                not pos.gewicht and bool(bekannt) and all(abs(je - b) > kueche.EPS for b in bekannt)
            )
            if pos.gewicht:
                return round(je, 3), f"{groesse:g} {basis} gewogen", False
            return round(anzahl * je, 3), f"{anzahl:g} × {je:g} {p.unit} laut Bon", abweichend
        if p.grams_per_unit:
            return anzahl * groesse / p.grams_per_unit, "über g je Einheit umgerechnet", False
    if groesse and basis == "Stück" and unit in STUECK:
        return anzahl * groesse, f"{anzahl:g} × {groesse:g} Stück laut Bon", False
    if p.pack_size:
        pos.annahme = True
        return (
            anzahl * p.pack_size,
            f"Annahme: {anzahl:g} × Standard-Packung {p.pack_size:g} {p.unit} (nicht auf dem Bon)",
            False,
        )
    if unit in STUECK:
        return anzahl, "", False
    return None, f"Menge in {p.unit} unbekannt", False


def _typ(e: dict, text: str) -> str:
    """Zeilentyp: KI-Angabe, aber eindeutige Bon-Wörter gehen vor (Code-Guard)."""
    t = text.lower()
    if re.match(r"\s*storno\b", t):
        return "storno"
    if re.match(r"\s*(leergut|pfandrückgabe|pfandrueckgabe)\b", t):
        return "leergut"
    if re.match(r"\s*(pfand|einwegpfand|mehrwegpfand)\b", t):
        return "pfand"
    if re.match(r"\s*(rabatt|coupon|gutschein|preisvorteil|aktionsrabatt|nachlass)\b", t):
        return "rabatt"
    if mengenzeile(text):
        return "mengenzeile"
    if SONST.search(text):
        return "sonstiges"
    ki = str(e.get("typ") or "").strip().lower()
    return ki if ki in TYPEN else "artikel"


def _ziel(liste: list[Position], k: int, bezug: int | None, text: str = "") -> Position | None:
    """Zeile, auf die sich Rabatt/Storno an Stelle k bezieht: KI-Bezug, sonst gleicher Text,
    sonst die Ware direkt davor."""
    waren = [p for p in liste[:k] if p.typ in ("artikel", "non_food") and not p.storniert]
    if bezug is not None:
        treffer = next((p for p in waren if p.ki_index == bezug), None)
        if treffer:
            return treffer
    if text:
        rest = norm_text(re.sub(r"(?i)^\s*storno\s*", "", text))
        treffer = next((p for p in reversed(waren) if norm_text(p.text) == rest), None)
        if treffer:
            return treffer
    return waren[-1] if waren else None


def auswerten(
    daten: dict,
    produkte: list[Product],
    aliase: dict[tuple[str, str], str],
    weitere: dict[str, list[kueche.Groesse]],
    schwelle: float,
    alias_mengen: dict[tuple[str, str], float] | None = None,
) -> tuple[str | None, date | None, float | None, list[Position]]:
    """LLM-Antwort validieren und in Positionen übersetzen. Nichts wird geraten.

    1. Zeilen lesen und typisieren (Ware, Non-Food, Pfand, Leergut, Rabatt, Storno).
    2. Anzahl gegen Einzelpreis prüfen (Code rechnet), Rabatte und Stornos verrechnen.
    3. Waren zuordnen (Alias vor KI) und Mengen in Bestandseinheit rechnen.
    `betrag` je Zeile ergibt in Summe den Bon-Betrag (Summenprüfung in _bon_dict).
    """
    by_id = {p.id: p for p in produkte}
    markt = str(daten.get("markt") or "").strip() or None
    try:
        datum = date.fromisoformat(str(daten.get("datum") or "")[:10])
    except ValueError:
        datum = None
    summe = _zahl(daten.get("summe"))
    roh = daten.get("positionen") if isinstance(daten.get("positionen"), list) else []

    # 1. lesen
    liste: list[tuple[Position, dict]] = []
    for ki_index, e in enumerate(roh):
        if not isinstance(e, dict) or not str(e.get("text") or "").strip():
            continue
        text = str(e["text"]).strip()
        typ = _typ(e, text)
        if typ == "sonstiges":
            continue  # Summe, Zahlung, MwSt: keine Bon-Zeile
        bezug = _zahl(e.get("bezug"))
        if typ == "mengenzeile":
            liste.append((Position(index=-1, text=text, anzahl=1, preis=None, typ=typ), e))
            continue
        pos = Position(
            index=len(liste),
            text=text,
            typ=typ,
            ki_index=ki_index,
            bezug=int(bezug) if bezug is not None else None,
            anzahl=_zahl(e.get("anzahl")) or 1,
            preis=_betrag(e.get("preis")),
            einzelpreis=_betrag(e.get("einzelpreis")),
            konfidenz=min(max(_zahl(e.get("konfidenz")) or 0.0, 0.0), 1.0),
        )
        _packung_setzen(pos, e)
        liste.append((pos, e))

    # Mengenzeilen ("3 x 0,99") der Ware darüber oder darunter zuordnen, deren Preis passt
    for k, (pos, _) in enumerate(liste):
        if pos.typ == "mengenzeile":
            _mengenzeile_zuordnen(pos.text, liste, k)
    liste = [(p, e) for p, e in liste if p.typ != "mengenzeile"]
    for i, (pos, _) in enumerate(liste):
        pos.index = i
    positionen = [p for p, _ in liste]

    # 2. Anzahl × Einzelpreis, Rabatte, Stornos
    for pos in positionen:
        if pos.typ in ("artikel", "non_food"):
            _anzahl_pruefen(pos)
            if pos.packung_menge and pos.packung_einheit:
                anzahl = 1 if pos.gewicht else pos.anzahl
                pos.bon_menge = round(anzahl * pos.packung_menge, 3)
                pos.bon_einheit = pos.packung_einheit
    for k, pos in enumerate(positionen):
        if pos.typ not in ("rabatt", "storno"):
            continue
        ziel = _ziel(positionen, k, pos.bezug, pos.text if pos.typ == "storno" else "")
        wert = pos.preis or 0.0
        pos.aktion, pos.quelle, pos.status = "weglassen", "regel", "sicher"
        if ziel is None:
            pos.betrag = -wert
            pos.hinweis = f"{pos.typ.title()} ohne passende Zeile, bitte prüfen"
            continue
        pos.bezug = ziel.index
        if pos.typ == "rabatt":
            ziel.preis = round(max((ziel.preis or 0.0) - wert, 0.0), 2)
            ziel.rabatt = round(ziel.rabatt + wert, 2)
            pos.hinweis = f"verrechnet mit „{ziel.text}“"
        else:
            stueck = pos.anzahl if pos.anzahl and pos.anzahl < ziel.anzahl else None
            if stueck and float(ziel.anzahl).is_integer():
                anteil = stueck / ziel.anzahl
                ziel.anzahl -= stueck
                ziel.preis = round((ziel.preis or 0.0) * (1 - anteil), 2)
                pos.hinweis = f"{stueck:g} Stück von „{ziel.text}“ storniert"
            else:
                ziel.storniert = True
                pos.hinweis = f"storniert „{ziel.text}“"
        pos.betrag = 0.0

    # 3. zuordnen und Mengen rechnen
    for pos, e in liste:
        if pos.typ in ("rabatt", "storno"):
            continue
        if pos.typ in ("pfand", "leergut"):
            pos.aktion, pos.quelle, pos.status = "weglassen", "regel", "sicher"
            pos.hinweis = "Pfand" if pos.typ == "pfand" else "Leergut-Rückgabe"
            wert = pos.preis or 0.0
            pos.betrag = wert if pos.typ == "pfand" else -wert
            continue
        if pos.storniert:
            pos.aktion, pos.quelle, pos.status, pos.betrag = "weglassen", "regel", "sicher", 0.0
            pos.hinweis = "storniert"
            continue
        pos.betrag = pos.preis
        if pos.rabatt:
            pos.hinweis = f"Rabatt −{pos.rabatt:.2f} € verrechnet".replace(".", ",")
        schluessel = (norm_markt(markt), norm_text(pos.text))
        if schluessel not in aliase:
            schluessel = ("", norm_text(pos.text))
        pid = aliase.get(schluessel)
        if pid in by_id:
            pos.quelle, pos.produkt_id = "alias", pid
        elif pos.typ == "artikel" and (llm_id := str(e.get("produkt_id") or "").strip()) in by_id:
            pos.produkt_id = llm_id
        if pos.produkt_id:
            p = by_id[pos.produkt_id]
            pos.produkt, pos.einheit = p.name, p.unit
            gelernt = (alias_mengen or {}).get(schluessel) if pos.quelle == "alias" else None
            if gelernt and not pos.packung_menge:
                # Kein Größenaufdruck, aber bei diesem Bon-Text bestätigt: keine Annahme
                menge, hinweis = round(pos.anzahl * gelernt, 3), "Menge wie beim letzten Kauf"
            else:
                menge, hinweis, pos.packung_abweichend = menge_berechnen(pos, p, weitere.get(p.id))
            pos.menge = menge
            pos.hinweis = "; ".join(x for x in (pos.hinweis, hinweis) if x)
            if pos.menge is None or pos.zweifel:
                pos.status = "pruefen"
            elif pos.quelle == "alias" and not pos.annahme:
                pos.status = "sicher"
            else:
                pos.status = "vorschlag"
            pos.vorausgewaehlt = not pos.annahme and (
                pos.status == "sicher" or (pos.status == "vorschlag" and pos.konfidenz >= schwelle)
            )
            continue
        name = str(e.get("neu_name") or "").strip()[:60]
        if name and not name_passt(name, pos.text):
            pos.hinweis = "; ".join(
                x for x in (pos.hinweis, f"KI-Name „{name}“ passt nicht zum Bon-Text") if x
            )
            name = ""
        pos.aktion, pos.quelle, pos.neu_name = "neu", "neu", name or _name_aus_text(pos.text)
        pos.einheit = pos.bon_einheit or "g"
        if pos.typ == "non_food":
            pos.quelle = "non_food"
            pos.hinweis = "; ".join(
                x for x in (pos.hinweis, "Non-Food, nur buchen, wenn du es in Grocy führst") if x
            )
        if pos.bon_menge:
            pos.menge = pos.bon_menge
            pos.status = "pruefen" if pos.zweifel else "vorschlag"
        elif pos.typ == "artikel":
            pos.hinweis = "; ".join(x for x in (pos.hinweis, "Menge in g eintragen") if x)
    return markt, datum, summe, positionen


def _name_aus_text(text: str) -> str:
    """Bon-Text ohne Größenangabe als Namensvorschlag."""
    ohne = GROESSE.sub("", GEBINDE.sub("", text))
    return re.sub(r"\s+", " ", ohne).strip(" .,-").title() or text.title()


def _packung_setzen(pos: Position, e: dict) -> None:
    """Packungsgröße in Basiseinheit: Bon-Text (Code) vor KI. Gewichtsware: KI-Angabe in kg mit
    Nachkommastellen ohne Größe im Text."""
    aus_text = groesse_aus_text(pos.text)
    menge = _zahl(e.get("packung_menge")) or None
    einheit = str(e.get("packung_einheit") or "").strip().lower()
    ki = (
        (round(menge * BASIS[einheit][1], 3), BASIS[einheit][0])
        if menge and einheit in BASIS
        else None
    )
    if aus_text:
        pos.packung_menge, pos.packung_einheit = aus_text
        if ki and abs(ki[0] - aus_text[0]) > kueche.EPS:
            pos.hinweis = (
                f"Größe aus dem Bon-Text ({aus_text[0]:g} {aus_text[1]}), KI las {ki[0]:g}"
            )
    elif ki:
        pos.packung_menge, pos.packung_einheit = ki
        pos.gewicht = einheit == "kg" and not float(menge).is_integer()  # type: ignore[arg-type]


def _mengenzeile_zuordnen(text: str, liste: list[tuple[Position, dict]], k: int) -> None:
    """Ware direkt davor oder danach, deren Zeilenpreis Anzahl × Einzelpreis bzw. kg × €/kg ist.
    Passt keine oder beide nicht eindeutig: Review statt raten."""
    art, wert, preis = mengenzeile(text) or ("", 0.0, 0.0)
    nachbarn = [
        liste[j][0]
        for j in (k - 1, k + 1)
        if 0 <= j < len(liste) and liste[j][0].typ in ("artikel", "non_food")
    ]
    passend = [n for n in nachbarn if n.preis and abs(n.preis - round(wert * preis, 2)) < 0.005]
    if len(passend) != 1:
        for n in nachbarn:
            n.zweifel = True
            n.hinweis = "; ".join(
                x for x in (n.hinweis, f"„{text.strip()}“ nicht eindeutig zuzuordnen") if x
            )
        return
    ziel = passend[0]
    ziel.einzelpreis = preis
    if art == "gewicht":
        ziel.anzahl, ziel.gewicht = 1, True
        ziel.packung_menge, ziel.packung_einheit = round(wert * 1000, 3), "g"
        gewogen = f"{wert:g} kg × {preis:.2f} €/kg".replace(".", ",")
        ziel.hinweis = "; ".join(x for x in (ziel.hinweis, gewogen) if x)
    else:
        ziel.anzahl = wert
        ziel.hinweis = "; ".join(
            x for x in (ziel.hinweis, f"Anzahl {wert:g} aus „{text.strip()}“") if x
        )


def _betrag(wert: object) -> float | None:
    """Preis als Betrag ohne Vorzeichen (Rabatt/Storno/Leergut stehen auf dem Bon negativ)."""
    try:
        f = abs(float(wert))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(f, 2) if f == f else None


def _anzahl_pruefen(pos: Position) -> None:
    """Code rechnet: Passt Anzahl × Einzelpreis nicht zum Preis, gilt Preis ÷ Einzelpreis,
    wenn das eine ganze Zahl ist, sonst Review. Gewichtsware: Gewicht × kg-Preis prüfen."""
    if not pos.einzelpreis or not pos.preis:
        return
    if pos.gewicht and pos.packung_menge:
        if abs(pos.packung_menge / 1000 * pos.einzelpreis - pos.preis) > 0.02:
            pos.zweifel = True
            pos.hinweis = "Gewicht × kg-Preis passt nicht zum Preis, bitte prüfen"
        return
    if abs(pos.anzahl * pos.einzelpreis - pos.preis) <= 0.02:
        return
    n = pos.preis / pos.einzelpreis
    if round(n) >= 1 and abs(n - round(n)) < 0.03:
        pos.hinweis = "; ".join(
            x
            for x in (pos.hinweis, f"Anzahl {pos.anzahl:g} → {round(n)} (Preis ÷ Einzelpreis)")
            if x
        )
        pos.anzahl = round(n)
    else:
        pos.zweifel = True
        pos.hinweis = "Anzahl × Einzelpreis passt nicht zum Preis, bitte prüfen"


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
            daten = await app.state.ki.generate(
                "bon",
                "rationPlaner Kassenbon",
                anweisung(produkte),
                STRUKTUR,
                [
                    {
                        "media_content_id": f"{MEDIA_SOURCE}/{UNTERORDNER}/{bon.datei}",
                        "media_content_type": typ,
                    }
                ],
            )
            log.info("Bon %s Rohantwort: %s", bon_id, json.dumps(daten, ensure_ascii=False))
            alle = list(s.scalars(select(BonAlias)))
            aliase = {(a.markt, a.text): a.produkt_id for a in alle}
            alias_mengen = {(a.markt, a.text): a.menge for a in alle if a.menge}
            weitere = _weitere(s)
            markt, datum, summe, pos = auswerten(
                daten, produkte, aliase, weitere, st.bon_konfidenz_schwelle, alias_mengen
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
    return packungen.weitere(s, alle=True)


def summen_pruefung(summe: float | None, positionen: list[dict]) -> dict | None:
    """Code rechnet nach: Summe der Zeilenbeträge (nach Rabatt/Storno, Pfand +, Leergut −)."""
    if summe is None or not positionen or any(p.get("betrag") is None for p in positionen):
        return None
    berechnet = round(sum(p["betrag"] for p in positionen), 2)
    return {"berechnet": berechnet, "abweichung": round(summe - berechnet, 2)}


def _bon_dict(bon: BonImport) -> dict:
    return {
        "pruefung": summen_pruefung(bon.summe, bon.positionen or []),
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
    request.app.state.ki.pruefen("bon")
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
    request.app.state.ki.pruefen("bon")
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
    request.app.state.ki.pruefen("bon")
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
    einheit: Literal["g", "ml", "Stück"] = "g"  # Bestandseinheit für neu_anlegen
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
            p = await b.create_product(z.neu_anlegen.strip(), z.einheit)
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
        # Packungsgröße zählen, wenn sie auf dem Bon stand oder bestätigt ist (keine Annahme,
        # keine Gewichtsware). Neue Größe → Variante als Vorschlag.
        if not pos.get("gewicht") and (pos.get("packung_menge") or not pos.get("annahme")):
            packungen.kauf_merken(
                s, p, z.menge / anzahl, anzahl, bon.datum or date.today(), bon.markt
            )
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
