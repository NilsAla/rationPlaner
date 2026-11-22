# CLAUDE.md – rationPlaner

Vollständiges Konzept: `docs/konzept.md`. Vor jeder Änderung lesen, Entscheidungen dort
nicht stillschweigend aufweichen. Nutzer ist erfahrener Sysadmin (HAOS auf N100, UniFi).

## Architektur

- **Grocy** (HAOS-App) ist Master für Produkte, Chargen, MHD, Einheiten, Rezepte. Nur Backend,
  keine Abhängigkeit von der Grocy-UI. Zugriff ausschließlich über die Grocy-REST-API.
- **ration** (`ration/`, HAOS-App) enthält die gesamte Logik: FastAPI, SQLite unter `/data`,
  Ingress. Hält nur, was Grocy nicht kann (Aliase, Präferenzen, Rezept-Meta, Pläne, Preise,
  Nährwerte, Tageslog, ID-Abgleich).
- **Bridge** (`custom_components/ration/`): Entitäten und Services, delegiert per REST an die
  App (Coordinator pollt, Refresh nach Service-Call). Liefert auch die Küchen-Card aus.
- **Card** (`lovelace/`): Lit, keine Logik.
- **KI** nur über HA `ai_task.generate_data` (Entität aus Option `ai_task_entity`, OpenAI oder
  Anthropic). Kein API-Key in der App. Bilder über `/media` als media-source übergeben.

## Grundregeln

1. **LLM wählt aus, Code rechnet.** Mengen, Ablaufdaten, Toleranz-Skalierung, Nährwerte,
   Preise: immer deterministischer Code. LLM-Antworten nur mit Structured Output, gegen
   Schema und Backend validieren, Rohantwort loggen, IDs statt Namen.
2. **Grocy nur über `InventoryBackend`** (`ration/app/inventory/base.py`). Nur
   `inventory/grocy.py` kennt Grocy-Endpunkte, -IDs, Userfields und Einheitenlogik. Fachcode
   nutzt Domain-Objekte. `FakeBackend` bildet die Grocy-Semantik nach (FEFO, Parent-Produkte,
   Gefrieren) und wird mit denselben Contract-Tests geprüft.
3. **Keine Daten im Repo.** Keine Rezept-, Produkt- oder BLS-Daten committen. Erlaubt sind
   1–3 Testrezepte als Fixtures unter `ration/tests/fixtures/`.
4. **Nichts automatisch übernehmen,** was das LLM an Stammdaten vorschlägt: immer Diff + Review.
5. **API-Vertrag:** App-Endpunkte unter `/api/v1/`. Inkompatible Änderung → Major-Version in
   `ration/app/__init__.py` (`API_VERSION`) und `SUPPORTED_API_MAJOR` der Bridge anheben.
6. **Name ist Platzhalter.** Slug/Domain `ration` nur in `ration/config.yaml` und
   `custom_components/ration/const.py` definieren, nirgends sonst hart codieren.

## Tracking-Modi

`exakt` (Menge + Ablauf je Charge), `menge` (Menge ohne Ablaufdruck), `fuellstand`
(voll/knapp/leer, nie abgebucht), `immer_da` (nie getrackt). Produkte ohne Modus gelten als
`menge`. Gramm-Umrechnung je Produkt ist Pflicht (Nährwerte, Toleranz).

## Konventionen

- Python 3.12, FastAPI, SQLAlchemy 2 + SQLite (WAL), httpx async, durchgehend typisiert.
- `ruff check` und `ruff format` (Konfiguration in `ration/pyproject.toml`), Tests mit pytest.
- Migrationen schlicht halten (kein Alembic, solange es nicht nötig ist).
- Keine Over-Engineering-Abstraktionen außer `InventoryBackend`.
- Commits klein und thematisch, Sprache in Code/Kommentaren: Deutsch für Fachbegriffe ok.

## Lokal entwickeln

```sh
cd ration
pip install -e ".[dev]"
RATION_DATA_DIR=./data uvicorn app.main:app --reload --port 8099
ruff check . && ruff format --check . && pytest -q
```

## Reihenfolge

Gerüst → Phase 0 (Grocy-Technik, Setup-Skript, Probe/Contract-Tests, CSV, BLS) → dünner
Durchstich (Bon → Gekocht → Einkaufsliste) → Phasen 1–6 laut `docs/konzept.md`.

## Offen

- Authentifizierung Bridge → App im internen Netz (Ingress-Requests kommen von 172.30.32.2,
  Bridge-Requests nicht): Token als App-Option, ab Phase 1.
- Interner Hostname der App hängt vom Repo-Hash ab, Bridge fragt die URL im Config Flow ab.
