import {$, $$, api, esc, guarded, lokal, merken, titel} from "./lib.js";

const V = new URL(import.meta.url).search; // ?v=<version> für alle Views

// Menü: Gruppen mit Unterpunkten. route → Modul in views/
export const MENUE = [
  {route: "", titel: "Übersicht", ico: "🏠", view: "uebersicht"},
  {gruppe: "Küche", ico: "🍳", punkte: [
    {route: "heute", titel: "Heute kochen", ico: "🔥", view: "heute"},
    {route: "kochen", titel: "Kochmodus", ico: "👩‍🍳", view: "kochen"},
    {route: "plan", titel: "Wochenplan", ico: "📅", view: "plan"},
    {route: "einkauf", titel: "Einkauf", ico: "🛒", view: "einkauf"},
  ]},
  {gruppe: "Rezepte", ico: "📖", punkte: [
    {route: "rezepte", titel: "Rezeptbuch", ico: "📚", view: "rezepte"},
    {route: "rezept-neu", titel: "Neues Rezept", ico: "✏️", view: "rezept_edit"},
    {route: "import", titel: "Import per Link", ico: "🔗", view: "import"},
    {route: "anleitungen", titel: "Kochanleitungen", ico: "🪜", view: "anleitungen"},
  ]},
  {gruppe: "Vorrat", ico: "🥫", punkte: [
    {route: "inventar", titel: "Inventar", ico: "📦", view: "inventar"},
    {route: "einbuchen", titel: "Einbuchen & Bon", ico: "🧾", view: "einbuchen"},
    {route: "scannen", titel: "Scannen", ico: "📱", view: "scannen"},
    {route: "reste", titel: "Reste & Füllstand", ico: "🥡", view: "reste"},
    {route: "ausgaben", titel: "Ausgaben", ico: "💶", view: "ausgaben"},
  ]},
  {gruppe: "Datenbank", ico: "🗂️", punkte: [
    {route: "produkte", titel: "Produkte", ico: "🥕", view: "produkte"},
    {route: "dubletten", titel: "Dubletten", ico: "👯", view: "dubletten", badge: "dubletten"},
    {route: "packungen", titel: "Packungsgrößen", ico: "📏", view: "packungen"},
    {route: "ki-vorschlaege", titel: "KI-Kategorisierung", ico: "✨", view: "vorschlaege", badge: "vorschlaege"},
  ]},
  {gruppe: "System", ico: "⚙️", punkte: [
    {route: "einstellungen", titel: "Einstellungen", ico: "🎛️", view: "einstellungen"},
    {route: "log/aenderungen", titel: "Änderungsprotokoll", ico: "📝", view: "log_aenderungen"},
    {route: "log/ki", titel: "KI-Protokoll", ico: "🤖", view: "log_ki"},
  ]},
];
// Routen, die nicht im Menü stehen: Detailseiten. Muster → View, markierter Menüpunkt
const DETAIL = [
  [/^kochen\/([^/]+)$/, "kochen", "kochen"],
  [/^rezept\/([^/]+)\/anleitung$/, "anleitung", "anleitungen"],
  [/^rezept\/([^/]+)\/bearbeiten$/, "rezept_edit", "rezepte"],
  [/^rezept\/([^/]+)$/, "rezept", "rezepte"],
  [/^produkt-neu$/, "produkt", "produkte"],
  [/^produkt\/([^/]+)$/, "produkt", "produkte"],
];
const alle = MENUE.flatMap((m) => m.punkte ?? [m]);
const badges = {};

function menueZeichnen(aktiv) {
  const zu = lokal("menue.zu", []);
  $("#menu").innerHTML = MENUE.map((m) => {
    if (!m.punkte) return link(m, aktiv);
    const offen = !zu.includes(m.gruppe) || m.punkte.some((p) => p.route === aktiv);
    return `<button class="gruppe ${offen ? "" : "zu"}" data-gruppe="${esc(m.gruppe)}"><span class="ico">${m.ico}</span>${esc(m.gruppe)}
      <span class="pfeil">▾</span></button><div class="unter">${m.punkte.map((p) => link(p, aktiv)).join("")}</div>`;
  }).join("");
  $$("#menu [data-gruppe]").forEach((b) => b.onclick = () => {
    const g = b.dataset.gruppe, liste = lokal("menue.zu", []);
    merken("menue.zu", liste.includes(g) ? liste.filter((x) => x !== g) : [...liste, g]);
    b.classList.toggle("zu");
  });
}
const link = (p, aktiv) => `<a href="#/${p.route}" class="${p.route === aktiv ? "on" : ""}"><span class="ico">${p.ico}</span>${esc(p.titel)}
  ${badges[p.badge] ? `<span class="badge ${p.badge === "dubletten" ? "warn" : ""}">${badges[p.badge]}</span>` : ""}</a>`;


let laufend = 0, aufraeumen = null;
async function zeigen() {
  // Views dürfen eine Aufräumfunktion zurückgeben (Intervalle, Wake Lock, Body-Klassen)
  try { aufraeumen?.(); } catch (e) { console.error(e); }
  aufraeumen = null;
  const pfad = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  const [route, query] = pfad.split("?");
  const params = Object.fromEntries(new URLSearchParams(query ?? ""));
  let eintrag = alle.find((p) => p.route === route), arg = null, markiert = route;
  if (!eintrag) {
    for (const [re, view, menue] of DETAIL) {
      const m = route.match(re);
      if (m) { eintrag = {view, titel: ""}; arg = m[1] ?? null; markiert = menue; break; }
    }
  }
  eintrag ??= alle[0];
  menueZeichnen(markiert);
  document.body.classList.remove("nav-auf");
  titel(eintrag.titel || "rationPlaner");
  const nr = ++laufend;
  const el = $("#view");
  el.innerHTML = `<div class="leer">Lädt …</div>`;
  await guarded(async () => {
    const mod = await import(`./views/${eintrag.view}.js${V}`);
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
    menueZeichnen(decodeURIComponent(location.hash.replace(/^#\/?/, "")).split("?")[0]);
  });
}

// --- Globale Suche: Rezepte, Produkte, Menüpunkte -----------------------------------------
let index = null;
async function suchindex() {
  if (index) return index;
  const [r, p] = await Promise.all([api("rezepte"), api("produkte")]);
  index = [
    ...alle.map((m) => ({text: m.titel, route: m.route, art: "Seite"})),
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

$("#burger").onclick = () => document.body.classList.toggle("nav-auf");
$("#scrim").onclick = () => document.body.classList.remove("nav-auf");
window.addEventListener("hashchange", () => { zeigen(); status(); });
sucheEinrichten();
zeigen();
status();
