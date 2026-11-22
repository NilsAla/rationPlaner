# rationPlaner

Küchenbestand, Wochenplan und Einkaufsliste für Home Assistant, mit Grocy als Backend.
Ziel: Nichts verdirbt, Packungen werden über mehrere Gerichte hinweg aufgebraucht.
Konzept: [`docs/konzept.md`](docs/konzept.md). Status: Durchstich (Einbuchen, Plan, Gekocht, Einkaufsliste), noch ohne Bon-Import und Planer.

## Bestandteile

| Pfad | Inhalt |
|---|---|
| `ration/` | HA-App (Add-on): FastAPI, SQLite, Ingress |
| `custom_components/ration/` | Bridge-Integration (HACS): Entitäten und Services |
| `lovelace/` | Küchen-Card |
| `scripts/` | Hilfsskripte für Grocy (Setup, Probe) |
| `docs/` | Konzept |

## Installation

Voraussetzung: Grocy als HAOS-App mit API-Key, eine AI-Task-Entität (OpenAI oder Anthropic)
und eine To-do-Entität für die Einkaufsliste.

1. **App:** Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories →
   `https://github.com/NilsAla/rationPlaner` hinzufügen, „rationPlaner“ installieren.
2. **Konfigurieren:** `grocy_url`, `grocy_api_key`, `ai_task_entity`, `todo_entity` setzen und starten.
   Die App erscheint in der Seitenleiste.
3. **Bridge:** In HACS das Repo als benutzerdefiniertes Repository (Typ Integration)
   hinzufügen, installieren, HA neu starten, Integration „rationPlaner Bridge“ hinzufügen und
   die interne URL der App eintragen (z. B. `http://<repo-hash>-ration:8099`, siehe App-Info).
4. **Token (empfohlen):** App-Option `api_token` auf einen zufälligen Wert setzen (z. B.
   `openssl rand -hex 24`) und denselben Wert in der Bridge eintragen. Ohne Token ist die API im
   internen hassio-Netz offen; die Web-App über Ingress braucht nie ein Token. Setzt man das
   Token nachträglich, fragt HA per Reparatur-Hinweis („Erneut authentifizieren“) danach.

## Was die Bridge liefert

| Entität / Service | Inhalt |
|---|---|
| `sensor.ration_rezept_heute` | Rezept aus dem Plan für heute, Attribute: Zutaten (skaliert), Schritte, Portionen |
| `sensor.ration_status` | Statuszeile „24 Artikel · 3 laufen bald ab“ |
| `sensor.ration_laeuft_ab` | Anzahl Chargen, die in 3 Tagen ablaufen, Liste als Attribut |
| `ration.gekocht` | Bucht ab (Rezeptmenge × Portionen, FEFO). Ohne `rezept_id` das Rezept von heute |
| `ration.einkaufsliste_erzeugen` | Fehlmengen der geplanten Gerichte in die To-do-Liste aus `todo_entity` |

Beide Services liefern eine Antwort (`response_variable`), z. B. was gefehlt hat.

Lesend (nur mit Antwort): `ration.heute`, `ration.status`, `ration.bestand`, `ration.produkte`,
`ration.lagerorte`, `ration.rezepte`, `ration.plan`, `ration.packungen`. Schreibend:
`ration.plan_setzen`, `ration.plan_loeschen`, `ration.einbuchen`, `ration.packung_setzen`,
`ration.packung_hinzufuegen`, `ration.packung_loeschen`, `ration.rezept_import_vorschau`
(nur lesend), `ration.rezept_import_uebernehmen`. Listen kommen als `{"items": [...]}`.

## LLM und MCP

Die Bridge registriert die LLM-API **rationPlaner**. Jede Aktion oben ist dort ein Tool
(`ration_rezepte`, `ration_plan_setzen`, …).

- **MCP Server von HA:** Integration „Model Context Protocol Server“ hinzufügen bzw. neu
  konfigurieren und als LLM-API „rationPlaner“ (zusätzlich zu Assist) auswählen.
- **Assist:** im Gesprächsagenten (OpenAI/Anthropic) unter „Home Assistant steuern“
  „rationPlaner“ aktivieren.
- **ha-mcp o. Ä.:** die Services oben mit `return_response` aufrufen.

Test: `ration.rezepte` unter Entwicklerwerkzeuge → Aktionen mit „Antwort“ ausführen.

## Packungsgrößen

Eingekauft wird in ganzen Packungen. Standard-Packung je Produkt = Einkaufseinheit in Grocy
(mit Umrechnung), weitere Größen (400/800 g, Angebote mit Ablauf) in der App. Pflege im Tab
*Packungen* oder per Service. Die Einkaufsliste wählt die Kombination mit dem kleinsten Überhang.

## Rezept-Import per Link

Tab *Import*: Link einfügen (z. B. HelloFresh), Vorschau prüfen, je Zutat Produkt und Menge
bestätigen, übernehmen. Übernommen werden Name, Portionen, Zutaten und die
Zubereitungsschritte (aus `recipeInstructions`, Fließtext wird an „1.“, „2.“ bzw. Zeilenumbrüchen
getrennt, vor dem Übernehmen editierbar); Bilder nicht, der Link zur Quelle steht unter den Schritten. Dubletten werden über die Quell-ID erkannt, bestätigte
Zuordnungen als Alias gemerkt. Ist `ai_task_entity` gesetzt (z. B. `ai_task.openai_ai_task`),
ordnet die KI offene Zutaten Produkten zu und schätzt fehlende Gewichte; der Code validiert und
rechnet. Per MCP: `ration_rezept_import_vorschau` →
`ration_rezept_import_uebernehmen`.

## Rezeptbuch, Produkte, Dubletten

Tab *Rezepte*: Suchen, Filter „Jetzt kochbar“ und Tags, Rezeptleser mit Portionen-Skalierung
und Bestandsampel, „Heute kochen“, Bearbeiten (Zutaten, Schritte, Tags, Status, Aufwand).
Tab *Produkte*: alle Produkte mit Attributen, Bestand, Packungen, Aliasen und Rezepten,
Filter „ohne Attribute“, Bearbeiten. Oben stehen mögliche Dubletten (Namensähnlichkeit ab
Option `dubletten_schwelle`, Standard 0,9) nebeneinander mit **Mergen** (Grocy-Merge: Bestand,
Rezeptzutaten, Varianten; App: Aliase, Packungen) und **Nicht mergen** (Paar wird gemerkt).
Mergen gibt es als Service `ration.dubletten_mergen`, bewusst nicht als LLM-Tool.

## Reste und Füllstand

„Gekocht“ bucht übrige Portionen als „Rest: <Rezept>“ in den Kühlschrank (Haltbarkeit aus der
Rest-Haltbarkeit des Rezepts, sonst 3 Tage). Tab *Bestand* zeigt Reste mit „1 gegessen“ und
„weg“ (verdorben) sowie alle Füllstand-Produkte (voll/knapp/leer). Nach dem Kochen fragt die App
den Füllstand verwendeter Füllstand-Zutaten ab, je Produkt höchstens alle 14 Tage. knapp und leer
kommen mit einer Packung auf die Einkaufsliste. API: `GET /reste`, `GET/PUT /fuellstand`,
`POST /bestand/abbuchen`.

## Kassenbon

Tab *Bon*: Foto aufnehmen oder wählen, hochladen. Die KI (`ai_task_entity`) liest den Bon im
Hintergrund, danach kommt eine HA-Benachrichtigung (optional zusätzlich an `notify_service`,
z. B. `notify.mobile_app_handy`). In der Review sind bekannte Bon-Texte und sichere KI-Treffer
vorausgewählt; Menge, Produkt und „neues Produkt“ lassen sich ändern, dann „Ausgewählte buchen“.
Jede gebuchte Zeile wird als Alias (Markt + Bon-Text) und mit Preis gemerkt. Per Service:
`ration.bon_aus_media` (Bild unter `/media`), `ration.bon_details`, `ration.bon_buchen`.

## Testrezepte

Button „Testrezepte in Grocy anlegen“ im Tab *Woche* (oder `POST /api/v1/testdaten`) legt
2 Rezepte samt 6 Produkten an. Idempotent, löscht nichts.

Das System startet ohne Rezepte und Produkte. Befüllung per Import und LLM-Kategorisierung,
siehe Konzept, Abschnitt „Stammdaten-Aufbau“.
