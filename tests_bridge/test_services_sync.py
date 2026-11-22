"""services.yaml und Übersetzungen passen zu den Aktionen (Service und Felder)."""

import json
from pathlib import Path

import yaml

from custom_components.ration.aktionen import AKTIONEN

BASE = Path(__file__).parent.parent / "custom_components" / "ration"


def _felder(aktion) -> set[str]:
    return {str(k) for k in aktion.schema.schema}


def test_services_yaml_und_uebersetzungen():
    services = yaml.safe_load((BASE / "services.yaml").read_text(encoding="utf-8"))
    for sprache in ("de", "en"):
        tr = json.loads((BASE / "translations" / f"{sprache}.json").read_text(encoding="utf-8"))
        for a in AKTIONEN:
            assert a.name in services, f"{a.name} fehlt in services.yaml"
            felder = set((services[a.name] or {}).get("fields") or {})
            assert felder == _felder(a), f"{a.name}: services.yaml {felder} ≠ Schema {_felder(a)}"
            t = tr["services"].get(a.name)
            assert t and t.get("name"), f"{a.name} ohne Übersetzung ({sprache})"
            assert set(t.get("fields", {})) == felder, f"{a.name}: Übersetzung ({sprache})"
