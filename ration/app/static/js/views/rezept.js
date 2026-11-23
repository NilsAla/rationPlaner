import {$, $$, AUFWAND, NAEHRFELDER, nutriBadge, api, bestaetigen, del, dmy, esc, fmt, guarded, heute, put, toast, gehe, titel, quelleBadge} from "../lib.js";

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
        · ${quelleBadge(r.quelle)}${r.url ? ` <a href="${esc(r.url)}" target="_blank" rel="noopener">Original ↗</a>` : ""}</p>
      <div class="row" style="align-items:end"><div style="flex:0 0 110px"><label>Portionen</label>
        <input id="por" type="number" min="1" max="24" value="${r.portionen}"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="koch">👩‍🍳 Kochmodus</button><button class="btn" id="test" title="Kochmodus ohne HA-Timer und ohne Buchung">🧪 Testlauf</button><button class="btn" id="heute">🔥 Heute kochen</button><button class="btn" id="planen">📅 Einplanen</button></div></div>
      <h3>Zutaten</h3><table>${r.zutaten.map((z) => `<tr><td><span class="dot ${z.ampel}" title="${ampel[z.ampel] ?? ""}"></span>
          <a href="#/produkt/${esc(z.produkt_id)}">${esc(z.name)}</a>${z.notiz ? `<div class="muted tiny">${esc(z.notiz)}</div>` : ""}</td>
        <td class="nw right">${fmt(z.menge)} ${esc(z.einheit)}</td><td class="nw muted right tiny">da: ${fmt(z.bestand)}</td></tr>`).join("")}</table></div>
    ${makros(r.naehrwerte)}
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

  const mt = $("#nwmehr", el);
  if (mt) mt.onclick = () => { $("#nwtab", el).hidden = !$("#nwtab", el).hidden; mt.textContent = $("#nwtab", el).hidden ? "alle Werte" : "weniger"; };
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
  $("#test", el).onclick = () => gehe(`kochen/${r.id}?test=1&portionen=${+$("#por", el).value}`);
  $("#planen", el).onclick = () => gehe(`plan?rezept=${encodeURIComponent(r.id)}&portionen=${+$("#por", el).value}`);
  $("#loeschen", el).onclick = () => guarded(async () => {
    if (!await bestaetigen(`„${esc(r.name)}“ in Grocy löschen? Geplante Tage damit werden entfernt.`, "Löschen", true)) return;
    await del(`rezepte/${r.id}`); toast("Rezept gelöscht"); gehe("rezepte");
  });
}

// Makros je Portion, Nutri-Score, Ziel-Passung. Werte rechnet der Server aus Nährwerten je 100 g × Gramm.
function makros(n) {
  if (!n) return "";
  const v = n.je_portion, unvoll = n.abdeckung < 0.95;
  const zahl = (x, d = 0) => x == null ? "–" : (unvoll ? "≥" : "") + (+x).toFixed(d).replace(".", ",");
  const kachel = (wert, einheit, name) => `<div class="makro"><b>${wert}</b><span>${einheit}</span><div class="muted tiny">${name}</div></div>`;
  const z = n.ziel, pct = (x) => `${Math.round(100 * x)} %`;
  const fehlt = n.fehlend.length ? `<p class="muted tiny">Ohne Werte (${pct(1 - n.abdeckung)} des Gewichts): ${n.fehlend.map((f) =>
    `<a href="#/produkt/${esc(f.produkt_id)}" title="${esc(f.grund)}">${esc(f.name)}</a>`).join(", ")}</p>` : "";
  if (!v.kcal) return `<div class="card"><h2>Makros</h2><p class="muted">Noch keine Nährwerte für die Zutaten.
    Am Produkt eintragen oder unter <a href="#/vorschlaege">KI-Vorschläge</a> schätzen lassen.</p>${fehlt}</div>`;
  return `<div class="card"><div class="kopf"><h2>Makros <span class="muted tiny">je Portion</span></h2>${nutriBadge(n.nutri_score?.klasse,
      n.nutri_score ? `Nutri-Score ${n.nutri_score.klasse}: ${n.nutri_score.punkte} Punkte (negativ ${n.nutri_score.negativ}, positiv ${n.nutri_score.positiv})` : "")}</div>
    <div class="makros">${kachel(zahl(v.kcal), "kcal", "Energie")}${kachel(zahl(v.protein), "g", "Protein")}
      ${kachel(zahl(v.kohlenhydrate), "g", "Kohlenhydrate")}${kachel(zahl(v.fett), "g", "Fett")}</div>
    ${z && z.fit != null ? `<div class="zielbalken" title="Passung zum Mahlzeiten-Ziel"><div style="width:${pct(z.fit)}"></div></div>
      <p class="small">Ziel ${z.kcal_ziel} kcal / ${z.protein_ziel} g Protein: <b>${pct(z.fit)}</b> passend
        <span class="muted">(${z.kcal_diff > 0 ? "+" : ""}${z.kcal_diff} kcal${z.fit_protein != null && z.fit_protein < 1 ? `, Protein ${pct(z.fit_protein)}` : ""})</span></p>` : ""}
    <p class="muted tiny">Protein ${n.protein_kcal_pct ?? "–"} % der Energie · Obst/Gemüse ${n.obst_gemuese_pct} % · Rohkost ${n.rohkost_pct} %
      ${n.nutri_score ? "" : " · Nutri-Score erst ab 80 % Abdeckung"} · <a href="javascript:void 0" id="nwmehr">alle Werte</a></p>
    <table id="nwtab" hidden>${NAEHRFELDER.map(([k, t]) => `<tr><td>${t}</td><td class="right">${zahl(v[k], 1)}</td>
      <td class="right muted tiny">${n.je_100g[k] == null ? "–" : (+n.je_100g[k]).toFixed(1).replace(".", ",")} / 100 g</td></tr>`).join("")}</table>
    ${fehlt}</div>`;
}
