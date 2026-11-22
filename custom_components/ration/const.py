"""Konstanten der Bridge. DOMAIN ist ein Platzhalter (siehe ration/config.yaml)."""

DOMAIN = "ration"
CONF_URL = "url"
# Interner Hostname der App im hassio-Netz. Bei Installation aus einem Repo
# lautet er <repo-hash>-ration, lokal installiert local-ration.
DEFAULT_URL = "http://local-ration:8099"
SUPPORTED_API_MAJOR = 1

SERVICE_GEKOCHT = "gekocht"
SERVICE_PLAN_ERZEUGEN = "plan_erzeugen"
SERVICE_KATEGORISIEREN = "kategorisieren"
