"""KI-Zuordnung für den Rezept-Import über HA ai_task.generate_data.

Grundregel "LLM wählt aus, Code rechnet": Das LLM schlägt nur vor, welches Produkt gemeint
ist (ID aus dem Katalog oder neues Produkt, dessen Namen der Code bildet) und wie viel Gramm
eine Einheit der Mengenangabe wiegt. Die Menge rechnet der Code. Sichere Treffer (Alias,
gleicher Name) und Tabellenwerte aus mengen.py haben Vorrang; die KI füllt nur Lücken.
Alles bleibt ein Vorschlag in der Vorschau, übernommen wird erst nach Review.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.ha import HomeAssistant
from app.inventory.base import Product
from app.rezept_import import STUECK, RohZutat, Zuordnung, aehnlichkeit, gramm, kern_name

log = logging.getLogger("ration.ki")

MAX_G = 5000.0

ANWEISUNG = """Du ordnest Zutaten eines Rezepts den Produkten eines Haushaltsvorrats zu.

Katalog (id|name|einheit):
{katalog}

Offene Zutaten (index: Originaltext | Einheit der Mengenangabe):
{zutaten}

Regeln je Zutat:
1. aktion "zuordnen" mit produkt_id NUR, wenn das Katalogprodukt dasselbe Lebensmittel unter
   anderem Namen ist (Möhre = Karotte, Lauchzwiebel = Frühlingszwiebel, Spaghetti = Nudeln).
   Andere Sorte oder anderes Lebensmittel ist KEIN Treffer: Salz ≠ Öl, Frühlingszwiebel ≠ Zwiebel,
   Süßkartoffel ≠ Kartoffel, Sesamöl ≠ Olivenöl, Tomatenmark ≠ Tomate. Im Zweifel "neu".
2. Sonst aktion "neu" (das Produkt wird neu angelegt), produkt_id leer.
3. g_je_einheit: realistisches Gewicht in Gramm für genau 1 Einheit der Mengenangabe
   (1 Stück, 1 Packung, 1 Dose, 1 Bund, 1 EL, 1 TL, 1 Zehe, 1 Prise; typische Handelsgröße,
   z. B. 1 Dose Kokosmilch 400). Bei g/kg/ml/l oder ohne Menge: 0.
4. stueck_g: Gewicht eines Stücks in Gramm, wenn man das Lebensmittel stückweise kauft
   (Gemüse, Obst, Eier, Brötchen), sonst 0.
Gib für jede offene Zutat genau einen Eintrag mit ihrem index zurück."""

STRUKTUR = {
    "zutaten": {
        "description": "Genau ein Eintrag je Zutat",
        "required": True,
        "selector": {
            "object": {
                "multiple": True,
                "fields": {
                    "index": {"required": True, "selector": {"number": {}}},
                    "aktion": {
                        "required": True,
                        "selector": {"select": {"options": ["zuordnen", "neu"]}},
                    },
                    "produkt_id": {"selector": {"text": {}}},
                    "g_je_einheit": {"required": True, "selector": {"number": {}}},
                    "stueck_g": {"required": True, "selector": {"number": {}}},
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


def anweisung(zuordnung: list[Zuordnung], produkte: list[Product]) -> str:
    katalog = "\n".join(f"{p.id}|{p.name}|{p.unit}" for p in produkte) or "(leer)"
    zutaten = "\n".join(
        f"{z.index}: {z.text} | {z.einheit or ('ohne Menge' if z.menge is None else 'Stück')}"
        for z in zuordnung
        if z.aktion == "neu"
    )
    return ANWEISUNG.format(katalog=katalog, zutaten=zutaten)


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


def anwenden(zuordnung: list[Zuordnung], produkte: list[Product], daten: dict) -> int:
    """Validiert die KI-Antwort und übernimmt sie, wo der Code keine sichere Antwort hat.

    Gibt die Anzahl geänderter Zutaten zurück. Ungültiges wird verworfen, nie geraten.
    """
    by_id = {p.id: p for p in produkte}
    by_index = {z.index: z for z in zuordnung}
    geaendert: set[int] = set()
    eintraege = daten.get("zutaten") if isinstance(daten, dict) else None
    for e in eintraege if isinstance(eintraege, list) else []:
        if not isinstance(e, dict):
            continue
        try:
            z = by_index[int(e.get("index"))]
        except (KeyError, TypeError, ValueError):
            continue
        if z.aktion != "neu":
            continue  # Alias/Name-Treffer und "weglassen" entscheidet der Code
        vorher = (z.produkt_id, z.menge_bestand, z.stueck_g, z.hinweis)
        pid = str(e.get("produkt_id") or "").strip()
        if e.get("aktion") == "zuordnen" and pid in by_id:
            p = by_id[pid]
            if aehnlichkeit(kern_name(z.name), p.name) >= 0.5:
                z.aktion, z.produkt_id, z.produkt, z.neu_name = "zuordnen", p.id, p.name, None
                z.alternativen = []
                _menge_neu_berechnen(z, p)
            else:
                # Name ganz anders (Synonym oder Fehlgriff): nur als erste Alternative zeigen
                z.alternativen = [{"produkt_id": p.id, "name": p.name}] + [
                    a for a in z.alternativen if a["produkt_id"] != p.id
                ]
                z.hinweis = " · ".join(t for t in (f"KI meint: {p.name}?", z.hinweis) if t)
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
        if (z.produkt_id, z.menge_bestand, z.stueck_g, z.hinweis) != vorher:
            if not z.hinweis.startswith("KI"):
                z.hinweis = " · ".join(t for t in ("KI-Vorschlag", z.hinweis) if t)
            geaendert.add(z.index)
    return len(geaendert)


async def verfeinern(
    ha: HomeAssistant, entity_id: str, zuordnung: list[Zuordnung], produkte: list[Product]
) -> KiErgebnis:
    """Fragt die KI nur bei offenen Zutaten. Bei Fehlern bleibt der Code-Vorschlag."""
    if not any(z.aktion == "neu" for z in zuordnung):
        return KiErgebnis(aktiv=True)
    try:
        daten = await ha.ai_task(
            entity_id, "rationPlaner Rezept-Import", anweisung(zuordnung, produkte), STRUKTUR
        )
    except Exception as err:  # noqa: BLE001 - KI ist optional, Import muss weiterlaufen
        log.warning("KI-Zuordnung fehlgeschlagen: %s", err)
        return KiErgebnis(aktiv=True, fehler=str(err) or type(err).__name__)
    log.info("KI-Zuordnung Rohantwort: %s", json.dumps(daten, ensure_ascii=False))
    return KiErgebnis(aktiv=True, geaendert=anwenden(zuordnung, produkte, daten))
