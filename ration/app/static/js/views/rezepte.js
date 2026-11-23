import {$, $$, AUFWAND, nutriBadge, api, quelleBadge, quelleInfo, bestaetigen, dmy, esc, formDialog, guarded, lokal, meldeMassen, merken, post, sterne, tabelle, gehe} from "../lib.js";

// Rezeptbuch: Suche, Filter, Sortierung, Massenaktionen. ?kochbar=1, ?produkt=<id>
export default async function render(el, {params}) {
  const f = {q: "", tag: "", quelle: "", kochbar: params.kochbar === "1", nur_fav: false, ansicht: lokal("rezepte.ansicht", "tabelle"), ...lokal("rezepte.filter", {})};
  if (params.kochbar === "1") f.kochbar = true;
  const [buch, produkt] = await Promise.all([api("rezeptbuch"), params.produkt ? api(`produkte/${params.produkt}`).catch(() => null) : null]);
  const mitProdukt = produkt ? new Set(produkt.rezepte.map((r) => r.id)) : null;
  const tags = [...new Set(buch.flatMap((r) => r.tags))].sort((a, b) => a.localeCompare(b, "de"));
  const quellen = new Map(buch.map((r) => quelleInfo(r.quelle)).map((i) => [i.key, i.key === "web" ? "andere Websites" : i.label]));
  if (f.quelle && !quellen.has(f.quelle)) f.quelle = "";

  el.innerHTML = `<div class="card">
    <div class="werkzeug"><input type="search" id="q" placeholder="Rezept suchen …" value="${esc(f.q)}">
      <select id="tag"><option value="">Alle Tags</option>${tags.map((t) => `<option ${t === f.tag ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      <select id="quelle"><option value="">Alle Quellen</option>${[...quellen].sort((a, b) => a[1].localeCompare(b[1], "de")).map(([k, t]) =>
        `<option value="${k}" ${k === f.quelle ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      <a class="btn primary" href="#/rezept-neu">+ Neu</a><a class="btn" href="#/import">🔗 Import</a></div>
    <div class="chips"><button class="chip ${f.kochbar ? "on" : ""}" data-f="kochbar">✅ jetzt kochbar</button>
      <button class="chip ${f.nur_fav ? "on" : ""}" data-f="nur_fav">★ Favoriten</button>
      ${produkt ? `<a class="chip on" href="#/rezepte">mit ${esc(produkt.name)} ✕</a>` : ""}
      <span class="grow"></span>
      <button class="chip ${f.ansicht === "tabelle" ? "on" : ""}" data-ansicht="tabelle">☰ Tabelle</button>
      <button class="chip ${f.ansicht === "karten" ? "on" : ""}" data-ansicht="karten">▦ Karten</button></div>
    <div id="liste" style="margin-top:10px"></div></div>`;

  const sichtbar = () => {
    const q = f.q.toLowerCase();
    return buch.filter((r) => (!q || r.name.toLowerCase().includes(q) || r.tags.some((t) => t.toLowerCase().includes(q)))
      && (!f.tag || r.tags.includes(f.tag)) && (!f.quelle || quelleInfo(r.quelle).key === f.quelle) && (!f.kochbar || r.kochbar) && (!f.nur_fav || r.favorit)
      && (!mitProdukt || mitProdukt.has(r.id)));
  };
  const liste = $("#liste", el);
  const zeichnen = () => {
    merken("rezepte.filter", {q: f.q, tag: f.tag, quelle: f.quelle, nur_fav: f.nur_fav, ansicht: f.ansicht});
    const zeilen = sichtbar();
    if (f.ansicht === "karten") return karten(liste, zeilen);
    tabelle(liste, {
      zeilen, sortierung: "name", auswahl: () => {}, onKlick: (r) => gehe(`rezept/${r.id}`), leer: "Keine Rezepte gefunden.",
      aktionen: `<button class="btn klein" data-akt="tag_hinzu">+ Tag</button><button class="btn klein" data-akt="tag_weg">− Tag</button>
        <button class="btn klein" data-akt="aufwand">Aufwand</button><button class="btn klein" data-akt="status">Status</button>
        <button class="btn klein" data-akt="fav">★ Favorit</button><button class="btn klein" data-akt="unfav">☆ kein Favorit</button>
        <button class="btn klein danger" data-akt="loeschen">Löschen</button>`,
      spalten: [
        {key: "name", titel: "Rezept", wert: (r) => `${sterne(r)} <b>${esc(r.name)}</b>${r.status === "ungetestet" ? ` <span class="tag warn">neu</span>` : ""}
          ${r.tags.length ? `<div class="muted tiny">${r.tags.map(esc).join(" · ")}</div>` : ""}`},
        {key: "quelle", titel: "Quelle", klasse: "nw", sort: (r) => quelleInfo(r.quelle).label, wert: (r) => quelleBadge(r.quelle)},
        {key: "kochbar", titel: "Vorrat", klasse: "nw", sort: (r) => r.fehlend.length,
          wert: (r) => r.kochbar ? `<span class="dot"></span>alles da` : `<span class="dot ${r.fehlend.length > 2 ? "fehlt" : "knapp"}"></span><span title="${esc(r.fehlend.join(", "))}">${r.fehlend.length} fehlen</span>`},
        {key: "kcal", titel: "kcal", klasse: "nw right", sort: (r) => r.kcal ?? 1e9,
          wert: (r) => r.kcal ? `${r.naehrwert_abdeckung < 0.95 ? `<span class="muted" title="${Math.round(100 * r.naehrwert_abdeckung)} % der Zutaten mit Nährwerten">≥</span>` : ""}${Math.round(r.kcal)}` : `<span class="muted">–</span>`},
        {key: "nutri", titel: "Nutri", klasse: "nw", sort: (r) => r.nutri ?? "Z", wert: (r) => nutriBadge(r.nutri)},
        {key: "aufwand", titel: "Aufwand", klasse: "nw", wert: (r) => esc(AUFWAND[r.aufwand] ?? "–")},
        {key: "gekocht", titel: "Gekocht", klasse: "nw right", wert: (r) => r.gekocht ? `${r.gekocht}×` : `<span class="muted">nie</span>`},
        {key: "zuletzt", titel: "Zuletzt", klasse: "nw", sort: (r) => r.zuletzt ?? "", wert: (r) => r.zuletzt ? dmy(r.zuletzt) : "–"},
        {key: "bewertung", titel: "👍", klasse: "nw", sort: (r) => (r.favorit ? 2 : 0) + r.bewertung, wert: (r) => r.bewertung > 0 ? "👍" : r.bewertung < 0 ? "👎" : ""},
      ],
    });
  };
  liste.onclick = (ev) => {
    const b = ev.target.closest("[data-akt]");
    if (b) guarded(() => massenaktion(b.dataset.akt, [...liste._tabelle.gewaehlt], tags, () => render(el, {params})));
  };
  $("#q", el).oninput = (e) => { f.q = e.target.value; zeichnen(); };
  $("#tag", el).onchange = (e) => { f.tag = e.target.value; zeichnen(); };
  $("#quelle", el).onchange = (e) => { f.quelle = e.target.value; zeichnen(); };
  $$("[data-f]", el).forEach((b) => b.onclick = () => { f[b.dataset.f] = !f[b.dataset.f]; b.classList.toggle("on"); zeichnen(); });
  $$("[data-ansicht]", el).forEach((b) => b.onclick = () => {
    f.ansicht = b.dataset.ansicht; merken("rezepte.ansicht", f.ansicht);
    $$("[data-ansicht]", el).forEach((x) => x.classList.toggle("on", x === b)); liste._tabelle = undefined; zeichnen();
  });
  zeichnen();
}

function karten(el, zeilen) {
  el.innerHTML = zeilen.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(220px,100%),1fr))">${zeilen.map((r) => `
    <a class="kachel" href="#/rezept/${esc(r.id)}" style="text-align:left">
      <div><b>${esc(r.name)}</b> ${sterne(r)} ${quelleBadge(r.quelle)}</div>
      <div class="muted tiny">${r.portionen} P.${r.kcal ? ` · ${Math.round(r.kcal)} kcal` : ""} ${nutriBadge(r.nutri)}${r.aufwand ? " · " + esc(AUFWAND[r.aufwand]) : ""}${r.gekocht ? ` · ${r.gekocht}× gekocht` : " · noch nie gekocht"}</div>
      <div class="tiny" style="margin-top:4px">${r.kochbar ? `<span class="dot"></span>alles da` : `<span class="dot ${r.fehlend.length > 2 ? "fehlt" : "knapp"}"></span>${r.fehlend.length} fehlen`}</div>
      ${r.tags.length ? `<div style="margin-top:6px">${r.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join(" ")}</div>` : ""}</a>`).join("")}</div>`
    : `<div class="leer">Keine Rezepte gefunden.</div>`;
}

async function massenaktion(akt, ids, tags, fertig) {
  if (!ids.length) return;
  let body = {ids, aktion: akt};
  if (akt === "loeschen") {
    if (!await bestaetigen(`${ids.length} Rezept(e) in Grocy löschen? Geplante Tage damit werden entfernt, gekochte bleiben im Verlauf.`, "Löschen", true)) return;
  } else if (akt === "fav" || akt === "unfav") {
    body = {ids, aktion: "favorit", wert: akt === "fav"};
  } else if (akt === "tag_hinzu" || akt === "tag_weg") {
    const w = await formDialog(`<h3>${akt === "tag_hinzu" ? "Tag hinzufügen" : "Tag entfernen"}</h3>
      <label>Tag</label><input name="wert" list="tagliste" required autofocus><datalist id="tagliste">${tags.map((t) => `<option value="${esc(t)}">`).join("")}</datalist>`, "Anwenden");
    if (!w?.wert) return;
    body.wert = w.wert.trim();
  } else if (akt === "aufwand") {
    const w = await formDialog(`<h3>Aufwand setzen</h3><label>Aufwand</label><select name="wert">${Object.entries(AUFWAND).map(([k, t]) => `<option value="${k}">${t}</option>`).join("")}</select>`, "Anwenden");
    if (!w) return; body.wert = w.wert;
  } else if (akt === "status") {
    const w = await formDialog(`<h3>Status setzen</h3><label>Status</label><select name="wert"><option>getestet</option><option>ungetestet</option></select>`, "Anwenden");
    if (!w) return; body.wert = w.wert;
  }
  meldeMassen(await post("rezepte/aktion", body), "Rezepte geändert");
  fertig();
}
