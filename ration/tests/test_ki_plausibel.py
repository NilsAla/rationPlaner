"""Code-Guards für KI-Stammdaten: Haltbarkeit 0, Monate statt Tage, Ausreißer, Packung."""

from app.inventory.base import Product
from app.kategorisierung import produkt_werte


def werte(name, ki, **p):
    verworfen = []
    w = produkt_werte(
        ki, Product(id="x", name=name, unit=p.pop("unit", "g"), **p), False, verworfen
    )
    return w, verworfen


def test_null_tage_fliegt_raus():
    w, v = werte("Basmatireis", {"haltbarkeit_tage": 0})
    assert "haltbarkeit_tage" not in w and "heute" in v[0]
    w, _ = werte("Pfeffer schwarz", {"haltbarkeit_tage": 0})
    assert "haltbarkeit_tage" not in w


def test_monate_statt_tage_fliegt_raus():
    w, v = werte("Rinderbrühe", {"haltbarkeit_tage": 12})
    assert "haltbarkeit_tage" not in w and "Monate statt Tage" in v[0]
    w, v = werte("Tomatenpesto", {"haltbarkeit_tage": 12}, shelf_life_days=365)
    assert "haltbarkeit_tage" not in w


def test_plausible_werte_bleiben():
    assert werte("Rinderbrühe", {"haltbarkeit_tage": 540})[0]["haltbarkeit_tage"] == 540
    assert werte("Hackfleisch", {"haltbarkeit_tage": 2})[0]["haltbarkeit_tage"] == 2
    assert werte("Salz", {"haltbarkeit_tage": -1})[0]["haltbarkeit_tage"] == -1
    # unbekanntes Produkt ohne Richtwert: KI-Wert gilt
    assert werte("Tahin", {"haltbarkeit_tage": 300})[0]["haltbarkeit_tage"] == 300
    # unbegrenzt für Frischware nicht
    assert "haltbarkeit_tage" not in werte("Joghurt natur", {"haltbarkeit_tage": -1})[0]


def test_rauschen_ueberschreibt_nicht():
    w, _ = werte("Tomatenpesto", {"haltbarkeit_tage": 300}, shelf_life_days=365)
    assert "haltbarkeit_tage" not in w  # 18 % Abweichung: gepflegter Wert bleibt


def test_offen_laenger_als_ungeoeffnet():
    w, v = werte("Joghurt", {"haltbarkeit_tage": 21, "haltbarkeit_offen_tage": 30})
    assert "haltbarkeit_offen_tage" not in w and v


def test_packung_nur_ohne_gepflegte_und_ohne_kaeufe():
    ki = {"packung_einheit": "Becher", "packung_menge": 150}
    assert werte("Joghurt", ki)[0]["packung"] == {"einheit": "Becher", "menge": 150}
    assert "packung" not in werte("Joghurt", ki, pack_unit="Becher", pack_size=500)[0]
    verworfen = []
    w = produkt_werte(ki, Product(id="x", name="Joghurt", unit="g"), True, verworfen)
    assert "packung" not in w  # Käufe laut Bon vorhanden → Analyse „Packungsgrößen“ entscheidet


# Echte Antworten aus dem Lauf vom 2026-11-23 18:31 (KI-Log #47, alter Prompt)
LIVE = [
    ("Ketchup", "Saucen", 12), ("Tomatenpesto", "Saucen", 12), ("Pfeffer", "Gewürze", 0),
    ("Senf", "Saucen", 12), ("Rinderbrühe", "Saucen", 12), ("Korma-Paste", "Saucen", 12),
    ("Sriracha Sauce", "Saucen", 12), ("Currypaste", "Saucen", 12),
]  # fmt: skip


def test_live_lauf_wird_komplett_abgefangen():
    for name, kat, tage in LIVE:
        w, v = werte(name, {"haltbarkeit_tage": tage, "kategorie": kat})
        assert "haltbarkeit_tage" not in w, (name, w)
    # plausible Werte desselben Laufs bleiben
    assert (
        werte("Honig", {"haltbarkeit_tage": 365, "kategorie": "Süßes"})[0]["haltbarkeit_tage"]
        == 365
    )
    assert (
        werte("Bio Schweinefilet", {"haltbarkeit_tage": 3, "kategorie": "Fleisch"})[0][
            "haltbarkeit_tage"
        ]
        == 3
    )


def test_nebenfelder_ohne_rauschen_und_ohne_starke_kuerzung():
    ki = {
        "haltbarkeit_offen_tage": 0,
        "haltbarkeit_gefroren_tage": 0,
        "haltbarkeit_aufgetaut_tage": 0,
    }
    assert werte("Nudeln", ki)[0] == {}  # leer → 0: nichts vorschlagen
    w, v = werte("Erdnussbutter", {"haltbarkeit_offen_tage": 30}, shelf_life_opened_days=180)
    assert "haltbarkeit_offen_tage" not in w and "Kürzung" in v[0]
    w, _ = werte("Rinderbrühe", {"haltbarkeit_offen_tage": 7}, shelf_life_opened_days=180)
    assert "haltbarkeit_offen_tage" not in w
    # moderate Korrektur bleibt, neue Werte auch
    assert werte("Cashewkerne", {"haltbarkeit_offen_tage": 30}, shelf_life_opened_days=90)[0]
    assert (
        werte("Babyspinat", {"haltbarkeit_gefroren_tage": 180})[0]["haltbarkeit_gefroren_tage"]
        == 180
    )
    # 0 bei gepflegtem Wert (z. B. „unverändert beim Öffnen“) ist eine echte Änderung
    assert werte("Pfeffer", {"haltbarkeit_offen_tage": 0}, shelf_life_opened_days=365)[0] == {
        "haltbarkeit_offen_tage": 0
    }
