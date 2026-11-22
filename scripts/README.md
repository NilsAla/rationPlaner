# Hilfsskripte

Laufen gegen eine echte Grocy-Instanz. Zugang über `RATION_GROCY_URL` und
`RATION_GROCY_API_KEY` (Umgebung oder `.env` im Repo-Root, Vorlage `.env.example`).
Vorher einmal `pip install -e "ration[dev]"`.

Grocy-API von außen erreichbar machen: in der Grocy-App unter Netzwerk einen Port
setzen (z. B. 9192), API-Key in Grocy unter Einstellungen → API-Schlüssel anlegen.

## `grocy_setup.py`

Legt idempotent an, was rationPlaner braucht. Löscht nichts, legt keine Produkte oder
Rezepte an. Mehrfach ausführbar.

- Einheit `g`
- Lagerorte Kühlschrank, Vorrat, Gefrierschrank (als Gefrierort)
- Userfields Produkte: `tracking_modus`, `kategorie`, `fuellstand`, `toleranz_pct`
- Userfields Rezepte: `status`, `aufwand`, `tags`, `rest_haltbarkeit_tage`

## `grocy_probe.py`

Prüft die Grocy-Semantik, auf der die App aufbaut, und gibt eine Tabelle aus. Dieselben
Fälle laufen als Contract-Tests gegen das `FakeBackend` und in CI gegen ein Grocy im
Container.

| Frage | Grocy 4.7.1 |
|---|---|
| Erfüllt eine Variante ein übergeordnetes Produkt (Bestand + Abbuchung)? | ja |
| Bucht Abbuchen die Charge mit frühestem Ablauf zuerst ab? | ja |
| Setzt Umbuchen in den Gefrierort das neue Ablaufdatum? | ja, heute + Haltbarkeit eingefroren |
| Setzt Umbuchen aus dem Gefrierort das Auftau-Datum? | ja, heute + Haltbarkeit aufgetaut |
| Wird Überbuchung abgelehnt, ohne Bestand zu ändern? | ja |

Legt dafür eigene Testobjekte an (Produkte und Lagerorte mit Präfix `zz ration test`),
bringt deren Bestand am Ende auf 0 und löscht nichts. Die Objekte kannst du nach dem
Lauf in Grocy von Hand entfernen oder für spätere Läufe stehen lassen.
