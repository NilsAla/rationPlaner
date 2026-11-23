import {$, $$, ablaufText, api, bestaetigen, esc, fmt, formDialog, guarded, lokal, meldeMassen, merken, patch, post, tabelle, toast} from "../lib.js";

// Inventar: jede Charge einzeln, filterbar, inline bearbeiten, Massenaktionen. ?filter=bald|abgelaufen|gefroren|offen
export default async function render(el, {params}) {
  const [chargen, orte] = await Promise.all([api("inventar"), api("lagerorte")]);
  const f = {q: "", ort: "", ...lokal("inventar.filter", {}), art: params.filter ?? lokal("inventar.art", "alle")};
  const FILTER = {alle: "Alle", bald: "⏰ bald ab", abgelaufen: "⚠️ abgelaufen", gefroren: "❄️ gefroren", offen: "📭 geöffnet"};
  const passt = {alle: () => true, bald: (c) => c.tage != null && c.tage >= 0 && c.tage <= 3, abgelaufen: (c) => c.tage != null && c.tage < 0,
    gefroren: (c) => c.gefroren, offen: (c) => c.geoeffnet};
  const anzahl = (k) => chargen.filter(passt[k]).length;

  el.innerHTML = `<div class="card">
    <div class="werkzeug"><input type="search" id="q" placeholder="Produkt oder Kategorie …" value="${esc(f.q)}">
      <select id="ort"><option value="">Alle Lagerorte</option>${orte.map((o) => `<option value="${esc(o.id)}" ${o.id === f.ort ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select>
      <a class="btn primary" href="#/einbuchen">+ Einbuchen</a></div>
    <div class="chips">${Object.entries(FILTER).map(([k, t]) => `<button class="chip ${f.art === k ? "on" : ""}" data-art="${k}">${t} <span class="muted">${anzahl(k)}</span></button>`).join("")}</div>
    <div id="liste" style="margin-top:10px"></div></div>`;

  const liste = $("#liste", el);
  const zeichnen = () => {
    merken("inventar.filter", {q: f.q, ort: f.ort}); merken("inventar.art", f.art);
    const q = f.q.toLowerCase();
    const zeilen = chargen.filter((c) => passt[f.art](c) && (!f.ort || c.lagerort_id === f.ort)
      && (!q || c.name.toLowerCase().includes(q) || (c.kategorie ?? "").toLowerCase().includes(q)));
    tabelle(liste, {
      zeilen, sortierung: "ablauf", auswahl: () => {}, leer: "Keine Chargen.", onKlick: (c) => guarded(() => bearbeiten(c, orte, () => render(el, {params}))),
      aktionen: `<button class="btn klein" data-akt="verbraucht">✓ verbraucht</button><button class="btn klein danger" data-akt="verdorben">🗑 verdorben</button>
        <button class="btn klein" data-akt="lagerort">⇄ Lagerort</button><button class="btn klein" data-akt="geoeffnet">📭 geöffnet</button>
        <button class="btn klein" data-akt="ablauf_plus">+ Tage Ablauf</button>`,
      spalten: [
        {key: "name", titel: "Produkt", wert: (c) => `<a href="#/produkt/${esc(c.produkt_id)}">${esc(c.name)}</a>${c.geoeffnet ? ` <span class="tag warn">offen</span>` : ""}
          ${c.kategorie ? `<div class="muted tiny">${esc(c.kategorie)}</div>` : ""}`},
        {key: "menge", titel: "Menge", klasse: "nw right", wert: (c) => `${fmt(c.menge)} ${esc(c.einheit)}`},
        {key: "lagerort", titel: "Lagerort", klasse: "nw", wert: (c) => `${c.gefroren ? "❄️ " : ""}${esc(c.lagerort)}`},
        {key: "ablauf", titel: "Ablauf", klasse: "nw", sort: (c) => c.ablauf ?? "9999", wert: (c) => ablaufText(c.ablauf, c.tage)},
      ],
    });
  };
  liste.onclick = (ev) => {
    const b = ev.target.closest("[data-akt]");
    if (b) guarded(() => massen(b.dataset.akt, [...liste._tabelle.gewaehlt], orte, () => render(el, {params})));
  };
  $("#q", el).oninput = (e) => { f.q = e.target.value; zeichnen(); };
  $("#ort", el).onchange = (e) => { f.ort = e.target.value; zeichnen(); };
  $$("[data-art]", el).forEach((b) => b.onclick = () => {
    f.art = b.dataset.art; $$("[data-art]", el).forEach((x) => x.classList.toggle("on", x === b)); zeichnen();
  });
  zeichnen();
}

async function bearbeiten(c, orte, fertig) {
  const w = await formDialog(`<h2>${esc(c.name)}</h2>
    <div class="row"><div><label>Menge (${esc(c.einheit)})</label><input name="menge" type="number" min="0" step="any" value="${c.menge}"></div>
      <div><label>Ablauf</label><input name="ablauf" type="date" value="${c.ablauf ?? ""}"></div></div>
    <label>Lagerort</label><select name="lagerort_id">${orte.map((o) => `<option value="${esc(o.id)}" ${o.id === c.lagerort_id ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select>
    <label class="inline"><input type="checkbox" name="geoeffnet" ${c.geoeffnet ? "checked" : ""}> geöffnet</label>
    <p class="muted tiny">Menge 0 bucht die Charge als verbraucht aus. Ablauf leer = unbegrenzt.</p>`);
  if (!w) return;
  if (+w.menge === 0) {
    await post("inventar/aktion", {ids: [c.id], aktion: "verbraucht"});
  } else {
    await patch(`inventar/${c.id}`, {menge: +w.menge, ablauf: w.ablauf || null, ohne_ablauf: !w.ablauf, lagerort_id: w.lagerort_id, geoeffnet: w.geoeffnet});
  }
  toast("Charge gespeichert"); fertig();
}

async function massen(akt, ids, orte, fertig) {
  if (!ids.length) return;
  const body = {ids, aktion: akt};
  if (akt === "verdorben" && !await bestaetigen(`${ids.length} Charge(n) als verdorben ausbuchen?`, "Ausbuchen", true)) return;
  if (akt === "verbraucht" && !await bestaetigen(`${ids.length} Charge(n) komplett als verbraucht ausbuchen?`, "Ausbuchen")) return;
  if (akt === "lagerort") {
    const w = await formDialog(`<h3>Umlagern</h3><label>Neuer Lagerort</label><select name="lagerort_id">${orte.map((o) =>
      `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("")}</select>`, "Umlagern");
    if (!w) return; body.lagerort_id = w.lagerort_id;
  }
  if (akt === "ablauf_plus") {
    const w = await formDialog(`<h3>Ablauf verschieben</h3><label>Tage (negativ = früher)</label><input name="tage" type="number" value="3">`, "Verschieben");
    if (!w) return; body.tage = +w.tage;
  }
  meldeMassen(await post("inventar/aktion", body), "Chargen geändert");
  fertig();
}
