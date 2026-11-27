# Roadmap und Übergabe (Stand 2026-11-27, Version 0.35.0 / API 1.30)

Diese Datei ist der Übergabepunkt für die Weiterarbeit mit Claude Code direkt im Repo.
Erst `CLAUDE.md` und `docs/konzept.md` lesen, dann hier die offenen Aufträge.

Arbeit läuft auf `main`; nach jedem Push auch `git push origin main:claude/project-thread-jm020d`
(beide Branches deckungsgleich). Nils' HA trackt `claude/project-thread-jm020d` als Add-on-Repo
mit Auto-Update: **jede Versionsanhebung in `ration/config.yaml` wird ausgerollt.** Der Store
merkt neue Versionen manchmal spät; per HA-MCP `ha_manage_app(action="check_updates")`, dann
`action="update"` nur für `bd5fe677_ration`. Version immer an vier Stellen gleich halten: `ration/config.yaml`,
`ration/app/__init__.py` (`__version__`, `API_VERSION`), `ration/pyproject.toml`,
`custom_components/ration/manifest.json`.

## ▶ Als Nächstes (Stand 2026-11-23 abends, Nils schläft)

**Stand auf Nils' HA:** 0.28.5 läuft. KI-Zuordnung (Einstellungen → KI, App-DB):
Kategorisierung, Packungsgrößen, Bon → `ai_task.openai_stammdaten` (gpt-4.1, Temperatur 0,2,
max 8000 Tokens, ohne Websuche/Code-Interpreter/Links); Planer, Import, Kochanleitung,
Nährwerte → `ai_task.openai_ai_task` (gpt-4o-mini, „Recommended“). Preistabelle leer.
Wochenende: 3 Gerichte geplant, Einkaufsliste schon in `todo.einkaufsliste` geschrieben.

### 1. Mit Nils zuerst (er testet, Claude wertet per HA-MCP aus)
- [ ] **Kategorisierung neu laufen lassen** („Auch schon kategorisierte prüfen“), danach Claude:
  KI-Log über `/api/v1/log/ki` und offene `/api/v1/vorschlaege` durchgehen, Empfehlung je Gruppe
  (übernehmen/ablehnen). Erwartung mit 0.28.4/0.28.5: deutlich weniger Vorschläge, keine
  „leer → 0“, keine Kürzungen > Faktor 4. Noch offene Altlasten: Basmati-Reis ohne Modus und
  Haltbarkeit 0; Ingwer als „Gewürze“ statt „Gemüse“; Knoblauch 14 Tage (zu knapp, eher 60);
  Brokkoli frisch lief früher als „Tiefkühl“.
- [ ] **Bon-Live-Nachtest** mit Test-Bon (Scan und Foto, nicht buchen), jetzt mit gpt-4.1.
  Prüfen: Mengenzeilen als `mengenzeile`, Gebinde, ml, Namen, Annahmen. Auswertung über KI-Log.
- [ ] **Barcode-Scan am Handy** (Android live, iPhone per Foto → `/barcode/lesen`), MHD-Schätzung.
- [ ] **Bon-Kamera** in der Companion-App (In-App-Kamera, HTTPS nötig, Kamera-Berechtigung).
- [ ] **Ausgaben** mit einem echten Bon; „Bereits gebuchte Bons übernehmen“.
- [ ] **Import** nach dem Füllwort-Fix (0.25.2).
- [ ] Optional: OpenAI-Direktmodus mit eigenem Key (bisher nur gemockt getestet).
- [ ] Preise für gpt-4.1 / gpt-4o-mini in Einstellungen → KI eintragen (Nils), sonst nur Tokens.

- [ ] **Rezept generieren (0.35.0):** Menü „Rezept generieren“, Wunsch + Portionen + Aufwand
  (+ optional Vorrat), 1–2 Änderungswünsche, „Übernehmen“ → Import-Vorschau → in Grocy. Vorher
  in Einstellungen → KI dem Job „Rezept generieren“ ein kreatives Modell geben (OpenAI direkt).
  Danach Claude: KI-Log Job `rezept` (Dauer, Kosten, Reparaturrunden, Zutatenzeilen sauber
  parsebar?), Import-Zuordnung der generierten Zutaten (Katalognamen getroffen?).
- [ ] **Angebote (0.32.0):** Angebote → „Markt wählen“ (PLZ), „Jetzt abrufen“, KI-Treffer
  prüfen und bestätigen. Danach Claude: KI-Log Job `angebote` auswerten (Fehlgriffe wie Chips →
  Kartoffeln?), `/api/v1/angebote/status`, Preistabelle. KI-Job ggf. auf gpt-4.1 stellen.

### 2. Bauen (Reihenfolge nach Nutzen)
0. **Angebotstracker weiter** (siehe Punkt 13): Kaufland/Lidl (Schwarz-Leaflets, Seitenbilder →
   Vision), dann K+K und EDEKA (Blätterkatalog). Healthcheck-Sensor in der Bridge.
1. **Einkaufsliste abgleichen statt nur anhängen** (Nils' Frage: „was passiert, wenn ich Reis
   einbuche?“). Heute schreibt `POST /einkaufsliste` nur hinzu, nichts wird entfernt. Ziel:
   Einträge, die die App selbst geschrieben hat (in `einkauf_eintrag` merken, mit Todo-UID bzw.
   Text), beim nächsten Erzeugen aktualisieren oder per `todo.remove_item` entfernen, wenn Bestand
   oder Ersatz (Basmati statt Jasmin) reicht. Eigene Einträge von Nils nie anfassen. Optional
   automatisch nach Einbuchen/Bon. Tests mit FakeHomeAssistant (`todo_items`/neue `todo_remove`).
2. **Lovelace-Card** (`lovelace/`, Lit, keine Logik, von der Bridge ausgeliefert): Gericht
   heute/morgen mit Foto (`image.ration_gericht_*`), fehlt/kochbar, „läuft bald ab“, Knöpfe
   Gekocht und Einkaufsliste (Services). Bisher gibt es nur `lovelace/README.md`.
3. **Ersatz verfeinern:** über verschiedene Einheiten (Gramm-Umrechnung), Ausnahme je Rezept
   („nur genau dieses Produkt“), Hinweis „mit Fusilli“ auch im Kochmodus-Schritt.
4. **Doppelte Rezeptzeilen nach Merge** (z. B. Salz zweimal nach „Geschmack Salz“ → Salz):
   beim Merge gleiche Produkte im Rezept zusammenfassen (Menge addieren) statt doppelt.
5. **KI-Plausibilität weiter:** Richtwerte auch für „geöffnet“ im Code (heute nur im Prompt);
   Kategorie-Guard (frisch ≠ Tiefkühl, frischer Ingwer ≠ Gewürz).

### 3. Gelernt heute (nicht wiederholen)
- KI-Werte nie ungeprüft vorschlagen: gpt-4o-mini lieferte Monate statt Tage und 0 = „heute“.
  Jede neue KI-Ausgabe bekommt Code-Guards und einen Test mit echten Antworten aus dem KI-Log.
- Vor dem Löschen in Grocy prüfen, ob das Produkt in Rezepten steckt; lieber mergen.
- Befehlsketten für Commit/Push mit `set -e`, sonst landet ein Commit trotz rotem Lint im Push.

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
- [x] **Mehrfachmengen:** SPAGHETTI 2,97 € = 3 × 0,99 € → 1500 g (erkannt: 500 g). TOMATEN PASS.
  1,38 € vor Coupon = 2 × 500 g (erkannt: 1 ×). Anzahl im Code aus Betrag ÷ Einzelpreis bzw. aus
  der Zeile „N x“ ableiten. Im Zweifel Review statt raten.
- [x] **Gebinde:** MINERALW. 12X0,7L → 8400 ml. Muster `NxM(G|KG|ML|L)` im Code parsen, nicht per KI.
- [x] **Einheiten:** ml/l für Flüssiges statt g.
- [x] **Lesefehler:** EINWEGHANDSCHUHE wurde als „EINWEG SCHUHE“ gelesen und zu „Einweggeschirr“.
  Bon-Text originalgetreu übernehmen, Namensvorschlag nur aus dem Bon-Text.
- [x] **Annahmen:** Knoblauch bekam stillschweigend 50 g Standardpackung. Solche Werte als
  „Annahme“ markieren (sichtbar im Review).
- [x] **Pfand und Storno** mitprüfen.
- **Akzeptanz:** Ein automatischer Test vergleicht jede Zeile gegen die Fixture (Menge, Einheit,
  Anzahl), alle grün. Danach dasselbe mit der Foto-Variante (`testbon-foto.jpg`).
- **Umgesetzt (0.21.0):** `tests/test_bon_live.py` (KI-Antwort mit den Live-Fehlern gegen
  `fixtures/testbon-loesung.json`, Sollwerte je Zeile jetzt in der Fixture), grün. Mengenzeilen
  („3 x 0,99“, „0,486 kg x 3,99“) kommen als typ `mengenzeile`, der Code ordnet sie über den
  Zeilenpreis der Ware davor/danach zu, unklar → `zweifel` (Review). Größen und Gebinde parst
  `groesse_aus_text` (g/ml/Stück), Bon-Text vor KI. Neue Produkte mit Einheit g/ml/Stück.
  KI-Namen, die nicht zum Bon-Text passen (`name_passt`), werden verworfen. Standard-Packung ohne
  Aufdruck = „Annahme“ (nicht vorausgewählt); bei bekanntem Alias gilt die zuletzt bestätigte Menge.
- **Offen: Live-Nachtest** mit Scan und Foto gegen die echte KI (Nils).

### 2. Neue Startseite – erledigt (0.19.0)
- `views/uebersicht.js` neu: Heute groß mit Foto, nächste 6 Tage als Fotokarten (Drag&Drop,
  „→ Tag“ mit 1 Klick, ⇄ Tag wählen mit 2 Klicks, belegte Tage werden getauscht), Plan vorschlagen
  mit Tages- und Wunsch-Chips (Ergebnis mit Foto/kcal/Nutri, übernehmen), läuft bald ab, Reste,
  Vorrat, Zu tun, „Jetzt kochbar“ als Karten. Ohne Plan für heute: kochbare Rezepte per Klick einplanen.
- Lokal mit Playwright geprüft (1400 px und 390 px, kein horizontales Scrollen, keine Konsolenfehler).
  Auf Touch gibt es kein Drag&Drop, dafür die Knöpfe.

**Bereich „Spontan kochbar“ – erledigt (0.22.0):** `GET /spontan`, `POST /plan/spontan`, Einstellung
`anzeige.spontan_pct`. „Zusätzlich“ öffnet den Kochmodus für das Rezept (der Plan hat einen
Eintrag je Tag, ändert sich also nicht; Abbuchen wie gewohnt über „Gekocht“).
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

### 6. Packungsgrößen clever – erledigt (0.23.0, API 1.18)
- Umsetzung: `app/packungen.py` (Kaufhistorie `packung_kauf`, `kauf_merken`, Varianten-Pflege
  `PATCH /packungen/{id}`), `Packung` mit marke/haendler/ean/zuletzt/haeufigkeit/bestaetigt.
  Bon-Buchung zählt jede Größe, die auf dem Bon stand oder bestätigt ist; neue Größe →
  unbestätigte Variante + Vorschlag `variante` (KI-Vorschläge-Seite). KI-Lauf über
  `POST /kategorisieren {packungen: true, monate}` (KI-Job `packungen`): Standard = häufigste
  Größe laut Bons im Zeitraum (Code), sonst KI-typisch nur ohne Standard; gängige Größen als
  Varianten-Vorschläge. `waehle_packungen`: nach Überhang bevorzugt gekaufte Größen.
- Ursprüngliche Anforderung:
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

### 7. Kostenplanung / Ausgaben-Recap – erledigt (0.24.0, API 1.19)
- Umsetzung: `app/ausgaben.py`. Beim Buchen eines Bons werden **alle** Zeilen (ohne Storno,
  Pfand/Leergut als „Pfand“) zu `ausgabe`-Zeilen, ins Inventar geht nur, was angehakt ist.
  Kategorie: gemerkter Bon-Text (`ausgabe_kategorie_alias`) > Produkt-Kategorie > KI-Feld
  `kategorie` im Bon-Schema > Drogerie/Sonstiges. Geplant: Produkt auf der Einkaufsliste der 7 Tage
  davor (`einkauf_eintrag`, gespeichert bei `POST /einkaufsliste`) oder Zutat eines Gerichts der
  7 Tage danach. Seite „Ausgaben“ (Vorrat → Ausgaben), Kategorie und geplant je Zeile änderbar.
  Recap am Monatsanfang als HA-Hinweis (Hintergrundschleife, nur im Add-on). Alte, schon gebuchte
  Bons: Knopf „Bereits gebuchte Bons übernehmen“ (`POST /ausgaben/neu-berechnen`, ohne
  Zuordnung der damals gebuchten Produkte, nur KI-Treffer).
- Ursprüngliche Anforderung:
- Grundlage sind **alle** Bon-Zeilen (auch Non-Food, Snacks, Alkohol), unabhängig davon, ob sie
  eingebucht werden. Diese Ausgaben tauchen **nicht** im Inventar auf (Verbrauchen wäre zu
  kompliziert), sie liegen nur in der App-DB (Ausgaben-Tabelle je Bon-Zeile, Betrag nach
  Rabatt/Storno, Pfand/Leergut getrennt).
- Kategorie je Zeile: Gemüse, Obst, Snacks, Getränke, Alkohol, Drogerie, Haushalt usw.
  Gebuchte Produkte nehmen ihre Produkt-Kategorie, der Rest kommt per KI-Vorschlag beim Bon-Lesen
  (Feld im Structured Output). Kategorie je Bon-Text merken (wie Alias), damit sie beim nächsten
  Mal ohne KI feststeht. Im Review änderbar.
- **Monats-Recap** automatisch (z. B. am 1. als HA-Hinweis): Summe, je Kategorie, je Markt,
  Vergleich zum Vormonat. Rechnet der Code, keine KI.
- **Geplant vs. spontan**: Eine Bon-Zeile gilt als geplant, wenn ihr Produkt auf der erzeugten
  Einkaufsliste bzw. im Bedarf eines geplanten Gerichts (Plan der Kaufwoche) stand, sonst als
  spontan. Dafür Einkaufslisten-Positionen mit Datum speichern.
- Seite „Ausgaben“: Monat wählbar, Balken je Kategorie, Anteil geplant/spontan, Top-Spontankäufe,
  Verlauf über Monate. Desktop und 390 px.
- Akzeptanz: Tests für Kategorisierung per gemerktem Bon-Text, Recap-Rechnung, Zuordnung
  geplant/spontan; Bon-Zeilen ohne Buchung landen trotzdem in den Ausgaben.

### 8. Barcode-Scan zum Einbuchen – erledigt (0.25.0, API 1.20)
- Umsetzung: Seite Vorrat → „Scannen“ (`views/scannen.js`), `app/barcode.py`.
  Scannen live per `BarcodeDetector` in der In-App-Kamera (`lib.kamera`, Android/Chrome); ohne
  BarcodeDetector (iPhone) Foto → `POST /barcode/lesen` (pyzbar + libzbar, `apk add zbar` im
  Dockerfile, im Add-on-Image geprüft); ohne Kamera Eingabe der EAN. `GET /barcode/{ean}`:
  Grocy-Barcode (`InventoryBackend.find_by_barcode/add_barcode`, Contract-Case `barcode`, gegen
  Grocy 4.7.1 grün) bzw. Variante mit EAN, sonst Open Food Facts (Cache 30 Tage, User-Agent).
  Vorschläge über Namen und OFF-Kategorien. `POST /barcode/einbuchen`: Barcode merken,
  Größe als Variante (mit EAN/Marke), Bestand anlegen, OFF-Nährwerte nur als Vorschlag.
  Mehrfach-Scan: gleicher Code zählt hoch, Liste bleibt im Browser bis zum Einbuchen.
- Ursprüngliche Anforderung:
- Ziel: Vorhandenes per Handy erfassen („2 Packungen Nudeln im Schrank“): Barcode scannen →
  Produkt und Packungsgröße erkennen → Anzahl wählen → einbuchen.
- **Scannen im Browser:** `BarcodeDetector` (Chrome/Android) mit Fallback auf eine
  JS-Bibliothek (ZXing, lokal ausgeliefert, kein CDN, da Ingress). iOS-Safari kann
  `BarcodeDetector` nicht → Fallback nötig. Manuelle EAN-Eingabe als letzte Option.
- **Abgleich:** zuerst bekannte EAN (Grocy `product_barcodes` über `InventoryBackend`, neue
  Methoden `find_by_barcode`/`add_barcode`; Varianten aus Punkt 6 mit EAN). Unbekannt →
  **Open Food Facts** (`https://world.openfoodfacts.org/api/v2/product/<ean>.json`, kostenlos,
  ohne Key, User-Agent setzen): Name, Marke, `quantity`/`product_quantity` (+ Einheit),
  Kategorien, Nährwerte je 100 g. Größe parst der Code (gleicher Parser wie beim Bon).
- Vorschlag: vorhandenes Produkt (Name/Kategorie-Ähnlichkeit) oder neues Produkt anlegen,
  EAN merken, Packungsgröße als Variante, Nährwerte als Vorschlag (Review, nichts automatisch).
- Mehrfach-Scan-Modus: mehrere Artikel nacheinander, am Ende eine Liste zum Einbuchen
  (MHD je Zeile optional, sonst Standard-Haltbarkeit).
- Akzeptanz: Tests mit gemockter OFF-Antwort (Größe, Marke, Nährwerte), Barcode-Abgleich im
  FakeBackend und Contract-Test gegen Grocy; UI am Handy (390 px) mit Kamera, ohne Kamera per Eingabe.

### 9. Personen je Tag im Planer – erledigt (0.26.0, API 1.21, Nils 2026-11-23)
- `POST /plan/erzeugen {personen: {datum: n}}`, Standard je Wochentag in den Einstellungen
  (`planer.personen_je_wochentag`, sonst `planer.portionen`). Portionen = Personen des Tages,
  vom Code gesetzt (KI-Angabe wird überschrieben). UI: Planer, Startseite, Einstellungen.

### 10. Austauschbare Zutaten – erledigt (0.27.0, API 1.22, Nils 2026-11-23)
- Varianten desselben Parent-Produkts mit gleicher Bestandseinheit ersetzen sich (Fusilli statt
  Spaghetti): `kueche.ersatz_karte`, genutzt in Rezeptbuch/kochbar, Rezept-Ampel (`ersatz`),
  Startseite, Spontan kochbar, Planer (Lager: eigene Chargen zuerst), Einkaufsliste (Ersatz-
  Bestand wird reserviert, nicht doppelt gezählt) und „Gekocht“ (bucht den Ersatz ab, Name
  „Fusilli (statt Spaghetti)“). Pro Gruppe abschaltbar: Einstellung `ersatz.gesperrt`
  (Einstellungen → Austauschbare Zutaten). Gruppen entstehen über „Variante von“ am Produkt.
- 0.28.0: KI schlägt Austauschgruppen im Kategorisierungslauf vor (`gruppen: true`, Vorschlag
  `variante_von` je Produkt). Code prüft gleiche Einheit, nur eine Ebene (Grocy), keine Produkte
  mit eigenen Varianten; neues Oberprodukt wird erst beim Übernehmen angelegt.
- Offen/Idee: Ersatz über verschiedene Einheiten (Gramm-Umrechnung), Ausnahme je Rezept.

### 11. MHD beim Einbuchen ohne Datum – erledigt (0.27.1)
- Grocy setzte bei Standard-Haltbarkeit 0 das MHD auf heute. `app/haltbarkeit.py` schätzt die
  Tage aus Kategorie, Name (Nudeln 540, Konserven 730, Milch 10 …) oder OFF-Kategorie und legt
  `haltbarkeit_tage` als Vorschlag fürs Produkt an. Gilt für Scan, Bon und Einbuchen von Hand.

### 12. KI-Stammdaten plausibel – erledigt (0.28.1)
- Live: Haltbarkeit 0 (Reis, Pfeffer), 12 statt 365/540 (Monate statt Tage: Brühe, Pesto),
  Joghurt-Packung 150 g. Jetzt: Prompt mit Richtwerten „in Tagen, nie 0“; Code-Guard
  `haltbarkeit_pruefen` (0 raus, Abweichung > Faktor 4 vom Richtwert raus, „unbegrenzt“ nur bei
  Lagerware, geöffnet > ungeöffnet raus, < 30 % Abweichung = Rauschen), KI-Standardpackung nur
  ohne gepflegte Packung und ohne Bon-Käufe. Neue Vorschläge ersetzen ältere offene desselben Felds.

### 13. Angebotstracker – Penny erledigt (0.32.0, API 1.28, Nils 2026-11-25)
- Grundlage: Claude-Doc „Prospekt-Quellen für rationPlaner“ (https://claude.ai/artifact/B2qpmJsEWDqc6oegSfuvfD):
  alle Händler ohne Login und ohne Headless Browser, Empfehlung Penny zuerst.
- Umsetzung: `app/angebote.py` (Tabellen `angebot`, `angebot_alias`, `angebot_abruf`, Spalte
  `preis.angebot_id`), Seite Küche → Angebote (`views/angebote.js`), Einstellung
  `angebote.penny_markt` (wwIdent aus der Marktsuche). Endpunkte: `GET /angebote`,
  `GET /angebote/status`, `POST /angebote/abrufen`, `POST /angebote/ki`,
  `POST /angebote/{id}/zuordnen`, `POST /angebote/bestaetigen`, `GET /angebote/maerkte/penny?q=`.
- Lokal live gegen penny.de geprüft (KW 48, Emsdetten: 344 Angebote, 293 Lebensmittel,
  Gültigkeit Mo/Do/Fr–Sa, 288 mit erkannter Größe, Rest wie „3 x 100 Blatt“). Tests mit
  nachgebauten Antworten (`tests/test_angebote.py`). KI-Zuordnung live noch ungetestet.
- 0.32.1: Penny-Abschnitte ohne Lebensmittel werden nicht abgerufen (`data-category-type`
  nonfood plus Sperrliste `PENNY_OHNE`: Drogerie, Haushalt, Pflanzen, Kinderwelt, Weihnachten,
  Black Week). KW 48: 264 statt 344 Angebote, 16 statt 25 Abrufe.
- 0.32.2 nach erstem Live-Lauf (gpt-4o-mini, Nils warf ~2/3 der Treffer raus: Mandeln → Butter,
  Rouladen → Chorizo, Fruchtessig → Olivenöl; Hack/Tomaten/Nudeln kamen als Name statt ID):
  KI nennt erst `sorte` und `kategorie`, `produkt_id` als Auswahlliste, Code-Guard
  `ki_pruefen` (Kategorie muss passen, exakter Produktname als ID-Ersatz), Grund sichtbar als
  `ki_info`. „KI neu zuordnen“ (`POST /angebote/ki?neu=true`) fragt Unbestätigtes neu.
  Empfehlung: Job `angebote` auf `ai_task.openai_stammdaten` (gpt-4.1) stellen.
- 0.33.0 **Kaufland ohne Bilderkennung:** `filiale.kaufland.de/angebote/uebersicht.html`
  ist serverseitig gerendert (Preis, Streichpreis, Card-Preis, Menge, Grundpreis). Filiale per
  Cookie `x-aem-variant=DE8360` (geprüft: regionale Artikel wie Böke, Grünkohl), Filialsuche
  über `.klstorefinder.json`. Nur Hauptkategorien `cat_01`–`cat_08` (decken die Lebensmittel
  der Themen-Abschnitte ab), Gültigkeit aus „Gültig vom … bis …“. Nächste Woche zeigt Kaufland
  anfangs nur „Vorwerbung“ ohne Kategorien, die wird übersprungen. Live KW 48: 317 Angebote.
  Risiko: Ignoriert Kaufland das Cookie, kommt still die Standardfiliale.
- 0.33.1/0.33.2: Pflanzen-Wortliste eingebaut und auf Nils' Wunsch wieder entfernt
  (Grundregel 7 in CLAUDE.md: keine White-/Blacklists für Einzelfälle).
- 0.34.0 **Namensregel vor der KI** (`namensregel`, Zuordnung `code`, „🔤“ in der Liste):
  Kopfwort = letztes großgeschriebenes Wort (Marken in Großbuchstaben fallen weg,
  Bindestrich-Wörter zählen als eins). Gleich = gleiches Kopfwort wie das Produkt, Variante =
  Kopf endet auf das Produkt-Kopfwort (Spitzpaprika → Paprika). Zusatzwörter des Produkts
  müssen im Titel stehen. Mehrdeutig → KI. Wie KI-Treffer nur Vorschlag (✓ nötig).
  Live KW 48 mit Nils' Katalog: Penny 15/264, Kaufland 20/317 ohne KI; daneben etwa 5
  (Rama mit Butter, Balsamico Zwiebeln, Sprühsahne, Instant-Nudeln, Marzipankartoffeln).
- Offen:
  1. ~~Kaufland~~ (0.33.0, HTML). **Lidl** (Übersicht parsen, Food-Prospekt): erst prüfen, ob
     lidl.de ähnlich gerendert ist, sonst Seitenbilder + `keyWords` an einen Vision-Job.
  2. **K+K** (`kundk_kw{WW}`, Text je Seite als Hilfe) und **EDEKA** (Blätterkatalog-Slot von
     der Marktseite lesen, PDF/Seitenbild).
  3. Bridge: `binary_sensor`/`sensor` für den Healthcheck je Händler (heute nur HA-Hinweis).
  4. Einkaufsliste/Startseite: „im Angebot bei Penny bis Sa“ an Positionen zeigen.

## Getroffene Entscheidungen (nicht stillschweigend ändern)

- **Angebote nur per Kategorie der Quelle filtern** (Nils 2026-11-25): keine Wortlisten auf
  Titeln („Gesteck“, „Kranz“ …), das ist ein Loch ohne Boden. Was in einer gemischten
  Kategorie durchrutscht (Kaufland „Obst, Gemüse, Pflanzen“), bleibt drin; die KI ordnet es
  keinem Produkt zu.

- Grocy bleibt Master, Zugriff nur über `InventoryBackend`. Fotos, Nährwerte, Quellen,
  Anleitungen liegen in der App-DB bzw. `/data`.
- LLM wählt aus, Code rechnet. Nichts aus der KI automatisch übernehmen (Vorschläge + Review).
- Gesundheitsfaktor = Nutri-Score 2023 + Ziel-Passung (Begründung in `konzept.md`).
- Kalorien-Ziel vorerst für **eine** Mahlzeit, keine 3-Mahlzeiten-Pläne.
- Fotos sind ausdrücklich erwünscht (frühere Regel „keine Bilder“ aufgehoben).
- In Nils' Grocy nur Testdaten anlegen, nichts löschen ohne Rückfrage, Test-Bon nicht buchen,
  App-Optionen in HA ändert nur Nils.

## Live-Diagnose über HA (ohne Zugriff aufs Netz)

Das HA-MCP kann die App-API per Ingress aufrufen: `ha_manage_app(slug="bd5fe677_ration",
path="/api/v1/log/ki?limit=20")`, Details `/api/v1/log/ki/<id>`, ebenso jeder andere GET-Endpoint.
Version/Update-Stand: `ha_get_app(slug="bd5fe677_ration")`. Achtung: liefert auch die
App-Optionen inkl. Grocy-API-Key, den nie weitergeben oder committen.

## Lokal testen

```sh
cd ration && pip install -e ".[dev]"
ruff check . && ruff format --check . && python -m pytest -q   # auch im Repo-Root ruff laufen lassen
RATION_DATA_DIR=./data uvicorn app.main:app --reload --port 8099
```

Grocy für Contract-Tests: `docker run -d -p 9283:80 linuxserver/grocy` (API-Key in Grocy anlegen,
nie committen). UI-Checks mit Playwright (Desktop 1400 px und Handy 390 px, Konsolenfehler prüfen).
