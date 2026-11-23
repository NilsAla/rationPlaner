import {$$, api, dialog, esc, fmt, guarded, lokal, merken, zeit} from "../lib.js";

// Jede KI-Anfrage: Aufgabe, Backend, Tokens (≈ = geschätzt), Kosten; Details mit Anweisung, Schema, Rohantwort
const ZEITRAUM = {tag: "Heute (24 h)", woche: "7 Tage", monat: "30 Tage"};
const JOB = {bon: "Kassenbon", import: "Rezept-Import", kochanleitung: "Kochanleitung", kategorisierung: "Kategorisierung",
  naehrwerte: "Nährwerte", planer: "Wochenplan", packungen: "Packungsgrößen", unbekannt: "älter (ohne Job)"};
const euro = (x) => x == null ? "–" : x < 0.01 && x > 0 ? `${(x * 100).toLocaleString("de-DE", {maximumFractionDigits: 3})} ct`
  : x.toLocaleString("de-DE", {style: "currency", currency: "EUR", maximumFractionDigits: 4});
const tok = (n, geschaetzt) => n == null ? "–" : `${geschaetzt ? "≈" : ""}${fmt(n, 0)}`;

export default async function render(el) {
  const zr = lokal("ki.zeitraum", "monat");
  const [liste, kosten] = await Promise.all([api("log/ki?limit=200"), api(`log/ki-kosten?zeitraum=${zr}`)]);
  const fehler = liste.filter((k) => k.fehler).length;
  const dauer = liste.length ? liste.reduce((s, k) => s + k.dauer_s, 0) / liste.length : 0;
  const jobs = Object.entries(kosten.jobs).sort((a, b) => b[1].kosten_eur - a[1].kosten_eur);
  const ohnePreis = jobs.reduce((s, [, j]) => s + j.ohne_preis, 0);
  el.innerHTML = `<div class="card"><div class="kopf"><h2>Kosten</h2><div class="aktionen chips">${Object.entries(ZEITRAUM).map(([k, t]) =>
      `<button class="chip ${k === zr ? "on" : ""}" data-zr="${k}">${t}</button>`).join("")}</div></div>
    <div class="row">
      <div class="kachel"><div class="zahl">${euro(kosten.summe_eur)}</div><div class="muted small">Summe ${esc(ZEITRAUM[zr])}</div></div>
      <div class="kachel"><div class="zahl">${jobs.reduce((s, [, j]) => s + j.aufrufe, 0)}</div><div class="muted small">Aufrufe</div></div>
      <div class="kachel"><div class="zahl ${fehler ? "over" : ""}">${fehler}</div><div class="muted small">Fehler (letzte ${liste.length})</div></div>
      <div class="kachel"><div class="zahl">${dauer.toFixed(1)} s</div><div class="muted small">Ø Dauer</div></div></div>
    ${jobs.length ? `<div class="tabelle"><table><thead><tr><th>Aufgabe</th><th class="right">Aufrufe</th><th class="right">Tokens in</th>
      <th class="right">Tokens out</th><th class="right">Kosten</th></tr></thead><tbody>${jobs.map(([k, j]) => `<tr><td>${esc(JOB[k] ?? k)}</td>
      <td class="right">${j.aufrufe}</td><td class="right nw">${tok(j.tokens_in, j.geschaetzt)}</td><td class="right nw">${tok(j.tokens_out, j.geschaetzt)}</td>
      <td class="right nw">${euro(j.kosten_eur)}${j.ohne_preis ? ` <span class="muted tiny">+${j.ohne_preis} ohne Preis</span>` : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <p class="muted tiny">≈ = geschätzt (HA-Modus: Zeichen ÷ 3,5, Bilder pauschal). ${ohnePreis ? `${ohnePreis} Aufrufe ohne Preis: ` : ""}Preise pflegst du in den <a href="#/einstellungen">Einstellungen → KI</a>.</p></div>
    <div class="card">${liste.length ? `<div class="tabelle"><table><thead><tr><th>Zeit</th><th>Aufgabe</th><th>Backend</th><th class="right">Tokens</th>
      <th class="right">Kosten</th><th class="right">Dauer</th></tr></thead>
      <tbody>${liste.map((k) => `<tr class="klick" data-id="${k.id}"><td class="nw muted">${zeit(k.zeit)}</td>
        <td>${esc(k.aufgabe)}${k.anhaenge ? ` <span class="tag">📎 ${k.anhaenge}</span>` : ""}${k.fehler ? `<div class="over tiny">${esc(k.fehler)}</div>` : ""}</td>
        <td class="tiny"><span class="tag">${k.backend === "openai" ? "OpenAI" : "HA"}</span> <span class="muted">${esc(k.entitaet)}</span></td>
        <td class="right nw tiny">${k.tokens_in == null ? "–" : `${tok(k.tokens_in, k.geschaetzt)} / ${tok(k.tokens_out, k.geschaetzt)}`}</td>
        <td class="right nw">${euro(k.kosten_eur)}</td><td class="right nw">${k.dauer_s.toFixed(1)} s</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="leer">Noch keine KI-Anfragen.</div>`}</div>`;
  $$("[data-zr]", el).forEach((b) => b.onclick = () => { merken("ki.zeitraum", b.dataset.zr); guarded(() => render(el)); });
  $$("[data-id]", el).forEach((tr) => tr.onclick = () => guarded(async () => {
    const k = await api(`log/ki/${tr.dataset.id}`);
    const j = (x) => esc(JSON.stringify(x, null, 2) ?? "–");
    await dialog(`<h2>${esc(k.aufgabe)}</h2><p class="muted small">${zeit(k.zeit)} · ${k.backend === "openai" ? "OpenAI" : "HA"} · ${esc(k.entitaet)}
      · ${k.dauer_s.toFixed(1)} s${k.tokens_in != null ? ` · ${tok(k.tokens_in, k.geschaetzt)} in / ${tok(k.tokens_out, k.geschaetzt)} out · ${euro(k.kosten_eur)}` : ""}</p>
      ${k.fehler ? `<p class="over">${esc(k.fehler)}</p>` : ""}
      <details open><summary>Anweisung</summary><pre>${esc(k.anweisung)}</pre></details>
      <details><summary>Struktur (Schema)</summary><pre>${j(k.struktur)}</pre></details>
      ${k.anhaenge?.length ? `<details><summary>Anhänge</summary><pre>${j(k.anhaenge)}</pre></details>` : ""}
      <details open><summary>Antwort</summary><pre>${j(k.antwort)}</pre></details>`, [["ok", "Schließen", "primary"]]);
  }));
}
