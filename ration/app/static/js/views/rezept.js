import {$, $$, AUFWAND, api, bestaetigen, del, dmy, esc, fmt, guarded, heute, put, toast, gehe, titel} from "../lib.js";

// Rezeptleser mit Portionen, Bestandsampel, Meinung und Kochverlauf
export default async function render(el, {arg, params}) {
  const p = params.portionen ? `?portionen=${encodeURIComponent(params.portionen)}` : "";
  const r = await api(`rezepte/${arg}${p}`);
  titel(r.name);
  const ampel = {ok: "alles da", knapp: "knapp", fehlt: "fehlt"};
  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr))">
    <div class="card"><div class="kopf"><h2>${esc(r.name)}</h2><div class="aktionen">
        <a class="btn klein" href="#/rezept/${esc(r.id)}/anleitung">🪜 Anleitung</a>
        <a class="btn klein" href="#/rezept/${esc(r.id)}/bearbeiten">✎ Bearbeiten</a></div></div>
      <p class="muted small">${[r.status === "ungetestet" ? "noch nie gekocht" : null, AUFWAND[r.aufwand], ...r.tags].filter(Boolean).map(esc).join(" · ")}
        ${r.url ? ` · <a href="${esc(r.url)}" target="_blank" rel="noopener">Quelle ↗</a>` : ""}</p>
      <div class="row" style="align-items:end"><div style="flex:0 0 110px"><label>Portionen</label>
        <input id="por" type="number" min="1" max="24" value="${r.portionen}"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="koch">👩‍🍳 Kochmodus</button><button class="btn" id="heute">🔥 Heute kochen</button><button class="btn" id="planen">📅 Einplanen</button></div></div>
      <h3>Zutaten</h3><table>${r.zutaten.map((z) => `<tr><td><span class="dot ${z.ampel}" title="${ampel[z.ampel] ?? ""}"></span>
          <a href="#/produkt/${esc(z.produkt_id)}">${esc(z.name)}</a>${z.notiz ? `<div class="muted tiny">${esc(z.notiz)}</div>` : ""}</td>
        <td class="nw right">${fmt(z.menge)} ${esc(z.einheit)}</td><td class="nw muted right tiny">da: ${fmt(z.bestand)}</td></tr>`).join("")}</table></div>
    <div class="card"><h2>Zubereitung</h2>${r.schritte.length ? `<ol class="schritte">${r.schritte.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`
      : `<p class="muted">Keine Schritte hinterlegt. <a href="#/rezept/${esc(r.id)}/bearbeiten">Ergänzen</a></p>`}</div>
    <div class="card"><h2>Meine Meinung</h2>
      <div class="chips"><button class="chip ${r.bewertung > 0 ? "on" : ""}" data-b="1">👍 gern wieder</button>
        <button class="chip ${r.bewertung === 0 ? "on" : ""}" data-b="0">😐 neutral</button>
        <button class="chip ${r.bewertung < 0 ? "on" : ""}" data-b="-1">👎 eher nicht</button>
        <button class="chip ${r.favorit ? "on" : ""}" id="fav">${r.favorit ? "★" : "☆"} Favorit</button></div>
      <label>Notiz</label><textarea id="notiz" rows="2" placeholder="z. B. mehr Knoblauch, Kinder mögen es nicht …">${esc(r.notiz ?? "")}</textarea>
      <p class="muted tiny">Daumen und Favoriten nutzt der Wochenplaner (Regler „Lieblinge ↔ Neues“).</p>
      <h3>Verlauf</h3>${r.verlauf.length ? `<table>${r.verlauf.map((k) => `<tr><td>${dmy(k.datum)}</td><td>${k.portionen} P.</td>
        <td class="right">${k.bewertung > 0 ? "👍" : k.bewertung < 0 ? "👎" : k.bewertung === 0 ? "😐" : ""}</td></tr>`).join("")}</table>`
        : `<p class="muted">Noch nie gekocht.</p>`}
      <button class="btn danger klein" id="loeschen" style="margin-top:14px">Rezept löschen</button></div></div>`;

  const meinung = (body) => guarded(async () => { await put(`rezepte/${r.id}/meinung`, body); toast("Gespeichert"); render(el, {arg, params}); });
  $$("[data-b]", el).forEach((b) => b.onclick = () => meinung({bewertung: +b.dataset.b}));
  $("#fav", el).onclick = () => meinung({favorit: !r.favorit});
  $("#notiz", el).onchange = (e) => guarded(async () => { await put(`rezepte/${r.id}/meinung`, {notiz: e.target.value}); toast("Notiz gespeichert"); });
  $("#por", el).onchange = (e) => gehe(`rezept/${r.id}?portionen=${+e.target.value}`);
  $("#heute", el).onclick = () => guarded(async () => {
    await put(`plan/${heute()}`, {rezept_id: r.id, portionen: +$("#por", el).value});
    gehe("heute");
  });
  $("#koch", el).onclick = () => gehe(`kochen/${r.id}?portionen=${+$("#por", el).value}`);
  $("#planen", el).onclick = () => gehe(`plan?rezept=${encodeURIComponent(r.id)}&portionen=${+$("#por", el).value}`);
  $("#loeschen", el).onclick = () => guarded(async () => {
    if (!await bestaetigen(`„${esc(r.name)}“ in Grocy löschen? Geplante Tage damit werden entfernt.`, "Löschen", true)) return;
    await del(`rezepte/${r.id}`); toast("Rezept gelöscht"); gehe("rezepte");
  });
}
