# Küchen-Card (`ration-card.js`)

Custom Lovelace Card in vanilla Lit, ohne Build-Overkill. Enthält keine Logik: liest
Entitäten der Bridge und ruft Services auf.

Auslieferung: Die Bridge stellt die Datei per Static Path bereit und registriert sie als
Lovelace-Resource. Quelle liegt hier, beim Release wird sie nach
`custom_components/ration/frontend/` kopiert. Umsetzung ab Phase 1.
