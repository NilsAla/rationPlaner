"""Standardwerte für Mengen-Umrechnung beim Import: Stückgewichte, Dichten, Löffelmaße.

Keine Produktdatenbank, sondern grobe Küchen-Richtwerte, damit ein Import ohne Handarbeit
durchläuft. Ergebnisse sind als Schätzung markiert; echte Werte setzt später der
Kategorisierungslauf je Produkt (Gramm-Umrechnung in Grocy).
Schlüssel sind Wortstämme (siehe rezept_import._stamm), Suche über das letzte Wort.
"""

from __future__ import annotations

# g je Stück
STUECK_G: dict[str, float] = {
    "aubergin": 300,
    "zucchini": 250,
    "paprika": 160,
    "spitzpaprika": 100,
    "zwiebel": 80,
    "schalott": 30,
    "frühlingszwiebel": 15,
    "lauchzwiebel": 15,
    "knoblauchzeh": 4,
    "knoblauch": 50,
    "knoblauchknoll": 50,
    "karott": 80,
    "möhr": 80,
    "kartoffel": 100,
    "süßkartoffel": 250,
    "tomat": 80,
    "cherrytomat": 15,
    "rispentomat": 80,
    "gurk": 400,
    "salatgurk": 400,
    "ei": 60,
    "zitron": 100,
    "limett": 60,
    "orang": 200,
    "apfel": 150,
    "birn": 170,
    "banan": 120,
    "avocado": 200,
    "chilischot": 10,
    "chili": 10,
    "peperoni": 15,
    "champignon": 20,
    "lauch": 200,
    "porre": 200,
    "stangenselleri": 50,
    "selleri": 500,
    "brokkoli": 400,
    "blumenkohl": 600,
    "fenchel": 250,
    "kohlrabi": 300,
    "mango": 350,
    "ingwer": 20,
    "mozzarella": 125,
    "burrata": 125,
    "feta": 200,
    "baguett": 250,
    "brötch": 60,
    "tortilla": 60,
    "wrap": 60,
    "fladenbrot": 400,
    "hähnchenbrustfilet": 150,
    "hähnchenbrust": 150,
    "lachsfilet": 125,
    "pak choi": 150,
    "radiesch": 15,
    "eier": 60,
    "äpfel": 150,
}
# g je Bund
BUND_G: dict[str, float] = {
    "petersili": 25,
    "koriander": 25,
    "schnittlauch": 20,
    "dill": 20,
    "basilikum": 20,
    "minz": 20,
    "frühlingszwiebel": 100,
    "lauchzwiebel": 100,
    "radiesch": 200,
    "suppengrün": 400,
    "thymian": 10,
    "rosmarin": 10,
}
# g je ml
DICHTE: dict[str, float] = {
    "öl": 0.92,
    "olivenöl": 0.92,
    "sesamöl": 0.92,
    "rapsöl": 0.92,
    "sonnenblumenöl": 0.92,
    "milch": 1.03,
    "sahn": 1.0,
    "kokosmilch": 1.0,
    "sojasauc": 1.15,
    "sojasoß": 1.15,
    "essig": 1.0,
    "honig": 1.4,
    "sirup": 1.3,
    "wein": 1.0,
    "brüh": 1.0,
    "fond": 1.0,
    "wasser": 1.0,
    "joghurt": 1.03,
    "schmand": 1.0,
}
LOEFFEL_ML = {"el": 15.0, "tl": 5.0}
LOEFFEL_FEST_G = {"el": 10.0, "tl": 4.0}  # Pulver, Paste, Gewürz
KLEIN_G = {
    "prise": 0.5,
    "msp": 0.5,
    "messerspitze": 0.5,
    "schuss": 10.0,
    "spritzer": 5.0,
    "zweig": 1.0,  # Kräuter: Zweig, Stiel, Stängel
    "stiel": 1.0,
    "stängel": 1.0,
}

# Zutaten-Namen, die auf ein allgemeineres Produkt gehen, mit eigenem Stückgewicht
SYNONYM: dict[str, str] = {
    "knoblauchzeh": "Knoblauch",
    "möhr": "Karotte",
    "paprikaschot": "Paprika",
    "muskatnus": "Muskat",
    "hackfleischzubereitung": "Hackfleisch",
    "öl": "Pflanzenöl",  # HelloFresh „Öl“ = neutrales Öl
}
# Nie als Zutat übernehmen
WEGLASSEN: set[str] = {
    "wasser",
    "leitungswasser",
    "eiswürfel",
    "salzwasser",
    "kochwasser",
    "nudelwasser",
}


def nachschlagen(tabelle: dict[str, float], staemme: list[str]) -> float | None:
    """Erst ganzer Name (letzte zwei Stämme), dann letzter Stamm."""
    if not staemme:
        return None
    for key in (" ".join(staemme[-2:]), staemme[-1]):
        if key in tabelle:
            return tabelle[key]
    # Kompositum: "Sesamöl" → "öl", "Cherrytomate" → "tomat"
    letztes = staemme[-1]
    treffer = [
        k for k in tabelle if " " not in k and letztes.endswith(k) and (len(k) >= 3 or k == "öl")
    ]
    return tabelle[max(treffer, key=len)] if treffer else None
