import {$$, api, dialog, esc, guarded, zeit} from "../lib.js";

// Jede Anfrage an ai_task: Aufgabe, Dauer, Fehler; Details mit Anweisung, Schema und Rohantwort
export default async function render(el) {
  const liste = await api("log/ki?limit=200");
  const fehler = liste.filter((k) => k.fehler).length;
  const dauer = liste.length ? liste.reduce((s, k) => s + k.dauer_s, 0) / liste.length : 0;
  el.innerHTML = `<div class="card"><div class="row">
      <div class="kachel"><div class="zahl">${liste.length}</div><div class="muted small">Anfragen</div></div>
      <div class="kachel"><div class="zahl ${fehler ? "over" : ""}">${fehler}</div><div class="muted small">Fehler</div></div>
      <div class="kachel"><div class="zahl">${dauer.toFixed(1)} s</div><div class="muted small">Ø Dauer</div></div></div></div>
    <div class="card">${liste.length ? `<div class="tabelle"><table><thead><tr><th>Zeit</th><th>Aufgabe</th><th>Entität</th><th class="right">Dauer</th><th class="right">Antwort</th></tr></thead>
      <tbody>${liste.map((k) => `<tr class="klick" data-id="${k.id}"><td class="nw muted">${zeit(k.zeit)}</td>
        <td>${esc(k.aufgabe)}${k.anhaenge ? ` <span class="tag">📎 ${k.anhaenge}</span>` : ""}${k.fehler ? `<div class="over tiny">${esc(k.fehler)}</div>` : ""}</td>
        <td class="muted tiny">${esc(k.entitaet)}</td><td class="right nw">${k.dauer_s.toFixed(1)} s</td>
        <td class="right nw muted">${k.fehler ? "–" : `${(k.antwort_bytes / 1024).toFixed(1)} kB`}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="leer">Noch keine KI-Anfragen.</div>`}</div>`;
  $$("[data-id]", el).forEach((tr) => tr.onclick = () => guarded(async () => {
    const k = await api(`log/ki/${tr.dataset.id}`);
    const j = (x) => esc(JSON.stringify(x, null, 2) ?? "–");
    await dialog(`<h2>${esc(k.aufgabe)}</h2><p class="muted small">${zeit(k.zeit)} · ${esc(k.entitaet)} · ${k.dauer_s.toFixed(1)} s</p>
      ${k.fehler ? `<p class="over">${esc(k.fehler)}</p>` : ""}
      <details open><summary>Anweisung</summary><pre>${esc(k.anweisung)}</pre></details>
      <details><summary>Struktur (Schema)</summary><pre>${j(k.struktur)}</pre></details>
      ${k.anhaenge?.length ? `<details><summary>Anhänge</summary><pre>${j(k.anhaenge)}</pre></details>` : ""}
      <details open><summary>Antwort</summary><pre>${j(k.antwort)}</pre></details>`, [["ok", "Schließen", "primary"]]);
  }));
}
