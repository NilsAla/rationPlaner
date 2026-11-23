"""Nährwerte, Makros und Gesundheits-Score je Rezept.

Nährwerte je 100 g liegen in der App-DB (Konzept: nicht in Grocy). Quellen: von Hand oder als
KI-Schätzung über den Kategorisierungslauf (immer mit Review, Regel 4).

"LLM wählt aus, Code rechnet": Rezeptwerte rechnet nur der Code, Σ(Gramm × Wert/100 g).

Gesundheits-Score: Nutri-Score-Algorithmus in der Fassung 2023 für allgemeine Lebensmittel,
angewandt auf das ganze Rezept je 100 g (Summe der Rohgewichte). Für ein selbst gekochtes Gericht
ist das eine Annäherung (Nutri-Score ist für verpackte Lebensmittel gedacht), aber ein etablierter,
dokumentierter Algorithmus statt eines eigenen Faktors. Obst/Gemüse/Hülsenfrüchte-Anteil über das
Produkt-Flag `obst_gemuese`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import einstellungen
from app.db import Base
from app.deps import backend, session
from app.inventory.base import InventoryBackend, Product, Recipe

router = APIRouter(prefix="/api/v1")

# je 100 g; Reihenfolge = Anzeige
FELDER = (
    "kcal",
    "protein",
    "fett",
    "fett_gesaettigt",
    "kohlenhydrate",
    "zucker",
    "ballaststoffe",
    "salz",
)
GRENZEN = {"kcal": 900.0, "salz": 100.0}  # sonst 0 … 100 g je 100 g
KJ_JE_KCAL = 4.184


class Naehrwert(Base):
    __tablename__ = "naehrwert"

    produkt_id: Mapped[str] = mapped_column(String, primary_key=True)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein: Mapped[float | None] = mapped_column(Float, nullable=True)
    fett: Mapped[float | None] = mapped_column(Float, nullable=True)
    fett_gesaettigt: Mapped[float | None] = mapped_column(Float, nullable=True)
    kohlenhydrate: Mapped[float | None] = mapped_column(Float, nullable=True)
    zucker: Mapped[float | None] = mapped_column(Float, nullable=True)
    ballaststoffe: Mapped[float | None] = mapped_column(Float, nullable=True)
    salz: Mapped[float | None] = mapped_column(Float, nullable=True)
    obst_gemuese: Mapped[bool] = mapped_column(Boolean, default=False)  # Nutri-Score-Anteil
    roh: Mapped[bool] = mapped_column(Boolean, default=False)  # roh essbar (Rohkost)
    quelle: Mapped[str] = mapped_column(String, default="manuell")  # manuell / ki
    geaendert: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


def als_dict(n: Naehrwert | None) -> dict | None:
    if n is None:
        return None
    return {
        **{f: getattr(n, f) for f in FELDER},
        "obst_gemuese": n.obst_gemuese,
        "roh": n.roh,
        "quelle": n.quelle,
    }


def werte_pruefen(roh: dict) -> dict:
    """Validiert Nährwerte je 100 g. Unplausibles fällt weg (None), nichts wird geraten."""
    out: dict[str, object] = {}
    for f in FELDER:
        v = roh.get(f)
        if v in (None, ""):
            out[f] = None
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            out[f] = None
            continue
        out[f] = round(x, 2) if 0 <= x <= GRENZEN.get(f, 100.0) else None
    # Plausibilität: Teilmengen nicht größer als das Ganze, Makros nicht über 100 g
    if (
        out.get("fett_gesaettigt") is not None
        and out.get("fett") is not None
        and out["fett_gesaettigt"] > out["fett"] + 0.5  # type: ignore[operator]
    ):
        out["fett_gesaettigt"] = None
    if (
        out.get("zucker") is not None
        and out.get("kohlenhydrate") is not None
        and out["zucker"] > out["kohlenhydrate"] + 0.5  # type: ignore[operator]
    ):
        out["zucker"] = None
    makros = [out.get(f) for f in ("protein", "fett", "kohlenhydrate", "ballaststoffe")]
    if sum(x for x in makros if isinstance(x, float)) > 105:
        for f in ("protein", "fett", "kohlenhydrate", "ballaststoffe"):
            out[f] = None
    out["obst_gemuese"] = bool(roh.get("obst_gemuese"))
    out["roh"] = bool(roh.get("roh"))
    return out


# --- Nutri-Score 2023 (allgemeine Lebensmittel) ---------------------------------------------


def _punkte(wert: float, grenzen: tuple[float, ...]) -> int:
    """Anzahl der Grenzen, die überschritten sind (Wert > Grenze)."""
    return sum(1 for g in grenzen if wert > g)


ENERGIE_KJ = tuple(335.0 * i for i in range(1, 11))  # 0–10
ZUCKER = (3.4, 6.8, 10, 14, 17, 20, 24, 27, 31, 34, 37, 41, 44, 48, 51)  # 0–15
GES_FETT = tuple(float(i) for i in range(1, 11))  # 0–10
SALZ = tuple(round(0.2 * i, 1) for i in range(1, 21))  # 0–20
PROTEIN = (2.4, 4.8, 7.2, 9.6, 12, 14, 17)  # 0–7
BALLASTSTOFFE = (3.0, 4.1, 5.2, 6.3, 7.4)  # 0–5


def _ogh_punkte(anteil_pct: float) -> int:
    return 5 if anteil_pct > 80 else 2 if anteil_pct > 60 else 1 if anteil_pct > 40 else 0


def nutri_score(je100: dict[str, float], ogh_pct: float) -> dict:
    """Nutri-Score 2023 für allgemeine Lebensmittel aus Werten je 100 g.

    N = Energie + Zucker + gesättigte Fette + Salz; P = Ballaststoffe + Obst/Gemüse/Hülsenfrüchte
    (+ Protein, außer bei N ≥ 11 ohne volle Obst/Gemüse-Punkte). Score = N − P.
    Klassen: A ≤ 0, B 1–2, C 3–10, D 11–18, E ≥ 19."""
    n = (
        _punkte(je100["kcal"] * KJ_JE_KCAL, ENERGIE_KJ)
        + _punkte(je100["zucker"], ZUCKER)
        + _punkte(je100["fett_gesaettigt"], GES_FETT)
        + _punkte(je100["salz"], SALZ)
    )
    ogh = _ogh_punkte(ogh_pct)
    prot = _punkte(je100["protein"], PROTEIN)
    p = _punkte(je100["ballaststoffe"], BALLASTSTOFFE) + ogh
    if n < 11 or ogh == 5:
        p += prot
    score = n - p
    klasse = (
        "A"
        if score <= 0
        else "B"
        if score <= 2
        else "C"
        if score <= 10
        else ("D" if score <= 18 else "E")
    )
    return {"punkte": score, "klasse": klasse, "negativ": n, "positiv": p}


def gesund_faktor(punkte: int) -> float:
    """0 … 1 für den Planer: −5 Punkte oder besser = 1, 25 oder schlechter = 0, linear."""
    return round(max(0.0, min(1.0, (25 - punkte) / 30)), 3)


# --- Rezept --------------------------------------------------------------------------------


@dataclass
class RezeptNaehrwerte:
    portionen: int
    je_portion: dict[str, float | None]
    je_100g: dict[str, float | None]
    gewicht_g: float  # Rohgewicht gesamt (bekannte Gramm)
    abdeckung: float  # Anteil des Gewichts mit Nährwerten (0 … 1)
    fehlend: list[dict] = field(default_factory=list)  # Zutaten ohne Nährwerte oder Gramm
    obst_gemuese_pct: float = 0.0
    rohkost_pct: float = 0.0
    protein_kcal_pct: float | None = None  # Energieanteil aus Protein
    nutri_score: dict | None = None
    gesund: float | None = None
    ziel: dict | None = None


def gramm(p: Product, menge: float) -> float | None:
    unit = p.unit.strip().lower()
    if unit in ("g", "ml", "gramm"):
        return menge
    if unit in ("kg", "l"):
        return menge * 1000
    return menge * p.grams_per_unit if p.grams_per_unit else None


def rezept_naehrwerte(
    r: Recipe,
    produkte: dict[str, Product],
    nw: dict[str, Naehrwert],
    portionen: int | None = None,
    ziel: dict | None = None,
) -> RezeptNaehrwerte:
    """Σ(Gramm × Wert/100 g); je Portion bezogen auf die Grundportionen des Rezepts."""
    summe = dict.fromkeys(FELDER, 0.0)
    bekannt = dict.fromkeys(FELDER, 0.0)  # Gewicht, für das der Wert bekannt ist
    gewicht = abgedeckt = ogh = roh = 0.0
    fehlend = []
    for z in r.ingredients:
        p = produkte.get(z.product_id)
        if p is None:
            continue
        g = gramm(p, z.amount)
        n = nw.get(p.id)
        if g is None:
            fehlend.append({"produkt_id": p.id, "name": p.name, "grund": "Gramm je Einheit fehlt"})
            continue
        gewicht += g
        if n is None or n.kcal is None:
            fehlend.append({"produkt_id": p.id, "name": p.name, "grund": "keine Nährwerte"})
            continue
        abgedeckt += g
        ogh += g if n.obst_gemuese else 0.0
        roh += g if n.roh else 0.0
        for f in FELDER:
            if (v := getattr(n, f)) is not None:
                summe[f] += g * v / 100
                bekannt[f] += g
    basis = max(r.servings, 1)
    ziel_portionen = portionen or basis
    je_portion = {f: (round(summe[f] / basis, 1) if bekannt[f] else None) for f in FELDER}
    je_100g = {f: (round(summe[f] / bekannt[f] * 100, 2) if bekannt[f] else None) for f in FELDER}
    erg = RezeptNaehrwerte(
        portionen=ziel_portionen,
        je_portion=je_portion,
        je_100g=je_100g,
        gewicht_g=round(gewicht, 1),
        abdeckung=round(abgedeckt / gewicht, 3) if gewicht else 0.0,
        fehlend=fehlend,
        obst_gemuese_pct=round(100 * ogh / abgedeckt, 1) if abgedeckt else 0.0,
        rohkost_pct=round(100 * roh / abgedeckt, 1) if abgedeckt else 0.0,
    )
    if je_portion["kcal"] and je_portion["protein"] is not None:
        erg.protein_kcal_pct = round(100 * je_portion["protein"] * 4 / je_portion["kcal"], 1)
    pflicht = ("kcal", "zucker", "fett_gesaettigt", "salz", "protein", "ballaststoffe")
    if erg.abdeckung >= 0.8 and all(je_100g[f] is not None for f in pflicht):
        erg.nutri_score = nutri_score({f: je_100g[f] for f in pflicht}, erg.obst_gemuese_pct)  # type: ignore[misc]
        erg.gesund = gesund_faktor(erg.nutri_score["punkte"])
    if ziel and je_portion["kcal"]:
        erg.ziel = ziel_fit(je_portion, ziel)
    return erg


def ziel_fit(je_portion: dict[str, float | None], ziel: dict) -> dict:
    """Wie gut eine Portion zum Mahlzeiten-Ziel passt: 1 = genau, 0 = ≥ 50 % daneben."""
    kcal_ziel = float(ziel.get("kcal_mahlzeit") or 0)
    prot_ziel = float(ziel.get("protein_mahlzeit") or 0)
    kcal = je_portion.get("kcal") or 0.0
    prot = je_portion.get("protein") or 0.0
    fit_kcal = max(0.0, 1 - abs(kcal - kcal_ziel) / (0.5 * kcal_ziel)) if kcal_ziel else None
    # Protein: Unterschreiten zählt, Überschreiten nicht
    fit_prot = min(1.0, prot / prot_ziel) if prot_ziel else None
    teile = [x for x in (fit_kcal, fit_prot) if x is not None]
    return {
        "kcal_ziel": kcal_ziel or None,
        "protein_ziel": prot_ziel or None,
        "kcal_diff": round(kcal - kcal_ziel) if kcal_ziel else None,
        "fit_kcal": round(fit_kcal, 3) if fit_kcal is not None else None,
        "fit_protein": round(fit_prot, 3) if fit_prot is not None else None,
        "fit": round(sum(teile) / len(teile), 3) if teile else None,
    }


def alle(s: Session) -> dict[str, Naehrwert]:
    return {n.produkt_id: n for n in s.scalars(select(Naehrwert))}


def ziel_aus(s: Session) -> dict:
    return einstellungen.lesen(s)["ernaehrung"]


def rezept_dict(
    r: Recipe,
    produkte: dict[str, Product],
    s: Session,
    portionen: int | None = None,
    nw: dict[str, Naehrwert] | None = None,
) -> dict:
    return asdict(rezept_naehrwerte(r, produkte, nw or alle(s), portionen, ziel_aus(s)))


def speichern(s: Session, produkt_id: str, werte: dict, quelle: str) -> Naehrwert:
    n = s.get(Naehrwert, produkt_id) or Naehrwert(produkt_id=produkt_id)
    for f in FELDER:
        setattr(n, f, werte.get(f))
    n.obst_gemuese, n.roh, n.quelle = (
        bool(werte.get("obst_gemuese")),
        bool(werte.get("roh")),
        quelle,
    )
    s.add(n)
    return n


# --- API -----------------------------------------------------------------------------------


class NaehrwertIn(BaseModel):
    kcal: float | None = Field(default=None, ge=0, le=900)
    protein: float | None = Field(default=None, ge=0, le=100)
    fett: float | None = Field(default=None, ge=0, le=100)
    fett_gesaettigt: float | None = Field(default=None, ge=0, le=100)
    kohlenhydrate: float | None = Field(default=None, ge=0, le=100)
    zucker: float | None = Field(default=None, ge=0, le=100)
    ballaststoffe: float | None = Field(default=None, ge=0, le=100)
    salz: float | None = Field(default=None, ge=0, le=100)
    obst_gemuese: bool = False
    roh: bool = False


@router.get("/naehrwerte")
async def naehrwerte_liste(
    s: Session = Depends(session), b: InventoryBackend = Depends(backend)
) -> list[dict]:
    """Alle Produkte mit ihren Nährwerten je 100 g (oder null)."""
    nw = alle(s)
    return [
        {
            "produkt_id": p.id,
            "name": p.name,
            "einheit": p.unit,
            "naehrwerte": als_dict(nw.get(p.id)),
        }
        for p in sorted(await b.list_products(), key=lambda p: p.name.lower())
    ]


@router.get("/produkte/{produkt_id}/naehrwerte")
async def produkt_naehrwerte(produkt_id: str, s: Session = Depends(session)) -> dict | None:
    return als_dict(s.get(Naehrwert, produkt_id))


@router.put("/produkte/{produkt_id}/naehrwerte")
async def produkt_naehrwerte_setzen(
    produkt_id: str,
    data: NaehrwertIn,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict | None:
    await b.get_product(produkt_id)  # 404, wenn es das Produkt nicht gibt
    werte = werte_pruefen(data.model_dump())
    if werte["kcal"] is None:
        raise HTTPException(422, "kcal je 100 g fehlt")
    speichern(s, produkt_id, werte, "manuell")
    s.commit()
    return als_dict(s.get(Naehrwert, produkt_id))


@router.delete("/produkte/{produkt_id}/naehrwerte")
async def produkt_naehrwerte_loeschen(produkt_id: str, s: Session = Depends(session)) -> dict:
    if n := s.get(Naehrwert, produkt_id):
        s.delete(n)
        s.commit()
    return {"geloescht": produkt_id}


@router.get("/rezepte/{rezept_id}/naehrwerte")
async def rezept_naehrwerte_api(
    rezept_id: str,
    portionen: int | None = None,
    s: Session = Depends(session),
    b: InventoryBackend = Depends(backend),
) -> dict:
    r = await b.get_recipe(rezept_id)
    produkte = {p.id: p for p in await b.list_products()}
    return rezept_dict(r, produkte, s, portionen)
