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
- Lösung liegt als Fixture unter `ration/tests/fixtures/testbon-loesung.json` (aus
  `scripts/testbon.py`, Seed 42). Für den Zeilenvergleich braucht jede Zeile noch strukturierte
  Sollwerte (Menge, Einheit, Anzahl) zusätzlich zum Text in `erwartung`.

**Ergebnis erster Live-Test (Scan, 2026-11-23), offene Fixes:**
- [ ] **Mehrfachmengen:** SPAGHETTI 2,97 € = 3 × 0,99 € → 1500 g (erkannt: 500 g). TOMATEN PASS.
  1,38 € vor Coupon = 2 × 500 g (erkannt: 1 ×). Anzahl im Code aus Betrag ÷ Einzelpreis bzw. aus
  der Zeile „N x“ ableiten. Im Zweifel Review statt raten.
- [ ] **Gebinde:** MINERALW. 12X0,7L → 8400 ml. Muster `NxM(G|KG|ML|L)` im Code parsen, nicht per KI.
- [ ] **Einheiten:** ml/l für Flüssiges statt g.
- [ ] **Lesefehler:** EINWEGHANDSCHUHE wurde als „EINWEG SCHUHE“ gelesen und zu „Einweggeschirr“.
  Bon-Text originalgetreu übernehmen, Namensvorschlag nur aus dem Bon-Text.
- [ ] **Annahmen:** Knoblauch bekam stillschweigend 50 g Standardpackung. Solche Werte als
  „Annahme“ markieren (sichtbar im Review).
- [ ] **Pfand und Storno** mitprüfen.
- **Akzeptanz:** Ein automatischer Test vergleicht jede Zeile gegen die Fixture (Menge, Einheit,
  Anzahl), alle grün. Danach dasselbe mit der Foto-Variante (`testbon-foto.jpg`).

### 2. Neue Startseite – erledigt (0.19.0)
- `views/uebersicht.js` neu: Heute groß mit Foto, nächste 6 Tage als Fotokarten (Drag&Drop,
  „→ Tag“ mit 1 Klick, ⇄ Tag wählen mit 2 Klicks, belegte Tage werden getauscht), Plan vorschlagen
  mit Tages- und Wunsch-Chips (Ergebnis mit Foto/kcal/Nutri, übernehmen), läuft bald ab, Reste,
  Vorrat, Zu tun, „Jetzt kochbar“ als Karten. Ohne Plan für heute: kochbare Rezepte per Klick einplanen.
- Lokal mit Playwright geprüft (1400 px und 390 px, kein horizontales Scrollen, keine Konsolenfehler).
  Auf Touch gibt es kein Drag&Drop, dafür die Knöpfe.

**Neu (offen): Bereich „Spontan kochbar“**
- 3–6 Rezeptkarten mit Foto, deren Zutaten zu ≥ 95 % vorrätig sind, gewichtet nach Menge (Gramm).
  Basics wie Salz und Öl zählen nicht (Modus `immer_da`/`fuellstand` bzw. Basics-Liste).
- Fehlende Kleinigkeiten stehen auf der Karte („fehlt: 1 Zwiebel“).
- Sortierung: nach Abdeckung, dann nach bald ablaufenden Zutaten.
- Je Karte zwei Knöpfe: „Statt heute“ setzt das Rezept auf heute und verschiebt das bisher
  geplante Gericht (Tausch-Logik von `POST /plan/{datum}/verschieben`). „Zusätzlich“ trägt es ein,
  ohne den Plan zu ändern.
- Schwellwert (Standard 95 %) in den Einstellungen einstellbar.
- Akzeptanz: Abdeckung rechnet der Code (Test mit FakeBackend), Desktop und 390 px ohne
  horizontales Scrollen, beide Knöpfe mit 1 Klick.

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

### 6. Packungsgrößen clever (offen)
1. **Varianten erweitern:** Marke/Hersteller, Händler, EAN (optional), zuletzt gesehen am,
   Häufigkeit. Liegt in der App-DB (`Packung`), Grocy bleibt Master für die Standard-Packung.
2. **Bon-Import erkennt Größen** aus Kürzeln wie „KIDNEYB. 400G“ oder „2x250g“ (im Code geparst)
   und gleicht sie mit den Varianten ab. Bekannte Größe: Häufigkeit hochzählen, zuletzt gesehen
   setzen. Neue Größe: als Variante vorschlagen (Review, nichts automatisch übernehmen).
3. **KI-Modus „Packungsgrößen analysieren“** (manuell startbar, eigener KI-Job): wertet
   Bon-Historie, Angebote und KI-Wissen aus und schlägt je Produkt die gängigen Größen und den
   Standard vor (die häufigste in den letzten X Monaten, vom Code bestimmt). Ergebnis als
   Diff-Review mit Übernehmen/Verwerfen, Kosten im KI-Protokoll.
4. **Einkaufsliste** nutzt weiter die Kombination mit dem geringsten Überschuss, bevorzugt aber
   Varianten, die tatsächlich gekauft werden (Häufigkeit/zuletzt gesehen als Tie-Breaker).
- Akzeptanz: Tests für Größen-Parser (400G, 2x250g, 12X0,7L, 1,5KG), Abgleich und
  Häufigkeitszählung, Einkaufslisten-Auswahl mit Präferenz. Kein Stammdaten-Update ohne Review.

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
