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
| Generiert | Prompt in der App | KI-Job `rezept`, Katalog und optional Bestand als Kontext, dann Import-Vorschau | ungetestet |
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
Hackfleischzubereitung→Hackfleisch, Öl→Pflanzenöl; Salz-/Kochwasser wird weggelassen. Feste
Begriffe (saure Sahne, Dosentomaten). Synonyme, Kräutermix und feste Begriffe sind seit 0.29.0
wieder raus, das entscheidet der KI-Schritt. Chefkoch-PLUS-Rezepte (Paywall, kein JSON-LD) werden abgelehnt; der
Überschriften-Fallback braucht Zubereitungsschritte, sonst kein Rezept.

**KI-Schritt** (`app/llm_import.py`, seit 0.29.0, wenn für den Job „import“ eine KI eingerichtet
ist, abschaltbar mit `ki: false`): Der Code liefert nur **sichere** Treffer selbst: bestätigter
Alias oder gleicher voller Name (alle Wortstämme inklusive Klammern und Zusätzen, nur
Plural-Marker wie „(n)“ fallen weg). Treffer über den Kern-Namen („rote Zwiebel“ → Zwiebel) sind
ein Vorschlag. Alles nicht Sichere geht in **einem** Aufruf je Rezept mit dem Katalog
(id|name|einheit|kategorie|oberprodukt) an die KI. Sie liefert je Zutat Kategorie, Treffer oder
„neu“, Gramm je Einheit (auch bei Treffern, damit Dose/Tube eine Menge bekommen), Stückgewicht und
für neue Produkte Tracking-Modus, Ablauf-Typ, Haltbarkeit (ungeöffnet/geöffnet) und „Variante
von“. **Prüfung im Code:** Ein KI-Treffer zählt nur, wenn die Kategorie der Zutat zur Kategorie des
Katalogprodukts passt (Lauchzwiebel → Frühlingszwiebel ja, Paprika edelsüß → Gemüse-Paprika nein);
ohne Kategorie am Produkt gilt wie früher die Namensähnlichkeit (live: Salz → Olivenöl). Werte
neuer Produkte laufen durch dieselben Regeln wie der Kategorisierungslauf (`produkt_werte`),
Varianten nur auf Produkte ohne eigenes Oberprodukt und mit gleicher Einheit. Den Namen neuer
Produkte darf die KI vorschlagen, der Code nimmt ihn nur, wenn jedes Wort im Zutatentext steht
(„Stückige Tomaten“ ja, erfundenes „Schmand“ für „saure Sahne“ nein); sonst Kern-Name, bei
Namensgleichheit der volle Name („Paprika edelsüß“). Je Zutat listet der Code bis zu fünf
namensähnliche Katalogprodukte als Kandidaten (live übersah gpt-4.1 sonst „Paprikapulver“).
Modell: der Import-Job läuft wie die Kategorisierung über gpt-4.1 (Temperatur 0,2);
gpt-4o-mini mit Temperatur 1 war live inkonsistent. Vor der Entscheidung schreibt die KI je Zutat
einen Satz `pruefung` (welches Katalogprodukt, dasselbe/Sorte/anders); ohne dieses Feld kippte
gpt-4.1 bei Paprikaschote je nach Prompt-Detail zwischen Treffer und Variante. Ein Code-Vorschlag
„neu“ wird nicht angezeigt (verankerte die KI). Varianten nur in gleicher Kategorie.
**Live-Test 0.29.9 (2026-11-24, leerer Katalog, 25 Rezepte HelloFresh/Chefkoch/Magazin,
Ein-Klick):** 117 Produkte, alle mit Modus/Kategorie/Haltbarkeit. Korrekt u. a. Paprikaschote,
gelbe/rote Paprika, Paprika edelsüß → Paprikapulver, Lachs → Lachsfilet, Gemüsebrühepulver →
Gemüsebrühe, Dose/Tube/Becher/Paket mit Menge, saure Sahne und Dosentomaten getrennt.
Fehler: Pflanzenöl neben Öl, Spaghetti einmal Spaghettini, einmal neu; Grenzfälle Schafskäse/Feta,
Peperoni/Chilischote; Varianten teils schief (Couscous → Jasminreis, Reis → Jasminreis statt
umgekehrt, getrocknete Tomaten → Tomate). Kosten ≈ 1,5 ct je Rezept (gpt-4.1, viel Ausgabe durch
Begründung und Attribute), Vorschau bis ≈ 19 s.

**Modellwahl Import (Entscheidung Nils 2026-11-24): pragmatischer Katalog, gpt-6.2-sol über
„OpenAI direkt“** (Job `import`, echte Tokens und Kosten im KI-Protokoll; Preise in
Einstellungen → KI, € je 1 Mio. Tokens). Benchmark: künstliches Rezept mit 30 kniffligen
Zutaten gegen den Testkatalog (117 Produkte), je Lauf echte Kosten:

| Modell | genau richtig | + als richtige Variante | Kosten/Lauf | Dauer |
|---|---|---|---|---|
| gpt-4.1 | 23–26 | +0–1 | 2,9 ct | 14 s |
| gpt-6-luna | 26–28 | +1 | 0,2 ct | 30 s |
| gpt-6.1-terra | 24–25 | einzelne | 1,8 ct | 40 s |
| gpt-6.2-sol | 24 | +4 | 4,9 ct | 58 s |
| gpt-6.2-astra | 23 | +6 | 13,7 ct | 40 s |
| **gpt-6.2-sol + pragmatische Regel (0.29.11)** | **29, 29** | +1 | 3,9 ct | 50–57 s |

Die stärkeren Modelle waren nicht schlechter, sondern feiner (Rinderhack, Fleischtomaten,
Sonnenblumenöl als eigene Varianten). Gewollt ist ein pragmatischer Haushaltsvorrat: im Haushalt
austauschbare Sorten sind ein Treffer, Varianten nur für bewusst getrennt gekaufte Sorten, die das
Gericht verändern (Vollkorn-Penne, Kirschtomaten, Olivenöl). Die Beispiele im Prompt stammen
bewusst nicht aus dem Benchmark. Typisches Rezept (8–15 offene Zutaten): ≈ 1,5–2,5 ct.
**Hintergrund-Import (0.30.0, `app/importlauf.py`):** `POST /import/start` legt den Vorgang an
und rechnet im Hintergrund (Schritte: Seite lesen → Zuordnen → KI prüft n Zutaten);
`GET /import/{id}` liefert Fortschritt 0–100 und Restzeit, am Ende die Vorschau. Den Fortschritt
der KI kennt niemand: Schätzung aus dem Median der letzten fünf echten Import-Laufzeiten im
KI-Protokoll, der Balken bleibt bei 95 % stehen, bis die Antwort da ist. Hängt ein Vorgang länger
als 15 min (App neu gestartet), gilt er als abgebrochen. UI: Balken auf der Import-Seite, auf allen
Seiten oben rechts ein Kreis mit Prozent, nach Abschluss ein Haken, Klick öffnet die Vorschau
(`#/import?id=…`, gemerkt je Gerät). `POST /import/vorschau` bleibt synchron (Tests, Bridge). Die Vorschau
zeigt alles, der Klick auf „Übernehmen“ ist das Review (ab 0.31.0 farbig je Zeile: grün =
sicherer oder „gleich“-Treffer, gelb = Oberbegriff/austauschbare Sorte laut KI-Feld `treffer`,
blau = neu, rot = Menge fehlt oder KI widerspricht; Antippen öffnet eine Suche über alle Produkte
mit Kategorie und Anzahl Rezepte, `GET /produkte?nutzung=true`); neue Produkte entstehen dann mit
Attributen und Oberprodukt. Aliase merken die Quelle: unverändert übernommene KI-Vorschläge
(`quelle = ki`) gelten beim nächsten Import nicht als sicher und gehen wieder an die KI, damit sich
ein Fehlgriff nicht festsetzt. Kosten: Katalog ≈ 15 Tokens je Produkt und Aufruf (200 Produkte
≈ 3k Tokens, gpt-4.1-mini ≈ 0,1 ct je Rezept). Fällt die KI aus, bleibt der Code-Vorschlag.
Hart codiert bleiben nur Mengen und Einheiten; Produkt-Synonyme und „feste Begriffe“ aus 0.28.10
sind wieder entfernt (Live-Test: hart codierte Regeln haben selbst Dubletten erzeugt).

**Rezept generieren** (0.35.0, `app/rezept_ki.py`, Idee Nils 2026-11-27): Wunsch als Freitext,
Portionen, Aufwand (schnell/normal/aufwändig), optional „Vorrat nutzen“ (Bestand je Produkt mit
frühestem MHD, bald Ablaufendes zuerst, ohne Immer-da). Der Prompt enthält immer die
Katalognamen, damit die KI Zutaten so nennt wie bei uns. Die KI (Job `rezept`) liefert Name,
Portionen, Zutatenzeilen als Text („200 g Crème fraîche“) und **die Kochanleitung direkt im
Kochmodus-Format** (Schritte mit Zutaten-Indizes, Teilmengen, Timer, nebenher; Entscheidung
Nils: kein zweiter Kochanleitungs-Aufruf). Der Code prüft die Schritte wie bei der
Kochanleitung (`schritt_pruefen`, eine Reparaturrunde, fehlende Zutaten ordnet notfalls der Code
zu). Entwürfe liegen mit allen Versionen in `rezept_entwurf`; Änderungswünsche („statt Sahne
Crème fraîche“) gehen mit dem Entwurf als Vorlage an die KI und hängen eine neue Version an, jede
Version bleibt wählbar. Nach Grocy geht nichts direkt: „Übernehmen“ startet den normalen
Hintergrund-Import mit dem Entwurf als Rohrezept (Quelle `ki`, Quell-ID `ki-<Entwurf>-<Version>`,
kein Quellenlink). Zutatenzeilen laufen durch den Parser des Link-Imports, Zuordnung und Gramm
rechnet der Code bzw. die Import-KI, der Nutzer prüft wie beim Link (Grundregeln 1 und 4).
Die Entwurfsansicht zeigt je Zutat den Code-Abgleich mit dem Katalog (0.35.1, Wunsch Nils): aus
unserer DB (Alias/gleicher Name, mit Bestand), ähnliches Produkt (Kern-Name), neu oder
weggelassen. Zutaten-Indizes der Schritte sind eine feste Auswahl 0–39 wie bei der
Kochanleitung; als freie Textliste ließ gpt-4o-mini sie live in allen Schritten leer. Als Auswahl
verzählte sich gpt-4o-mini (Champignons statt Knoblauch), deshalb prüft der Code jeden Schritt
gegen seinen Text (0.35.2, `text_abgleich`): Index ohne Erwähnung fällt weg, erwähnte Zutaten
kommen dazu. Grün im Abgleich heißt Alias (auch von der KI) oder gleicher voller Name.

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

**Anleitung schon beim Import (0.31.0):** Die Vorschau startet den Job `kochanleitung` parallel
zur KI-Zuordnung (`asyncio.gather`, Wartezeit = der längere Aufruf, Schätzung aus beiden
Medianen). Weil die Produkte da noch nicht feststehen, verweisen die Schritte auf die
Import-Zeilen (Index, Zutatentext + Menge). Eigener Aufruf statt eines gemeinsamen Prompts: der
Zuordnungs-Prompt trägt den Katalog, die Anleitung braucht ihn nicht; jeder Job behält sein
Modell, und scheitert die Anleitung, bleibt die Zuordnung (dann Code-Vorschlag aus den
Originalschritten). Beim Übernehmen rechnet der Code die Indizes auf die Rezept-Zutaten um
(weggelassene Zeilen fallen heraus) und speichert die Anleitung als `geprueft`, weil sie im
Import gesehen wurde. Leiter-Icon und Sammellauf bleiben für alte Rezepte.

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

**Umsetzung (0.32.0, `app/angebote.py`):** Recherche „Prospekt-Quellen für rationPlaner“
(Claude-Doc, 2026-11-25): alle fünf Händler (Penny, Kaufland, Lidl, K+K, EDEKA) liefern ohne
Login, ein Headless Browser ist nicht nötig. Penny zuerst, weil JSON mit Preisen je
Verkaufsregion (`/.rest/market` → `sellingRegion`, Kategorien aus `/angebote`,
`/.rest/offers/by-category/{KW}/{kategorie}?region=`). Gültigkeit aus dem Abschnitt
(`ab-montag`/`ab-donnerstag`/`ab-freitag`) bis Samstag. Größe parst der Code.

- **Zuordnung:** gemerkter Titel je Händler (`angebot_alias`) > Namensregel im Code
  (Kopfwort des Titels = Kopfwort des Produkts, Komposita als Variante) > KI-Job `angebote` (nur IDs aus
  dem Katalog, `gleich`/`variante`/`keins`, unbekannte ID verworfen). KI-Treffer zählen sofort
  als Overlay im Planer-Scoring, Preise (`preis.angebot_id`, Quelle angebot/streichpreis),
  Alias und Angebots-Packung (abweichende Größe, gültig bis Aktionsende) erst nach ✓.
- **Kosten:** Angebotspreise älter als eine Woche zählen nicht mehr (aktueller Angebotspreis).
- **Kaufland (0.33.0):** serverseitig gerenderte Angebotsseite, Filiale per Cookie
  `x-aem-variant`, nur Hauptkategorien `cat_01`–`cat_08`. Kein Vision-Modell nötig.
- **Zeitplan:** stündliche Prüfung, höchstens ein Abruf am Tag ab 6 Uhr, solange die aktuelle
  Woche fehlt, ab Mittwoch auch die nächste. **Healthcheck:** `GET /angebote/status`, Abruf
  ohne Lebensmittel oder mit Fehler = kaputt, HA-Hinweis beim Wechsel ok → kaputt.

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

- **Navigation (0.36.0, Umbau auf Wunsch Nils):** sieben Bereiche mit Tabs statt 25
  Menüpunkten (`BEREICHE` in `static/js/main.js`): Start · Kochen (Heute, Wochenplan,
  Kochmodus) · Rezepte (Rezeptbuch, **Neu & KI**) · Vorrat (Bestand, **Einbuchen**, Reste &
  Füllstand) · Einkauf (Liste, Angebote, Ausgaben) · Produkte (Produkte, KI-Kategorisierung,
  Dubletten, Packungsgrößen) · System. „Neu & KI“ und „Einbuchen“ sind Auswahl-Tabs: oben
  Kacheln für KI generieren / Per Link / Von Hand / Kochanleitungen bzw. Bon & von Hand /
  Barcode. Jede Seite behält ihre Route (alte Links gelten weiter); gemerkt wird je Bereich
  und Auswahl die zuletzt besuchte Seite. Desktop: Seitenleiste mit den Bereichen, im aktiven
  die Tabs. Handy: Leiste unten (Start, Kochen, Rezepte, Vorrat, Einkauf, Mehr), Tabs
  wischbar unter der Kopfzeile, Detailseiten mit Zurück-Pfeil, im Kochmodus ohne Leisten.
  Globale Suche (Taste `/`) über Seiten, Rezepte und Produkte. Badges für offene Dubletten
  und KI-Vorschläge. Hell/Dunkel/System.
- **Küche:** Übersicht (Heute, läuft bald ab, Woche, Zu tun, Reste, Vorrat), Heute kochen mit
  Bewertung, Wochenplan als Kalender (0.36.1, Wunsch Nils: schneller von Hand
  planen): freier Tag mit ＋ → Rezeptsuche (kochbare zuerst, Portionen-Stepper), × entfernt,
  Antippen öffnet Portionen/Tag/anderes Rezept/KI-Tausch, Ziehen verschiebt (belegt: tauschen;
  Pointer Events, am Handy 300 ms lange drücken, sonst scrollt die Seite). Am Handy Tagesliste
  ab heute statt 7 Spalten. Die Tagesauswahl für den Planer liegt als Chips in dessen Karte,
  Regler unter „Wünsche und Optionen“, Einkauf nach Kategorie gruppiert mit Kosten.
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
