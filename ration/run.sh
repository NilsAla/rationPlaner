#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e
bashio::log.info "Starte rationPlaner"
cd /opt/ration
exec uvicorn app.main:app --host 0.0.0.0 --port 8099 --proxy-headers --forwarded-allow-ips="172.30.32.2"
