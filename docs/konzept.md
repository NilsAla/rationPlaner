# rationPlaner – Konzept für ein Küchen-System in Home Assistant

Stand: 22.11.2026 · Autor: Nils · Name und Slug `ration` sind vorerst Platzhalter.

## Ziel und Leitprinzipien

Einmal pro Woche erzeugt das System aus einer eigenen Rezeptdatenbank, dem aktuellen
Küchenbestand und den Angeboten der Woche einen Wochenplan für einen Single-Haushalt.
Die Einkaufsliste landet automatisch als To-do in Home Assistant. Hauptziel: Nichts
verdirbt, Packungsgrößen werden über mehrere Gerichte und Wochen hinweg sinnvoll
aufgebraucht (500 g Paprika → Salat am Montag, Soße am Mittwoch).

- **Pflegeaufwand schlägt Präzision.** Ein Bestand, der nach drei Wochen nicht mehr
  stimmt, macht den Planer wertlos. Jede Funktion muss im Alltag in Sekunden bedienbar sein.
- **Das LLM wählt aus, der Code rechnet.** Mengen, Restmengen, Ablaufdaten und
  Nährwerte berechnet immer deterministischer Code, nie das Modell.
- **Eine Quelle der Wahrheit.** Grocy hält Bestand, Produkte und Rezepte. Die App hält
  nur, was Grocy nicht kann.
- **Preise und Angebote sind fester Bestandteil.** Bon-Preise sind die Wahrheit,
  Angebots- und Streichpreise aus Prospekten die Schätzung.
- **Keine Daten im Repo.** Das Repo enthält keine Rezept-, Produkt- oder
  Nährwertdatenbank. Höchstens 1–3 Testrezepte als Fixtures für Tests. Der Nutzer
  befüllt das System selbst, automatisiert per LLM.

## Architektur

Alles läuft auf dem bestehenden HAOS (N100). Grocy ist reines Backend ohne genutzte UI.
Die App `ration` enthält die gesamte Logik, HA liefert KI, Einkaufsliste und Dashboard.

| Komponente   | Technik                                   | Verantwortung |
|--------------|-------------------------------------------|---------------|
| Grocy        | HAOS-App                                  | Produkte, Chargen, Haltbarkeiten, Rezepte, Abbuchung, Einheiten |
| ration (App) | HAOS-App, Python 3.12, FastAPI, SQLite, Ingress | Planer, Bon-Import, Rezept-Import, Review, Präferenzen, Bewertungen, Nährwerte |
| Bridge       | Custom Integration `custom_components/ration` (HACS-Struktur) | Entitäten mit `unique_id`, Services wie `ration.gekocht`, delegiert an die App |
| KI           | HA-Integration (OpenAI oder Anthropic) als AI-Task-Entität | Bilder lesen, Mapping, Planauswahl |
| Küchen-Card  | Custom Lovelace Card (Lit), von der Bridge ausgeliefert | Rezept heute, Steps, Timer, Gekocht |

Kommunikation:

- **App → Grocy:** REST, ausschließlich über das Interface `InventoryBackend` (siehe unten).
- **App → HA:** Core-API über den Supervisor (`homeassistant_api: true`): `ai_task.generate_data`,
  `todo.*`, Notifications.
- **Bridge → App:** `DataUpdateCoordinator` pollt die App über den internen Hostnamen,
  nach jedem Service-Call sofortiger Refresh.
- **Handy und Browser** nutzen die Web-UI über Ingress (HA-Login, keine Freigabe nach außen).
- **Tablet** liest nur Entitäten und ruft Services auf.

### Backend-Abstraktion

Kein Code außerhalb von `app/inventory/grocy.py` kennt Grocy-Endpunkte, Grocy-IDs,
Userfields oder Grocy-Einheitenlogik. Planer, Bon-Import und Gekocht-Flow arbeiten nur mit
eigenen Domain-Objekten über das Protokoll `InventoryBackend`
(`list_products`, `get_batches`, `add_stock`, `consume` mit FEFO …).

- `GrocyBackend`: einzige Grocy-kennende Implementierung.
- `FakeBackend`: In-Memory mit Fixtures. Muss die Grocy-Semantik nachbilden (FEFO,
  Erfüllung über übergeordnete Produkte, Haltbarkeit beim Einfrieren).
- Die Grocy-Probe (Phase 0) ist gleichzeitig **Contract-Testsuite** und läuft gegen beide Backends.
- Exit-Option: Grocy lässt sich später durch eigene Tabellen ersetzen, ohne den Rest anzufassen.

### API-Vertrag

App-API unter `/api/v1/`. Die App meldet `api_version` an die Bridge, die Card prüft die
Version der Bridge. Inkompatible Änderungen erhöhen die Major-Version.

## Datenmodell

Grocy ist Master für alles mit Bestands- und Rezeptlogik. Die App hält in SQLite (`/data`)
nur Zuordnungen, Präferenzen, Planungsdaten und Nährwerte und referenziert Grocy-IDs.

| Entität | Ort | Inhalt |
|---|---|---|
| Produkt | Grocy | Name, Einheit, Standard-Haltbarkeit, Haltbarkeit geöffnet/eingefroren, Typ MHD/Verbrauchsdatum, Mindestbestand |
| Übergeordnetes Produkt | Grocy | Gruppiert Varianten (Brokkoli → frisch, TK) |
| Einheiten + Umrechnung | Grocy | Stück ↔ g, Packung ↔ g, EL ↔ g, produktspezifisch. **Gramm-Umrechnung ist für jedes Produkt Pflicht** |
| Barcode | Grocy | Mehrere je Produkt, jeweils mit eigener Menge |
| Standard-Packung | Grocy | Einkaufseinheit des Produkts + produktspezifische Umrechnung (Dose = 400 g) |
| Charge | Grocy | Menge, Einkaufsdatum, Ablaufdatum, Lagerort, geöffnet |
| Lagerort | Grocy | Kühlschrank, Vorrat, Gefrierschrank (Gefrierort) |
| Rezept | Grocy | Zutaten mit Mengen, Grundportionen, Schritte |
| Produkt-Userfields | Grocy | `tracking_modus`, `kategorie`, `fuellstand`, `toleranz_pct` |
| Rezept-Userfields | Grocy | `status`, `aufwand`, `tags`, `rest_haltbarkeit_tage` |
| `packung` | App | Weitere Packungsgrößen je Produkt: Einheit, Menge, Quelle (manuell/bon/angebot/llm), gültig bis (Angebot) |
| `zutat_alias` | App | Zutatentext aus Rezept-Importen (normalisiert) → Produkt |
| `rezept_import` | App | Import-Vorgang: URL, Quell-ID, gelesenes Rezept, angelegtes Rezept |
| `bon_alias` | App | Bon-Text je Markt → Produkt + Standardmenge |
| `praeferenz` | App | Pro übergeordnetem Produkt/Kategorie: TK / frisch / egal |
| `rezept_meta` | App | Quelle, „muss frisch sein“ je Zutat, `toleranz_pct`-Override je Zutat |
| `bewertung` | App | Daumen + Notiz je Kochvorgang |
| `wochenplan` | App | Tag, Rezept oder einfache Mahlzeit, Portionen, Status |
| `bon_import` | App | Bild, LLM-Rohantwort, Positionen mit Konfidenz, Review-Status |
| `preis` | App | Produkt, Preis, Quelle (bon/angebot/streichpreis), Markt, Datum, Grundpreis |
| `naehrwert` | App | Produkt, Quelle (BLS/OFF), Code, kcal/Protein/KH/Fett pro 100 g |
| `bls` | App | Importierte BLS-Tabelle (zur Laufzeit geladen, nicht im Repo) |
| `tageslog` | App | Datum, Mahlzeit, Portionen, Nährwerte, Flag Schätzung |
| `id_map` | App | Abgleich Grocy-IDs, Soft-Delete für gelöschte/zusammengeführte Produkte |

Der Planer arbeitet auf Ebene der übergeordneten Produkte. Ein periodischer Abgleich-Job
erkennt gelöschte oder zusammengeführte Grocy-Produkte und markiert App-Referenzen als
soft-deleted statt sie zu verlieren.

## Bestandsführung

| Modus | Beispiele | Getrackt | Rolle im Planer |
|---|---|---|---|
| `exakt` | Paprika, Hack, Sahne | Menge + Ablaufdatum je Charge | Kern der Optimierung |
| `menge` | Nudeln, Reis, Dosen, Zwiebeln | Menge, kein Ablaufdruck | Verfügbarkeit, Nachkauf bei Mindestbestand |
| `fuellstand` | Mehl, Öl, Zucker, Senf | voll / knapp / leer | Gilt als vorhanden bis „leer“ |
| `immer_da` | Salz, Pfeffer, Gewürze | nichts | Gilt immer als vorhanden |

- **Haltbarkeit:** Einkaufsdatum + Standard-Haltbarkeit. MHD = weiche Grenze,
  Verbrauchsdatum = harte Grenze (Bon-Review fragt echtes Datum ab). Öffnen setzt die
  kürzere Haltbarkeit.
- **TK vs. frisch:** Gekaufte TK-Ware ist ein eigenes Produkt unter demselben übergeordneten
  Produkt. Selbst Eingefrorenes bleibt dasselbe Produkt im Gefrierort.
- **Verbrauch:** immer FEFO, keine Präferenz. **Einkauf:** Präferenzen greifen, außer
  „muss frisch sein“.
- **Reste als Bestand:** Mehr gekochte als gegessene Portionen werden als Rest-Produkt eingebucht.
- **Drift-Kennzahl:** Der wöchentliche Kühlschrank-Check misst, wie weit Soll und Ist
  auseinanderliegen. Das ist neben „verdorben“ die zweite Gesundheitskennzahl des Systems.

### Mengen-Toleranz

Rezeptmengen sind flexibel, damit Packungen aufgehen statt komplexer Packungs-Optimierung.
`toleranz_pct` ist ein Default am Produkt (Grocy-Userfield), optional pro Rezeptzutat in
`rezept_meta` überschreibbar. Richtwerte: Gemüse in Soße/Salat ±30 %, Hauptzutaten ±15 %,
Backen/fest dosiert 0 %. Globaler Default als App-Option (±10 %).

Die Simulation skaliert deterministisch innerhalb der Toleranz: nach oben, um Restmengen
vor Ablauf zu verbrauchen; nach unten, wenn ein kleiner Rest sonst eine neue Packung
auslöst. Card und Abbuchung verwenden die angepasste Menge.

### Packungsgrößen

Eingekauft werden ganze Packungen, nie Grammmengen.

- **Standard-Packung** je Produkt = die meistverkaufte Größe (Schokolade 100 g, Ravioli 800 g).
  Liegt in Grocy als Einkaufseinheit mit Umrechnung in die Bestandseinheit, in der App-UI
  (Bestand → Packungen) und per Service/MCP änderbar.
- **Weitere Größen** (Bohnen 400 g und 800 g) in der App-Tabelle `packung`, mit Quelle:
  `manuell`, `bon` (auf dem Bon gekaufte Größe), `angebot` (Prospektgröße, gilt bis Ende der
  Aktion), `llm` (Vorschlag aus dem Kategorisierungslauf).
- **Bon:** Gekaufte Größe wird mit ihrer echten Menge eingebucht. Weicht sie ab, schlägt
  das Review sie als weitere Größe oder neuen Standard vor (nie automatisch).
- **Kategorisierungslauf:** schlägt die Standard-Packung mit vor (Diff + Review).
- **Einkauf (Code):** Fehlmenge + Mindestbestand − Bestand, dann die Kombination aus
  höchstens zwei Größen mit dem kleinsten Überhang; bei Gleichstand weniger Packungen,
  dann laufendes Angebot, dann Standard. Ohne Packungsgröße: ganze Bestandseinheiten.
  Später (Planer) zusätzlich Toleranz-Skalierung und Preis.

## Stammdaten-Aufbau (Bootstrap)

Das System startet leer. Der Nutzer füllt es selbst, automatisiert per LLM:

1. **Import** (Rezepte per URL/Foto/Generierung, Produkte und Erstbestand per CSV oder Bon)
   legt Rezepte und Zutaten als Produkte **ohne Attribute** an. Unbekannte Zutaten werden
   gemappt oder neu angelegt, mehr nicht.
2. **Kategorisierungslauf** (ab ca. 50 Rezepten / 200 Zutaten): LLM-Batch setzt je Produkt
   Tracking-Modus, Kategorie, Haltbarkeiten, MHD/Verbrauchsdatum, Gramm-Umrechnung,
   `toleranz_pct`, Standard-Packung und BLS-/OFF-Zuordnung. Rezepte: Tags, Aufwand, „muss frisch sein“,
   Rest-Haltbarkeit, Dubletten. Ergebnis als **Diff mit Review**, nie automatisch übernommen.
3. **Monatlich** per HA-Automation über `ration.kategorisieren`: inkrementell für neue oder
   geänderte Produkte, Diff in die Review-Liste mit Push. Prüft zusätzlich Werte gegen echte
   Daten (z. B. regelmäßig vor MHD verdorben → kürzere Haltbarkeit vorschlagen).

Vor dem ersten Lauf gelten Produkte ohne Attribute als `menge` mit globaler Toleranz.
Der Planer startet erst, wenn alle Produkte Attribute haben.

## Erfassung

**Kassenbon (Hauptweg):**

1. Foto auf der mobilen Seite (Ingress). Upload kehrt sofort zurück, Verarbeitung im Hintergrund.
2. **Regel-Vorverarbeitung:** Pfand, Tüten, Rabattzeilen, Mehrfachzeilen („2 x 1,29“) per
   Regeln je Markt. Test-Bons je Markt als Fixtures.
3. Alias-Tabelle zuerst, Treffer ohne LLM.
4. Rest per `ai_task.generate_data` mit Bild-Anhang und Structured Output. Das Bild liegt
   dafür unter `/media` und wird als `media-source://media_source/local/...` übergeben.
5. Validierung (ID existiert, Menge plausibel). Konfidenz ≥ Schwelle → buchen, sonst Review + Push.
6. Jede Bestätigung schreibt einen Alias. Bonpreise gehen in `preis`. Füllstand-Produkte auf voll.
   Abweichende Packungsgröße → Vorschlag für `packung` (siehe Packungsgrößen).

**Barcode-Scanner (optional):** Open Food Facts, je Barcode einmal einem übergeordneten Produkt zuordnen.

**Manuelle Buttons:** Frischware ohne Bon, plus wöchentlicher Kühlschrank-Check (~30 s).

## Rezeptdatenbank

| Weg | Quelle | Extraktion | Status |
|---|---|---|---|
| Foto/PDF | Rezeptkarten, Kochbücher | `ai_task` mit Bild | ungetestet |
| URL | HelloFresh, Chefkoch & Co. | schema.org/Recipe (JSON-LD), Zuordnung per Alias/Name, Review | ungetestet |
| Generiert | Prompt in der App | `ai_task` mit Bestand als Kontext | ungetestet |
| Eigenes | Manuelle Eingabe | – | getestet |

**Import per Link:** Vorschau liest Name, Portionen und Zutaten aus dem JSON-LD der Seite.
Herkunft und Quell-ID (HelloFresh: 24-stellige Hex-ID am URL-Ende) landen in `rezept_meta`,
darüber werden Dubletten erkannt. **Ziel: ein Klick.** Sicher zugeordnet wird nur über Alias
(`zutat_alias`), gleichen Namen oder gleichen Kern-Namen (ohne vorangestellte Adjektive,
„rote Zwiebeln“ → Zwiebel). Alles andere wird standardmäßig als **neues Produkt ohne
Attribute** angelegt; ähnliche Produkte erscheinen nur als Alternative („Frühlingszwiebel“
ist keine „Zwiebel“). Mengen gehen in g: g/kg direkt, ml/l über Dichte, Stück/Bund/EL/TL
über eingebaute Richtwerte (`app/mengen.py`, z. B. Knoblauchzehe 4 g, Aubergine 300 g),
als Schätzung markiert. Neue Stück-Produkte bekommen das Stückgewicht als Standard-Packung.
Review nur bei echten Zweifeln (Menge nicht bestimmbar). Wasser wird weggelassen. Statt der Zubereitung steht
ein Link zur Quelle in der Beschreibung; eine eigene Kurzfassung erzeugt später `ai_task`.
Blockt eine Seite den Abruf, kann der Quelltext eingefügt werden. Später übernimmt das LLM
die Zuordnung unbekannter Zutaten (wählt Produkt-IDs, Code rechnet Mengen).

Kuratierung über Daumen hoch/runter. Planer mischt höchstens 1–2 ungetestete Rezepte pro Woche.
Die Datenbank bleibt privat; fremde Fotos und Fließtexte werden nicht übernommen.

## Wochenplaner

Einmal pro Woche (Button oder Automation) entsteht ein 7-Tage-Plan. Nur ein LLM-Schritt:

1. **Kandidaten (Code):** Score aus Aufbrauch-Druck, Deckungsgrad, Packungs-Fit
   (über Toleranzbänder), Bewertung/Abwechslung, Angebote, Makro-Fit (weich). Top 20–30 ans LLM.
2. **Auswahl (LLM):** je Tag Rezept-ID und Portionen, optional Reste-Verwertung.
3. **Simulation (Code):** Tag für Tag gegen Chargen, FEFO, Toleranz-Skalierung, Zukauf auf Packungen.
4. **Repair-Loop:** konkretes Feedback, max. 2 Runden.
5. **Einkauf (Code):** Fehlmengen nach Präferenz → Produkte → Packungen → HA-To-do mit Preis.

Vollständige Tage: Frühstück und Abendbrot als **einfache Mahlzeiten** ohne Rezept,
verbrauchen Bestand und zählen in die Makros. Tausch einzelner Tage ohne neuen LLM-Aufruf.

**Kostenschätzung:** letzter Bonpreis > aktueller Angebotspreis > Streichpreis.

## Kochen und Abbuchung

„Gekocht“ bucht über die Grocy-API ab (nicht über Grocys Rezept-Abbuchung): `exakt` und
`menge` mit (angepasster) Rezeptmenge × Portionen, FEFO, Frischware vor TK. `fuellstand`
und `immer_da` nie. Reste als Rest-Produkt. Kurzbewertung. Füllstand-Pop-up höchstens
einmal pro Kochvorgang, je Produkt höchstens alle 14 Tage. „Verdorben“-Button als Kennzahl.

## Nährwerte und Makros

- **Quellen:** BLS 4.0 (Bundeslebensmittelschlüssel, Max Rubner-Institut, >7.000 generische
  Lebensmittel) für Frischware; Open Food Facts für Markenprodukte.
- BLS wird **zur Laufzeit** heruntergeladen bzw. hochgeladen und in die App-DB importiert,
  nicht ins Repo committet. **Lizenz vor Umsetzung prüfen.**
- Nährwerte liegen in der App-DB (`naehrwert`), nicht in Grocy.
- Rezept: Code rechnet Σ(Menge in g × Nährwert/100 g) ÷ Grundportionen. Öl zählt mit der
  Rezeptmenge in die Kalorien, wird aber nie abgebucht.
- Tageslog: „Gekocht“ mit gegessenen Portionen schreibt automatisch. Schnell-Log per Text/Foto,
  KI schätzt, als Schätzung markiert.
- Ziele in den Settings, im Planer nur weiche Vorgabe. Verderb vermeiden hat Vorrang.
- Sensoren `sensor.ration_kcal_heute`, `sensor.ration_protein_heute` … mit
  `state_class: total_increasing`, Reset um Mitternacht.

## Angebote und Preise

Samstags lädt die App die Online-Prospekte der 2–3 genutzten Märkte:

- Je Markt ein Adapter, Reihenfolge: Headless Browser > Scraping > PDF-Download > manueller Upload.
- Headless Browser (Playwright/Chromium) nicht im Haupt-Image, sondern als optionaler Dienst
  (eigene App oder Browserless), damit die Kern-App schlank bleibt.
- **Healthcheck je Adapter:** kaputte Adapter fallen auf (Sensor + Push).
- Seiten per Vision-Modell auslesen, auf Produkte mappen. Angebote sind nur ein Overlay;
  Normalpreise kommen aus eigenen Bons. Nennt ein Angebot eine andere Größe, wird sie als
  `packung` mit Quelle `angebot` und Ablauf zum Aktionsende angelegt.
- Offen: Märkte, Quellen je Markt, Nutzungsbedingungen, Blockaden.

## Oberflächen

| Oberfläche | Wo | Funktionen |
|---|---|---|
| Web-App | HA-Sidebar (Ingress) | Plan, Rezepte, Bestand, Review-Listen, Settings, Statistik |
| Küchenseite | Tablet, Custom Card | Rezept heute, Steps, Timer, Gekocht, „läuft bald ab“, „Jetzt kochbar“ |
| Handy | Companion-App | Bon-Foto, Einkaufsliste, Push, Widgets „Heute“ und „Einkauf“ |

**Schnittstelle zu HA:** `sensor.ration_rezept_heute`, `sensor.ration_laeuft_ab`,
`sensor.ration_status` (Statuszeile „24 Artikel · 3 laufen bald ab · 2 Reste“), Nährwert-Sensoren.
Services `ration.gekocht`, `ration.plan_erzeugen`, `ration.kategorisieren`. Einkaufsliste ist
eine normale `todo`-Entität. Abhaken ≠ Einbuchen.

Die Card wird von der Bridge per Static Path ausgeliefert und als Lovelace-Resource
registriert (HACS kann aus einem Repo nicht Integration und Card zugleich installieren).

## KI-Einsatz und Kosten

Alle KI-Aufrufe laufen über eine AI-Task-Entität in HA (OpenAI oder Anthropic, wählbar per
App-Option `ai_task_entity`). Kein API-Key in der App. Immer Structured Output.

Prompt-Disziplin: nie die komplette DB im Prompt, IDs statt Namen, jede Antwort gegen Schema
und Backend validieren, Rohantworten loggen. Laufzeit-Prompts liegen in `ration/app/prompts/`.

## Betrieb

- **Backup:** Grocy- und App-DB sind Teil des HA-Backups. Die App setzt einen `backup_pre`-
  Hook, der SQLite per `.backup` konsistent sichert.
- **Grocy-Erreichbarkeit:** in Phase 0 klären, wie die App Grocy intern erreicht (Hostname des Grocy-Add-ons).

## Roadmap

Erst Gerüst, dann ein **dünner Durchstich** (Bon → Gekocht → Einkaufsliste, minimal, ohne
Planer und Nährwerte), danach Ausbau Phase für Phase.

- **Phase 0 – Technik:** Grocy installieren, API-Key, Erreichbarkeit. `grocy_setup.py`
  (Lagerorte, Userfields inkl. `toleranz_pct`), Grocy-Probe als Contract-Tests, CSV-Import,
  BLS-Lizenz prüfen und Import. Keine Stammdaten.
- **Durchstich:** Manuelles Einbuchen, Plan von Hand, Gekocht mit FEFO-Abbuchung, Einkaufsliste in
  HA-To-do, Sensoren „Rezept heute“, Status und „läuft ab“. Bon-Upload folgt mit der AI-Task-Entität.
  Noch offen im Durchstich: Reste als Rest-Produkt einbuchen, Füllstand-Abfrage.
- **Phase 1 – Grundgerüst + Kochen:** Ingress-UI, Bestandsansicht, manuelles Einbuchen,
  Gekocht-Flow mit Portionen und Resten, Bridge-Entitäten, Küchen-Card, Statuszeile, Widgets.
- **Phase 2 – Kassenbon:** Vorverarbeitung, Alias, `ai_task`, Review, Push.
- **Phase 3 – Wochenplaner:** Kategorisierungslauf (vorgezogen, sobald Daten da sind),
  Scoring, Auswahl, Simulation mit Toleranz, Repair-Loop, Einkauf, Plan-Ansicht, Nährwerte,
  einfache Mahlzeiten, „Jetzt kochbar“.
- **Phase 4 – Rezept-Import + Kuratierung.**
- **Phase 5 – Optional:** Barcode-Scanner, Statistik, HA Assist mit App-Werkzeugen (ggf. als
  LLM-API der Bridge registriert), Embedding-Matching (sqlite-vec), MCP-Zugriff.
- **Phase 6 – Angebote und Preise:** Prospekt-Adapter mit Healthcheck, Preistabelle, Scoring-Bonus.

## Offene Punkte und Risiken

| Punkt | Umgang |
|---|---|
| Bestandsdrift | Kühlschrank-Check, Drift-Kennzahl, nur Frischware exakt |
| Grocy-API-Grenzen | Probe/Contract-Tests: auf Grocy 4.7.1 verhalten sich Parent-Erfüllung, FEFO, Einfrieren und Auftauen wie benötigt. Auf der eigenen Instanz per `scripts/grocy_probe.py` prüfen |
| Grocy-ID-Drift | Abgleich-Job, Soft-Delete |
| Bon-Qualität | Regel-Vorverarbeitung, Alias je Markt, Test-Bons, Konfidenz-Schwelle |
| Planer-Qualität | Abwechslungsregeln, Bewertungen, Tausch per Klick |
| Versionsdrift App/Bridge/Card | `/api/v1`, Versions-Handshake |
| Prospekt-Adapter brechen | Adapter je Markt, Healthcheck |
| BLS-Lizenz | vor Import prüfen |
| Gramm-Umrechnung fehlt | Pflichtfeld im Kategorisierungslauf, Planer startet erst bei Vollständigkeit |
| Name | `rationPlaner` / `ration` ist Platzhalter; Slug zentral halten |
