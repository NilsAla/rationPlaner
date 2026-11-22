"""Gemeinsames für Hilfsskripte: Importpfad der App und Grocy-Zugang aus Umgebung/.env."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ration"))


def load_env() -> tuple[str, str]:
    """RATION_GROCY_URL und RATION_GROCY_API_KEY aus der Umgebung oder ROOT/.env."""
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"'))
    url = os.environ.get("RATION_GROCY_URL")
    key = os.environ.get("RATION_GROCY_API_KEY")
    if not url or not key:
        sys.exit("RATION_GROCY_URL und RATION_GROCY_API_KEY setzen (Umgebung oder .env).")
    return url, key
