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
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Date, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import kueche
from app.db import Base, Packung, PlanEintrag, Preis
from app.deps import backend, session, settings
from app.inventory.base import Batch, InventoryBackend, Product, Recipe, TrackingMode, consume_order
from app.kueche import EPS, Groesse, PackAnzahl, wird_abgebucht
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

    def __init__(self, produkte: dict[str, Product], batches: list[Batch]):
        self.produkte = produkte
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
        return sorted(
            (
                c
                for c in self.chargen
                if c.produkt_id in familie
                and c.menge > EPS
                and (c.ablauf is None or c.ablauf >= tag)
            ),
            key=lambda c: c.ablauf or date.max,
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

    def toleranz(self, p: Product) -> float:
        return (p.tolerance_pct if p.tolerance_pct is not None else self.toleranz_default) / 100


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
    score = (
        GEWICHT["aufbrauch"] * aufbrauch
        + GEWICHT["deckung"] * deckung
        + GEWICHT["fit"] * fit
        + GEWICHT["abwechslung"] * abwechslung
        + GEWICHT["angebot"] * min(angebote, 3)
        - (0.3 if rezept.status == "ungetestet" else 0.0)
    )
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
    )


def kandidaten(k: Kontext, lager: Lager, tag: date, portionen: int) -> list[Kandidat]:
    alle = [bewerten(r, portionen, lager, k, tag) for r in k.rezepte.values() if r.ingredients]
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
        self.lager = Lager(k.produkte, k.batches)
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
                    for c in kandidaten(k, sim.lager, d, portionen)
                    if c.rezept_id not in benutzt
                    and (c.status != "ungetestet" or ungetestet < MAX_UNGETESTET)
                ),
                None,
            )
            if wahl is None:
                continue
            benutzt.add(wahl.rezept_id)
            ungetestet += wahl.status == "ungetestet"
            t = PlanTag(d, wahl.rezept_id, portionen, "code", _begruendung(wahl))
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
tags | aufwand), höherer Score ist besser:
{kandidaten}

Regeln:
- Für jeden zu planenden Tag genau ein Eintrag mit datum und rezept_id aus den Kandidaten.
- Kein Rezept zweimal, höchstens {max_ungetestet} mit status ungetestet.
- Rezepte, die bald Ablaufendes verbrauchen, früh in den Zeitraum legen.
- Aufwändige Rezepte eher am Wochenende.
- portionen: {portionen} (1 bis 6; mehr nur, wenn sonst etwas verdirbt).
- begruendung: ein kurzer Satz."""


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


def _tag_text(d: date) -> str:
    return f"{d.isoformat()} ({WOCHENTAG[d.weekday()]})"


def anweisung(
    k: Kontext,
    daten: list[date],
    fest: dict[date, PlanTag],
    liste: list[Kandidat],
    lager: Lager,
    start: date,
    portionen: int,
) -> str:
    bald = []
    for c in sorted(lager.chargen, key=lambda c: c.ablauf or date.max):
        p = k.produkte[c.produkt_id]
        if c.ablauf and start <= c.ablauf <= start + timedelta(days=BALD_TAGE) and c.menge > EPS:
            bald.append(f"{p.name} {kueche.menge(c.menge)} {p.unit} bis {c.ablauf.isoformat()}")
    zeilen = [
        f"{c.rezept_id} | {c.name} | {c.score} | {round(c.deckung * 100)} | "
        f"{', '.join(c.nutzt_bald) or '-'} | {c.zuletzt_vor_tagen if c.zuletzt_vor_tagen is not None else '-'} | "  # noqa: E501
        f"{c.status or '-'} | {', '.join(c.tags) or '-'} | {c.aufwand or '-'}"
        for c in liste
    ]
    return ANWEISUNG.format(
        tage=", ".join(_tag_text(d) for d in daten),
        fest="; ".join(f"{_tag_text(d)}: {k.rezepte[t.rezept_id].name}" for d, t in fest.items())
        or "nichts",
        bald="; ".join(bald) or "nichts",
        kandidaten="\n".join(zeilen),
        max_ungetestet=MAX_UNGETESTET,
        portionen=portionen,
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
        elif k.rezepte[rid].status == "ungetestet" and ungetestet >= MAX_UNGETESTET:
            probleme.append(f"{d.isoformat()}: zu viele ungetestete Rezepte")
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


async def kontext_laden(b: InventoryBackend, s: Session, st: Settings, start: date) -> Kontext:
    produkte = {p.id: p for p in await b.list_products()}
    rezepte = {r.id: r for r in await b.list_recipes()}
    weitere: dict[str, list[Groesse]] = defaultdict(list)
    angebote: set[str] = set()
    for pk in s.scalars(select(Packung)):
        if pk.gueltig_bis and pk.gueltig_bis < start:
            continue
        weitere[pk.produkt_id].append(Groesse(pk.einheit, pk.menge, pk.quelle))
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
    return Kontext(
        produkte=produkte,
        rezepte=rezepte,
        batches=await b.list_all_batches(),
        weitere=dict(weitere),
        toleranz_default=st.toleranz_default_pct,
        historie=historie,
        angebote=angebote,
        preise={pid: v[2] for pid, v in preise.items()},
    )


def feste_tage(
    s: Session, k: Kontext, daten: list[date], ueberschreiben: bool
) -> dict[date, PlanTag]:
    if not daten:
        return {}
    fest = {}
    for e in s.scalars(
        select(PlanEintrag).where(PlanEintrag.datum >= daten[0], PlanEintrag.datum <= daten[-1])
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
    ha = app.state.ha
    b: InventoryBackend = app.state.backend
    with Session(app.state.engine) as s:
        lauf = s.get(PlanLauf, lauf_id)
        if lauf is None:
            return
        par = lauf.parameter
        try:
            start = lauf.start
            alle = [start + timedelta(days=i) for i in range(par["tage"])]
            ende = alle[-1]
            k = await kontext_laden(b, s, st, start)
            if not any(r.ingredients for r in k.rezepte.values()):
                raise ValueError("Keine Rezepte mit Zutaten in Grocy")
            fest = feste_tage(s, k, alle, par.get("ueberschreiben", False))
            frei = [d for d in alle if d not in fest and d >= start]
            offen_fest = {d: t for d, t in fest.items() if d >= start}
            lager = Lager(k.produkte, k.batches)
            liste = kandidaten(k, lager, start, par["portionen"])[:MAX_KANDIDATEN]
            liste = [c for c in liste if c.rezept_id not in {t.rezept_id for t in fest.values()}]
            ki_info: dict = {"aktiv": False, "runden": 0, "probleme": [], "fehler": None}
            vorgabe: dict[date, PlanTag] = {}
            if frei and par.get("ki", True) and st.ai_task_entity and ha is not None and liste:
                ki_info["aktiv"] = True
                erlaubt = {c.rezept_id for c in liste}
                text = anweisung(k, frei, fest, liste, lager, start, par["portionen"])
                struktur_ = struktur(frei, sorted(erlaubt))
                probleme: list[str] = []
                try:
                    for runde in range(MAX_RUNDEN + 1):
                        if probleme:
                            text += "\n\nDein letzter Plan hatte Probleme:\n- " + "\n- ".join(
                                probleme
                            )
                            text += "\nGib den vollständigen Plan für alle Tage neu aus."
                        antwort = await ha.ai_task(
                            st.ai_task_entity, "rationPlaner Wochenplan", text, struktur_
                        )
                        ki_info["runden"] = runde + 1
                        log.info(
                            "Plan %s Runde %s Rohantwort: %s",
                            lauf_id,
                            runde + 1,
                            json.dumps(antwort, ensure_ascii=False),
                        )
                        vorgabe, probleme = pruefen(antwort, k, frei, fest, erlaubt)
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
            plan = gierig(k, alle, offen_fest, par["portionen"], start, ende, vorgabe)
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
    tage: int = Field(default=7, ge=1, le=14)
    portionen: int = Field(default=2, ge=1, le=6)  # Annahme: 1 essen, 1 Rest für mittags
    ki: bool = True
    ueberschreiben: bool = False  # True: auch geplante (nicht gekochte) Tage neu planen
    uebernehmen: bool = False  # True: direkt in den Wochenplan schreiben (Automation)


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
    lauf = PlanLauf(start=data.start or date.today(), parameter=data.model_dump(mode="json"))
    s.add(lauf)
    s.commit()
    hintergrund.add_task(planen, request.app, lauf.id)
    return {"lauf_id": lauf.id, "status": lauf.status}


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
