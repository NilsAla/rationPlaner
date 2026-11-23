# Roadmap und Übergabe (Stand 2026-11-23, Version 0.20.0 / API 1.16)

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

## Geprüft auf Nils' HA (2026-11-23)

- 0.17.0 baut auf Alpine inkl. Pillow, HelloFresh-Haupt- und Schrittbilder kommen an.
- Ältere KI-Anleitungen ohne `original` bleiben ohne Schrittbild (nur Testdaten, egal).

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

### 2. Neue Startseite – erledigt (0.19.0)
- `views/uebersicht.js` neu: Heute groß mit Foto, nächste 6 Tage als Fotokarten (Drag&Drop,
  „→ Tag“ mit 1 Klick, ⇄ Tag wählen mit 2 Klicks, belegte Tage werden getauscht), Plan vorschlagen
  mit Tages- und Wunsch-Chips (Ergebnis mit Foto/kcal/Nutri, übernehmen), läuft bald ab, Reste,
  Vorrat, Zu tun, „Jetzt kochbar“ als Karten. Ohne Plan für heute: kochbare Rezepte per Klick einplanen.
- Lokal mit Playwright geprüft (1400 px und 390 px, kein horizontales Scrollen, keine Konsolenfehler).
  Auf Touch gibt es kein Drag&Drop, dafür die Knöpfe.

### 3. HA-Entitäten über die Bridge – erledigt (0.18.0)
- `GET /api/v1/uebersicht` (heute, morgen, nächste Tage mit kcal/Nutri/Foto/Ampel, läuft ab nach
  `bald_tage`, Reste), `POST /api/v1/plan/{datum}/verschieben {ziel}` (tauscht, falls belegt).
- Bridge: `sensor.ration_gericht_heute/_morgen`, `image.ration_gericht_heute/_morgen`,
  `sensor.ration_laeuft_ab` nutzt `bald_tage`. Ältere App ohne `/uebersicht`: neue Entitäten
  nicht verfügbar, Rest läuft weiter.

### 4. KI-Kosten und zwei KI-Backends – erledigt (0.20.0, API 1.16)
- `app/ki.py`: `KiClient.generate(job, name, anweisung, struktur, anhaenge)`, alle Aufrufe
  umgestellt (bon, import, kochanleitung, kategorisierung, naehrwerte, planer). `ProtokollHA` entfällt.
- Einstellung `ki` (App-DB): `modus` ha/openai, `openai_modell`, `jobs` (je Job modus/entitaet/modell),
  `preise` (€ je 1 Mio. Tokens, Schlüssel = Modell oder AI-Task-Entität). UI: Einstellungen → KI.
- OpenAI: Chat Completions mit `response_format: json_schema` (strict), Schema aus der HA-Struktur
  (`schema_aus_struktur`, optionale Felder als `null`, null wird vor der Rückgabe entfernt), Bilder
  aus `/media` als data-URL, echte `usage`. Key nur als App-Option `openai_api_key`.
- KI-Protokoll: Backend, Tokens (≈ im HA-Modus), Kosten je Aufruf, Summe je Job für 24 h/7/30 Tage
  (`GET /log/ki-kosten`). `GET /ki/status` zeigt je Job, ob er nutzbar ist.
- DB: neue Spalten werden beim Start per `ALTER TABLE` ergänzt (`db.spalten_ergaenzen`).
- **Ungetestet live:** OpenAI-Modus gegen die echte API (lokal nur gemockt). Zum Testen
  `openai_api_key` in den App-Optionen setzen, unter Einstellungen → KI einen Job auf OpenAI stellen.

### 5. Fotos: Rest
- ~~Startseite mit Fotos~~ erledigt (0.19.0).
- ~~Anleitungs-Editor: je Schritt Foto zuordnen/hochladen~~ erledigt (0.19.1): Foto wählen aus
  den Rezeptfotos, hochladen oder Originalschritte (Chips 1…n) zuordnen.
- Sensor-Attribut `foto` + `image.ration_gericht_heute/_morgen` erledigt (0.18.0). Lovelace-Card offen.

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
