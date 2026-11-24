"""KI-Schritt beim Rezept-Import über ``KiClient`` (ein Aufruf je Rezept).

Grundregel "LLM wählt aus, Code rechnet": Das LLM schlägt je offener Zutat vor, welches
Katalogprodukt gemeint ist oder dass ein neues Produkt nötig ist, nennt die Kategorie und für
neue Produkte Tracking-Modus, Haltbarkeit und ggf. das Oberprodukt (Variante). Der Code prüft:
IDs müssen existieren, ein Treffer muss in der Kategorie zum Katalogprodukt passen, Werte gehen
durch dieselben Regeln wie der Kategorisierungslauf. Mengen rechnet der Code. Den Namen neuer
Produkte bildet der Code. Alles bleibt ein Vorschlag in der Vorschau; der Klick auf
„Übernehmen“ ist das Review (Grundregel 4).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from app.inventory.base import Product, TrackingMode
from app.kategorisierung import KATEGORIEN, produkt_werte
from app.rezept_import import (
    STUECK,
    RohZutat,
    Zuordnung,
    _staemme,
    aehnlichkeit,
    gramm,
    kern_name,
)

log = logging.getLogger("ration.ki")

MAX_G = 5000.0
# Werte, die der Import für neue Produkte setzt; der Rest bleibt dem Kategorisierungslauf.
ATTRIBUTE = (
    "tracking_modus",
    "kategorie",
    "ablauf_typ",
    "haltbarkeit_tage",
    "haltbarkeit_offen_tage",
)

ANWEISUNG = """Du ordnest die Zutaten eines Rezepts den Produkten eines Haushaltsvorrats zu.

Katalog (id|name|einheit|kategorie|oberprodukt):
{katalog}

Zutaten (nr: Originaltext | Einheit der Mengenangabe | Vorschlag des Codes | ähnliche
Katalogprodukte):
{zutaten}

Je Zutat genau ein Eintrag, index = die nr vor dem Doppelpunkt:
1. kategorie: Kategorie des Lebensmittels in der Zutat, eine aus: {kategorien}.
2. aktion "zuordnen" mit produkt_id, wenn ein Katalogprodukt dasselbe Lebensmittel ist, auch
   unter anderem Namen oder in anderer Schreibweise (Möhre = Karotte, Lauchzwiebel =
   Frühlingszwiebel, Porree = Lauch, Sojasoße = Sojasauce, gelbe Paprika = Paprika,
   Paprika edelsüß = Paprikapulver, Gemüsebrühepulver = Gemüsebrühe). Suche im ganzen Katalog,
   nicht nur nach dem Vorschlag des Codes.
   Kein Treffer ist ein anderes Lebensmittel oder eine andere Verarbeitungsform:
   Paprika edelsüß (Gewürz) ≠ Paprika (Gemüse), Tomatenmark ≠ Tomate, saure Sahne ≠ Sahne,
   Dosentomaten (stückig, passiert, geschält) ≠ frische Tomaten, Süßkartoffel ≠ Kartoffel,
   Sesamöl ≠ Olivenöl, Salz ≠ Öl. Im Zweifel "neu".
   Steht "fest" beim Vorschlag, ist das Produkt gesetzt: gib dieselbe produkt_id zurück.
   Steht ein anderer Vorschlag dabei, prüfe ihn und korrigiere ihn, wenn er falsch ist.
   Prüfe die ähnlichen Katalogprodukte zuerst; "neu" nur, wenn keins davon und auch sonst
   kein Katalogprodukt dasselbe Lebensmittel ist.
3. Sonst aktion "neu" (das Produkt wird angelegt, produkt_id leer). Dann zusätzlich:
   - tracking_modus: "exakt" (verderblich: Frischgemüse, Fleisch, Milchprodukte),
     "menge" (lange haltbar, Menge zählt: Nudeln, Reis, Dosen, Zwiebeln, Kartoffeln),
     "fuellstand" (angebrochen: Mehl, Zucker, Öl, Essig, Senf, Saucen),
     "immer_da" (Kleinkram: Salz, Pfeffer, getrocknete Gewürze).
   - ablauf_typ: "verbrauchsdatum" nur bei Fleisch, Fisch, Hack; sonst "mhd".
   - haltbarkeit_tage: ungeöffnet ab Kauf in TAGEN (-1 = praktisch unbegrenzt, nie 0).
   - haltbarkeit_offen_tage: nach dem Öffnen in Tagen (0, wenn Öffnen nichts ändert).
   - variante_von: id eines Katalogprodukts, für das das neue Produkt im Rezept einspringen
     kann, weil es eine Sorte davon ist (Vollkorn-Penne → Penne, Spitzpaprika → Paprika,
     Kirschtomaten → Tomate). Nur Produkte ohne eigenes Oberprodukt. Sonst leer.
4. Immer angeben, auch bei "zuordnen":
   g_je_einheit: Gramm für genau 1 Einheit der Mengenangabe (1 Stück, Packung, Dose, Tube,
   Bund, EL, TL, Zehe, Prise; typische Handelsgröße, z. B. 1 Dose Tomaten 400).
   Bei g/kg/ml/l oder ohne Menge: 0.
   stueck_g: Gewicht eines Stücks in Gramm, wenn man es stückweise kauft (Zitrone 100,
   Zwiebel 80), sonst 0."""


def _feld(selector: dict, required: bool = False) -> dict:
    return {"required": required, "selector": selector}


STRUKTUR = {
    "zutaten": {
        "description": "Genau ein Eintrag je Zutat",
        "required": True,
        "selector": {
            "object": {
                "multiple": True,
                "fields": {
                    # Kategorie vor der Entscheidung: erst „Gewürz“, dann zuordnen
                    "index": _feld({"number": {}}, True),
                    "kategorie": _feld({"select": {"options": KATEGORIEN}}, True),
                    "aktion": _feld({"select": {"options": ["zuordnen", "neu"]}}, True),
                    "produkt_id": _feld({"text": {}}),
                    "g_je_einheit": _feld({"number": {}}, True),
                    "stueck_g": _feld({"number": {}}, True),
                    "tracking_modus": _feld(
                        {"select": {"options": [str(m) for m in TrackingMode]}}
                    ),
                    "ablauf_typ": _feld({"select": {"options": ["mhd", "verbrauchsdatum"]}}),
                    "haltbarkeit_tage": _feld({"number": {}}),
                    "haltbarkeit_offen_tage": _feld({"number": {}}),
                    "variante_von": _feld({"text": {}}),
                },
            }
        },
    }
}


@dataclass
class KiErgebnis:
    aktiv: bool
    geaendert: int = 0
    fehler: str | None = None


def _zahl(wert: object) -> float | None:
    try:
        f = float(wert)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if 0 < f <= MAX_G else None


def offen(z: Zuordnung) -> bool:
    """Geht an die KI: alles ohne sicheren Treffer, sichere Treffer nur, wenn die Menge fehlt."""
    if z.aktion == "weglassen":
        return False
    return not z.sicher or z.status == "pruefen"


KANDIDATEN = 5


def aehnliche(z: Zuordnung, produkte: list[Product]) -> str:
    """Bis zu fünf Katalogprodukte mit Namensbezug (Code filtert vor, die KI wählt aus).
    Live: ohne Liste übersah gpt-4.1 „Paprikapulver“ für „Paprika, edelsüß“."""
    kern = kern_name(z.name)
    endung = _staemme(kern)[-1:] or [""]

    def wert(p: Product) -> float:
        s = max(aehnlichkeit(kern, p.name), aehnlichkeit(z.name, p.name))
        # gleiches Grundwort am Ende: Lauchzwiebel ~ Frühlingszwiebel
        letzte = _staemme(p.name)[-1:] or [""]
        if len(os.path.commonprefix([endung[0][::-1], letzte[0][::-1]])) >= 5:
            s = max(s, 0.3)
        return s

    wertung = sorted(((wert(p), p) for p in produkte), key=lambda x: -x[0])
    return ", ".join(f"{p.id} {p.name}" for s, p in wertung[:KANDIDATEN] if s > 0)


def offene(zuordnung: list[Zuordnung]) -> list[Zuordnung]:
    """Die Zutaten, die an die KI gehen, in fester Reihenfolge (nr = Position + 1)."""
    return [z for z in zuordnung if offen(z)]


def anweisung(zuordnung: list[Zuordnung], produkte: list[Product]) -> str:
    namen = {p.id: p.name for p in produkte}
    katalog = (
        "\n".join(
            f"{p.id}|{p.name}|{p.unit}|{p.category or '-'}|{namen.get(p.parent_id or '', '-')}"
            for p in produkte
        )
        or "(leer)"
    )

    def vorschlag(z: Zuordnung) -> str:
        if z.aktion == "zuordnen":
            return f"{z.produkt_id} {z.produkt}" + (" (fest)" if z.sicher else "")
        return "neu"

    # eigene Nummern 1…n statt Rezept-Index: live zählte die KI sonst selbst (3, 7 → 1, 2)
    zutaten = "\n".join(
        f"{nr}: {z.text} | {z.einheit or ('ohne Menge' if z.menge is None else 'Stück')}"
        f" | {vorschlag(z)} | {aehnliche(z, produkte) or '-'}"
        for nr, z in enumerate(offene(zuordnung), 1)
    )
    return ANWEISUNG.format(katalog=katalog, zutaten=zutaten, kategorien=", ".join(KATEGORIEN))


def _menge_neu_berechnen(z: Zuordnung, p: Product | None) -> None:
    """Menge für das (ggf. neu gewählte) Ziel deterministisch rechnen, wie zuordnen()."""
    roh = RohZutat(z.text, z.menge, z.einheit, z.name)
    unit = (p.unit if p else "g").strip().lower()
    stueck_g = p.pack_size if p and p.pack_unit and p.pack_unit.lower() in STUECK else None
    if unit in ("g", "ml"):
        menge, z.status, z.hinweis = gramm(roh, z.name, stueck_g or z.stueck_g)
    elif z.einheit == unit or (z.einheit is None and unit in STUECK):
        menge, z.status, z.hinweis = z.menge or 0.0, "ok", ""
    else:
        menge, z.status, z.hinweis = None, "pruefen", f"Einheit {z.einheit} → {p.unit if p else ''}"
    z.menge_bestand = round(menge, 1) if menge is not None else None


def passt(z: Zuordnung, p: Product, kategorie: str | None) -> bool:
    """KI-Treffer prüfen: gleiche Kategorie wie das Katalogprodukt. Hat das Produkt noch keine
    Kategorie, muss wenigstens der Name ähnlich sein (live gesehen: Salz → Olivenöl)."""
    if p.category:
        return kategorie == p.category
    return aehnlichkeit(kern_name(z.name), p.name) >= 0.5


def _neuer_name(z: Zuordnung, produkte: list[Product]) -> str:
    """Name eines neuen Produkts (Code): Kern-Name, bei Namensgleichheit mit einem bestehenden
    Produkt der volle Name („Paprika, edelsüß“ → „Paprika edelsüß“, nicht „Paprika“)."""
    kern = kern_name(z.name)
    vergeben = {p.name.strip().lower() for p in produkte}
    if kern.lower() not in vergeben:
        return kern
    voll = " ".join(z.name.replace(",", " ").replace("(", " ").replace(")", " ").split())
    return voll[:1].upper() + voll[1:]


def _als_alternative(z: Zuordnung, p: Product) -> None:
    z.alternativen = [{"produkt_id": p.id, "name": p.name}] + [
        a for a in z.alternativen if a["produkt_id"] != p.id
    ]


def anwenden(zuordnung: list[Zuordnung], produkte: list[Product], daten: dict) -> int:
    """Validiert die KI-Antwort und übernimmt sie, wo der Code keine sichere Antwort hat.

    Gibt die Anzahl geänderter Zutaten zurück. Ungültiges wird verworfen, nie geraten.
    """
    by_id = {p.id: p for p in produkte}
    by_nr = dict(enumerate(offene(zuordnung), 1))
    geaendert: set[int] = set()
    eintraege = daten.get("zutaten") if isinstance(daten, dict) else None
    for e in eintraege if isinstance(eintraege, list) else []:
        if not isinstance(e, dict):
            continue
        try:
            z = by_nr[int(e.get("index"))]
        except (KeyError, TypeError, ValueError):
            continue
        vorher = (z.aktion, z.produkt_id, z.menge_bestand, z.stueck_g, z.hinweis)
        kategorie = e.get("kategorie") if e.get("kategorie") in KATEGORIEN else None
        z.kategorie = kategorie or z.kategorie
        pid = str(e.get("produkt_id") or "").strip()
        if z.sicher:
            pass  # nur die Menge (g_je_einheit) unten
        elif e.get("aktion") == "zuordnen" and pid in by_id:
            p = by_id[pid]
            if passt(z, p, kategorie):
                if z.produkt_id != p.id:
                    z.aktion, z.produkt_id, z.produkt, z.neu_name = "zuordnen", p.id, p.name, None
                    z.alternativen = []
                    _menge_neu_berechnen(z, p)
                z.ki = True
            else:
                # andere Kategorie (Fehlgriff oder Grenzfall): nur als erste Alternative zeigen
                _als_alternative(z, p)
                z.hinweis = " · ".join(t for t in (f"KI meint: {p.name}?", z.hinweis) if t)
        elif e.get("aktion") == "neu":
            if z.aktion == "zuordnen":  # Kern-Treffer des Codes verworfen
                alt = by_id.get(z.produkt_id or "")
                z.aktion, z.produkt_id, z.produkt = "neu", None, None
                z.neu_name = _neuer_name(z, produkte)
                _menge_neu_berechnen(z, None)
                if alt:
                    _als_alternative(z, alt)
            z.ki = True
            _attribute(z, e, kategorie, by_id)
        sg = _zahl(e.get("stueck_g"))
        if (
            z.aktion == "neu"
            and not z.stueck_g
            and sg
            and (z.einheit is None or z.einheit in STUECK)
        ):
            z.stueck_g = sg
            _menge_neu_berechnen(z, None)
        g = _zahl(e.get("g_je_einheit"))
        if z.status == "pruefen" and g and z.menge is not None:
            ziel = by_id.get(z.produkt_id or "")
            if ziel is None or ziel.unit.strip().lower() in ("g", "ml"):
                z.menge_bestand = round(z.menge * g, 1)
                z.status = "schaetzung"
                z.hinweis = f"KI: 1 {z.einheit or 'Stück'} ≈ {g:g} g"
        if (z.aktion, z.produkt_id, z.menge_bestand, z.stueck_g, z.hinweis) != vorher:
            if not z.hinweis.startswith("KI"):
                z.hinweis = " · ".join(t for t in ("KI-Vorschlag", z.hinweis) if t)
            geaendert.add(z.index)
    return len(geaendert)


def _attribute(z: Zuordnung, e: dict, kategorie: str | None, by_id: dict[str, Product]) -> None:
    """Werte für ein neues Produkt: dieselben Regeln wie der Kategorisierungslauf."""
    name = z.neu_name or kern_name(z.name)
    werte = produkt_werte(e, Product(id="", name=name, unit="g", category=kategorie))
    z.attribute = {k: v for k, v in werte.items() if k in ATTRIBUTE}
    oben = by_id.get(str(e.get("variante_von") or "").strip())
    # Grocy kennt eine Ebene; Varianten brauchen dieselbe Bestandseinheit (neu: g)
    if oben and not oben.parent_id and oben.unit.strip().lower() == "g":
        z.variante_von = {"produkt_id": oben.id, "name": oben.name}
    else:
        z.variante_von = None


async def verfeinern(ki, zuordnung: list[Zuordnung], produkte: list[Product]) -> KiErgebnis:
    """Fragt die KI nur bei offenen Zutaten. Bei Fehlern bleibt der Code-Vorschlag."""
    if not any(offen(z) for z in zuordnung):
        return KiErgebnis(aktiv=True)
    try:
        daten = await ki.generate(
            "import", "rationPlaner Rezept-Import", anweisung(zuordnung, produkte), STRUKTUR
        )
    except Exception as err:  # noqa: BLE001 - KI ist optional, Import muss weiterlaufen
        log.warning("KI-Zuordnung fehlgeschlagen: %s", err)
        return KiErgebnis(aktiv=True, fehler=str(err) or type(err).__name__)
    log.info("KI-Zuordnung Rohantwort: %s", json.dumps(daten, ensure_ascii=False))
    return KiErgebnis(aktiv=True, geaendert=anwenden(zuordnung, produkte, daten))
