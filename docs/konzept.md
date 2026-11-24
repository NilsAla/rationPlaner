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
| `dublette_entscheidung` | App | Produktpaar (sortierte IDs), Entscheidung „nicht mergen“, Datum. Wird nie wieder vorgeschlagen |
| `produkt_merge` | App | Zusammengeführt: alte ID → neue ID, alter Name, Datum |

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

**Umsetzung (0.11.0, `app/kategorisierung.py`):** `POST /kategorisieren` (Service
`ration.kategorisieren`, monatlich per Automation mit `nur_ohne_attribute: false`) läuft im
Hintergrund in Batches zu 25. Produkte: Modus, Kategorie (feste Liste), Ablauf-Typ,
Haltbarkeiten, g je Einheit, Toleranz, Standard-Packung. Rezepte: Tags (feste Liste), Aufwand,
Rest-Haltbarkeit. Der Code validiert Enums und Bereiche (z. B. g je Stück ≥ 2, ml-Dichte 0,5–2),
legt nur echte Änderungen als Vorschlag ab (Standard: nur leere Felder) und setzt nichts.
Übernahme im Tab *Produkte* per Haken. Live gegen OpenAI getestet: Struktur funktioniert,
Inhalte teils schwach (Ei „1 g“, Kidneybohnen 24 Tage), daher Plausibilitätsregeln und Review.
Offen: BLS-/OFF-Zuordnung, „muss frisch sein“ je Zutat, Rezept-Dubletten.

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

**Umsetzung (0.10.0, `app/bon.py`):** Ein `ai_task`-Aufruf je Bon mit Bild und Katalog
(id|name|einheit, ohne Reste). Das LLM liefert Markt, Datum, Summe und je Zeile Text, Anzahl,
Preis, aufgedruckte Größe, Produkt-ID oder neuen Namen und Konfidenz. Der Code verwirft
Pfand/Tüten/Rabatte/Summen per Regex, prüft IDs, nimmt zuerst `bon_alias` (Markt + Text) und
rechnet die Menge: Größe laut Bon × Anzahl, sonst Standard-Packung × Anzahl, sonst Stück.
**Annahme (abweichend von Schritt 5):** Gebucht wird nie ohne Review. Vorausgewählt sind
Alias-Treffer und KI-Treffer mit Konfidenz ≥ `bon_konfidenz_schwelle`, der Nutzer bestätigt mit
einem Klick. Grund: Das OpenAI-Modell hat beim Import live Fehlgriffe geliefert (Salz → Olivenöl).
Füllstand-Produkte werden nur auf voll gesetzt (kein Bestand), Immer-da gar nicht gebucht.
Weicht die Größe laut Bon von allen bekannten Packungen ab, entsteht eine Packung mit Quelle
`bon`. Hinweis per `persistent_notification`, optional zusätzlich `notify_service`.
Fotos liegen unter `/media/ration/bons`. Alternativ `POST /bon/media` für ein Bild unter
`/media` (z. B. per Automation). Live gegen OpenAI noch nicht getestet (kein Weg, ein
Testbild in Nils' `/media` zu legen, ohne die neue App-Version).

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
Review nur bei echten Zweifeln (Menge nicht bestimmbar). Wasser wird weggelassen.

**Zubereitungsschritte** (Änderung 0.12.0, Entscheidung Nils für die private Datenbank: ohne
Schritte ist der Import wertlos). Quelle ist `recipeInstructions` aus dem JSON-LD: HowToStep
ist ein Schritt, HowToSection wird zu „Titel:“ plus Unterschritte, Listen werden flach gemacht.
Liefert die Seite Fließtext, trennt der Code an aufsteigender Nummerierung ab 1 („1.“, „2)“,
„Schritt 3:“), sonst an Zeilenumbrüchen; Zahlen mitten im Satz zählen nicht. Kappung auf 40
Schritte × 1500 Zeichen. Die Schritte stehen in der Vorschau als editierbares Textfeld (ein
Schritt pro Zeile) und landen als `<p>`-Absätze in der Grocy-Beschreibung, darunter der
Quellenlink, den `kueche.schritte()` beim Lesen wieder herausfiltert. Bilder werden weiterhin
nicht übernommen, das Repo enthält weiterhin keine Rezeptdaten.
Blockt eine Seite den Abruf, kann der Quelltext eingefügt werden.
**Seiten ohne JSON-LD** (z. B. Chefkoch-Magazinartikel, 0.28.6): `rezept_aus_ueberschriften`
baut aus dem HTML ein schema.org/Recipe-Dict: Überschrift „Zutaten (für N Portionen)“, Listen
danach als Zutaten bis „Zubereitung“, dann Listen als Schritte bis zur nächsten h2 oder dem
nächsten Rezept. Name ist die Überschrift davor, Hauptbild `og:image`. Nur das erste Rezept der
Seite. Galerie-Links sind eigene Rezepte, ein `#fragment` am Link wird verworfen. Chefkoch-
Rezeptseiten: „… von <Nutzer>“ fällt aus dem Namen weg, ein einzelner Abschnitt „Zubereitung“
wird nicht zum Schritt. Chefkoch hat keine Schrittbilder.
Magazinartikel ohne eigenes Rezept, aber mit genau einer eingebetteten Rezeptkarte („Das Rezept
aus dem Video“, `ds-list-view-item--recipe`), laden das verlinkte Rezept; Quelle ist dann das
Rezept (0.28.7). HelloFresh: Mengen für 3/4 Personen in eckigen Klammern („300 ml [450 ml |
600 ml]“) und Vorrats-Sternchen fallen aus den Schritten. Der Name kommt aus `__NEXT_DATA__`,
Untertitel mit Zahlen (Werbung) fallen weg. Gewürzlisten ohne Menge („Salz, Pfeffer, Muskat“)
werden einzelne Zutaten, Einheiten wie „Prise(n)“, „Scheibe/n“, „Pck.“ werden normalisiert.
Mengen mit „ca.“ davor, „1 kl. Glas“ und „Essig*“ werden erkannt (0.28.8). Sammelseiten
(„Das perfekte Dinner“, mehrere verlinkte Rezepte, keine Karte) melden, dass ein einzelner
Rezeptlink nötig ist. Stichprobe 2026-11-24: 45 Seiten (15 HelloFresh, 15 Chefkoch-Rezepte,
15 Magazin) ok, außer Sammelseiten und einem Produktartikel ohne Rezept.
**Dubletten-Großtest (0.28.10):** 30 Paprika-/Nudel-/Gemüserezepte von HelloFresh und Chefkoch
nacheinander mit Ein-Klick-Vorschlag importiert, ohne KI. Danach: Einheiten Tube, Zweig, Stiel,
Stängel, Kugel, Paket, Kopf (auch „Stiel/e“, „Zweige“); Zusätze hinter dem Namen („multicolor“,
„geraspelt“, „ital. Art“, „vom Weiderind“) und HelloFresh-Kräutermix („Basilikum/Oregano“ →
erste Sorte) fallen weg; Synonyme Paprikaschote→Paprika, Muskatnuss→Muskat,
Hackfleischzubereitung→Hackfleisch, Öl→Pflanzenöl; Salz-/Kochwasser wird weggelassen. **Feste
Begriffe** behalten ihr Adjektiv (saure Sahne, stückige/passierte/geschälte/getrocknete Tomaten),
auch im Alias-Schlüssel. Chefkoch-PLUS-Rezepte (Paywall, kein JSON-LD) werden abgelehnt; der
Überschriften-Fallback braucht Zubereitungsschritte, sonst kein Rezept.

**KI-Zuordnung** (`app/llm_import.py`, wenn `ai_task_entity` gesetzt, abschaltbar mit `ki: false`):
Nach dem Code-Lauf gehen nur die offenen Zutaten (Aktion „neu“) mit dem Produktkatalog
(id|name|einheit) per `ai_task.generate_data` und Structured Output ans LLM. Es liefert je Zutat
`zuordnen` + `produkt_id` oder `neu`, dazu `g_je_einheit` und `stueck_g`. Den Namen neuer
Produkte bildet weiter der Code (im Test lieferte das LLM schlechtere Namen). Eine KI-Zuordnung
mit völlig anderem Namen (Synonym oder Fehlgriff, live gesehen: „Salz“ → Olivenöl) wird nicht
übernommen, sondern als erste Alternative mit „KI meint: …?“ angezeigt.
Der Code validiert (Index und Produkt-ID müssen existieren, Gewichte 0–5000 g) und rechnet:
Mengen wie beim Code-Lauf, `g_je_einheit` nur dort, wo Tabelle und Einheit keine Menge liefern.
Alias-/Namens-Treffer und „weglassen“ bleiben Sache des Codes. Rohantwort wird geloggt,
KI-Vorschläge sind in der Vorschau als „KI“ markiert, angelegt wird erst nach Klick. Fällt die
KI aus, bleibt der Code-Vorschlag (Hinweis in der Vorschau).

Kuratierung über Daumen hoch/runter. Planer mischt höchstens 1–2 ungetestete Rezepte pro Woche.
Die Datenbank bleibt privat; fremde Fotos werden nicht übernommen, Zubereitungsschritte seit 0.12.0 schon (s. Import).

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

**Umsetzung (0.13.0, `app/planer.py`):** `POST /plan/erzeugen` (Service `ration.plan_erzeugen`)
läuft im Hintergrund und legt einen `plan_lauf` als Vorschlag ab; in `wochenplan` landet er erst
mit „Übernehmen“ (oder `uebernehmen: true`, für die Wochen-Automation).
- **Score (Code):** 3 × Aufbrauch (Anteil der Zutat aus Chargen, die in ≤ 4 Tagen ablaufen,
  gewichtet nach Dringlichkeit) + 2 × Deckung (mit Toleranz) + 1 × Packungs-Fit (Fehlmenge ÷
  gekaufte Packungsmenge) − 2 × Abwechslung (gekocht in den letzten 14 Tagen) + 0,2 je Zutat
  mit laufendem Angebot − 0,3 für ungetestet. Top 25 ans LLM.
- **Auswahl (LLM):** Struktur mit `select` für Datum und Rezept-ID, Portionen und Begründung.
  Code prüft: Tag zu planen, ID ist Kandidat, kein Rezept doppelt (auch nicht zu festen Tagen),
  höchstens 2 ungetestete, Portionen auf 1–6 geklemmt.
- **Simulation:** Tag für Tag, FEFO über Parent + Varianten, abgelaufene Chargen sind weg.
  Reicht der Bestand bis zur Toleranz, wird weniger genommen; sonst Zukauf in Packungen am
  Planstart (Ablauf = Start + Haltbarkeit, Hinweis „erst kurz vorher kaufen“, wenn das nicht
  reicht). Bleibt von einer frisch gekauften Packung (`exakt`) höchstens die Toleranz übrig,
  geht sie ganz ins Rezept. Ergebnis: Einkauf mit Kosten (Preis je Bestandseinheit: Bon >
  Angebot > Streichpreis), Verderb im Planzeitraum, Überhang aus Zukauf.
- **Repair-Loop:** Formfehler und „verdirbt, obwohl ein nicht gewählter Kandidat es nutzt“
  gehen als Liste zurück ans LLM, höchstens 2 Runden (≤ 3 Aufrufe). Was danach fehlt oder
  ungültig ist, füllt der Code gierig. Ohne ai_task oder bei Fehler plant nur der Code.
- **Feste Tage:** Geplante Tage bleiben (außer `ueberschreiben`), gekochte verbrauchen nichts mehr.
- **Tauschen:** `POST /plan/{datum}/tauschen` nimmt den besten Kandidaten gegen den simulierten
  Bestand, ohne LLM. `GET /plan/simulation` rechnet den aktuellen Plan durch.
- **Annahmen:** Standard 2 Portionen je Tag (1 essen, 1 Rest mittags), nur Abendessen.
  Offen: einfache Mahlzeiten (Frühstück/Abendbrot), Reste-Tage, Makro-Fit (braucht
  Nährwerte); `/einkaufsliste` rechnet noch ohne Toleranz (die Einkaufsseite der Web-App
  zeigt die Simulation mit Toleranz).

**Präferenzen und Wochenrhythmus (0.14.0, Wunsch Nils):** Einstellungen liegen in der App-DB
(`einstellung`, Gruppen `planer`, `praeferenzen`, `anzeige`, `GET/PUT/DELETE /einstellungen`),
`plan/erzeugen` nimmt sie als Standard und erlaubt je Lauf Abweichungen (`praeferenzen`,
`max_ungetestet`, `daten` = beliebige Tage aus dem Kalender statt `start` + `tage`).
- **Regler** −1 … 1, alle als zusätzliche Score-Terme, die LLM-Auswahl bleibt unverändert:
  - *Fleisch ↔ Gemüse:* + 1,5 · Regler · (Fleischanteil − Gemüseanteil) der Zutaten, Anteile
    über die Produktkategorie (Fleisch/Fisch vs. Gemüse/Obst/Hülsenfrüchte/Kräuter).
  - *Günstiger ↔ teurer:* + 1,0 · Regler · relative Kosten je Portion gegenüber dem Median.
  - *Vorrat abbauen ↔ extravagant:* Bestandsterme (Aufbrauch, Deckung, Fit) werden mit
    (1 − 0,5 · Regler) gewichtet, bei > 0 zusätzlich + Regler · (1 − Deckung).
  - *Lieblinge ↔ Neues:* − 1,5 · Regler · Beliebtheit (Favorit, Daumen, wie oft gekocht) und bei
    > 0 + Regler für nie gekochte Rezepte. Daumen zählen immer mit + 0,5 · Bewertung.
- **Ausschluss-Tags:** Rezepte mit diesen Tags sind keine Kandidaten.
- **Aufwand je Wochentag:** höchster Aufwand (schnell/normal/aufwändig). Zu aufwändige Rezepte
  kosten − 2 im Score und sind für das LLM ein Fehler im Repair-Loop.
- **Kochtage:** Standard-Wochentage für die Vorauswahl im Kalender; im Kalender lassen sich
  beliebige Tage an- und abwählen.
- **Bewertung nach dem Kochen:** 👍/😐/👎 je Kochvorgang (`kochvorgang`), letzte Bewertung gilt
  als Meinung zum Rezept (`rezept_bewertung` mit Favorit und Notiz).
- **Annahme:** Rezepte ohne Kategorie-Treffer zählen beim Fleisch-Regler neutral; ohne Preise
  wirkt der Preis-Regler nicht.

## Kochen und Abbuchung

„Gekocht“ bucht über die Grocy-API ab (nicht über Grocys Rezept-Abbuchung): `exakt` und
`menge` mit (angepasster) Rezeptmenge × Portionen, FEFO, Frischware vor TK. `fuellstand`
und `immer_da` nie. Reste als Rest-Produkt. Kurzbewertung. Füllstand-Pop-up höchstens
einmal pro Kochvorgang, je Produkt höchstens alle 14 Tage. „Verdorben“-Button als Kennzahl.

## Kochmodus und Kochanleitungen

**Ziel:** Rezepte Schritt für Schritt kochen, so wie ein Koch arbeitet: Wasser aufsetzen und Herd
an, während es heiß wird Gemüse schneiden, dann Nudeln ins Wasser, dann anbraten. Jeder Schritt
zeigt seine Zutaten mit gerechneter Menge zum Abhaken und, wo nötig, einen Timer.

**Datenmodell (App-DB, nicht Grocy):**
- `KochAnleitung` je Rezept: Schritte `{titel, text, zutaten:[{zutat: Index, anteil 0–1}],
  timer_s, timer_name, parallel, timer_geschaetzt}`, `quelle` ki|code|manuell, `status`
  entwurf|geprueft, `basis` (Hash über Zubereitung und Zutaten, für „veraltet“), `hinweise`.
- `KochSitzung`: aktueller Schritt, erledigte Schritte, abgehakte Zutaten, laufende Timer.
  Liegt auf dem Server, damit Handy und Küchen-Tablet denselben Stand sehen (Poll alle 4 s).
- `AnleitungLauf`: Massenumschreibung im Hintergrund mit Fortschritt und Fehlern je Rezept.

**KI-Migrator (LLM wählt aus, Code rechnet):**
- Ein `ai_task.generate_data`-Aufruf je Rezept, Structured Output. Zutaten nur als Index ins
  Rezept, nie als Name oder Menge. Mengen rechnet der Code: Rezeptmenge × Portionen × Anteil.
- Validierung: unbekannte Indizes fallen weg, Timer 10 s bis 6 h. Fehlen Zutaten, gibt es eine
  Reparaturrunde; was dann noch fehlt, ordnet der Code dem ersten Schritt zu, der sie nennt.
  Mehrfachnennung heißt „später weiterbearbeitet“ (schneiden, dann anbraten) und zeigt die volle
  Menge mit „aus Schritt n“. Geteilt wird nur, was die KI als `teilweise` markiert (Butter halb/halb).
- Timer, deren Dauer nicht im Original steht, werden als geschätzt (≈) markiert.
- Einzeln: scheitert die KI, speichert die App den Code-Vorschlag mit Hinweis. Massenlauf: kein
  Fallback, die bisherige Anleitung bleibt stehen und der nächste Lauf versucht das Rezept erneut.
  Scheitern die ersten 3 Aufrufe, bricht der Lauf ab (keine Aufrufe verbrennen).
- Ohne gespeicherte Anleitung liefert der Code einen Vorschlag aus den Originalschritten
  (ein Schritt je Satz, Zutaten per Namensabgleich, Timer per Regex auf „x Minuten“).

**Annahme zu Regel 4 (nichts automatisch übernehmen):** Umgeschriebene Anleitungen sind keine
Stammdaten. Sie landen nur in der App-DB als Entwurf, das Rezept in Grocy bleibt unverändert.
Der Kochmodus nutzt auch Entwürfe und markiert sie. Freigabe einzeln im Editor oder gesammelt in
der Übersicht.

**Timer:** HA-`timer.*`-Entitäten aus der App-Option `timer_entities` (Liste, wie die anderen
Entitäts-Optionen). Leer = automatisch alle Timer mit Küche/kitchen/koch in ID oder Namen.
Gestartet wird der erste freie Timer (`timer.start` mit `duration`), Pause/Fortsetzen/Abbrechen
gehen über dieselben Services, Dashboard-Karten zeigen ihn also mit. Sind alle belegt oder ist HA
nicht erreichbar, nimmt die UI einen Browser-Timer (in der Sitzung gespeichert).

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

**Umsetzung Makro-Modus (0.17.0, `app/naehrwerte.py`):**

- Tabelle `naehrwert` je Produkt, Werte je 100 g: kcal, Protein, Fett, davon gesättigt,
  Kohlenhydrate, davon Zucker, Ballaststoffe, Salz, dazu die Flags `obst_gemuese` (für den
  Nutri-Score) und `roh`. Quelle `manuell` (Produktseite) oder `ki`.
- Die KI-Schätzung ist ein eigener Lauf der KI-Kategorisierung (Option „Nährwerte schätzen“). Sie
  erzeugt Vorschläge (Feld `naehrwerte`), die erst nach dem Review gespeichert werden (Regel 4).
  BLS/OFF bleiben als spätere Quelle offen und ersetzen dann die Schätzung.
- Rezept: Gramm je Zutat aus Bestandseinheit bzw. `g_je_einheit`. `abdeckung` ist der Anteil
  des Gewichts mit Werten. Fehlt etwas, zeigt die UI „≥“ und listet die Zutaten ohne Werte.
- **Gesundheitsfaktor: Nutri-Score 2023** (allgemeine Lebensmittel, Algorithmus nach der
  Überarbeitung des wissenschaftlichen Komitees 2022/23), angewandt auf die Rezeptsumme je 100 g.
  N = Energie + Zucker + gesättigte Fette + Salz, P = Ballaststoffe + Obst/Gemüse/Hülsenfrüchte
  (+ Protein, außer bei N ≥ 11 ohne volle Obst/Gemüse-Punkte), Klasse A ≤ 0, B 1–2, C 3–10,
  D 11–18, E ≥ 19. Erst ab 80 % Abdeckung. Für den Planer: `gesund = clamp((25 − Punkte)/30, 0, 1)`.
  Gewählt, weil er etabliert und in Deutschland bekannt ist, nur Werte braucht, die wir ohnehin
  haben, und als A–E sofort lesbar ist. Verworfen: NRF9.3 (braucht Vitamine und Mineralstoffe,
  die weder BLS-Light noch eine KI-Schätzung verlässlich liefern) und eigene Formeln (das Rad
  nicht neu erfinden). Einschränkung: Der Nutri-Score ist für Einzelprodukte gedacht; für Gerichte
  ist er eine gute Näherung, bewertet aber die Portionsgröße nicht. Das deckt die Ziel-Passung ab.
- **Mahlzeiten-Ziel** (Einstellungen → Ernährung, vorerst eine Mahlzeit): kcal und Protein je
  Portion. `fit_kcal = max(0, 1 − |kcal − Ziel| / (0,5 · Ziel))`, `fit_protein = min(1, Protein/Ziel)`
  (Überschreiten zählt nicht), `fit` = Mittelwert. Fließt nur mit `im_planer` in den Score ein.
- **Planer-Regler** gesund, protein, rohkost (−1 … 1): `1,5·gesund·(2g−1)`,
  `1,0·protein·clamp((Protein-Energie-% − 15)/15)`, `1,0·rohkost·clamp((Rohkost-% − 30)/30)`,
  mit Ziel `1,5·(fit − 0,5)`. Unter 80 % Abdeckung neutral (0): Rezepte ohne Daten werden weder
  bestraft noch bevorzugt. Die KI sieht kcal, Protein und Nutri-Score als Spalten.

**Mengen in Schritt-Texten** (`kueche.mengen_skalieren`): Schritte stehen für die Grundportionen.
Beim Anzeigen für andere Portionen rechnet der Code Zahl + Mengeneinheit (g, ml, EL, Zehe, Dose …)
und Zahl + Zutat des Rezepts um, Zeiten, °C und cm bleiben. Kein gespeicherter Platzhalter, damit
es auch mit den Original-Schritten aus Grocy funktioniert.

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

### Web-App (0.14.0)

Neu aufgebaut auf Wunsch Nils: Vanilla-ES-Module ohne Build (`app/static/`), Hash-Router,
jede Seite ein Modul unter `static/js/views/`, gemeinsame Helfer in `static/js/lib.js`. Alle
Pfade relativ (Ingress), statische Dateien mit `Cache-Control: no-cache`, Module mit `?v=`.

- **Navigation:** Seitenleiste mit Gruppen und Unterpunkten (einklappbar, gemerkt im Browser),
  auf dem Handy als Burgermenü. Globale Suche (Taste `/`) über Seiten, Rezepte und Produkte.
  Badges für offene Dubletten und KI-Vorschläge. Hell/Dunkel/System.
- **Küche:** Übersicht (Heute, läuft bald ab, Woche, Zu tun, Reste, Vorrat), Heute kochen mit
  Bewertung, Wochenplan als Kalender (Tage anklicken, Schnellwahl, Rezept direkt eintragen,
  Planer mit Reglern und Ausschluss-Tags), Einkauf nach Kategorie gruppiert mit Kosten.
- **Rezepte:** Rezeptbuch (Tabelle oder Karten, Suche, Tags, kochbar, Favoriten, „Rezepte mit
  Produkt X“), Massenaktionen (Tags, Aufwand, Status, Favorit, Löschen), Leser mit Meinung und
  Verlauf, Editor, Import per Link.
- **Vorrat:** Inventar je Charge (Filter bald/abgelaufen/gefroren/geöffnet, bearbeiten, Massen:
  verbraucht, verdorben, umlagern, geöffnet, Ablauf verschieben), Einbuchen mehrzeilig und Bon,
  Reste und Füllstand.
- **Datenbank:** alle Produkte inkl. „bekannt, nicht da“ und „in keinem Rezept“, Massen setzen
  (Kategorie, Modus, Lagerort, Toleranz, Haltbarkeiten, Variante), gezielt KI kategorisieren,
  Löschen mit Schutz; Dubletten Feld für Feld; Packungsgrößen; KI-Vorschläge als Diff.
- **System:** Einstellungen (Wochenrhythmus, Planer, Präferenzen, Anzeige, App-Optionen nur
  lesend), Änderungsprotokoll, KI-Protokoll.

**Protokolle (`app/protokoll.py`):** Jeder schreibende `/api/v1/`-Aufruf landet in
`aenderung_log` (Quelle `ui`/`bridge`/`extern` über Header `X-Ration-Client` bzw. Ingress-IP,
Body ohne Geheimnisse, max. 8 kB, die letzten 3000). Jede `ai_task`-Anfrage landet mit
Anweisung, Schema, Anhängen, Rohantwort, Fehler und Dauer in `ki_log` (die letzten 500).

**Löschen:** Produkte nur ohne Bestand, Varianten und Rezeptverwendung (Grocy löscht sonst
kaskadierend mit); Rezepte entfernen geplante, nicht gekochte Tage. Beides nur nach Rückfrage
in der UI.

### Rezeptbuch und Produktdatenbank (Web-App)

Wunsch Nils (2026-11-22), Go am selben Abend. Umgesetzt in 0.8.0 (`app/katalog.py`,
`app/dubletten.py`, Tabs *Rezepte* und *Produkte*). Noch offen: Bewertung/Daumen als Filter,
Toleranz-Override je Zutat im Editor.

- **Rezeptbuch:** Kachel-/Listenansicht zum Stöbern mit Suche und Filtern (Tags, Aufwand,
  Status getestet/ungetestet, Bewertung, „jetzt kochbar“). Detailansicht als Rezeptleser:
  Zutaten skaliert auf Portionen mit Bestandsampel, Schritte, Quelle. Bearbeiten: Name,
  Portionen, Zutaten (Produkt, Menge, Toleranz-Override), Schritte, Tags. Schreibt über
  `InventoryBackend` nach Grocy bzw. in `rezept_meta`.
- **Produktdatenbank:** Liste aller Produkte mit Tracking-Modus, Kategorie, Einheit/Gramm-
  Umrechnung, Standard-Packung und weiteren Größen, Mindestbestand, Aliasen (Rezept/Bon),
  Nährwert-Zuordnung, Verwendung in Rezepten. Inline bearbeiten, Aliase umhängen.
- **Dubletten-Check:** Kandidatenpaare aus Namensähnlichkeit (normalisierter Name/Kern-Name,
  Schwelle ca. 0,90–0,95, konfigurierbar) plus gleiche Aliase. Ansicht zeigt beide Produkte
  nebeneinander (Bestand, Packungen, Aliase, Rezepte, Attribute) mit zwei Buttons:
  - **Mergen:** Nutzer wählt das Ziel. Bestand, Rezeptzutaten, Packungen, Aliase, Preise und
    Nährwert-Zuordnung werden auf das Ziel umgehängt, das andere Produkt wird in Grocy
    entfernt (Grocy-Merge-Endpunkt `/stock/products/{keep}/merge/{remove}` per Contract-Test
    prüfen, sonst schrittweise). Der Klick ist die Bestätigung fürs Löschen; `id_map` hält den
    Verweis, damit alte IDs (Pläne, Logs) auflösbar bleiben.
  - **Nicht mergen:** Paar landet in `dublette_entscheidung` und taucht nie wieder auf.
  Läuft nach jedem Import und im monatlichen Kategorisierungslauf; das LLM darf Paare
  vorschlagen, entscheidet aber nie selbst.

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
  Reste und Füllstand-Abfrage erledigt (0.9.0): übrige Portionen werden als Produkt
  „Rest: <Rezept>“ (Kategorie Rest, Einheit Portion, Haltbarkeit aus `rest_haltbarkeit_tage`,
  sonst 3 Tage) in den Kühlschrank gebucht. Nach „Gekocht“ fragt die App Füllstand-Zutaten
  ab (je Produkt höchstens alle 14 Tage); knapp/leer landen mit einer Packung auf der Einkaufsliste.
- **Phase 1 – Grundgerüst + Kochen:** Ingress-UI, Bestandsansicht, manuelles Einbuchen,
  Gekocht-Flow mit Portionen und Resten, Bridge-Entitäten, Küchen-Card, Statuszeile, Widgets.
- **Phase 2 – Kassenbon:** Vorverarbeitung, Alias, `ai_task`, Review, Push.
- **Phase 3 – Wochenplaner:** Kategorisierungslauf (vorgezogen, sobald Daten da sind),
  Scoring, Auswahl, Simulation mit Toleranz, Repair-Loop, Einkauf, Plan-Ansicht, Nährwerte,
  einfache Mahlzeiten, „Jetzt kochbar“.
- **Phase 4 – Rezept-Import + Kuratierung:** Import per Link (erledigt, inkl. KI-Zuordnung),
  Rezeptbuch (Stöbern, Lesen, Bearbeiten), Produktdatenbank-Ansicht, Dubletten-Check mit
  Mergen/Nicht mergen (erledigt, 0.8.0), Daumen hoch/runter (erledigt, 0.14.0).
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
