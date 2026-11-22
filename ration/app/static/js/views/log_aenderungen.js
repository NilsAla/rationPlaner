import {$, api, dialog, esc, guarded, zeit} from "../lib.js";

// Was hat wer geändert: jeder schreibende API-Aufruf, Quelle ui/bridge/extern
const MUSTER = [
  [/^POST gekocht$/, "Gekocht gebucht"], [/^PUT plan\/([^/]+)$/, "Plan $1 gesetzt"], [/^DELETE plan\/([^/]+)$/, "Plan $1 entfernt"],
  [/^POST plan\/erzeugen$/, "Wochenplan vorgeschlagen"], [/^POST plan\/laeufe\/\d+\/uebernehmen$/, "Wochenplan übernommen"],
  [/^POST plan\/([^/]+)\/tauschen$/, "Tag $1 getauscht"], [/^POST bestand$/, "Eingebucht"], [/^POST bestand\/abbuchen$/, "Abgebucht"],
  [/^PATCH inventar\/(.+)$/, "Charge $1 geändert"], [/^POST inventar\/aktion$/, "Inventar-Massenaktion"],
  [/^POST produkte$/, "Produkt angelegt"], [/^PATCH produkte\/(.+)$/, "Produkt $1 geändert"], [/^DELETE produkte\/(.+)$/, "Produkt $1 gelöscht"],
  [/^POST produkte\/aktion$/, "Produkt-Massenaktion"], [/^POST rezepte$/, "Rezept angelegt"], [/^PATCH rezepte\/(.+)$/, "Rezept $1 geändert"],
  [/^DELETE rezepte\/(.+)$/, "Rezept $1 gelöscht"], [/^POST rezepte\/aktion$/, "Rezept-Massenaktion"], [/^PUT rezepte\/(.+)\/meinung$/, "Meinung zu Rezept $1"],
  [/^PUT kochvorgaenge\/(\d+)\/bewertung$/, "Kochvorgang $1 bewertet"], [/^PUT fuellstand\/(.+)$/, "Füllstand $1"],
  [/^POST dubletten\/mergen$/, "Dubletten zusammengeführt"], [/^POST dubletten\/ignorieren$/, "Kein Duplikat gemerkt"],
  [/^PUT aliase$/, "Alias gesetzt"], [/^DELETE aliase\/(.+)$/, "Alias entfernt"], [/^POST kategorisieren$/, "KI-Kategorisierung gestartet"],
  [/^POST vorschlaege$/, "KI-Vorschläge entschieden"], [/^PUT einstellungen\/(.+)$/, "Einstellungen $1"],
  [/^DELETE einstellungen\/(.+)$/, "Einstellungen $1 zurückgesetzt"], [/^POST bon$/, "Bon hochgeladen"], [/^POST bon\/(\d+)\/buchen$/, "Bon $1 gebucht"],
  [/^POST import\/vorschau$/, "Import-Vorschau"], [/^POST import\/.+\/uebernehmen$/, "Rezept importiert"], [/^POST einkaufsliste$/, "Einkaufsliste befüllt"],
];
const beschreiben = (m, pfad) => {
  const k = `${m} ${decodeURIComponent(pfad.replace(/^\/api\/v1\//, ""))}`;
  for (const [re, t] of MUSTER) if (re.test(k)) return k.replace(re, t);
  return k;
};

export default async function render(el, {params}) {
  const quelle = params.quelle ?? "", pfad = params.pfad ?? "";
  const liste = await api(`log/aenderungen?limit=500&quelle=${encodeURIComponent(quelle)}&pfad=${encodeURIComponent(pfad)}`);
  el.innerHTML = `<div class="card"><div class="werkzeug">
      <input type="search" id="pfad" placeholder="Pfad enthält … (z. B. produkte)" value="${esc(pfad)}">
      <select id="quelle">${[["", "Alle Quellen"], ["ui", "Web-UI"], ["bridge", "HA-Integration"], ["extern", "Extern"]].map(([k, t]) =>
        `<option value="${k}" ${k === quelle ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <p class="muted tiny">Jeder schreibende Aufruf der App, Geheimnisse geschwärzt. Behalten werden die letzten 3000. Zeile anklicken für Details.</p>
    ${liste.length ? `<div class="tabelle"><table><thead><tr><th>Zeit</th><th>Was</th><th>Quelle</th><th class="right">Status</th><th class="right">Dauer</th></tr></thead>
      <tbody>${liste.map((a, i) => `<tr class="klick" data-i="${i}"><td class="nw muted">${zeit(a.zeit)}</td><td>${esc(beschreiben(a.methode, a.pfad))}</td>
        <td><span class="tag">${esc(a.quelle)}</span></td><td class="right ${a.status >= 400 ? "over" : ""}">${a.status}</td>
        <td class="right muted nw">${Math.round(a.dauer_s * 1000)} ms</td></tr>`).join("")}</tbody></table></div>` : `<div class="leer">Keine Einträge.</div>`}</div>`;
  const filtern = () => { location.hash = `#/log/aenderungen?quelle=${encodeURIComponent($("#quelle", el).value)}&pfad=${encodeURIComponent($("#pfad", el).value)}`; };
  $("#quelle", el).onchange = filtern;
  $("#pfad", el).onchange = filtern;
  el.querySelectorAll("[data-i]").forEach((tr) => tr.onclick = () => guarded(async () => {
    const a = liste[+tr.dataset.i];
    await dialog(`<h2>${esc(beschreiben(a.methode, a.pfad))}</h2><p class="muted small">${zeit(a.zeit)} · ${esc(a.quelle)} · HTTP ${a.status}</p>
      <p><code>${esc(a.methode)} ${esc(a.pfad)}</code></p><pre>${esc(JSON.stringify(a.daten, null, 2) ?? "–")}</pre>`, [["ok", "Schließen", "primary"]]);
  }));
}
