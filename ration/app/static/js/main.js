import {$, $$, api, esc, guarded, importIds, importText, importVergessen, lokal, merken, titel} from "./lib.js";

const V = new URL(import.meta.url).search; // ?v=<version> für alle Views

// Bereiche mit Tabs. Ein Tab ist eine Seite (route → Modul in views/) oder eine Auswahl
// (modi): mehrere Seiten unter einem Tab, oben als Kacheln wählbar. Alte Routen bleiben gültig.
export const BEREICHE = [
  {id: "start", titel: "Start", ico: "🏠", unten: true, tabs: [
    {route: "", titel: "Übersicht", view: "uebersicht"},
  ]},
  {id: "kochen", titel: "Kochen", ico: "🍳", unten: true, tabs: [
    {route: "heute", titel: "Heute", ico: "🔥", view: "heute"},
    {route: "plan", titel: "Wochenplan", ico: "📅", view: "plan"},
    {route: "kochen", titel: "Kochmodus", ico: "👩‍🍳", view: "kochen"},
  ]},
  {id: "rezepte", titel: "Rezepte", ico: "📖", unten: true, tabs: [
    {route: "rezepte", titel: "Rezeptbuch", ico: "📚", view: "rezepte"},
    {titel: "Neu & KI", ico: "✨", modi: [
      {route: "generieren", titel: "KI generieren", ico: "🪄", text: "Wunsch eingeben, KI erfindet das Rezept", view: "generieren"},
      {route: "import", titel: "Per Link", ico: "🔗", text: "HelloFresh, Chefkoch & Co.", view: "import"},
      {route: "rezept-neu", titel: "Von Hand", ico: "✏️", text: "Zutaten selbst eintragen", view: "rezept_edit"},
      {route: "anleitungen", titel: "Kochanleitungen", ico: "🪜", text: "Schritt für Schritt von der KI", view: "anleitungen"},
    ]},
  ]},
  {id: "vorrat", titel: "Vorrat", ico: "🥫", unten: true, tabs: [
    {route: "inventar", titel: "Bestand", ico: "📦", view: "inventar"},
    {titel: "Einbuchen", ico: "➕", modi: [
      {route: "einbuchen", titel: "Bon & von Hand", ico: "🧾", text: "Kassenbon fotografieren oder Zeilen eintragen", view: "einbuchen"},
      {route: "scannen", titel: "Barcode", ico: "📷", text: "Packungen scannen", view: "scannen"},
    ]},
    {route: "reste", titel: "Reste & Füllstand", ico: "🥡", view: "reste"},
  ]},
  {id: "einkauf", titel: "Einkauf", ico: "🛒", unten: true, tabs: [
    {route: "einkauf", titel: "Einkaufsliste", ico: "📝", view: "einkauf"},
    {route: "angebote", titel: "Angebote", ico: "🏷️", view: "angebote"},
    {route: "ausgaben", titel: "Ausgaben", ico: "💶", view: "ausgaben"},
  ]},
  {id: "produkte", titel: "Produkte", ico: "🗂️", tabs: [
    {route: "produkte", titel: "Produkte", ico: "🥕", view: "produkte"},
    {route: "ki-vorschlaege", titel: "KI-Kategorisierung", ico: "✨", view: "vorschlaege", badge: "vorschlaege"},
    {route: "dubletten", titel: "Dubletten", ico: "👯", view: "dubletten", badge: "dubletten"},
    {route: "packungen", titel: "Packungsgrößen", ico: "📏", view: "packungen"},
  ]},
  {id: "system", titel: "System", ico: "⚙️", tabs: [
    {route: "einstellungen", titel: "Einstellungen", ico: "🎛️", view: "einstellungen"},
    {route: "log/aenderungen", titel: "Änderungen", ico: "📝", view: "log_aenderungen"},
    {route: "log/ki", titel: "KI-Protokoll", ico: "🤖", view: "log_ki"},
  ]},
];
// Detailseiten ohne Tab: Muster → View, Bereich
const DETAIL = [
  [/^kochen\/([^/]+)$/, "kochen", "kochen"],
  [/^rezept\/([^/]+)\/anleitung$/, "anleitung", "rezepte"],
  [/^rezept\/([^/]+)\/bearbeiten$/, "rezept_edit", "rezepte"],
  [/^rezept\/([^/]+)$/, "rezept", "rezepte"],
  [/^produkt-neu$/, "produkt", "produkte"],
  [/^produkt\/([^/]+)$/, "produkt", "produkte"],
];
// Alle Seiten flach: {route, titel, view, bereich, tab, modus?}
const alle = BEREICHE.flatMap((b) => b.tabs.flatMap((t) => t.modi
  ? t.modi.map((m) => ({...m, bereich: b, tab: t, modus: true}))
  : [{...t, bereich: b, tab: t}]));
const badges = {};
const tabBadge = (t) => (t.modi ?? [t]).reduce((n, x) => n + (badges[x.badge] || 0), 0);
const bereichBadge = (b) => b.tabs.reduce((n, t) => n + tabBadge(t), 0);
const badgeHtml = (n, warn = false) => n ? `<span class="badge ${warn ? "warn" : ""}">${n}</span>` : "";
// Ziel eines Tabs/Bereichs: zuletzt besuchte Seite darin, sonst die erste
const zielTab = (t) => t.modi ? (t.modi.find((m) => m.route === lokal(`modus.${t.titel}`, null)) ?? t.modi[0]).route : t.route;
const zielBereich = (b) => lokal(`bereich.${b.id}`, null) ?? zielTab(b.tabs[0]);

let aktiv = {bereich: BEREICHE[0], seite: alle[0]};
function menueZeichnen() {
  const {bereich, seite} = aktiv;
  $("#menu").innerHTML = BEREICHE.map((b) => `<a href="#/${zielBereich(b)}" class="bereich ${b === bereich ? "on" : ""}">
      <span class="ico">${b.ico}</span><span class="name">${esc(b.titel)}</span>${badgeHtml(bereichBadge(b), b.id === "produkte")}</a>
    ${b === bereich && b.tabs.length > 1 ? `<div class="unter">${b.tabs.map((t) => `<a href="#/${zielTab(t)}"
      class="${seite?.tab === t ? "on" : ""}">${esc(t.titel)}${badgeHtml(tabBadge(t), t.badge === "dubletten")}</a>`).join("")}</div>` : ""}`).join("");
  $("#unten").innerHTML = BEREICHE.filter((b) => b.unten).map((b) => `<a href="#/${zielBereich(b)}" class="${b === bereich ? "on" : ""}">
    <span class="ico">${b.ico}</span><span>${esc(b.titel)}</span>${bereichBadge(b) ? `<i class="punkt"></i>` : ""}</a>`).join("")
    + `<button id="mehr" class="${bereich.unten ? "" : "on"}" aria-label="Mehr"><span class="ico">☰</span><span>Mehr</span>${
      BEREICHE.some((b) => !b.unten && bereichBadge(b)) ? `<i class="punkt"></i>` : ""}</button>`;
  $("#mehr").onclick = () => document.body.classList.add("nav-auf");
  const tabs = $("#tabs");
  // Tabs nur auf Seiten des Bereichs (nicht auf Detailseiten), Auswahl-Kacheln unter dem Tab
  if (!seite || (bereich.tabs.length < 2 && !seite.modus)) { tabs.hidden = true; tabs.innerHTML = ""; return; }
  tabs.hidden = false;
  tabs.innerHTML = (bereich.tabs.length > 1 ? `<div class="tab-leiste">${bereich.tabs.map((t) => `<a href="#/${zielTab(t)}"
      class="${seite.tab === t ? "on" : ""}"><span class="ico">${t.ico ?? ""}</span>${esc(t.titel)}${badgeHtml(tabBadge(t), t.badge === "dubletten")}</a>`).join("")}</div>` : "")
    + (seite.modus ? `<div class="modi">${seite.tab.modi.map((m) => `<a href="#/${m.route}" class="modus ${m === seite.tab.modi.find((x) => x.route === seite.route) ? "on" : ""}">
      <span class="ico">${m.ico}</span><span><b>${esc(m.titel)}</b><small>${esc(m.text ?? "")}</small></span></a>`).join("")}</div>` : "");
  tabs.querySelector(".tab-leiste a.on")?.scrollIntoView({block: "nearest", inline: "center"});
}


let laufend = 0, aufraeumen = null;
async function zeigen() {
  // Views dürfen eine Aufräumfunktion zurückgeben (Intervalle, Wake Lock, Body-Klassen)
  try { aufraeumen?.(); } catch (e) { console.error(e); }
  aufraeumen = null;
  const pfad = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  const [route, query] = pfad.split("?");
  const params = Object.fromEntries(new URLSearchParams(query ?? ""));
  let seite = alle.find((p) => p.route === route), view = seite?.view, arg = null, bereich = seite?.bereich;
  if (!seite) {
    for (const [re, v, b] of DETAIL) {
      const m = route.match(re);
      if (m) { view = v; arg = m[1] ?? null; bereich = BEREICHE.find((x) => x.id === b); break; }
    }
  }
  if (!view) { seite = alle[0]; view = seite.view; bereich = seite.bereich; }
  if (seite) {
    merken(`bereich.${bereich.id}`, seite.route);
    if (seite.modus) merken(`modus.${seite.tab.titel}`, seite.route);
  }
  aktiv = {bereich, seite};
  menueZeichnen();
  // Detailseite: Zurück statt Menü (auf dem Handy)
  $("#zurueck").hidden = !!seite;
  document.body.classList.toggle("detail", !seite);
  document.body.classList.remove("nav-auf");
  titel(bereich.titel);
  const nr = ++laufend;
  const el = $("#view");
  el.innerHTML = `<div class="leer">Lädt …</div>`;
  await guarded(async () => {
    const mod = await import(`./views/${view}.js${V}`);
    if (nr !== laufend) return;
    el.innerHTML = "";
    const r = await mod.default(el, {arg, params, neu: () => zeigen()});
    if (typeof r === "function") { if (nr === laufend) aufraeumen = r; else r(); }
  });
  window.scrollTo(0, 0);
}


async function status() {
  await guarded(async () => {
    const [s, v, d] = await Promise.all([api("status"), api("vorschlaege").catch(() => []),
      api("dubletten").catch(() => [])]);
    $("#statusline").textContent = s.text;
    badges.vorschlaege = new Set(v.map((x) => `${x.typ}:${x.objekt_id}`)).size || 0;
    badges.dubletten = d.length || 0;
    menueZeichnen();
  });
}

// --- Globale Suche: Rezepte, Produkte, Menüpunkte -----------------------------------------
let index = null;
async function suchindex() {
  if (index) return index;
  const [r, p] = await Promise.all([api("rezepte"), api("produkte")]);
  index = [
    ...alle.map((m) => ({text: m.titel, route: m.route, art: m.bereich.titel})),
    ...r.map((x) => ({text: x.name, route: `rezept/${x.id}`, art: "Rezept"})),
    ...p.map((x) => ({text: x.name, route: `produkt/${x.id}`, art: "Produkt"})),
  ];
  setTimeout(() => { index = null; }, 60000);
  return index;
}
function sucheEinrichten() {
  const inp = $("#suche"), box = $("#treffer");
  let akt = 0, liste = [];
  const zeichnen = () => {
    box.hidden = !liste.length;
    box.innerHTML = liste.map((t, i) => `<a href="#/${t.route}" class="${i === akt ? "akt" : ""}">${esc(t.text)}
      <span class="muted tiny">· ${t.art}</span></a>`).join("");
  };
  inp.oninput = async () => {
    const q = inp.value.trim().toLowerCase();
    if (!q) { liste = []; zeichnen(); return; }
    const idx = await guarded(suchindex) ?? [];
    liste = idx.filter((t) => t.text.toLowerCase().includes(q))
      .sort((a, b) => a.text.toLowerCase().indexOf(q) - b.text.toLowerCase().indexOf(q)).slice(0, 12);
    akt = 0; zeichnen();
  };
  inp.onkeydown = (e) => {
    if (e.key === "ArrowDown") { akt = Math.min(akt + 1, liste.length - 1); zeichnen(); e.preventDefault(); }
    if (e.key === "ArrowUp") { akt = Math.max(akt - 1, 0); zeichnen(); e.preventDefault(); }
    if (e.key === "Enter" && liste[akt]) { location.hash = "#/" + liste[akt].route; inp.value = ""; liste = []; zeichnen(); inp.blur(); }
    if (e.key === "Escape") { inp.value = ""; liste = []; zeichnen(); inp.blur(); }
  };
  box.onclick = () => { inp.value = ""; liste = []; zeichnen(); };
  inp.onblur = () => setTimeout(() => { box.hidden = true; }, 150);
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !e.target.closest("input,textarea,select")) { e.preventDefault(); inp.focus(); }
  });
}

// --- Laufende Rezept-Importe: Kreis mit Fortschritt oben rechts, auf jeder Seite --------------
let importTimer = null;
async function importStatus() {
  clearTimeout(importTimer);
  const ids = importIds(), el = $("#importstatus");
  if (!ids.length) { el.hidden = true; el.innerHTML = ""; return; }
  const staende = await Promise.all(ids.map((id) => api(`import/${id}`).catch(() => null)));
  const liste = ids.map((id, i) => ({id, s: staende[i]}));
  liste.filter((x) => !x.s).forEach((x) => importVergessen(x.id));  // gelöscht/unbekannt
  el.innerHTML = liste.filter((x) => x.s).map(({id, s}) => {
    const href = `#/import?id=${id}`;
    if (s.status === "laeuft") {
      const rest = s.rest_s ? ` · noch ca. ${s.rest_s} s` : "";
      return `<a href="${href}" class="kreis" style="--p:${s.fortschritt}" title="Rezept-Import: ${esc(importText(s))}${rest}">${s.fortschritt}%</a>`;
    }
    if (s.status === "fertig") return `<a href="${href}" class="kreis fertig" title="Vorschau fertig: ${esc(s.name)} – öffnen">✓</a>`;
    return `<a href="${href}" class="kreis fehler" title="Import fehlgeschlagen: ${esc(s.fehler)}">!</a>`;
  }).join("");
  el.hidden = !el.innerHTML;
  const laeuft = liste.some((x) => x.s?.status === "laeuft");
  importTimer = setTimeout(importStatus, laeuft ? 1500 : 15000);
}
window.addEventListener("importe", importStatus);

$("#burger").onclick = () => document.body.classList.toggle("nav-auf");
$("#zurueck").onclick = () => {
  if (history.length > 1) history.back(); else location.hash = "#/" + zielBereich(aktiv.bereich);
};
$("#scrim").onclick = () => document.body.classList.remove("nav-auf");
window.addEventListener("hashchange", () => { zeigen(); status(); });
sucheEinrichten();
zeigen();
status();
importStatus();
