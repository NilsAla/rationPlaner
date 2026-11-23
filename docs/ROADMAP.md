# Roadmap und Übergabe (Stand 2026-11-23, Version 0.17.0 / API 1.14)

Diese Datei ist der Übergabepunkt für die Weiterarbeit mit Claude Code direkt im Repo.
Erst `CLAUDE.md` und `docs/konzept.md` lesen, dann hier die offenen Aufträge.

Branch: `claude/project-thread-jm020d`, PR #3 (nicht ohne Nils mergen). Nils' HA trackt diesen
Branch als Add-on-Repo mit Auto-Update: **jede Versionsanhebung in `ration/config.yaml` wird
automatisch ausgerollt.** Version immer an vier Stellen gleich halten: `ration/config.yaml`,
`ration/app/__init__.py` (`__version__`, `API_VERSION`), `ration/pyproject.toml`,
`custom_components/ration/manifest.json`.

## Fertig in PR #3 (Auszug der letzten Pakete)

- **Kochmodus** (0.15.0): KI-Anleitungen als Entwurf in der App-DB, Review/Freigabe, HA-Timer,
  Testlauf `#/kochen/<id>?test=1` (Dry-Run ohne Buchung, ohne HA-Timer, Zeitraffer).
- **Bon** (0.16.0): Strg+V/Drag&Drop, `POST /api/v1/bon/bild` (Base64), Zeilentypen
  artikel/non_food/pfand/leergut/rabatt/storno, Rabatt/Storno verrechnet, Anzahl-Prüfung,
  Summenprüfung. Test-Bon: `scripts/testbon.py` (erzeugt Scan, Foto und Lösung).
- **Schritt-Skalierung**: `kueche.mengen_skalieren` rechnet Mengen im Schritt-Text beim Anzeigen um
  (Rezeptbuch, Kochmodus, `/heute`). Kein gespeicherter Platzhalter.
- **Rezeptquelle**: Spalte, Filter, Badge (HelloFresh, Chefkoch, manuell, Test, Host), im Editor
  setzbar (`RezeptMeta.quelle/url`).
- **Makro-Modus**: `app/naehrwerte.py` (Nährwerte je 100 g, Nutri-Score 2023, Ziel-Passung),
  Planer-Regler gesund/protein/rohkost, kcal-/Protein-Ziel je Mahlzeit, KI-Schätzung als
  Vorschlag mit Review. Doku in `konzept.md` → „Nährwerte und Makros“.
- **Rezeptfotos** (0.17.0): `app/fotos.py`, Bilder unter `/data/fotos` (Pillow, 1600/480 px).
  HelloFresh-Haupt- und Schrittbilder beim Import (JSON-LD `image` + `__NEXT_DATA__` `steps[].images`),
  „⤓ Von der Quelle“ für ältere Importe, Upload am Rezept, je Schritt und als Ergebnisfoto im
  Kochmodus-Abschluss. Rezeptbuch standardmäßig als Karten mit Foto.

## Ungetestet / zuerst prüfen

1. **0.17.0 auf Nils' HA**: Build mit `pillow` auf Alpine (musllinux-Wheels sollten greifen).
   Wenn der Build scheitert: `apk add --no-cache jpeg zlib` bzw. Build-Deps im Dockerfile.
2. **HelloFresh-Bilder live**: Die `__NEXT_DATA__`-Struktur ist angenommen, nicht gegen eine echte
   Seite geprüft (aus der Cloud-Umgebung nicht erreichbar). Ein Rezept importieren bzw. am Rezept
   „⤓ Von der Quelle“ klicken, Ergebnis in `/data/fotos` und im Log prüfen. Falls Schrittbilder
   fehlen: `fotos._hf_schritte`/`_bild_url` an die echte Struktur anpassen, Test `tests/test_fotos.py`.
3. **KI-Anleitungen**: Neues Strukturfeld `original` (Originalschritte je Kochschritt) für
   Schrittbilder. Ältere gespeicherte Anleitungen haben es nicht → kein Schrittbild, bis neu
   generiert oder im Editor zugeordnet (Editor-UI dafür fehlt noch, siehe unten).

## Offene Aufträge (von Nils, 2026-11-23)

### 1. Bon-Live-Test mit Test-Bon voller Hürden
- Bilder liegen in den Projektdateien (`bon-test/testbon-scan.png`, `testbon-foto.jpg`,
  `testbon-loesung.json`), neu erzeugbar mit `python scripts/testbon.py`.
- Gegen die echte KI-Entität: Scan und Foto hochladen (UI: Einbuchen & Bon, Strg+V), Ergebnis mit
  `testbon-loesung.json` vergleichen. **Nicht buchen** (würde echten Bestand ändern).
- Akzeptanz: Summe stimmt (25,15), Pfand/Leergut/Rabatt/Storno/Coupon korrekt typisiert und
  verrechnet, Non-Food nicht vorausgewählt, Gewichtsartikel (Paprika 0,486 kg) korrekt. Schwächen
  im Prompt oder in `bon.py` (`_typ`, `_ziel`, `_anzahl_pruefen`) beheben, Fixture `HUERDEN`
  in `tests/test_bon.py` erweitern.

### 2. Neue Startseite (`views/uebersicht.js` ersetzen)
- Visuell aufgewertet, mit Fotos (Hauptbild via `rezeptbuch[].foto`, `lib.fotoUrl`).
- Inhalte: heute/nächste Tage aus dem Plan (`GET /plan`), „jetzt kochbar“ (`rezeptbuch` mit
  `kochbar`), Vorrat mit „läuft bald ab“ (`/status`, Inventar), Reste.
- **Verschieben**: Gericht per Drag&Drop oder Knopf „→ morgen“ auf einen anderen Tag
  (`PUT /plan/{datum}` + `DELETE /plan/{alt}`, besser ein neuer Endpoint
  `POST /plan/{datum}/verschieben {ziel}` mit Tausch, falls Ziel belegt). Schnell, ohne Dialog.
- **Plan generieren** direkt auf der Startseite (gleiche Logik wie `views/plan.js`, Regler als
  kompakte Chips), Ergebnis als Karten mit Foto, kcal und Nutri-Score.
- Akzeptanz: Desktop und Handy (390 px) ohne horizontales Scrollen, keine Konsolenfehler,
  Verschieben in ≤ 2 Klicks.

### 3. HA-Entitäten über die Bridge (`custom_components/ration/`)
- Neu: `sensor.ration_gericht_heute`, `sensor.ration_gericht_morgen` (State = Name, Attribute:
  rezept_id, portionen, status, kcal, nutri, foto-URL, zutaten, fehlend),
  `sensor.ration_laeuft_ab` (State = Anzahl in `bald_tage`, Attribut Liste mit Name, Menge, MHD).
- App-Seite: Endpoint z. B. `GET /api/v1/uebersicht` bzw. `/heute` um `morgen` erweitern;
  Bridge-Coordinator pollt, Übersetzungen in `translations/de.json`/`en.json`.
- Bei inkompatibler API-Änderung Major anheben (`SUPPORTED_API_MAJOR`), sonst Minor.
- Akzeptanz: Bridge-Tests (`tests_bridge`) grün, Entitäten erscheinen nach Update ohne Neuanlage.

### 4. KI-Kosten und zwei KI-Backends (Entscheidung Nils)
- **Modus HA** (Standard, wie bisher): `ai_task.generate_data`. Tokens nur **geschätzt**
  (Zeichen/≈3,5 für Text inkl. Schema und Antwort, pauschal je Bild), im Protokoll als „≈“.
- **Modus OpenAI direkt**: API-Key als `password`-Option in `ration/config.yaml` (Schema `password`),
  nie loggen, nie ans Frontend (Einstellungen zeigen nur „gesetzt“). Responses/Chat-API mit
  Structured Output (JSON Schema aus dem bestehenden `structure` ableiten), **echte Usage** aus der
  Antwort speichern.
- Schalter global (App-Einstellung), je Job überschreibbar: bon, import, kochanleitung,
  kategorisierung, naehrwerte, planer. Je Job im HA-Modus eine AI-Task-Entität, im OpenAI-Modus
  ein Modellname. Zuordnung in der App-DB (`einstellung`), nicht in den HA-Optionen.
- Preistabelle je Modell (€ je 1 Mio. Input/Output-Tokens), in den Einstellungen pflegbar.
- KI-Protokoll (`views/log_ki.js`, `app/protokoll.py`): Kosten je Aufruf, Summe je Job und
  Zeitraum (Tag/Woche/Monat).
- Umsetzung: eine Abstraktion `KiClient.generate(job, name, anweisung, struktur, anhaenge)` in
  `app/ha.py` bzw. neu `app/ki.py`, alle bisherigen `ha.ai_task(...)`-Aufrufe darauf umstellen.
  Regeln aus CLAUDE.md bleiben (validieren, Rohantwort loggen, IDs statt Namen).
- **CLAUDE.md-Regel „KI nur über HA, kein API-Key in der App“ dabei anpassen** (bewusst aufgehoben).

### 5. Fotos: Rest
- Startseite mit Fotos (siehe 2).
- Anleitungs-Editor (`views/anleitung.js`): je Schritt Foto zuordnen/hochladen (Feld `foto` bzw.
  `original` im Schritt, Backend kann das schon).
- Optional: Fotos in der Lovelace-Card / Sensor-Attribut `foto`.

## Getroffene Entscheidungen (nicht stillschweigend ändern)

- Grocy bleibt Master, Zugriff nur über `InventoryBackend`. Fotos, Nährwerte, Quellen,
  Anleitungen liegen in der App-DB bzw. `/data`.
- LLM wählt aus, Code rechnet. Nichts aus der KI automatisch übernehmen (Vorschläge + Review).
- Gesundheitsfaktor = Nutri-Score 2023 + Ziel-Passung (Begründung in `konzept.md`).
- Kalorien-Ziel vorerst für **eine** Mahlzeit, keine 3-Mahlzeiten-Pläne.
- Fotos sind ausdrücklich erwünscht (frühere Regel „keine Bilder“ aufgehoben).
- In Nils' Grocy nur Testdaten anlegen, nichts löschen ohne Rückfrage, Test-Bon nicht buchen,
  App-Optionen in HA ändert nur Nils.

## Lokal testen

```sh
cd ration && pip install -e ".[dev]"
ruff check . && ruff format --check . && python -m pytest -q   # auch im Repo-Root ruff laufen lassen
RATION_DATA_DIR=./data uvicorn app.main:app --reload --port 8099
```

Grocy für Contract-Tests: `docker run -d -p 9283:80 linuxserver/grocy` (API-Key in Grocy anlegen,
nie committen). UI-Checks mit Playwright (Desktop 1400 px und Handy 390 px, Konsolenfehler prüfen).
