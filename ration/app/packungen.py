"""Packungsgrößen-Varianten: Käufe laut Bon zählen, neue Größen als Vorschlag, Pflege der Varianten.

Die Standard-Packung liegt in Grocy. Weitere Größen (Packung) liegen in der App-DB, mit Marke,
Händler, EAN, zuletzt gesehen und Häufigkeit. Aus einem Bon erkannte neue Größen gelten erst
nach Review (bestaetigt), bis dahin nutzt die Einkaufsliste sie nicht.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import kueche
from app.db import Packung, PackungKauf
from app.deps import session
from app.inventory.base import Product

router = APIRouter(prefix="/api/v1")


def gilt(p: Packung) -> bool:
    return p.bestaetigt is not False


def weitere(
    s: Session, stichtag: date | None = None, alle: bool = False
) -> dict[str, list[kueche.Groesse]]:
    """Weitere Größen je Produkt, abgelaufene Angebote ausgenommen. alle=True: auch unbestätigte
    (für den Abgleich beim Bon, damit eine schon vorgeschlagene Größe nicht als neu gilt)."""
    stichtag = stichtag or date.today()
    result: dict[str, list[kueche.Groesse]] = {}
    for p in s.scalars(select(Packung)):
        if p.gueltig_bis is not None and p.gueltig_bis < stichtag:
            continue
        if not alle and not gilt(p):
            continue
        result.setdefault(p.produkt_id, []).append(
            kueche.Groesse(p.einheit, p.menge, p.quelle, p.haeufigkeit or 0)
        )
    return result


def kauf_merken(
    s: Session,
    produkt: Product,
    je: float,
    anzahl: float,
    datum: date,
    markt: str | None,
) -> Packung | None:
    """Kauf einer Packungsgröße festhalten. Bekannte Größe: Häufigkeit hoch. Neue Größe:
    unbestätigte Variante plus Vorschlag zur Prüfung. Gibt die neue Variante zurück."""
    from app.kategorisierung import Vorschlag  # zyklischer Import vermeiden

    if je <= kueche.EPS:
        return None
    varianten = list(s.scalars(select(Packung).where(Packung.produkt_id == produkt.id)))
    treffer = next((p for p in varianten if abs(p.menge - je) < kueche.EPS), None)
    neu = None
    if treffer is None:
        ist_standard = produkt.pack_size and abs(produkt.pack_size - je) < kueche.EPS
        treffer = Packung(
            produkt_id=produkt.id,
            einheit=(produkt.pack_unit or "Packung") if ist_standard else "Packung",
            menge=je,
            quelle="kauf" if ist_standard else "bon",
            bestaetigt=bool(ist_standard),
            haendler=markt,
            haeufigkeit=0,
        )
        s.add(treffer)
        s.flush()
        if not ist_standard:
            neu = treffer
            s.add(
                Vorschlag(
                    lauf_id=0,
                    typ="produkt",
                    objekt_id=produkt.id,
                    name=produkt.name,
                    feld="variante",
                    alt=None,
                    neu={
                        "packung_id": treffer.id,
                        "einheit": "Packung",
                        "menge": je,
                        "haendler": markt,
                        "quelle": "bon",
                    },  # fmt: skip
                )
            )
    treffer.haeufigkeit = (treffer.haeufigkeit or 0) + max(1, int(round(anzahl)))
    if treffer.zuletzt is None or datum > treffer.zuletzt:
        treffer.zuletzt = datum
    if markt and not treffer.haendler:
        treffer.haendler = markt
    s.add(
        PackungKauf(
            produkt_id=produkt.id, menge=je, anzahl=anzahl, datum=datum, markt=markt,
            packung_id=treffer.id,
        )
    )  # fmt: skip
    return neu


def kaeufe_je_groesse(s: Session, seit: date) -> dict[str, dict[float, float]]:
    """Produkt → {Größe je Packung: gekaufte Packungen} seit Stichtag."""
    out: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
    for k in s.scalars(select(PackungKauf).where(PackungKauf.datum >= seit)):
        out[k.produkt_id][round(k.menge, 3)] += k.anzahl
    return out


def haeufigste(kaeufe: dict[float, float]) -> float | None:
    """Häufigste Größe; Gleichstand: die größere (kauft man seltener nach)."""
    if not kaeufe:
        return None
    return max(kaeufe.items(), key=lambda x: (x[1], x[0]))[0]


def seit_monaten(monate: int) -> date:
    return date.today() - timedelta(days=round(monate * 30.4))


# --- Endpunkte ------------------------------------------------------------------------------


class VarianteIn(BaseModel):
    einheit: str | None = Field(default=None, max_length=40)
    marke: str | None = Field(default=None, max_length=60)
    haendler: str | None = Field(default=None, max_length=60)
    ean: str | None = Field(default=None, pattern=r"^(\d{8}|\d{12,14})?$")
    bestaetigt: bool | None = None


@router.patch("/packungen/{packung_id}")
async def variante_aendern(
    packung_id: int, data: VarianteIn, s: Session = Depends(session)
) -> dict:
    p = s.get(Packung, packung_id)
    if p is None:
        raise HTTPException(404, "Packung nicht gefunden")
    for feld, wert in data.model_dump(exclude_unset=True).items():
        if feld == "einheit" and not (wert or "").strip():
            continue
        setattr(p, feld, (wert.strip() or None) if isinstance(wert, str) else wert)
    s.commit()
    return als_dict(p)


def als_dict(p: Packung) -> dict:
    return {
        "id": p.id,
        "einheit": p.einheit,
        "menge": p.menge,
        "quelle": p.quelle,
        "gueltig_bis": p.gueltig_bis,
        "marke": p.marke,
        "haendler": p.haendler,
        "ean": p.ean,
        "zuletzt": p.zuletzt,
        "haeufigkeit": p.haeufigkeit or 0,
        "bestaetigt": gilt(p),
    }
