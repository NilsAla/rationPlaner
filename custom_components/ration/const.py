"""Konstanten der Bridge. DOMAIN ist ein Platzhalter (siehe ration/config.yaml)."""

from datetime import timedelta

DOMAIN = "ration"
CONF_URL = "url"
# Interner Hostname der App im hassio-Netz: <repo-hash>-ration, lokal installiert local-ration.
DEFAULT_URL = "http://local-ration:8099"
SUPPORTED_API_MAJOR = 1
SCAN_INTERVAL = timedelta(minutes=1)

SERVICE_GEKOCHT = "gekocht"
SERVICE_EINKAUFSLISTE = "einkaufsliste_erzeugen"
SERVICE_PLAN_ERZEUGEN = "plan_erzeugen"
SERVICE_KATEGORISIEREN = "kategorisieren"
