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

Das System startet ohne Rezepte und Produkte. Befüllung per Import und LLM-Kategorisierung,
siehe Konzept, Abschnitt „Stammdaten-Aufbau“.
