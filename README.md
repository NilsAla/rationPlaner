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
`ration.lagerorte`, `ration.rezepte`, `ration.plan`. Schreibend: `ration.plan_setzen`,
`ration.plan_loeschen`, `ration.einbuchen`. Listen kommen als `{"items": [...]}`.

## LLM und MCP

Die Bridge registriert die LLM-API **rationPlaner**. Jede Aktion oben ist dort ein Tool
(`ration_rezepte`, `ration_plan_setzen`, …).

- **MCP Server von HA:** Integration „Model Context Protocol Server“ hinzufügen bzw. neu
  konfigurieren und als LLM-API „rationPlaner“ (zusätzlich zu Assist) auswählen.
- **Assist:** im Gesprächsagenten (OpenAI/Anthropic) unter „Home Assistant steuern“
  „rationPlaner“ aktivieren.
- **ha-mcp o. Ä.:** die Services oben mit `return_response` aufrufen.

Test: `ration.rezepte` unter Entwicklerwerkzeuge → Aktionen mit „Antwort“ ausführen.

## Testrezepte

Button „Testrezepte in Grocy anlegen“ im Tab *Woche* (oder `POST /api/v1/testdaten`) legt
2 Rezepte samt 6 Produkten an. Idempotent, löscht nichts.

Das System startet ohne Rezepte und Produkte. Befüllung per Import und LLM-Kategorisierung,
siehe Konzept, Abschnitt „Stammdaten-Aufbau“.
