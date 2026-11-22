# Web-UI

Die Web-UI liegt seit 0.14.0 unter `ration/app/static/` und braucht keinen Build:
`index.html` (Gerüst), `app.css`, `js/main.js` (Menü, Router, Suche), `js/lib.js` (Helfer,
API-Client, Tabelle mit Mehrfachauswahl, Dialoge) und je Seite ein Modul in `js/views/`.

Neue Seite: Modul `js/views/<name>.js` mit `export default async function (el, {arg, params})`
anlegen und in `MENUE` bzw. `DETAIL` in `main.js` eintragen. Alle Pfade relativ halten
(Ingress), API nur über `api()`/`post()`/… aus `lib.js` (setzt `X-Ration-Client: ui` fürs
Änderungsprotokoll).
