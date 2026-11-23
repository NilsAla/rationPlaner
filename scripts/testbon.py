"""Erzeugt einen synthetischen Test-Kassenbon (PNG) mit typischen Hürden für den Bon-Import.

Fiktiver Markt, passend zu den Testrezepten (ration/app/fixtures/testdaten.json). Keine echten
Daten. Ausgabe: <ziel>/testbon-scan.png (gerade, sauber), <ziel>/testbon-foto.jpg (schief,
grau, Rauschen, wie vom Handy) und <ziel>/testbon-loesung.json (was richtig wäre).

    python scripts/testbon.py /tmp/bon

Hürden: Gewichtsware mit kg-Preis, "3 x 0,99" über der Zeile, Kürzel ("KIDNEYBOHN."),
abweichende Packungsgrößen (800 g statt 400 g, 2 kg statt 1 kg Netz), Rabatt auf eine Zeile,
Storno einer Zeile, Pfand und Leergut, Non-Food (auch eines, das nach "Einweg" klingt), ein
Artikel mit "Tüte" im Namen, der trotzdem Ware ist, unbekannte Artikel, Coupon.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BREITE = 40  # Zeichen je Zeile

# (Typ, Bon-Text, Anzahl, Einzelpreis, Gesamt, MwSt, Erwartung)
# Typ: artikel / gewicht / rabatt / storno / pfand / leergut / non_food / coupon
ZEILEN = [
    ("gewicht", "PAPRIKA ROT LOSE", 0.486, 3.99, None, "A", "Paprika rot 486 g"),
    ("artikel", "SPAGHETTI NR.5 500G", 3, 0.99, None, "A", "Nudeln 3 × 500 g = 1500 g"),
    ("artikel", "KIDNEYBOHN.ROT 400G", 1, 0.79, None, "A", "Kidneybohnen 400 g"),
    ("artikel", "KIDNEYBOHN.ROT 800G", 1, 1.39, None, "A", "Kidneybohnen 800 g, abweichende Packung"),
    ("artikel", "TOMATEN PASS. 500G", 2, 0.69, None, "A", "Tomaten passiert 2 × 500 g"),
    ("coupon", "COUPON TOMATEN", 1, None, -0.30, "A", "Rabatt auf Tomaten, Preis 1,08"),
    ("artikel", "ZWIEBELN GELB 2KG NETZ", 1, 1.99, None, "A", "Zwiebel 2000 g, abweichend (1 kg Netz)"),
    ("artikel", "OLIVENOEL NAT.EXTRA 0,75L", 1, 6.49, None, "A", "Olivenöl (Füllstand voll)"),
    ("rabatt", "  RABATT 20%", 1, None, -1.30, "A", "Rabatt auf Olivenöl, Preis 5,19"),
    ("non_food", "SPUELMITTEL ZITRONE 500ML", 1, 1.19, None, "B", "Non-Food, nicht vorausgewählt"),
    ("artikel", "MINERALW. MEDIUM 12X0,7L", 1, 3.99, None, "A", "neues Produkt Mineralwasser"),
    ("pfand", "  PFAND 12 x 0,25", 1, None, 3.00, "A", "ignorieren"),
    ("artikel", "KNOBLAUCH", 1, 0.49, None, "A", "neues Produkt Knoblauch, Menge offen"),
    ("artikel", "SPAGHETTI NR.5 500G", 1, 0.99, None, "A", "wird storniert"),
    ("storno", "STORNO SPAGHETTI NR.5 500G", 1, None, -0.99, "A", "hebt die Zeile davor auf"),
    ("artikel", "TUETENSUPPE TOMATE", 1, 0.89, None, "A", "Ware, trotz 'Tüte' im Namen, neu"),
    ("leergut", "LEERGUT", 1, None, -2.25, "A", "ignorieren"),
    ("non_food", "EINWEGHANDSCHUHE 100ST", 1, 2.49, None, "B", "Non-Food, trotz 'Einweg'"),
]


def betrag(z) -> float:
    typ, _, anzahl, einzel, gesamt, *_ = z
    if gesamt is not None:
        return gesamt
    return round(anzahl * einzel, 2)


def eur(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def zeile(links: str, rechts: str) -> str:
    return links[: BREITE - len(rechts) - 1].ljust(BREITE - len(rechts)) + rechts


def text_zeilen() -> tuple[list[str], float]:
    out = [
        "FRISCHE ECKE".center(BREITE),
        "Markt GmbH".center(BREITE),
        "Musterstrasse 12 · 48282 Emsdetten".center(BREITE),
        "Tel. 02572 000000".center(BREITE),
        "",
        "EUR".rjust(BREITE),
    ]
    summe = 0.0
    mwst = {"A": 0.0, "B": 0.0}
    for z in ZEILEN:
        typ, text, anzahl, einzel, gesamt, satz, _ = z
        b = betrag(z)
        summe += b
        mwst[satz] += b
        if typ == "gewicht":
            out.append(text)
            out.append(zeile(f"  {anzahl:.3f} kg x {eur(einzel)} EUR/kg".replace(".", ",", 1), f"{eur(b)} {satz}"))
        elif typ in ("artikel", "non_food") and anzahl > 1:
            out.append(f"  {anzahl} x {eur(einzel)}")
            out.append(zeile(text, f"{eur(b)} {satz}"))
        else:
            out.append(zeile(text, f"{eur(b)} {satz}"))
    summe = round(summe, 2)
    out += [
        "-" * BREITE,
        zeile("SUMME", eur(summe)),
        "=" * BREITE,
        zeile("Geg. EC-Karte", eur(summe)),
        "",
        zeile("Steuer  %      Netto     Steuer", "Brutto"),
    ]
    for satz, pct in (("A", 7), ("B", 19)):
        brutto = round(mwst[satz], 2)
        netto = round(brutto / (1 + pct / 100), 2)
        out.append(zeile(f"{satz}=  {pct:>2},0%   {eur(netto):>8}   {eur(brutto - netto):>6}", eur(brutto)))
    out += [
        "",
        zeile("21.11.2026", "17:42"),
        zeile("Bon-Nr. 4711", "Kasse 3"),
        "",
        "Vielen Dank für Ihren Einkauf!".center(BREITE),
        "Treuepunkte: 12".center(BREITE),
    ]
    return out, summe


def bild(zeilen: list[str]) -> Image.Image:
    font = ImageFont.truetype(FONT, 26)
    zh, rand = 34, 40
    breite = int(font.getlength("M" * BREITE)) + 2 * rand
    img = Image.new("L", (breite, len(zeilen) * zh + 2 * rand), 250)
    d = ImageDraw.Draw(img)
    for i, t in enumerate(zeilen):
        d.text((rand, rand + i * zh), t, font=font, fill=20)
    return img


def foto(scan: Image.Image) -> Image.Image:
    rnd = random.Random(42)
    img = scan.rotate(-2.3, resample=Image.BICUBIC, expand=True, fillcolor=120)
    # ungleichmäßig belichtet + Rauschen
    w, h = img.size
    verlauf = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(v * 0.25))
    img = Image.blend(img, verlauf, 0.18)
    px = img.load()
    for _ in range(w * h // 25):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = max(0, min(255, px[x, y] + rnd.randint(-60, 60)))
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    hintergrund = Image.new("L", (w + 160, h + 160), 70)
    hintergrund.paste(img, (80, 80))
    return hintergrund.resize((int((w + 160) * 0.8), int((h + 160) * 0.8)))


def main() -> None:
    ziel = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    ziel.mkdir(parents=True, exist_ok=True)
    zeilen, summe = text_zeilen()
    scan = bild(zeilen)
    scan.save(ziel / "testbon-scan.png", optimize=True)
    foto(scan).convert("RGB").save(ziel / "testbon-foto.jpg", quality=82)
    loesung = {
        "markt": "Frische Ecke",
        "datum": "2026-11-21",
        "summe": summe,
        "zeilen": [
            {"typ": z[0], "text": z[1].strip(), "betrag": betrag(z), "erwartung": z[6]} for z in ZEILEN
        ],
    }
    (ziel / "testbon-loesung.json").write_text(json.dumps(loesung, ensure_ascii=False, indent=1))
    print("\n".join(zeilen))
    print(f"\nSumme {eur(summe)} → {ziel}")


if __name__ == "__main__":
    main()
