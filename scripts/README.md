# Hilfsskripte

Einmalige bzw. administrative Skripte, die gegen eine echte Grocy-Instanz laufen.
URL und API-Key kommen aus `.env` (nicht im Repo).

Geplant (Phase 0):
- `grocy_setup.py` – legt idempotent Lagerorte und Userfields an (`tracking_modus`,
  `kategorie`, `fuellstand`, `toleranz_pct`, Rezept-Userfields). Keine Stammdaten.
- `grocy_probe.py` – Contract-Tests gegen echtes Grocy (Parent-Product-Erfüllung, FEFO,
  Umbuchen in den Gefrierschrank). Dieselben Fälle laufen gegen das `FakeBackend`.
