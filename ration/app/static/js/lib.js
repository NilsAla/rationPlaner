// Gemeinsame Helfer. Alle Pfade relativ, damit es hinter Ingress funktioniert.

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
export const fmt = (n, stellen = 1) => n == null ? "–" : Number(n).toLocaleString("de-DE", {maximumFractionDigits: stellen});
export const eur = (n) => n == null ? "–" : Number(n).toLocaleString("de-DE", {style: "currency", currency: "EUR"});
export const iso = (d) => {
  const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10);
};
export const heute = () => iso(new Date());
export const plusTage = (s, n) => { const d = new Date(s + "T12:00:00"); d.setDate(d.getDate() + n); return iso(d); };
export const dmy = (s) => s ? new Date(String(s).length === 10 ? s + "T12:00:00" : s)
  .toLocaleDateString("de-DE", {day: "2-digit", month: "2-digit", year: "2-digit"}) : "–";
export const wt = (s) => new Date(s + "T12:00:00").toLocaleDateString("de-DE", {weekday: "short", day: "2-digit", month: "2-digit"});
export const zeit = (s) => s ? new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z")
  .toLocaleString("de-DE", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "";
export const WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
export const AUFWAND = {schnell: "schnell", normal: "normal", aufwaendig: "aufwändig"};
// Rezept-Herkunft: Schlüssel für den Filter, Anzeige, Badge. quelle null = vor der Quellenspalte
// angelegt (manuell oder Testdaten), zählt als manuell.
export const QUELLEN = {hellofresh: "HelloFresh", chefkoch: "Chefkoch", manuell: "manuell", test: "Testrezept", ki: "KI"};
export function quelleInfo(q) {
  const v = (q ?? "manuell").toLowerCase();
  const key = v.includes("chefkoch") ? "chefkoch" : QUELLEN[v] ? v : "web";
  return {key, label: QUELLEN[key] ?? v.replace(/^www\./, "")};
}
export const quelleBadge = (q) => { const i = quelleInfo(q); return `<span class="tag quelle q-${i.key}">${esc(i.label)}</span>`; };
export const MODI = {exakt: "exakt", menge: "Menge", fuellstand: "Füllstand", immer_da: "immer da"};
export const KATEGORIEN = ["Gemüse", "Obst", "Kräuter", "Fleisch", "Fisch", "Milchprodukte", "Käse", "Eier", "Brot",
  "Nudeln & Reis", "Hülsenfrüchte", "Konserven", "Mehl & Backen", "Öl & Essig", "Gewürze", "Saucen",
  "Nüsse & Saaten", "Süßes", "Getränke", "Tiefkühl", "Sonstiges"];

export async function api(path, opts = {}) {
  const headers = {"X-Ration-Client": "ui"};
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const body = opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string"
    ? JSON.stringify(opts.body) : opts.body;
  const r = await fetch("api/v1/" + path, {...opts, headers: {...headers, ...(opts.headers || {})}, body});
  const daten = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) {
    const d = daten?.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0] ?? ""}: ${x.msg}`).join(", ") : d ?? r.statusText);
  }
  return daten;
}
export const post = (path, body) => api(path, {method: "POST", body: body ?? {}});
export const put = (path, body) => api(path, {method: "PUT", body});
export const patch = (path, body) => api(path, {method: "PATCH", body});
export const del = (path) => api(path, {method: "DELETE"});

export function toast(text, art = "") {
  const t = document.createElement("div");
  t.className = "toast " + art; t.textContent = text;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), art === "fehler" ? 7000 : 3500);
}

export async function guarded(fn) {
  try { return await fn(); } catch (e) { console.error(e); toast(e.message || String(e), "fehler"); }
}

/** Dialog mit eigenem HTML; liefert den Wert des gedrückten Knopfs (data-wert) oder null. */
export function dialog(html, knoepfe = [["abbrechen", "Abbrechen"], ["ok", "OK", "primary"]], vorbereiten) {
  const d = $("#dialog");
  d.innerHTML = `<form method="dialog"><div class="inhalt">${html}</div><div class="fuss">${knoepfe.map(([w, t, k]) =>
    `<button class="btn ${k ?? ""}" value="${w}">${esc(t)}</button>`).join("")}</div></form>`;
  vorbereiten?.(d);
  d.showModal();
  return new Promise((res) => d.addEventListener("close", () => res(d.returnValue && d.returnValue !== "abbrechen" ? d.returnValue : null), {once: true}));
}

export async function bestaetigen(text, ok = "OK", gefaehrlich = false) {
  return (await dialog(`<p style="margin:0">${text}</p>`, [["abbrechen", "Abbrechen"], ["ok", ok, gefaehrlich ? "danger" : "primary"]])) === "ok";
}

/** Werte eines Formulars im Dialog lesen, bevor er geschlossen wird. */
export async function formDialog(html, ok = "Speichern", vorbereiten) {
  let werte = null;
  const d = $("#dialog");
  const r = await dialog(html, [["abbrechen", "Abbrechen"], ["ok", ok, "primary"]], (el) => {
    vorbereiten?.(el);
    el.querySelector("form").addEventListener("submit", () => {
      werte = Object.fromEntries($$("[name]", el).map((i) => [i.name, i.type === "checkbox" ? i.checked : i.value]));
    });
  });
  void d;
  return r === "ok" ? werte : null;
}

/** Sortier- und auswählbare Tabelle mit Massenaktions-Leiste.
 *  spalten: [{key, titel, wert: (row) => html, sort: (row) => vergleichswert, klasse}]
 */
export function tabelle(el, {zeilen, spalten, id = (r) => r.id, auswahl = null, onKlick = null, leer = "Nichts gefunden.",
  sortierung = null, aktionen = ""}) {
  const state = el._tabelle ??= {sort: sortierung, auf: true, gewaehlt: new Set()};
  state.gewaehlt = new Set([...state.gewaehlt].filter((x) => zeilen.some((r) => String(id(r)) === x)));
  const render = () => {
    let liste = [...zeilen];
    const sp = spalten.find((s) => s.key === state.sort);
    if (sp) {
      const f = sp.sort ?? ((r) => r[sp.key]);
      liste.sort((a, b) => {
        const x = f(a), y = f(b);
        const c = x == null ? 1 : y == null ? -1 : typeof x === "number" ? x - y : String(x).localeCompare(String(y), "de");
        return state.auf ? c : -c;
      });
    }
    const alle = liste.length && liste.every((r) => state.gewaehlt.has(String(id(r))));
    el.innerHTML = `${auswahl ? `<div class="massen ${state.gewaehlt.size ? "an" : ""}"><b>${state.gewaehlt.size} ausgewählt</b>
        ${aktionen}<button class="btn klein" data-aus>Auswahl aufheben</button></div>` : ""}
      ${liste.length ? `<div class="tabelle"><table><thead><tr>
        ${auswahl ? `<th class="check"><input type="checkbox" data-alle ${alle ? "checked" : ""} aria-label="alle"></th>` : ""}
        ${spalten.map((s) => `<th data-sort="${s.key}" class="${s.klasse ?? ""}">${esc(s.titel)}${state.sort === s.key ? (state.auf ? " ▲" : " ▼") : ""}</th>`).join("")}
      </tr></thead><tbody>${liste.map((r) => {
        const k = String(id(r)), an = state.gewaehlt.has(k);
        return `<tr data-id="${esc(k)}" class="${onKlick ? "klick" : ""} ${an ? "gewaehlt" : ""}">
          ${auswahl ? `<td class="check"><input type="checkbox" data-wahl ${an ? "checked" : ""}></td>` : ""}
          ${spalten.map((s) => `<td class="${s.klasse ?? ""}">${s.wert ? s.wert(r) : esc(r[s.key])}</td>`).join("")}</tr>`;
      }).join("")}</tbody></table></div>` : `<div class="leer">${esc(leer)}</div>`}`;
    $$("th[data-sort]", el).forEach((th) => th.onclick = () => {
      if (state.sort === th.dataset.sort) state.auf = !state.auf; else { state.sort = th.dataset.sort; state.auf = true; }
      render();
    });
    $$("[data-wahl]", el).forEach((c) => c.onclick = (ev) => {
      ev.stopPropagation();
      const k = c.closest("tr").dataset.id;
      c.checked ? state.gewaehlt.add(k) : state.gewaehlt.delete(k); render(); auswahl?.([...state.gewaehlt]);
    });
    const a = $("[data-alle]", el);
    if (a) a.onclick = () => { liste.forEach((r) => a.checked ? state.gewaehlt.add(String(id(r))) : state.gewaehlt.delete(String(id(r))));
      render(); auswahl?.([...state.gewaehlt]); };
    const aus = $("[data-aus]", el);
    if (aus) aus.onclick = () => { state.gewaehlt.clear(); render(); auswahl?.([]); };
    if (onKlick) $$("tbody tr", el).forEach((tr) => tr.onclick = (ev) => {
      if (ev.target.closest("input,button,select,a")) return;
      onKlick(liste.find((r) => String(id(r)) === tr.dataset.id));
    });
  };
  render();
  return {gewaehlt: () => [...state.gewaehlt], leeren: () => { state.gewaehlt.clear(); render(); }, el};
}

/** Ergebnis einer Massenaktion melden. */
export function meldeMassen(e, was) {
  if (e.fehler?.length) toast(`${e.ok.length} ${was}, ${e.fehler.length} nicht: ` +
    e.fehler.slice(0, 3).map((f) => f.fehler).join("; "), "fehler");
  else toast(`${e.ok.length} ${was}`);
}

export function ablaufKlasse(tage) {
  if (tage == null) return "";
  return tage < 0 ? "over" : tage <= 2 ? "soon" : "";
}

export function ablaufText(ablauf, tage) {
  if (!ablauf) return `<span class="muted">unbegrenzt</span>`;
  const rel = tage < 0 ? `seit ${-tage} T.` : tage === 0 ? "heute" : tage === 1 ? "morgen" : `in ${tage} T.`;
  return `<span class="${ablaufKlasse(tage)}">${dmy(ablauf)} <span class="tiny">(${rel})</span></span>`;
}

export const sterne = (r) => `${r.favorit ? `<span class="stern" title="Favorit">★</span>` : ""}${r.bewertung > 0 ? "👍" : r.bewertung < 0 ? "👎" : ""}`;

export function lokal(key, standard) {
  try { const v = localStorage.getItem("ration." + key); return v == null ? standard : JSON.parse(v); } catch { return standard; }
}
export function merken(key, wert) { try { localStorage.setItem("ration." + key, JSON.stringify(wert)); } catch { /* egal */ } }

/** Produkt bekannt, aber nichts im Bestand (Füllstand und immer da zählen als vorhanden). */
export const nichtDa = (p) => !p.bestand && !["immer_da", "fuellstand"].includes(p.tracking_modus);

export function titel(t) { $("#titel").textContent = t; document.title = `${t} · rationPlaner`; }
export function gehe(route) { location.hash = "#/" + route; }

/** Planer-Regler: Schlüssel, Titel, linkes Ende (−1), rechtes Ende (+1). */
export const REGLER = [
  ["fleisch", "Fleisch", "weniger Fleisch, mehr Gemüse", "gern Fleisch"],
  ["preis", "Preis", "günstiger", "darf teurer sein"],
  ["extravagant", "Stil", "Vorrat abbauen", "extravagant"],
  ["neu", "Rezepte", "Lieblinge, oft gekocht", "lieber was Neues"],
  ["gesund", "Gesundheit", "Genuss zuerst", "gesund (Nutri-Score)"],
  ["protein", "Protein", "egal", "viel Protein"],
  ["rohkost", "Rohkost", "lieber gekocht", "viel Rohkost"],
];
// Nährwerte je 100 g (Reihenfolge wie naehrwerte.FELDER)
export const NAEHRFELDER = [["kcal", "kcal"], ["protein", "Protein g"], ["fett", "Fett g"], ["fett_gesaettigt", "davon gesättigt g"],
  ["kohlenhydrate", "Kohlenhydrate g"], ["zucker", "davon Zucker g"], ["ballaststoffe", "Ballaststoffe g"], ["salz", "Salz g"]];
export const nutriBadge = (k, titel = "Nutri-Score (2023), aus den Zutaten gerechnet") =>
  k ? `<span class="nutri n-${esc(k)}" title="${esc(titel)}">${esc(k)}</span>` : "";
