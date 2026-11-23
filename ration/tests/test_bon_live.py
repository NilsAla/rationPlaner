"""Akzeptanz Bon-Live-Test: KI-Antwort mit den Fehlern aus dem ersten Live-Test (2026-11-23)
gegen die Lösung des Test-Bons (scripts/testbon.py). Jede Zeile: Anzahl, Menge, Einheit."""

import json
from pathlib import Path

import pytest

from app.bon import auswerten, groesse_aus_text, mengenzeile, name_passt
from app.inventory.base import Product, TrackingMode

LOESUNG = json.loads(
    (Path(__file__).parent / "fixtures" / "testbon-loesung.json").read_text(encoding="utf-8")
)

KATALOG = [
    Product(id="paprika", name="Paprika rot", unit="g", tracking_mode=TrackingMode.EXAKT),
    Product(id="nudeln", name="Nudeln", unit="g", pack_unit="Packung", pack_size=500),
    Product(id="kidney", name="Kidneybohnen", unit="g", pack_unit="Dose", pack_size=400),
    Product(id="tomaten", name="Tomaten passiert", unit="g", pack_size=500),
    Product(id="zwiebel", name="Zwiebel", unit="g", pack_unit="Netz", pack_size=1000),
    Product(id="oel", name="Olivenöl", unit="ml", tracking_mode=TrackingMode.FUELLSTAND),
    Product(id="knoblauch", name="Knoblauch", unit="g", pack_unit="Knolle", pack_size=50),
]


def w(text, preis, typ="artikel", **x):
    """Eine Zeile, wie die KI sie im Live-Test lieferte (Anzahl 1, Einzelpreis leer)."""
    return {"text": text, "typ": typ, "anzahl": 1, "preis": preis, "packung_menge": 0,
            "packung_einheit": "", "konfidenz": 0.9, **x}  # fmt: skip


# Fehler aus dem Live-Test: Mengenzeilen als eigene Zeilen, Spaghetti/Tomaten mit Anzahl 1,
# Wasser mit 0,7 l statt Gebinde, Knoblauch ohne Größe.
LIVE = {
    "markt": "Frische Ecke",
    "datum": "2026-11-21",
    "summe": 25.15,
    "positionen": [
        w("PAPRIKA ROT LOSE", 1.94, produkt_id="paprika"),
        w("0,486 kg x 3,99 EUR/kg", 0, typ="mengenzeile"),
        w("3 x 0,99", 0, typ="artikel"),  # KI typisiert falsch, Code erkennt die Mengenzeile
        w("SPAGHETTI NR.5 500G", 2.97, produkt_id="nudeln", packung_menge=500, packung_einheit="g"),
        w("KIDNEYBOHN.ROT 400G", 0.79, produkt_id="kidney"),
        w("KIDNEYBOHN.ROT 800G", 1.39, produkt_id="kidney"),
        w("2 x 0,69", 0, typ="mengenzeile"),
        w("TOMATEN PASS. 500G", 1.38, produkt_id="tomaten"),
        w("COUPON TOMATEN", -0.30, typ="rabatt"),
        w("ZWIEBELN GELB 2KG NETZ", 1.99, produkt_id="zwiebel"),
        w("OLIVENOEL NAT.EXTRA 0,75L", 6.49, produkt_id="oel"),
        w("RABATT 20%", -1.30, typ="rabatt"),
        w("SPUELMITTEL ZITRONE 500ML", 1.19, typ="non_food", neu_name="Spülmittel"),
        w(
            "MINERALW. MEDIUM 12X0,7L",
            3.99,
            packung_menge=0.7,
            packung_einheit="l",
            neu_name="Mineralwasser",
        ),  # fmt: skip
        w("PFAND 12 x 0,25", 3.00, typ="pfand"),
        w("KNOBLAUCH", 0.49, produkt_id="knoblauch"),
        w("SPAGHETTI NR.5 500G", 0.99, produkt_id="nudeln"),
        w("STORNO SPAGHETTI NR.5 500G", -0.99, typ="storno"),
        w("TUETENSUPPE TOMATE", 0.89, neu_name="Tütensuppe Tomate"),
        w("LEERGUT", -2.25, typ="leergut"),
        w("EINWEGHANDSCHUHE 100ST", 2.49, typ="non_food", neu_name="Einweghandschuh"),
    ],
}


@pytest.fixture
def ergebnis():
    _, _, summe, pos = auswerten(LIVE, KATALOG, {}, {}, 0.85)
    return summe, pos


def test_jede_zeile_gegen_die_loesung(ergebnis):
    _, pos = ergebnis
    assert [p.text for p in pos] == [z["text"] for z in LOESUNG["zeilen"]]
    abweichungen = []
    for p, z in zip(pos, LOESUNG["zeilen"], strict=True):
        if z["anzahl"] is None:
            continue
        ist = (p.anzahl, p.bon_menge, p.bon_einheit)
        soll = (z["anzahl"], z["menge"], z["einheit"])
        if ist != soll:
            abweichungen.append(f"{z['text']}: ist {ist}, soll {soll}")
    assert not abweichungen, "\n".join(abweichungen)


def test_summe_pfand_storno_rabatt(ergebnis):
    summe, pos = ergebnis
    assert summe == 25.15
    assert round(sum(p.betrag for p in pos), 2) == 25.15
    by = {}
    for p in pos:
        by.setdefault(p.text, []).append(p)
    assert by["PFAND 12 x 0,25"][0].aktion == "weglassen" and by["PFAND 12 x 0,25"][0].betrag == 3
    assert by["LEERGUT"][0].betrag == -2.25
    nudeln, storniert = by["SPAGHETTI NR.5 500G"]
    assert (nudeln.menge, nudeln.aktion) == (1500, "buchen") and storniert.aktion == "weglassen"
    tomaten = by["TOMATEN PASS. 500G"][0]
    assert (tomaten.menge, tomaten.preis, tomaten.rabatt) == (1000, 1.08, 0.3)
    assert by["OLIVENOEL NAT.EXTRA 0,75L"][0].preis == 5.19


def test_einheiten_namen_annahmen(ergebnis):
    _, pos = ergebnis
    by = {p.text: p for p in pos}
    wasser = by["MINERALW. MEDIUM 12X0,7L"]
    assert (wasser.aktion, wasser.menge, wasser.einheit) == ("neu", 8400, "ml")
    assert "Größe" in wasser.hinweis  # KI las 0,7 l, Code nimmt das Gebinde
    assert by["SPUELMITTEL ZITRONE 500ML"].einheit == "ml"
    assert by["OLIVENOEL NAT.EXTRA 0,75L"].menge == 750  # Produkt in ml
    knoblauch = by["KNOBLAUCH"]
    assert knoblauch.annahme and knoblauch.menge == 50 and "Annahme" in knoblauch.hinweis
    assert knoblauch.status == "vorschlag" and not knoblauch.vorausgewaehlt
    hand = by["EINWEGHANDSCHUHE 100ST"]
    assert (hand.neu_name, hand.einheit, hand.vorausgewaehlt) == ("Einweghandschuh", "Stück", False)
    assert by["PAPRIKA ROT LOSE"].menge == 486 and by["PAPRIKA ROT LOSE"].gewicht


def test_falscher_ki_name_wird_verworfen():
    daten = {"positionen": [w("EINWEG SCHUHE", 2.49, typ="non_food", neu_name="Einweggeschirr")]}
    p = auswerten(daten, KATALOG, {}, {}, 0.85)[3][0]
    assert p.neu_name == "Einweg Schuhe" and "passt nicht" in p.hinweis


def test_mengenzeile_unklar_heisst_review():
    daten = {
        "positionen": [
            w("A 1", 1.38, produkt_id="nudeln"),
            w("2 x 0,69", 0, typ="mengenzeile"),
            w("B 1", 1.38, produkt_id="nudeln"),
        ]
    }
    pos = auswerten(daten, KATALOG, {}, {}, 0.85)[3]
    assert len(pos) == 2 and all(p.zweifel and p.status == "pruefen" for p in pos)
    assert not any(p.vorausgewaehlt for p in pos)


def test_anzahl_passt_nicht_heisst_review():
    daten = {"positionen": [w("X 500G", 2.50, produkt_id="nudeln", einzelpreis=0.99)]}
    p = auswerten(daten, KATALOG, {}, {}, 0.0)[3][0]
    assert p.zweifel and p.status == "pruefen" and not p.vorausgewaehlt


@pytest.mark.parametrize(
    ("text", "soll"),
    [
        ("KIDNEYB. 400G", (400, "g")),
        ("BUTTER 2x250g", (500, "g")),
        ("MINERALW. 12X0,7L", (8400, "ml")),
        ("KARTOFFELN 1,5KG", (1500, "g")),
        ("SAHNE 20CL", (200, "ml")),
        ("SPAGHETTI NR.5 500G", (500, "g")),
        ("PAPRIKA ROT LOSE", None),
        ("PFAND 12 x 0,25", None),
    ],
)
def test_groesse_aus_text(text, soll):
    assert groesse_aus_text(text) == soll


def test_mengenzeilen_und_namen():
    assert mengenzeile("2 Stk x 0,69") == ("stueck", 2, 0.69)
    assert mengenzeile("0,486 kg x 3,99 EUR/kg") == ("gewicht", 0.486, 3.99)
    assert mengenzeile("PFAND 12 x 0,25") is None
    assert name_passt("Tomate passiert", "TOMATEN PASS. 500G")
    assert not name_passt("Einweggeschirr", "EINWEG SCHUHE")
