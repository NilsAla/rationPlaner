"""Wochenplaner: Code bewertet und simuliert, das LLM wählt aus (Konzept, Wochenplaner).

1. Kandidaten (Code): Score aus Aufbrauch-Druck, Deckungsgrad, Packungs-Fit, Abwechslung,
   Angeboten. Die besten gehen ans LLM.
2. Auswahl (LLM, ai_task): je freiem Tag Rezept-ID und Portionen.
3. Simulation (Code): Tag für Tag gegen die Chargen, FEFO, Toleranz-Skalierung, Zukauf auf
   Packungen. Liefert Einkauf, Verderb und Überhang.
4. Repair-Loop: konkretes Feedback ans LLM, höchstens zwei Runden. Was danach noch ungültig
   ist, füllt der Code.
Ohne ai_task (oder mit ki=false) wählt der Code gierig Tag für Tag den besten Kandidaten.
Der Plan ist ein Vorschlag; in den Wochenplan kommt er erst mit „Übernehmen“.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Date, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import einstellungen, kueche, naehrwerte
from app.db import Base, Kochvorgang, Packung, PlanEintrag, Preis, RezeptBewertung
from app.deps import backend, session, settings
from app.inventory.base import Batch, InventoryBackend, Product, Recipe, TrackingMode, consume_order
from app.kueche import EPS, Groesse, PackAnzahl, wird_abgebucht
from app.naehrwerte import RezeptNaehrwerte
from app.settings import Settings

log = logging.getLogger("ration.planer")
router = APIRouter(prefix="/api/v1")

BALD_TAGE = 4  # Charge gilt als „läuft bald ab“, wenn sie innerhalb dieser Tage abläuft
ABWECHSLUNG_TAGE = 14  # so lange nach dem Kochen gibt es Abzug
MAX_UNGETESTET = 2  # Konzept: höchstens 1–2 ungetestete Rezepte pro Woche
MAX_KANDIDATEN = 25
MAX_RUNDEN = 2  # Repair-Loop
GEWICHT = {"aufbrauch": 3.0, "deckung": 2.0, "fit": 1.0, "abwechslung": -2.0, "angebot": 0.2}
WOCHENTAG = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


class PlanLauf(Base):
    __tablename__ = "plan_lauf"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(
        String, default="laeuft"
    )  # laeuft/fertig/fehler/uebernommen
    start: Mapped[date] = mapped_column(Date)
    parameter: Mapped[dict] = mapped_column(JSON)
    ergebnis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fehler: Mapped[str | None] = mapped_column(String, nullable=True)
    angelegt: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- Simuliertes Lager ---------------------------------------------------------------------


@dataclass
class _Charge:
    produkt_id: str
    menge: float
    ablauf: date | None
    zukauf: bool = False


class Lager:
    """Kopie der Chargen für die Simulation. Übergeordnete Produkte umfassen ihre Varianten."""

    def __init__(
        self,
        produkte: dict[str, Product],
        batches: list[Batch],
        ersatz: dict[str, list[str]] | None = None,
    ):
        self.produkte = produkte
        self.ersatz = ersatz or {}
        self.kinder: dict[str, set[str]] = defaultdict(set)
        for p in produkte.values():
            if p.parent_id:
                self.kinder[p.parent_id].add(p.id)
        self.chargen = [
            _Charge(b.product_id, b.amount, b.best_before)
            for b in sorted(batches, key=consume_order)
            if b.product_id in produkte and not kueche.ist_rest(produkte[b.product_id])
        ]

    def _nutzbar(self, produkt_id: str, tag: date) -> list[_Charge]:
        familie = {produkt_id} | self.kinder.get(produkt_id, set())
        ersatz = set(self.ersatz.get(produkt_id, [])) - familie
        # Eigene Chargen zuerst (FEFO), Ersatz (Fusilli statt Spaghetti) nur, wenn nötig
        return sorted(
            (
                c
                for c in self.chargen
                if (c.produkt_id in familie or c.produkt_id in ersatz)
                and c.menge > EPS
                and (c.ablauf is None or c.ablauf >= tag)
            ),
            key=lambda c: (c.produkt_id in ersatz, c.ablauf or date.max),
        )

    def menge(self, produkt_id: str, tag: date) -> float:
        return sum(c.menge for c in self._nutzbar(produkt_id, tag))

    def bald(self, produkt_id: str, tag: date) -> tuple[float, date | None]:
        """Menge, die bis tag + BALD_TAGE abläuft, und der früheste Ablauf."""
        grenze = tag + timedelta(days=BALD_TAGE)
        bald = [c for c in self._nutzbar(produkt_id, tag) if c.ablauf and c.ablauf <= grenze]
        return sum(c.menge for c in bald), (bald[0].ablauf if bald else None)

    def entnehmen(self, produkt_id: str, menge: float, tag: date) -> float:
        rest = menge
        for c in self._nutzbar(produkt_id, tag):
            if rest <= EPS:
                break
            nimm = min(c.menge, rest)
            c.menge -= nimm
            rest -= nimm
        return menge - rest

    def zukaufen(self, produkt_id: str, menge: float, ablauf: date | None) -> None:
        self.chargen.append(_Charge(produkt_id, menge, ablauf, zukauf=True))


# --- Bewertung ---------------------------------------------------------------------------


FLEISCH_KATEGORIEN = {"Fleisch", "Fisch"}
FLEISCH_TAGS = {"Fleisch", "Fisch"}
VEGGI_KATEGORIEN = {"Gemüse", "Obst", "Kräuter", "Hülsenfrüchte"}
VEGGI_TAGS = {"vegetarisch", "vegan"}
AUFWAND_RANG = {"schnell": 0, "normal": 1, "aufwaendig": 2}
NEUTRAL = {
    "fleisch": 0.0,
    "preis": 0.0,
    "extravagant": 0.0,
    "neu": 0.0,
    "gesund": 0.0,
    "protein": 0.0,
    "rohkost": 0.0,
}


@dataclass
class Kandidat:
    rezept_id: str
    name: str
    score: float
    deckung: float  # Anteil der Zutaten, die (mit Toleranz) da sind
    aufbrauch: float
    fit: float  # 1 = Zukauf geht in Packungen auf
    zuletzt_vor_tagen: int | None
    status: str | None
    tags: list[str]
    aufwand: str | None
    nutzt_bald: list[str] = field(default_factory=list)
    fleisch: float = 0.0  # Gewichtsanteil Fleisch/Fisch, -1 = unbekannt
    kosten_portion: float | None = None
    beliebt: float = 0.0  # 0 … 1 aus Favorit, Daumen, wie oft gekocht
    gekocht: int = 0
    zu_aufwendig: bool = False
    kcal: float | None = None  # je Portion
    protein: float | None = None  # g je Portion
    nutri: str | None = None  # Nutri-Score-Klasse A–E
    ziel_fit: float | None = None  # 0 … 1, Passung zum Mahlzeiten-Ziel


@dataclass
class Kontext:
    """Alles, was Bewertung und Simulation brauchen, einmal geladen."""

    produkte: dict[str, Product]
    rezepte: dict[str, Recipe]
    batches: list[Batch]
    weitere: dict[str, list[Groesse]]
    toleranz_default: int
    historie: dict[str, date]  # Rezept → zuletzt gekocht
    angebote: set[str]  # Produkte mit laufendem Angebot
    preise: dict[str, float]  # Produkt → EUR je Bestandseinheit
    praeferenzen: dict = field(default_factory=lambda: dict(NEUTRAL))
    beliebtheit: dict[str, float] = field(default_factory=dict)
    bewertung: dict[str, int] = field(default_factory=dict)  # Daumen -1/0/1
    gekocht: dict[str, int] = field(default_factory=dict)
    aufwand_je_tag: dict[int, str] = field(default_factory=dict)  # Wochentag → höchster Aufwand
    ausschluss_tags: set[str] = field(default_factory=set)
    max_ungetestet: int = MAX_UNGETESTET
    kosten_median: float | None = None
    naehr: dict[str, RezeptNaehrwerte] = field(default_factory=dict)  # je Rezept, einmal gerechnet
    ziel: dict = field(default_factory=dict)  # Mahlzeiten-Ziel aus den Einstellungen
    ziel_im_planer: bool = False
    ersatz: dict[str, list[str]] = field(default_factory=dict)  # austauschbare Geschwister

    def toleranz(self, p: Product) -> float:
        return (p.tolerance_pct if p.tolerance_pct is not None else self.toleranz_default) / 100

    def pref(self, name: str) -> float:
        try:
            return max(-1.0, min(1.0, float(self.praeferenzen.get(name, 0) or 0)))
        except (TypeError, ValueError):
            return 0.0


def _gramm(p: Product, menge: float) -> float:
    return menge * (1.0 if p.unit.lower() in ("g", "ml") else (p.grams_per_unit or 0.0))


def profil(rezept: Recipe, k: Kontext) -> tuple[float, float, float | None]:
    """Fleischanteil, Gemüseanteil (nach Gewicht, Tags überstimmen) und Kosten je Portion."""
    tags = set(rezept.tags)
    gesamt = fleisch = veggi = 0.0
    kosten, bekannt = 0.0, 0
    for z in rezept.ingredients:
        p = k.produkte.get(z.product_id)
        if p is None:
            continue
        g = _gramm(p, z.amount)
        gesamt += g
        if p.category in FLEISCH_KATEGORIEN:
            fleisch += g
        elif p.category in VEGGI_KATEGORIEN:
            veggi += g
        if wird_abgebucht(p) and (preis := k.preise.get(p.id)) is not None:
            kosten += preis * z.amount
            bekannt += 1
    f_anteil = fleisch / gesamt if gesamt else 0.0
    v_anteil = veggi / gesamt if gesamt else 0.0
    if tags & VEGGI_TAGS:
        f_anteil = 0.0
    elif tags & FLEISCH_TAGS:
        f_anteil = max(f_anteil, 0.5)
    portion = kosten / max(rezept.servings, 1) if bekannt else None
    return f_anteil, v_anteil, portion


def bewerten(rezept: Recipe, portionen: int, lager: Lager, k: Kontext, tag: date) -> Kandidat:
    faktor = portionen / max(rezept.servings, 1)
    n, gedeckt, aufbrauch, fits, nutzt, angebote = 0, 0.0, 0.0, [], [], 0
    for z in rezept.ingredients:
        p = k.produkte.get(z.product_id)
        if p is None or not wird_abgebucht(p):
            continue
        n += 1
        bedarf = z.amount * faktor
        if bedarf <= EPS:
            gedeckt += 1
            continue
        da = lager.menge(p.id, tag)
        if da + EPS >= bedarf * (1 - k.toleranz(p)):
            gedeckt += 1
        else:
            gedeckt += da / bedarf
            fehl = bedarf - da
            e = kueche.auf_packungen(p, fehl, k.weitere.get(p.id))
            fits.append(min(fehl / e.menge, 1.0) if e.menge > EPS else 1.0)
        bald, ablauf = lager.bald(p.id, tag)
        if bald > EPS and ablauf:
            dringlich = (BALD_TAGE + 1 - max((ablauf - tag).days, 0)) / (BALD_TAGE + 1)
            aufbrauch += min(bald, bedarf) / bedarf * dringlich
            nutzt.append(p.name)
        angebote += p.id in k.angebote
    deckung = gedeckt / n if n else 1.0
    fit = sum(fits) / len(fits) if fits else 1.0
    zuletzt = k.historie.get(rezept.id)
    vor = (tag - zuletzt).days if zuletzt else None
    abwechslung = max(ABWECHSLUNG_TAGE - vor, 0) / ABWECHSLUNG_TAGE if vor is not None else 0.0

    # Präferenzen (Regler -1 … 1, 0 = neutral)
    extravagant, neu = k.pref("extravagant"), k.pref("neu")
    vorrat_gewicht = 1 - 0.5 * extravagant  # -1: Vorrat doppelt so wichtig … 1: halb so wichtig
    f_anteil, v_anteil, kosten = profil(rezept, k)
    beliebt = k.beliebtheit.get(rezept.id, 0.0)
    nie_gekocht = k.gekocht.get(rezept.id, 0) == 0
    rel_kosten = 0.0
    if kosten is not None and k.kosten_median:
        rel_kosten = max(-1.0, min(1.0, kosten / k.kosten_median - 1))
    ernaehrung = ernaehrung_score(k.naehr.get(rezept.id), k)
    max_aufwand = k.aufwand_je_tag.get(tag.weekday(), "aufwaendig")
    zu_aufwendig = AUFWAND_RANG.get(rezept.effort or "", 0) > AUFWAND_RANG.get(max_aufwand, 2)
    score = (
        vorrat_gewicht
        * (GEWICHT["aufbrauch"] * aufbrauch + GEWICHT["deckung"] * deckung + GEWICHT["fit"] * fit)
        + GEWICHT["abwechslung"] * abwechslung
        + GEWICHT["angebot"] * min(angebote, 3)
        - 0.3 * (1 - max(neu, 0)) * (rezept.status == "ungetestet")
        + 1.5 * k.pref("fleisch") * (f_anteil - v_anteil)
        + 1.0 * k.pref("preis") * rel_kosten
        + 1.0 * max(extravagant, 0) * (1 - deckung)
        - 1.5 * neu * beliebt
        + 1.0 * max(neu, 0) * nie_gekocht
        + 0.5 * k.bewertung.get(rezept.id, 0)
        - (2.0 if zu_aufwendig else 0.0)
        + ernaehrung
    )
    n = k.naehr.get(rezept.id)
    return Kandidat(
        rezept.id,
        rezept.name,
        round(score, 3),
        round(deckung, 3),
        round(aufbrauch, 3),
        round(fit, 3),
        vor,
        rezept.status,
        list(rezept.tags),
        rezept.effort,
        nutzt,
        round(f_anteil, 2),
        round(kosten, 2) if kosten is not None else None,
        round(beliebt, 2),
        k.gekocht.get(rezept.id, 0),
        zu_aufwendig,
        n.je_portion["kcal"] if n else None,
        n.je_portion["protein"] if n else None,
        n.nutri_score["klasse"] if n and n.nutri_score else None,
        n.ziel["fit"] if n and n.ziel else None,
    )


def ernaehrung_score(n: RezeptNaehrwerte | None, k: Kontext) -> float:
    """Regler Gesund/Protein/Rohkost und kcal-Ziel. Ohne Nährwerte neutral (0), damit Rezepte
    ohne Daten nicht bestraft, aber auch nicht bevorzugt werden."""
    if n is None or n.abdeckung < 0.8:
        return 0.0
    wert = 0.0
    if n.gesund is not None:  # 0 … 1 → -1 … 1
        wert += 1.5 * k.pref("gesund") * (2 * n.gesund - 1)
    if n.protein_kcal_pct is not None:  # 15 % Energie aus Protein = neutral, 30 % = hoch
        wert += 1.0 * k.pref("protein") * max(-1.0, min(1.0, (n.protein_kcal_pct - 15) / 15))
    wert += 1.0 * k.pref("rohkost") * max(-1.0, min(1.0, (n.rohkost_pct - 30) / 30))
    if k.ziel_im_planer and n.ziel and n.ziel["fit"] is not None:
        wert += 1.5 * (n.ziel["fit"] - 0.5)
    return wert


def kandidaten(k: Kontext, lager: Lager, tag: date, portionen: int) -> list[Kandidat]:
    alle = [
        bewerten(r, portionen, lager, k, tag)
        for r in k.rezepte.values()
        if r.ingredients and not (set(r.tags) & k.ausschluss_tags)
    ]
    return sorted(alle, key=lambda c: (-c.score, c.name))


# --- Simulation --------------------------------------------------------------------------


@dataclass
class PlanTag:
    datum: date
    rezept_id: str
    portionen: int
    quelle: str  # fest / ki / code
    begruendung: str = ""


@dataclass
class Skaliert:
    name: str
    rezept: float
    genommen: float
    einheit: str


@dataclass
class TagErgebnis:
    datum: date
    rezept_id: str
    name: str
    portionen: int
    quelle: str
    begruendung: str
    skaliert: list[Skaliert] = field(default_factory=list)
    zukauf: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)


@dataclass
class Verderb:
    produkt_id: str
    name: str
    menge: float
    einheit: str
    ablauf: date | None
    zukauf: bool  # True = Überhang aus dem Einkauf dieser Woche


@dataclass
class Simulation:
    tage: list[TagErgebnis]
    einkauf: list[dict]
    verderb: list[Verderb]  # läuft innerhalb des Plans ab, ohne verbraucht zu werden
    ueberhang: list[Verderb]  # Rest aus Zukauf, hält über das Planende hinaus
    kosten: float | None  # Schätzung aus bekannten Preisen
    kosten_unbekannt: int  # Positionen ohne Preis


class Simulator:
    def __init__(self, k: Kontext, start: date, ende: date):
        self.k, self.start, self.ende = k, start, ende
        self.lager = Lager(k.produkte, k.batches, k.ersatz)
        self.tage: list[TagErgebnis] = []
        self.zukauf: dict[str, dict[tuple[str, float], int]] = defaultdict(dict)
        self.zukauf_menge: dict[str, float] = defaultdict(float)

    def kochen(self, t: PlanTag) -> TagErgebnis:
        rezept = self.k.rezepte[t.rezept_id]
        erg = TagErgebnis(t.datum, rezept.id, rezept.name, t.portionen, t.quelle, t.begruendung)
        if _abgebucht(t):
            self.tage.append(erg)
            return erg
        faktor = t.portionen / max(rezept.servings, 1)
        for z in rezept.ingredients:
            p = self.k.produkte.get(z.product_id)
            if p is None or not wird_abgebucht(p):
                continue
            bedarf = z.amount * faktor
            tol = self.k.toleranz(p)
            da = self.lager.menge(p.id, t.datum)
            if da + EPS >= bedarf:
                nimm = bedarf
            elif da + EPS >= bedarf * (1 - tol):
                nimm = da  # Toleranz: weniger nehmen statt zukaufen
                erg.skaliert.append(Skaliert(p.name, round(bedarf, 1), round(da, 1), p.unit))
            else:
                fehl = bedarf - da
                e = kueche.auf_packungen(p, fehl, self.k.weitere.get(p.id))
                self._buchen_zukauf(p, e, t.datum, erg)
                nimm = bedarf
                rest = e.menge - fehl
                if p.tracking_mode == TrackingMode.EXAKT and EPS < rest <= bedarf * tol:
                    nimm += rest  # Packung geht auf, statt einen Rest liegen zu lassen
                    erg.skaliert.append(Skaliert(p.name, round(bedarf, 1), round(nimm, 1), p.unit))
            self.lager.entnehmen(p.id, nimm, t.datum)
        self.tage.append(erg)
        return erg

    def _buchen_zukauf(self, p: Product, e: kueche.Einkauf, tag: date, erg: TagErgebnis) -> None:
        ablauf = None
        if p.shelf_life_days > 0:
            ablauf = self.start + timedelta(days=p.shelf_life_days)
            if ablauf < tag:
                erg.hinweise.append(
                    f"{p.name} hält nur {p.shelf_life_days} Tage: erst kurz vorher kaufen"
                )
                ablauf = tag + timedelta(days=p.shelf_life_days)
        self.lager.zukaufen(p.id, e.menge, ablauf)
        erg.zukauf.append(e.text)
        self.zukauf_menge[p.id] += e.menge
        for pack in e.packungen:
            schluessel = (pack.einheit, pack.menge)
            self.zukauf[p.id][schluessel] = self.zukauf[p.id].get(schluessel, 0) + pack.anzahl

    def ergebnis(self) -> Simulation:
        einkauf, kosten, unbekannt, bepreist = [], 0.0, 0, 0
        for pid, menge in self.zukauf_menge.items():
            p = self.k.produkte[pid]
            packs = [PackAnzahl(n, einheit, m) for (einheit, m), n in self.zukauf[pid].items()]
            pos = kueche.Einkauf(pid, p.name, menge, p.unit, packungen=packs)
            preis = self.k.preise.get(pid)
            betrag = round(preis * menge, 2) if preis is not None else None
            if betrag is None:
                unbekannt += 1
            else:
                kosten += betrag
                bepreist += 1
            einkauf.append({**asdict(pos), "text": pos.text, "preis": betrag})
        verderb, ueberhang = [], []
        for c in self.lager.chargen:
            p = self.k.produkte[c.produkt_id]
            if c.menge <= EPS or p.tracking_mode != TrackingMode.EXAKT:
                continue
            eintrag = Verderb(p.id, p.name, round(c.menge, 1), p.unit, c.ablauf, c.zukauf)
            if c.ablauf and self.start <= c.ablauf <= self.ende:
                verderb.append(eintrag)
            elif c.zukauf:
                ueberhang.append(eintrag)
        return Simulation(
            tage=sorted(self.tage, key=lambda t: t.datum),
            einkauf=sorted(einkauf, key=lambda x: x["name"]),
            verderb=sorted(verderb, key=lambda v: v.ablauf or date.max),
            ueberhang=ueberhang,
            kosten=round(kosten, 2) if bepreist else None,
            kosten_unbekannt=unbekannt,
        )


def simulieren(k: Kontext, tage: list[PlanTag], start: date, ende: date) -> Simulation:
    sim = Simulator(k, start, ende)
    for t in sorted(tage, key=lambda t: t.datum):
        sim.kochen(t)
    return sim.ergebnis()


def _abgebucht(t: PlanTag) -> bool:
    """Gekochte Tage sind in Grocy schon abgebucht und verbrauchen nichts mehr."""
    return t.quelle == "gekocht"


def gierig(
    k: Kontext,
    daten: list[date],
    fest: dict[date, PlanTag],
    portionen: int,
    start: date,
    ende: date,
    vorgabe: dict[date, PlanTag] | None = None,
    personen: dict[date, int] | None = None,
) -> list[PlanTag]:
    """Code-Auswahl: Tag für Tag der beste Kandidat gegen den simulierten Bestand.

    vorgabe: schon gültige Tage (z. B. aus der KI-Antwort), die übernommen werden.
    """
    sim = Simulator(k, start, ende)
    benutzt = {t.rezept_id for t in fest.values()} | {t.rezept_id for t in (vorgabe or {}).values()}
    ungetestet = sum(
        1
        for t in [*fest.values(), *(vorgabe or {}).values()]
        if k.rezepte[t.rezept_id].status == "ungetestet"
    )
    plan = []
    for d in daten:
        if d in fest or (vorgabe and d in vorgabe):
            t = fest.get(d) or vorgabe[d]  # type: ignore[index]
        else:
            wahl = next(
                (
                    c
                    for c in kandidaten(k, sim.lager, d, (personen or {}).get(d, portionen))
                    if c.rezept_id not in benutzt
                    and (c.status != "ungetestet" or ungetestet < k.max_ungetestet)
                ),
                None,
            )
            if wahl is None:
                continue
            benutzt.add(wahl.rezept_id)
            ungetestet += wahl.status == "ungetestet"
            t = PlanTag(
                d, wahl.rezept_id, (personen or {}).get(d, portionen), "code", _begruendung(wahl)
            )
        sim.kochen(t)
        plan.append(t)
    return plan


def _begruendung(c: Kandidat) -> str:
    if c.nutzt_bald:
        return "verbraucht " + ", ".join(c.nutzt_bald[:3])
    if c.deckung >= 0.999:
        return "alles da"
    return f"{round(c.deckung * 100)} % da"


# --- LLM ---------------------------------------------------------------------------------

ANWEISUNG = """Du planst die Abendessen eines Single-Haushalts in Deutschland.
Ziele in dieser Reihenfolge: nichts verderben lassen, wenig zukaufen, Abwechslung.

Zu planende Tage: {tage}
Schon fest geplant (nicht ändern, nicht wiederholen): {fest}
Läuft bald ab: {bald}

Kandidaten (id | name | score | deckung % | nutzt bald Ablaufendes | zuletzt vor Tagen | status |
tags | aufwand | Fleischanteil % | EUR je Portion | Liebling 0–1 | so oft gekocht |
kcal je Portion | Protein g je Portion | Nutri-Score), höherer Score ist besser, der Score
enthält die Wünsche unten schon:
{kandidaten}

Wünsche des Nutzers: {wuensche}

Regeln:
- Für jeden zu planenden Tag genau ein Eintrag mit datum und rezept_id aus den Kandidaten.
- Kein Rezept zweimal, höchstens {max_ungetestet} mit status ungetestet.
- Rezepte, die bald Ablaufendes verbrauchen, früh in den Zeitraum legen.
- Höchster Aufwand je Wochentag: {aufwand}.
- portionen: so viele, wie an dem Tag Personen essen (steht beim Tag; sonst {portionen}).
- begruendung: ein kurzer Satz."""


def wuensche_text(k: Kontext) -> str:
    """Präferenz-Regler in Worte, damit das LLM sie auch beim Abwägen berücksichtigt."""
    stufen = [
        ("fleisch", "weniger Fleisch, mehr Gemüse", "gern Fleisch"),
        ("preis", "günstig", "darf teurer sein"),
        ("extravagant", "Vorrat abbauen", "gern extravagant und neue Zutaten"),
        ("neu", "lieber Lieblingsrezepte", "lieber etwas Neues"),
        ("gesund", "Genuss vor Gesundheit", "gesund (guter Nutri-Score)"),
        ("protein", "Protein egal", "viel Protein"),
        ("rohkost", "lieber gekocht", "viel Rohkost"),
    ]
    teile = []
    for name, links, rechts in stufen:
        x = k.pref(name)
        if abs(x) >= 0.2:
            staerke = "stark: " if abs(x) >= 0.7 else ""
            teile.append(staerke + (links if x < 0 else rechts))
    if k.ausschluss_tags:
        teile.append("nie: " + ", ".join(sorted(k.ausschluss_tags)))
    if k.ziel_im_planer and k.ziel:
        teile.append(
            f"je Portion etwa {k.ziel['kcal_mahlzeit']} kcal und mindestens "
            f"{k.ziel['protein_mahlzeit']} g Protein"
        )
    return "; ".join(teile) or "keine besonderen"


def _aufwand_text(k: Kontext, daten: list[date]) -> str:
    tage = sorted({d.weekday() for d in daten})
    grenzen = [
        f"{WOCHENTAG[t]} {k.aufwand_je_tag.get(t, 'aufwaendig')}"
        for t in tage
        if k.aufwand_je_tag.get(t, "aufwaendig") != "aufwaendig"
    ]
    return ", ".join(grenzen) or "beliebig (aufwändige eher am Wochenende)"


def struktur(daten: list[date], ids: list[str]) -> dict:
    return {
        "tage": {
            "required": True,
            "selector": {
                "object": {
                    "multiple": True,
                    "fields": {
                        "datum": {
                            "required": True,
                            "selector": {"select": {"options": [d.isoformat() for d in daten]}},
                        },
                        "rezept_id": {"required": True, "selector": {"select": {"options": ids}}},
                        "portionen": {"required": True, "selector": {"number": {}}},
                        "begruendung": {"required": False, "selector": {"text": {}}},
                    },
                }
            },
        }
    }


def _tag_text(d: date, personen: dict[date, int] | None = None) -> str:
    extra = f", {personen[d]} Personen" if personen and d in personen else ""
    return f"{d.isoformat()} ({WOCHENTAG[d.weekday()]}{extra})"


def personen_von(par: dict) -> dict[date, int]:
    """Personen je Tag aus den Lauf-Parametern (ältere Läufe: überall portionen)."""
    return {date.fromisoformat(k): int(v) for k, v in (par.get("personen") or {}).items()}


def anweisung(
    k: Kontext,
    daten: list[date],
    fest: dict[date, PlanTag],
    liste: list[Kandidat],
    lager: Lager,
    start: date,
    portionen: int,
    personen: dict[date, int] | None = None,
) -> str:
    bald = []
    for c in sorted(lager.chargen, key=lambda c: c.ablauf or date.max):
        p = k.produkte[c.produkt_id]
        if c.ablauf and start <= c.ablauf <= start + timedelta(days=BALD_TAGE) and c.menge > EPS:
            bald.append(f"{p.name} {kueche.menge(c.menge)} {p.unit} bis {c.ablauf.isoformat()}")
    zeilen = [
        f"{c.rezept_id} | {c.name} | {c.score} | {round(c.deckung * 100)} | "
        f"{', '.join(c.nutzt_bald) or '-'} | {c.zuletzt_vor_tagen if c.zuletzt_vor_tagen is not None else '-'} | "  # noqa: E501
        f"{c.status or '-'} | {', '.join(c.tags) or '-'} | {c.aufwand or '-'} | "
        f"{round(c.fleisch * 100)} | "
        f"{c.kosten_portion if c.kosten_portion is not None else '-'} | "
        f"{c.beliebt} | {c.gekocht} | "
        f"{round(c.kcal) if c.kcal is not None else '-'} | "
        f"{round(c.protein) if c.protein is not None else '-'} | {c.nutri or '-'}"
        for c in liste
    ]
    return ANWEISUNG.format(
        tage=", ".join(_tag_text(d, personen) for d in daten),
        fest="; ".join(f"{_tag_text(d)}: {k.rezepte[t.rezept_id].name}" for d, t in fest.items())
        or "nichts",
        bald="; ".join(bald) or "nichts",
        kandidaten="\n".join(zeilen),
        max_ungetestet=k.max_ungetestet,
        portionen=portionen,
        wuensche=wuensche_text(k),
        aufwand=_aufwand_text(k, daten),
    )


def pruefen(
    antwort: dict, k: Kontext, daten: list[date], fest: dict[date, PlanTag], erlaubt: set[str]
) -> tuple[dict[date, PlanTag], list[str]]:
    """Validiert die KI-Antwort. Liefert die gültigen Tage und konkrete Probleme."""
    gueltig: dict[date, PlanTag] = {}
    probleme: list[str] = []
    benutzt = {t.rezept_id for t in fest.values()}
    ungetestet = sum(1 for t in fest.values() if k.rezepte[t.rezept_id].status == "ungetestet")
    eintraege = antwort.get("tage") if isinstance(antwort, dict) else None
    for e in eintraege if isinstance(eintraege, list) else []:
        if not isinstance(e, dict):
            continue
        try:
            d = date.fromisoformat(str(e.get("datum")))
        except ValueError:
            probleme.append(f"Ungültiges Datum {e.get('datum')!r}")
            continue
        rid = str(e.get("rezept_id"))
        if d not in daten:
            probleme.append(f"{d.isoformat()} ist nicht zu planen")
        elif d in gueltig:
            probleme.append(f"{d.isoformat()} doppelt belegt")
        elif rid not in erlaubt:
            probleme.append(f"{d.isoformat()}: {rid!r} ist kein Kandidat")
        elif rid in benutzt:
            probleme.append(f"{d.isoformat()}: {k.rezepte[rid].name} kommt schon vor")
        elif k.rezepte[rid].status == "ungetestet" and ungetestet >= k.max_ungetestet:
            probleme.append(f"{d.isoformat()}: zu viele ungetestete Rezepte")
        elif set(k.rezepte[rid].tags) & k.ausschluss_tags:
            probleme.append(f"{d.isoformat()}: {k.rezepte[rid].name} ist ausgeschlossen")
        elif AUFWAND_RANG.get(k.rezepte[rid].effort or "", 0) > AUFWAND_RANG.get(
            k.aufwand_je_tag.get(d.weekday(), "aufwaendig"), 2
        ):
            wt = WOCHENTAG[d.weekday()]
            probleme.append(f"{d.isoformat()}: {k.rezepte[rid].name} ist zu aufwändig für {wt}")
        else:
            try:
                portionen = min(max(int(float(e.get("portionen") or 0)), 1), 6)
            except (TypeError, ValueError):
                portionen = 1
            benutzt.add(rid)
            ungetestet += k.rezepte[rid].status == "ungetestet"
            gueltig[d] = PlanTag(d, rid, portionen, "ki", str(e.get("begruendung") or "")[:200])
    for d in daten:
        if d not in gueltig and not any(p.startswith(d.isoformat()) for p in probleme):
            probleme.append(f"{d.isoformat()} fehlt")
    return gueltig, probleme


def verderb_feedback(
    sim: Simulation, liste: list[Kandidat], im_plan: set[str], k: Kontext
) -> list[str]:
    """Verdirbt etwas, das ein nicht gewählter Kandidat verbrauchen würde? Dann nachbessern."""
    probleme = []
    for v in sim.verderb:
        if v.zukauf:
            continue
        passende = [
            c.rezept_id
            for c in liste
            if c.rezept_id not in im_plan
            and any(z.product_id == v.produkt_id for z in k.rezepte[c.rezept_id].ingredients)
        ]
        if passende:
            probleme.append(
                f"{v.name} ({kueche.menge(v.menge)} {v.einheit}) läuft am {v.ablauf} ab und wird "
                f"nicht verbraucht; Kandidaten dafür: {', '.join(passende[:4])}"
            )
    return probleme


# --- Ablauf ------------------------------------------------------------------------------


async def kontext_laden(
    b: InventoryBackend,
    s: Session,
    st: Settings,
    start: date,
    praeferenzen: dict | None = None,
    max_ungetestet: int | None = None,
) -> Kontext:
    """Lädt Grocy-Daten und App-Daten; Präferenzen aus den Einstellungen, überschreibbar."""
    einst = einstellungen.lesen(s)
    prefs = {**einst["praeferenzen"], **(praeferenzen or {})}
    produkte = {p.id: p for p in await b.list_products()}
    rezepte = {r.id: r for r in await b.list_recipes()}
    weitere: dict[str, list[Groesse]] = defaultdict(list)
    angebote: set[str] = set()
    for pk in s.scalars(select(Packung)):
        if (pk.gueltig_bis and pk.gueltig_bis < start) or pk.bestaetigt is False:
            continue
        weitere[pk.produkt_id].append(Groesse(pk.einheit, pk.menge, pk.quelle, pk.haeufigkeit or 0))
        if pk.quelle == "angebot":
            angebote.add(pk.produkt_id)
    historie: dict[str, date] = {}
    for e in s.scalars(
        select(PlanEintrag).where(
            PlanEintrag.status == "gekocht",
            PlanEintrag.datum >= start - timedelta(days=ABWECHSLUNG_TAGE),
        )
    ):
        if e.datum > historie.get(e.rezept_id, date.min):
            historie[e.rezept_id] = e.datum
    gekocht: dict[str, int] = defaultdict(int)
    for kv in s.scalars(select(Kochvorgang)):
        gekocht[kv.rezept_id] += 1
        if kv.datum < start and kv.datum > historie.get(kv.rezept_id, date.min):
            historie[kv.rezept_id] = kv.datum
    meinung = {m.rezept_id: m for m in s.scalars(select(RezeptBewertung))}
    beliebtheit = {
        rid: round(
            min(
                1.0,
                0.5 * bool(m.favorit if (m := meinung.get(rid)) else False)
                + 0.3 * max(m.bewertung if m else 0, 0)
                + 0.2 * min(gekocht.get(rid, 0) / 5, 1.0),
            ),
            2,
        )
        for rid in rezepte
    }
    # Kostenschätzung: letzter Bonpreis > Angebot > Streichpreis (Konzept)
    rang = {"bon": 0, "angebot": 1, "streichpreis": 2}
    preise: dict[str, tuple[int, date, float]] = {}
    for pr in s.scalars(select(Preis)):
        if pr.menge <= 0:
            continue
        kandidat = (rang.get(pr.quelle, 3), pr.datum, pr.preis / pr.menge)
        bisher = preise.get(pr.produkt_id)
        if bisher is None or (kandidat[0], -kandidat[1].toordinal()) < (
            bisher[0],
            -bisher[1].toordinal(),
        ):
            preise[pr.produkt_id] = kandidat
    k = Kontext(
        produkte=produkte,
        rezepte=rezepte,
        batches=await b.list_all_batches(),
        weitere=dict(weitere),
        toleranz_default=st.toleranz_default_pct,
        historie=historie,
        angebote=angebote,
        preise={pid: v[2] for pid, v in preise.items()},
        praeferenzen=prefs,
        beliebtheit=beliebtheit,
        bewertung={rid: m.bewertung for rid, m in meinung.items()},
        gekocht=dict(gekocht),
        aufwand_je_tag={int(t): a for t, a in (prefs.get("aufwand_je_tag") or {}).items()},
        ausschluss_tags=set(prefs.get("ausschluss_tags") or []),
        max_ungetestet=(
            max_ungetestet if max_ungetestet is not None else einst["planer"]["max_ungetestet"]
        ),
    )
    nw = naehrwerte.alle(s)
    k.ersatz = kueche.ersatz_karte(produkte, set(einst["ersatz"]["gesperrt"]))
    k.ziel = einst["ernaehrung"]
    k.ziel_im_planer = bool(einst["ernaehrung"]["im_planer"])
    k.naehr = {
        rid: naehrwerte.rezept_naehrwerte(r, produkte, nw, ziel=k.ziel)
        for rid, r in rezepte.items()
    }
    kosten = sorted(c for r in rezepte.values() if (c := profil(r, k)[2]) is not None)
    k.kosten_median = kosten[len(kosten) // 2] if kosten else None
    return k


def feste_tage(
    s: Session, k: Kontext, daten: list[date], ueberschreiben: bool
) -> dict[date, PlanTag]:
    if not daten:
        return {}
    fest = {}
    for e in s.scalars(
        select(PlanEintrag).where(PlanEintrag.datum >= min(daten), PlanEintrag.datum <= max(daten))
    ):
        if e.rezept_id not in k.rezepte:
            continue
        if e.status == "gekocht":
            fest[e.datum] = PlanTag(e.datum, e.rezept_id, e.portionen, "gekocht")
        elif not ueberschreiben:
            fest[e.datum] = PlanTag(e.datum, e.rezept_id, e.portionen, "fest")
    return fest


async def planen(app, lauf_id: int) -> None:
    st: Settings = app.state.settings
    ha, ki = app.state.ha, app.state.ki
    b: InventoryBackend = app.state.backend
    with Session(app.state.engine) as s:
        lauf = s.get(PlanLauf, lauf_id)
        if lauf is None:
            return
        par = lauf.parameter
        try:
            start = lauf.start
            kochtage = sorted(date.fromisoformat(d) for d in par["daten"])
            k = await kontext_laden(
                b, s, st, start, par.get("praeferenzen"), par.get("max_ungetestet")
            )
            if not any(r.ingredients for r in k.rezepte.values()):
                raise ValueError("Keine Rezepte mit Zutaten in Grocy")
            # Schon geplante Tage im Zeitraum bleiben fest, auch an Nicht-Kochtagen.
            fest = feste_tage(s, k, kochtage, par.get("ueberschreiben", False))
            alle = sorted(set(kochtage) | set(fest))
            ende = alle[-1]
            frei = [d for d in alle if d not in fest and d >= start]
            offen_fest = {d: t for d, t in fest.items() if d >= start}
            lager = Lager(k.produkte, k.batches, k.ersatz)
            liste = kandidaten(k, lager, start, par["portionen"])[:MAX_KANDIDATEN]
            liste = [c for c in liste if c.rezept_id not in {t.rezept_id for t in fest.values()}]
            ki_info: dict = {"aktiv": False, "runden": 0, "probleme": [], "fehler": None}
            vorgabe: dict[date, PlanTag] = {}
            if frei and par.get("ki", True) and ki.verfuegbar("planer") and liste:
                ki_info["aktiv"] = True
                erlaubt = {c.rezept_id for c in liste}
                personen = personen_von(par)
                text = anweisung(k, frei, fest, liste, lager, start, par["portionen"], personen)
                struktur_ = struktur(frei, sorted(erlaubt))
                probleme: list[str] = []
                try:
                    for runde in range(MAX_RUNDEN + 1):
                        if probleme:
                            text += "\n\nDein letzter Plan hatte Probleme:\n- " + "\n- ".join(
                                probleme
                            )
                            text += "\nGib den vollständigen Plan für alle Tage neu aus."
                        antwort = await ki.generate(
                            "planer", "rationPlaner Wochenplan", text, struktur_
                        )
                        ki_info["runden"] = runde + 1
                        log.info(
                            "Plan %s Runde %s Rohantwort: %s",
                            lauf_id,
                            runde + 1,
                            json.dumps(antwort, ensure_ascii=False),
                        )
                        vorgabe, probleme = pruefen(antwort, k, frei, fest, erlaubt)
                        # Portionen = Personen des Tages (Code entscheidet, nicht die KI)
                        vorgabe = {
                            d: replace(t, portionen=personen.get(d, t.portionen))
                            for d, t in vorgabe.items()
                        }
                        if not probleme:
                            sim = simulieren(
                                k, [*offen_fest.values(), *vorgabe.values()], start, ende
                            )
                            im_plan = {t.rezept_id for t in [*fest.values(), *vorgabe.values()]}
                            probleme = verderb_feedback(sim, liste, im_plan, k)
                        if not probleme:
                            break
                    ki_info["probleme"] = probleme
                except Exception as err:  # noqa: BLE001 - KI-Ausfall: Code plant allein
                    log.warning("Plan %s: KI fehlgeschlagen: %s", lauf_id, err)
                    ki_info["fehler"] = str(err) or type(err).__name__
            # Ungültige oder fehlende Tage füllt der Code.
            plan = gierig(
                k, alle, offen_fest, par["portionen"], start, ende, vorgabe, personen_von(par)
            )
            sim = simulieren(k, plan, start, ende)
            lauf.ergebnis = json.loads(
                json.dumps(
                    {
                        **asdict(sim),
                        "kandidaten": [asdict(c) for c in liste],
                        "ki": ki_info,
                        "ohne_rezept": [
                            d.isoformat() for d in alle if d not in {t.datum for t in plan}
                        ],
                    },
                    default=str,
                )
            )
            lauf.status = "fertig"
            if par.get("uebernehmen"):
                uebernehmen_(s, lauf)
            s.commit()
            if ha is not None:
                try:
                    kosten = f", ca. {sim.kosten:.2f} €".replace(".", ",") if sim.kosten else ""
                    await ha.notify(
                        "Wochenplan fertig",
                        f"{len(sim.tage)} Tage geplant, {len(sim.einkauf)} Einkaufspositionen"
                        f"{kosten}. " + ("Übernommen." if par.get("uebernehmen") else "Tab Woche."),
                        st.notify_service,
                    )
                except Exception as err:  # noqa: BLE001
                    log.warning("Hinweis fehlgeschlagen: %s", err)
        except Exception as err:  # noqa: BLE001 - Fehler am Lauf festhalten
            log.exception("Plan %s fehlgeschlagen", lauf_id)
            s.rollback()
            lauf = s.get(PlanLauf, lauf_id)
            lauf.status, lauf.fehler = "fehler", str(err) or type(err).__name__
            s.commit()


def uebernehmen_(s: Session, lauf: PlanLauf) -> int:
    """Schreibt die vorgeschlagenen Tage in den Wochenplan. Feste Tage bleiben, wie sie sind."""
    n = 0
    for t in (lauf.ergebnis or {}).get("tage", []):
        if t["quelle"] in ("fest", "gekocht"):
            continue
        d = date.fromisoformat(t["datum"])
        eintrag = s.get(PlanEintrag, d) or PlanEintrag(datum=d)
        if eintrag.status == "gekocht":
            continue
        eintrag.rezept_id, eintrag.portionen, eintrag.status = (
            t["rezept_id"],
            t["portionen"],
            "geplant",
        )
        s.add(eintrag)
        n += 1
    lauf.status = "uebernommen"
    return n


# --- Endpunkte ---------------------------------------------------------------------------


class PlanErzeugenIn(BaseModel):
    start: date | None = None  # Standard: heute
    tage: int = Field(default=7, ge=1, le=31)
    daten: list[date] | None = Field(default=None, max_length=31)  # Kochtage direkt (Kalender)
    kochtage: list[int] | None = None  # Wochentage 0–6, Standard aus den Einstellungen
    portionen: int | None = Field(default=None, ge=1, le=12)  # Standard aus den Einstellungen
    personen: dict[date, int] | None = None  # Personen je Tag, überschreibt portionen
    ki: bool | None = None
    praeferenzen: dict | None = None  # überschreibt die gespeicherten für diesen Lauf
    max_ungetestet: int | None = Field(default=None, ge=0, le=7)
    ueberschreiben: bool = False  # True: auch geplante (nicht gekochte) Tage neu planen
    uebernehmen: bool = False  # True: direkt in den Wochenplan schreiben (Automation)


def parameter(data: PlanErzeugenIn, s: Session) -> dict:
    """Füllt fehlende Werte aus den Einstellungen; der Lauf speichert das vollständige Ergebnis."""
    einst = einstellungen.lesen(s)
    planer_ = einst["planer"]
    if data.daten:
        daten = sorted(set(data.daten))
    else:
        start = data.start or date.today()
        wochentage = set(planer_["kochtage"] if data.kochtage is None else data.kochtage)
        daten = [
            d for i in range(data.tage) if (d := start + timedelta(days=i)).weekday() in wochentage
        ]
    if not daten:
        raise HTTPException(422, "Keine Kochtage im Zeitraum")
    prefs = einst["praeferenzen"]
    if data.praeferenzen:
        prefs = {**prefs, **einstellungen.pruefen("praeferenzen", data.praeferenzen)}
    standard = data.portionen or planer_["portionen"]
    je_wt = planer_.get("personen_je_wochentag") or {}
    personen = {}
    for d in daten:
        n = (data.personen or {}).get(d) or je_wt.get(str(d.weekday())) or standard
        if not 1 <= int(n) <= 12:
            raise HTTPException(422, f"{d}: Personen 1–12")
        personen[d.isoformat()] = int(n)
    return {
        "daten": [d.isoformat() for d in daten],
        "portionen": standard,
        "personen": personen,
        "ki": planer_["ki"] if data.ki is None else data.ki,
        "praeferenzen": prefs,
        "max_ungetestet": (
            planer_["max_ungetestet"] if data.max_ungetestet is None else data.max_ungetestet
        ),
        "ueberschreiben": data.ueberschreiben,
        "uebernehmen": data.uebernehmen,
    }


@router.post("/plan/erzeugen", status_code=202)
async def plan_erzeugen(
    data: PlanErzeugenIn,
    request: Request,
    hintergrund: BackgroundTasks,
    s: Session = Depends(session),
) -> dict:
    """Startet den Planer im Hintergrund. Ergebnis unter GET /plan/laeufe/{id}."""
    if s.scalars(select(PlanLauf).where(PlanLauf.status == "laeuft")).first():
        raise HTTPException(409, "Es läuft schon ein Planer")
    par = parameter(data, s)
    lauf = PlanLauf(start=date.fromisoformat(par["daten"][0]), parameter=par)
    s.add(lauf)
    s.commit()
    hintergrund.add_task(planen, request.app, lauf.id)
    return {"lauf_id": lauf.id, "status": lauf.status, "daten": par["daten"]}


@router.get("/plan/vorschau-kandidaten")
async def plan_kandidaten(
    datum: date | None = None,
    portionen: int | None = None,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> list[dict]:
    """Bewertete Rezepte für einen Tag mit den gespeicherten Präferenzen (ohne KI)."""
    tag = datum or date.today()
    k = await kontext_laden(b, s, st, tag)
    lager = Lager(k.produkte, k.batches, k.ersatz)
    por = portionen or einstellungen.lesen(s)["planer"]["portionen"]
    return [asdict(c) for c in kandidaten(k, lager, tag, por)]


@router.get("/plan/laeufe")
async def plan_laeufe(s: Session = Depends(session)) -> list[dict]:
    rows = s.scalars(select(PlanLauf).order_by(PlanLauf.id.desc()).limit(10))
    return [
        {
            "id": r.id,
            "status": r.status,
            "start": r.start,
            "fehler": r.fehler,
            "angelegt": r.angelegt,
            "tage": len((r.ergebnis or {}).get("tage", [])),
        }
        for r in rows
    ]


@router.get("/plan/laeufe/{lauf_id}")
async def plan_lauf(lauf_id: int, s: Session = Depends(session)) -> dict:
    lauf = s.get(PlanLauf, lauf_id)
    if lauf is None:
        raise HTTPException(404, "Planer-Lauf nicht gefunden")
    return {
        "id": lauf.id,
        "status": lauf.status,
        "start": lauf.start,
        "parameter": lauf.parameter,
        "fehler": lauf.fehler,
        **(lauf.ergebnis or {}),
    }


@router.post("/plan/laeufe/{lauf_id}/uebernehmen")
async def plan_uebernehmen(lauf_id: int, s: Session = Depends(session)) -> dict:
    lauf = s.get(PlanLauf, lauf_id)
    if lauf is None:
        raise HTTPException(404, "Planer-Lauf nicht gefunden")
    if lauf.status != "fertig":
        raise HTTPException(409, f"Lauf ist {lauf.status}")
    n = uebernehmen_(s, lauf)
    s.commit()
    return {"uebernommen": n}


@router.get("/plan/simulation")
async def plan_simulation(
    tage: int = 7,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> dict:
    """Simuliert den aktuellen Wochenplan: Einkauf mit Toleranz, Verderb, Überhang."""
    start = date.today()
    daten = [start + timedelta(days=i) for i in range(max(min(tage, 14), 1))]
    k = await kontext_laden(b, s, st, start)
    fest = feste_tage(s, k, daten, ueberschreiben=False)
    return asdict(simulieren(k, list(fest.values()), start, daten[-1]))


class TauschenIn(BaseModel):
    portionen: int | None = Field(default=None, ge=1, le=6)


@router.post("/plan/{datum}/tauschen")
async def plan_tauschen(
    datum: date,
    data: TauschenIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
    st: Settings = Depends(settings),
) -> dict:
    """Ersetzt einen Tag durch den nächstbesten Kandidaten, ohne LLM-Aufruf."""
    eintrag = s.get(PlanEintrag, datum)
    if eintrag and eintrag.status == "gekocht":
        raise HTTPException(409, "Tag ist schon gekocht")
    start = min(date.today(), datum)
    woche = [datum - timedelta(days=3) + timedelta(days=i) for i in range(7)]
    k = await kontext_laden(b, s, st, start)
    fest = {d: t for d, t in feste_tage(s, k, woche, False).items() if d != datum}
    portionen = data.portionen or (eintrag.portionen if eintrag else 2)
    sim = Simulator(k, start, woche[-1])
    for t in sorted(fest.values(), key=lambda t: t.datum):
        if t.datum < datum:
            sim.kochen(t)
    ausschluss = {t.rezept_id for t in fest.values()} | ({eintrag.rezept_id} if eintrag else set())
    wahl = next(
        (c for c in kandidaten(k, sim.lager, datum, portionen) if c.rezept_id not in ausschluss),
        None,
    )
    if wahl is None:
        raise HTTPException(404, "Kein anderes Rezept verfügbar")
    eintrag = eintrag or PlanEintrag(datum=datum)
    eintrag.rezept_id, eintrag.portionen, eintrag.status = wahl.rezept_id, portionen, "geplant"
    s.add(eintrag)
    s.commit()
    return {
        "datum": datum,
        "rezept_id": wahl.rezept_id,
        "name": wahl.name,
        "portionen": portionen,
        "begruendung": _begruendung(wahl),
    }
