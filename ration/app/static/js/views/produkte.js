import {$, $$, KATEGORIEN, MODI, api, nichtDa, bestaetigen, esc, fmt, formDialog, guarded, lokal, meldeMassen, merken, post, tabelle, toast, gehe} from "../lib.js";



// Produktdatenbank: alles, was Grocy kennt, auch ohne Bestand. ?filter=nicht_da|vorraetig|ohne
export default async function render(el, {params}) {
  const [liste, orte] = await Promise.all([api("katalog/produkte"), api("lagerorte")]);
  const ortName = Object.fromEntries(orte.map((o) => [o.id, o.name]));
  const name = Object.fromEntries(liste.map((p) => [p.id, p.name]));
  const f = {q: "", kat: "", modus: "", ...lokal("produkte.filter", {}), art: params.filter ?? "alle"};
  const FILTER = {alle: ["Alle", () => true], vorraetig: ["vorrätig", (p) => !nichtDa(p)], nicht_da: ["bekannt, nicht da", nichtDa],
    ohne: ["ohne Attribute", (p) => p.attribute_fehlen], ungenutzt: ["in keinem Rezept", (p) => !p.rezepte]};
  const kategorien = [...new Set([...KATEGORIEN, ...liste.map((p) => p.kategorie).filter(Boolean)])].sort((a, b) => a.localeCompare(b, "de"));

  el.innerHTML = `<div class="card">
    <div class="werkzeug"><input type="search" id="q" placeholder="Produkt suchen …" value="${esc(f.q)}">
      <select id="kat"><option value="">Alle Kategorien</option><option value="-" ${f.kat === "-" ? "selected" : ""}>ohne Kategorie</option>
        ${kategorien.map((k) => `<option ${k === f.kat ? "selected" : ""}>${esc(k)}</option>`).join("")}</select>
      <select id="modus"><option value="">Alle Modi</option>${Object.entries(MODI).map(([k, t]) => `<option value="${k}" ${k === f.modus ? "selected" : ""}>${t}</option>`).join("")}</select>
      <a class="btn primary" href="#/produkt-neu">+ Neu</a></div>
    <div class="chips">${Object.entries(FILTER).map(([k, [t, fn]]) => `<button class="chip ${f.art === k ? "on" : ""}" data-art="${k}">${t} <span class="muted">${liste.filter(fn).length}</span></button>`).join("")}</div>
    <div id="liste" style="margin-top:10px"></div></div>`;

  const tab = $("#liste", el);
  const zeichnen = () => {
    merken("produkte.filter", {q: f.q, kat: f.kat, modus: f.modus});
    const q = f.q.toLowerCase();
    const zeilen = liste.filter((p) => FILTER[f.art][1](p) && (!q || p.name.toLowerCase().includes(q))
      && (!f.kat || (f.kat === "-" ? !p.kategorie : p.kategorie === f.kat)) && (!f.modus || p.tracking_modus === f.modus));
    tabelle(tab, {
      zeilen, sortierung: "name", auswahl: () => {}, leer: "Keine Produkte.", onKlick: (p) => gehe(`produkt/${p.id}`),
      aktionen: `<button class="btn klein" data-akt="kategorie">Kategorie</button><button class="btn klein" data-akt="modus">Modus</button>
        <button class="btn klein" data-akt="lagerort">Lagerort</button><button class="btn klein" data-akt="mehr">Weitere Felder</button>
        <button class="btn klein" data-akt="ki">✨ KI kategorisieren</button><button class="btn klein danger" data-akt="loeschen">Löschen</button>`,
      spalten: [
        {key: "name", titel: "Produkt", wert: (p) => `<b>${esc(p.name)}</b>${p.parent_id ? `<div class="muted tiny">Variante von ${esc(name[p.parent_id] ?? "?")}</div>` : ""}`},
        {key: "kategorie", titel: "Kategorie", wert: (p) => p.kategorie ? esc(p.kategorie) : `<span class="muted">–</span>`},
        {key: "tracking_modus", titel: "Modus", klasse: "nw", wert: (p) => p.tracking_modus ? esc(MODI[p.tracking_modus]) : `<span class="tag warn">fehlt</span>`},
        {key: "bestand", titel: "Bestand", klasse: "nw right", wert: (p) => p.tracking_modus === "fuellstand" ? esc(p.fuellstand ?? "voll")
          : p.tracking_modus === "immer_da" ? `<span class="muted">immer da</span>` : p.bestand ? `${fmt(p.bestand)} ${esc(p.einheit)}` : `<span class="muted">0 ${esc(p.einheit)}</span>`},
        {key: "lagerort_id", titel: "Lagerort", klasse: "nw", sort: (p) => ortName[p.lagerort_id] ?? "", wert: (p) => esc(ortName[p.lagerort_id] ?? "–")},
        {key: "haltbarkeit_tage", titel: "Haltbar", klasse: "nw right", wert: (p) => !p.haltbarkeit_tage ? "–" : p.haltbarkeit_tage < 0 ? "∞" : `${p.haltbarkeit_tage} T.`},
        {key: "rezepte", titel: "Rezepte", klasse: "nw right"},
      ],
    });
  };
  tab.onclick = (ev) => {
    const b = ev.target.closest("[data-akt]");
    if (b) guarded(() => massen(b.dataset.akt, [...tab._tabelle.gewaehlt], {orte, kategorien, liste}, () => render(el, {params})));
  };
  $("#q", el).oninput = (e) => { f.q = e.target.value; zeichnen(); };
  $("#kat", el).onchange = (e) => { f.kat = e.target.value; zeichnen(); };
  $("#modus", el).onchange = (e) => { f.modus = e.target.value; zeichnen(); };
  $$("[data-art]", el).forEach((b) => b.onclick = () => {
    f.art = b.dataset.art; $$("[data-art]", el).forEach((x) => x.classList.toggle("on", x === b)); zeichnen();
  });
  zeichnen();
}

async function massen(akt, ids, {orte, kategorien, liste}, fertig) {
  if (!ids.length) return;
  const n = (v) => v === "" || v == null ? undefined : +v;
  let werte = null;
  if (akt === "loeschen") {
    if (!await bestaetigen(`${ids.length} Produkt(e) in Grocy löschen? Produkte mit Bestand, Varianten oder in Rezepten werden übersprungen.`, "Löschen", true)) return;
    meldeMassen(await post("produkte/aktion", {ids, aktion: "loeschen"}), "Produkte gelöscht"); return fertig();
  }
  if (akt === "ki") {
    if (!await bestaetigen(`${ids.length} Produkt(e) von der KI kategorisieren lassen? Das ist eine KI-Anfrage. Gesetzt wird erst nach deiner Prüfung.`, "Starten")) return;
    await post("kategorisieren", {produkt_ids: ids, rezepte: false, nur_ohne_attribute: false, nur_leere: false});
    toast("Kategorisierung läuft, Ergebnis unter KI-Kategorisierung"); return gehe("ki-vorschlaege");
  }
  if (akt === "kategorie") {
    const w = await formDialog(`<h3>Kategorie für ${ids.length} Produkt(e)</h3><label>Kategorie</label>
      <input name="kategorie" list="katliste" required><datalist id="katliste">${kategorien.map((k) => `<option value="${esc(k)}">`).join("")}</datalist>`, "Setzen");
    if (!w) return; werte = {kategorie: w.kategorie.trim()};
  } else if (akt === "modus") {
    const w = await formDialog(`<h3>Tracking-Modus für ${ids.length} Produkt(e)</h3><label>Modus</label><select name="m">${Object.entries(MODI).map(([k, t]) =>
      `<option value="${k}">${t}</option>`).join("")}</select>
      <p class="muted tiny">exakt: Menge und Ablauf je Charge · Menge: ohne Ablaufdruck · Füllstand: voll/knapp/leer · immer da: nie getrackt</p>`, "Setzen");
    if (!w) return; werte = {tracking_modus: w.m};
  } else if (akt === "lagerort") {
    const w = await formDialog(`<h3>Standard-Lagerort</h3><label>Lagerort</label><select name="l">${orte.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("")}</select>`, "Setzen");
    if (!w) return; werte = {lagerort_id: w.l};
  } else if (akt === "mehr") {
    const w = await formDialog(`<h3>Felder für ${ids.length} Produkt(e)</h3><p class="muted tiny">Nur ausgefüllte Felder werden gesetzt.</p>
      <div class="row"><div><label>Toleranz %</label><input name="toleranz_pct" type="number" min="0" max="100"></div>
        <div><label>Haltbar (Tage, −1 = nie)</label><input name="haltbarkeit_tage" type="number" min="-1"></div></div>
      <div class="row"><div><label>geöffnet (Tage)</label><input name="haltbarkeit_offen_tage" type="number" min="0"></div>
        <div><label>gefroren (Tage)</label><input name="haltbarkeit_gefroren_tage" type="number" min="0"></div></div>
      <div class="row"><div><label>Ablauf-Typ</label><select name="ablauf_typ"><option value="">–</option><option value="mhd">MHD</option><option value="verbrauchsdatum">Verbrauchsdatum</option></select></div>
        <div><label>Variante von</label><select name="parent_id"><option value="">–</option><option value="-">keine (lösen)</option>
          ${liste.filter((p) => !p.parent_id && !ids.includes(p.id)).map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select></div></div>`, "Setzen");
    if (!w) return;
    werte = {toleranz_pct: n(w.toleranz_pct), haltbarkeit_tage: n(w.haltbarkeit_tage), haltbarkeit_offen_tage: n(w.haltbarkeit_offen_tage),
      haltbarkeit_gefroren_tage: n(w.haltbarkeit_gefroren_tage), ablauf_typ: w.ablauf_typ || undefined,
      parent_id: w.parent_id === "-" ? "" : w.parent_id || undefined};
    if (!Object.values(werte).some((v) => v !== undefined)) return;
  }
  meldeMassen(await post("produkte/aktion", {ids, aktion: "setzen", werte}), "Produkte geändert");
  fertig();
}
