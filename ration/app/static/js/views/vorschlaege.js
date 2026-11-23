import {$, $$, api, dmy, esc, fmt, guarded, post, toast} from "../lib.js";

const FELD = {tracking_modus: "Modus", kategorie: "Kategorie", ablauf_typ: "Ablauf", haltbarkeit_tage: "Haltbar (Tage)",
  haltbarkeit_offen_tage: "geöffnet", haltbarkeit_gefroren_tage: "gefroren", haltbarkeit_aufgetaut_tage: "aufgetaut",
  g_je_einheit: "g je Einheit", toleranz_pct: "Toleranz %", packung: "Packung", tags: "Tags", aufwand: "Aufwand",
  rest_haltbarkeit_tage: "Rest-Haltbarkeit", naehrwerte: "Nährwerte /100 g", variante: "Neue Größe", variante_von: "Austauschbar unter"};
const wert = (v, feld) => feld === "haltbarkeit_offen_tage" && v === 0 ? "unverändert beim Öffnen"
  : feld === "haltbarkeit_tage" && v === -1 ? "unbegrenzt" : v == null ? "–" : Array.isArray(v) ? v.join(", ") : typeof v === "object" ? ("name" in v && !("menge" in v)
  ? `${v.name}${v.parent_id ? "" : " (neu anlegen)"}` : "kcal" in v
  ? `${fmt(v.kcal)} kcal · P ${fmt(v.protein)} · F ${fmt(v.fett)} · KH ${fmt(v.kohlenhydrate)} · Z ${fmt(v.zucker)} · Salz ${fmt(v.salz)}${v.obst_gemuese ? " · OGH" : ""}${v.roh ? " · roh" : ""}`
  : [`${v.einheit ?? ""} ${fmt(v.menge)}`, v.marke, v.haendler && `bei ${v.haendler}`, v.quelle === "bon" && "vom Bon", v.grund]
    .filter(Boolean).join(" · ")) : String(v);

// KI-Kategorisierung: Läufe starten, Vorschläge als Diff prüfen und übernehmen
export default async function render(el) {
  const [vor, laeufe] = await Promise.all([api("vorschlaege"), api("kategorisieren")]);
  const lauf = laeufe[0], laeuft = lauf?.status === "laeuft";
  const gruppen = {};
  vor.forEach((v) => (gruppen[`${v.typ}:${v.objekt_id}`] ??= {name: v.name, typ: v.typ, id: v.objekt_id, felder: []}).felder.push(v));
  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))">
    <div class="card"><h2>Lauf starten</h2>
      <p class="muted small">Die KI schlägt Modus, Kategorie, Haltbarkeiten, Packung und Toleranz vor (Rezepte: Tags, Aufwand).
        Gesetzt wird erst nach deiner Prüfung.</p>
      <label class="inline"><input type="checkbox" id="kp" checked> Produkte</label>
      <label class="inline"><input type="checkbox" id="kr" checked> Rezepte</label>
      <label class="inline"><input type="checkbox" id="kn"> Nährwerte je 100 g schätzen (Produkte ohne Werte)</label>
      <label class="inline"><input type="checkbox" id="kgr"> Austauschbare Zutaten gruppieren (Fusilli ↔ Spaghetti unter „Nudeln“)</label>
      <label class="inline"><input type="checkbox" id="kpk"> Packungsgrößen analysieren (Bons, Angebote, KI-Wissen)</label>
      <div class="row" style="align-items:center"><label class="inline" style="flex:0 0 auto">Standard = häufigste Größe der letzten</label>
        <input id="kmon" type="number" min="1" max="36" value="6" style="flex:0 0 70px"><span class="muted small" style="flex:0 0 auto">Monate</span></div>
      <label class="inline"><input type="checkbox" id="kalle"> Auch schon kategorisierte prüfen</label>
      <button class="btn primary voll" id="start" ${laeuft ? "disabled" : ""}>${laeuft ? "Läuft …" : "Kategorisierung starten"}</button>
      <p class="muted tiny">Einzelne Produkte gezielt: in der <a href="#/produkte">Produktliste</a> auswählen → „KI kategorisieren“.</p>
      <h3>Letzte Läufe</h3>${laeufe.length ? `<table>${laeufe.map((l) => `<tr><td class="nw">${dmy(l.angelegt)}</td>
        <td><span class="tag ${l.status === "fehler" ? "bad" : l.status === "laeuft" ? "warn" : "good"}">${esc(l.status)}</span></td>
        <td class="muted tiny">${l.fehler ? esc(l.fehler) : l.umfang?.produkt_ids ? `${l.umfang.produkt_ids.length} Produkte` : l.umfang?.nur_ohne_attribute === false ? "alle" : "nur neue"}</td></tr>`).join("")}</table>`
        : `<p class="muted">Noch keine.</p>`}
      <p class="muted tiny">Anfragen und Rohantworten stehen im <a href="#/log/ki">KI-Protokoll</a>.</p></div>
    <div class="card"><div class="kopf"><h2>Vorschläge <span class="muted">(${Object.keys(gruppen).length} Objekte, ${vor.length} Felder)</span></h2>
      ${vor.length ? `<div class="aktionen"><button class="btn klein" id="alle">alle an</button><button class="btn klein" id="keine">alle aus</button></div>` : ""}</div>
      ${vor.length ? `<p class="muted small">Haken = übernehmen, der Rest wird abgelehnt.</p>
        <div class="tabelle"><table><tbody>${Object.values(gruppen).map((g) => `<tr><td colspan="4" style="background:var(--hover)">
            <label class="inline" style="margin:0"><input type="checkbox" data-gruppe="${esc(g.typ + g.id)}" checked>
            <b>${g.typ === "produkt" ? `<a href="#/produkt/${esc(g.id)}">${esc(g.name)}</a>` : `<a href="#/rezept/${esc(g.id)}">${esc(g.name)}</a>`}</b>
            <span class="muted tiny">${esc(g.typ)}</span></label></td></tr>
          ${g.felder.map((v) => `<tr><td class="check"><input type="checkbox" data-v="${v.id}" data-g="${esc(g.typ + g.id)}" checked></td>
            <td class="muted nw">${esc(FELD[v.feld] ?? v.feld)}</td><td class="muted">${esc(wert(v.alt, v.feld))}</td><td>→ <b>${esc(wert(v.neu, v.feld))}</b></td></tr>`).join("")}`).join("")}
        </tbody></table></div>
        <button class="btn primary voll" id="ok">Auswahl übernehmen</button>`
      : `<div class="leer">${laeuft ? "Läuft … Ergebnis kommt gleich." : "Keine offenen Vorschläge."}</div>`}</div></div>`;
  if (laeuft) setTimeout(() => { if (el.isConnected) guarded(() => render(el)); }, 4000);
  $("#start", el).onclick = () => guarded(async () => {
    const alle = $("#kalle", el).checked;
    await post("kategorisieren", {produkte: $("#kp", el).checked, rezepte: $("#kr", el).checked, naehrwerte: $("#kn", el).checked,
      packungen: $("#kpk", el).checked, gruppen: $("#kgr", el).checked, monate: +$("#kmon", el).value || 6, nur_ohne_attribute: !alle, nur_leere: !alle});
    toast("Kategorisierung läuft"); render(el);
  });
  if (!vor.length) return;
  const setzen = (an) => $$("[data-v],[data-gruppe]", el).forEach((c) => { c.checked = an; });
  $("#alle", el).onclick = () => setzen(true);
  $("#keine", el).onclick = () => setzen(false);
  $$("[data-gruppe]", el).forEach((g) => g.onchange = () => $$(`[data-g="${CSS.escape(g.dataset.gruppe)}"]`, el).forEach((c) => { c.checked = g.checked; }));
  $("#ok", el).onclick = () => guarded(async () => {
    const boxen = $$("[data-v]", el);
    const e = await post("vorschlaege", {uebernehmen: boxen.filter((b) => b.checked).map((b) => +b.dataset.v),
      ablehnen: boxen.filter((b) => !b.checked).map((b) => +b.dataset.v)});
    toast(`${e.uebernommen} übernommen` + (e.fehler.length ? ` · Fehler: ${e.fehler.join("; ")}` : ""), e.fehler.length ? "fehler" : "");
    render(el);
  });
}
