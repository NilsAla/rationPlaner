"""App-Einstellungen aus /data/options.json (HA) bzw. Umgebungsvariablen (lokal)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    grocy_url: str = ""
    grocy_api_key: str = ""
    ai_task_entity: str = ""
    todo_entity: str = ""
    toleranz_default_pct: int = 10
    bon_konfidenz_schwelle: float = 0.85
    data_dir: Path = Path("/data")
    supervisor_token: str = ""

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'ration.db'}"


def load_settings() -> Settings:
    """Liest die App-Optionen. RATION_OPTIONS und RATION_DATA_DIR erlauben lokale Tests."""
    data_dir = Path(os.environ.get("RATION_DATA_DIR", "/data"))
    options_path = Path(os.environ.get("RATION_OPTIONS", data_dir / "options.json"))
    options: dict = {}
    if options_path.is_file():
        options = json.loads(options_path.read_text(encoding="utf-8"))
    known = Settings.__dataclass_fields__.keys()
    return Settings(
        **{k: v for k, v in options.items() if k in known},
        data_dir=data_dir,
        supervisor_token=os.environ.get("SUPERVISOR_TOKEN", ""),
    )
