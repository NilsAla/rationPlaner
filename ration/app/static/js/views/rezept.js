import {$, $$, AUFWAND, NAEHRFELDER, nutriBadge, api, bestaetigen, del, dmy, esc, fmt, guarded, heute, put, toast, gehe, titel, quelleBadge, fotoUrl, fotoHochladen, patch, post} from "../lib.js";

// Rezeptleser mit Portionen, Bestandsampel, Meinung und Kochverlauf
export default async function render(el, {arg, params}) {
  const p = params.portionen ? `?portionen=${encodeURIComponent(params.portionen)}` : "";
  const r = await api(`rezepte/${arg}${p}`);
  titel(r.name);
  const ampel = {ok: "alles da", knapp: "knapp", fehlt: "fehlt"};
  const haupt = r.fotos.find((f) => f.haupt) ?? r.fotos.find((f) => f.schritt == null);
  const jeSchritt = {};
  r.fotos.filter((f) => f.schritt != null).forEach((f) => (jeSchritt[f.schritt] ??= []).push(f));
  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr))">
    <div class="card">${haupt ? `<img class="hero" src="${fotoUrl(haupt.id, false)}" alt="${esc(r.name)}">` : ""}<div class="kopf"><h2>${esc(r.name)}</h2><div class="aktionen">
        <a class="btn klein" href="#/rezept/${esc(r.id)}/anleitung">🪜 Anleitung</a>
        <a class="btn klein" href="#/rezept/${esc(r.id)}/bearbeiten">✎ Bearbeiten</a></div></div>
      <p class="muted small">${[r.status === "ungetestet" ? "noch nie gekocht" : null, AUFWAND[r.aufwand], ...r.tags].filter(Boolean).map(esc).join(" · ")}
        · ${quelleBadge(r.quelle)}${r.url ? ` <a href="${esc(r.url)}" target="_blank" rel="noopener">Original ↗</a>` : ""}</p>
      <div class="row" style="align-items:end"><div style="flex:0 0 110px"><label>Portionen</label>
        <input id="por" type="number" min="1" max="24" value="${r.portionen}"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="koch">👩‍🍳 Kochmodus</button><button class="btn" id="test" title="Kochmodus ohne HA-Timer und ohne Buchung">🧪 Testlauf</button><button class="btn" id="heute">🔥 Heute kochen</button><button class="btn" id="planen">📅 Einplanen</button></div></div>
      <h3>Zutaten</h3><table>${r.zutaten.map((z) => `<tr><td><span class="dot ${z.ampel}" title="${ampel[z.ampel] ?? ""}"></span>
          <a href="#/produkt/${esc(z.produkt_id)}">${esc(z.name)}</a>${z.ersatz?.length ? ` <span class="tag" title="Austauschbare Variante">↔ ${esc(z.ersatz.join(", "))}</span>` : ""}${z.notiz ? `<div class="muted tiny">${esc(z.notiz)}</div>` : ""}</td>
        <td class="nw right">${fmt(z.menge)} ${esc(z.einheit)}</td><td class="nw muted right tiny">da: ${fmt(z.bestand)}</td></tr>`).join("")}</table></div>
    ${makros(r.naehrwerte)}
    <div class="card"><h2>Zubereitung</h2>${r.schritte.length ? `<ol class="schritte">${r.schritte.map((s, k) => `<li>${esc(s)}${(jeSchritt[k] ?? []).map((f) =>
        `<a href="${fotoUrl(f.id, false)}" target="_blank"><img class="schritt-mini" src="${fotoUrl(f.id)}" alt="" loading="lazy"></a>`).join("")}</li>`).join("")}</ol>`
      : `<p class="muted">Keine Schritte hinterlegt. <a href="#/rezept/${esc(r.id)}/bearbeiten">Ergänzen</a></p>`}</div>
    <div class="card"><div class="kopf"><h2>Fotos</h2><div class="aktionen">
        ${r.url ? `<button class="btn klein" id="quellbilder" title="Haupt- und Schrittbilder von der Originalseite laden">⤓ Von der Quelle</button>` : ""}
        <button class="btn klein primary" id="fotoneu">📷 Foto</button></div></div>
      ${r.fotos.length ? `<div class="fotogrid">${r.fotos.map((f) => `<figure><a href="${fotoUrl(f.id, false)}" target="_blank"><img src="${fotoUrl(f.id)}" alt="" loading="lazy"></a>
        <figcaption>${f.haupt ? "⭐ Titelbild" : f.schritt != null ? `Schritt ${f.schritt + 1}` : "Ergebnis"}
          <span class="grow"></span>${f.haupt ? "" : `<button class="icon" data-fhaupt="${f.id}" title="Als Titelbild">⭐</button>`}
          <button class="icon" data-fdel="${f.id}" title="Löschen">✕</button></figcaption></figure>`).join("")}</div>`
        : `<p class="muted small">Noch keine Fotos. Nach dem Kochen fragt der Kochmodus nach einem Foto vom Ergebnis.</p>`}</div>
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

  $("#fotoneu", el).onclick = () => guarded(async () => { if (await fotoHochladen(r.id)) render(el, {arg, params}); });
  const qb = $("#quellbilder", el);
  if (qb) qb.onclick = () => guarded(async () => {
    const e = await post(`rezepte/${r.id}/fotos/laden`);
    toast(`Lade ${e.hauptbild ? "Titelbild und " : ""}${e.schrittbilder} Schrittbilder …`);
    setTimeout(() => render(el, {arg, params}), 4000);
  });
  $$("[data-fhaupt]", el).forEach((b) => b.onclick = () => guarded(async () => { await patch(`fotos/${b.dataset.fhaupt}`, {haupt: true}); render(el, {arg, params}); }));
  $$("[data-fdel]", el).forEach((b) => b.onclick = () => guarded(async () => {
    if (!await bestaetigen("Foto löschen?", "Löschen", true)) return;
    await del(`fotos/${b.dataset.fdel}`); render(el, {arg, params});
  }));
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
    Am Produkt eintragen oder unter <a href="#/ki-vorschlaege">KI-Vorschläge</a> schätzen lassen.</p>${fehlt}</div>`;
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
