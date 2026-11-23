"""MHD beim Einbuchen, wenn keins angegeben ist und das Produkt keine Standard-Haltbarkeit hat.

Grocy setzt sonst „heute“ (Standard-Haltbarkeit 0 Tage), Nudeln liefen also sofort ab. Der Code
schätzt die Tage aus Kategorie oder Namen (deutsche Supermarktware, ungeöffnet) und legt die
Haltbarkeit fürs Produkt als Vorschlag ab (Review), damit es beim nächsten Mal Grocy weiß.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.base import Product

# Katalog-Kategorie → typische Tage ungeöffnet
JE_KATEGORIE = {
    "Nudeln & Reis": 540, "Hülsenfrüchte": 540, "Konserven": 730, "Mehl & Backen": 270,
    "Öl & Essig": 365, "Gewürze": 540, "Saucen": 270, "Nüsse & Saaten": 180, "Süßes": 180,
    "Getränke": 270, "Tiefkühl": 180, "Gemüse": 7, "Obst": 7, "Kräuter": 5, "Fleisch": 3,
    "Fisch": 2, "Milchprodukte": 10, "Käse": 21, "Eier": 21, "Brot": 4,
}  # fmt: skip
# Stichwort im Namen → Tage (erstes Muster gewinnt)
JE_STICHWORT = [
    (r"nudel|spaghetti|fusilli|penne|pasta|makkaroni|maccheroni|farfalle|tagliatelle|lasagne"
     r"|rigatoni|linguine|tortiglioni|spätzle|couscous|bulgur|quinoa|reis", 540),
    (r"linse|bohne|kichererbse|erbse.*getrock", 540),
    (r"dose|konserve|passiert|tomatenmark|pizzatomat|gehackte tomat|mais\b", 730),
    (r"mehl|grieß|stärke|backpulver|hefe.*trocken|trockenhefe", 270),
    (r"zucker|salz|honig|essig", 1095),
    (r"öl\b|olivenöl|rapsöl|sonnenblumenöl", 365),
    (r"brühe|fond|gewürz|pfeffer|paprikapulver|curry|zimt|oregano|basilikum getr", 540),
    (r"haferflocken|müsli|cornflakes|flocken", 270),
    (r"nuss|nüsse|mandel|samen|kerne", 180),
    (r"schokolade|keks|chips|gummi|bonbon", 180),
    (r"wasser|saft|limo|cola|bier|wein", 270),
    (r"pesto|soße|sauce|ketchup|senf|mayonnaise|chutney", 365),
    (r"joghurt|skyr", 21),
    (r"h-milch|haltbare milch", 90),
    (r"milch|quark|sahne|schmand|frischkäse", 10),
    (r"käse|parmesan|gouda|mozzarella", 21),
    (r"\bei\b|eier", 21),
    (r"hack|hähnchen|huhn|rind|schwein|wurst|schinken|speck", 3),
    (r"lachs|fisch|thunfisch frisch|garnele", 2),
    (r"brot|brötchen|toast|baguette|wrap|tortilla", 5),
    (r"salat|spinat|rucola|kräuter|petersilie|koriander|schnittlauch", 5),
]  # fmt: skip


def tage_schaetzen(p: Product, extra_kategorien: list[str] | None = None) -> int | None:
    """Tage ungeöffnet: Produkt-Kategorie, sonst Name, sonst weitere Kategorien (z. B. OFF)."""
    if p.category in JE_KATEGORIE:
        return JE_KATEGORIE[p.category]
    name = p.name.lower()
    for muster, tage in JE_STICHWORT:
        if re.search(muster, name):
            return tage
    for k in extra_kategorien or []:
        if k in JE_KATEGORIE:
            return JE_KATEGORIE[k]
    return None


def mhd_fuer(
    s: Session,
    p: Product,
    mhd: date | None,
    extra_kategorien: list[str] | None = None,
) -> tuple[date | None, str | None]:
    """(MHD, Hinweis). Angegebenes MHD und gepflegte Haltbarkeit gehen vor (Grocy rechnet dann
    selbst, None). Sonst Schätzung plus Vorschlag „haltbarkeit_tage“ fürs Produkt."""
    if mhd is not None or p.shelf_life_days != 0:
        return mhd, None
    tage = tage_schaetzen(p, extra_kategorien)
    if tage is None:
        return None, f"{p.name}: keine Haltbarkeit bekannt, MHD bitte im Inventar setzen"
    _vorschlag(s, p, tage)
    return date.today() + timedelta(days=tage), f"{p.name}: MHD geschätzt (+{tage} Tage)"


def _vorschlag(s: Session, p: Product, tage: int) -> None:
    from app.kategorisierung import Vorschlag  # zyklischer Import vermeiden

    offen = s.scalars(
        select(Vorschlag).where(
            Vorschlag.objekt_id == p.id,
            Vorschlag.feld == "haltbarkeit_tage",
            Vorschlag.status == "offen",
        )
    ).first()
    if offen is None:
        s.add(Vorschlag(lauf_id=0, typ="produkt", objekt_id=p.id, name=p.name,
                        feld="haltbarkeit_tage", alt=0, neu=tage))  # fmt: skip
