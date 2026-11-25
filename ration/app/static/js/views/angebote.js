import {$, $$, api, dialog, dmy, esc, eur, guarded, lokal, merken, meldeMassen, post, put, toast} from "../lib.js";

// Angebote aus Prospekten: Abruf-Status je Händler, Zuordnung zu Produkten prüfen.
// Die KI schlägt nur vor; Preise, Alias und Angebots-Packung entstehen erst mit ✓.
const FILTER = [
  ["pruefen", "Zu prüfen", (a) => a.zuordnung === "ki"],
  ["zugeordnet", "Zugeordnet", (a) => ["alias", "nutzer"].includes(a.zuordnung)],
  ["offen", "Ohne Treffer", (a) => !a.zuordnung || a.zuordnung === "ki_keins"],
  ["ignoriert", "Ignoriert", (a) => a.zuordnung === "ignoriert"],
  ["alle", "Alle", () => true],
];
// Abrufzeiten speichert die App in Ortszeit (Zeitplan „einmal am Tag ab 6 Uhr“)
const wann = (s) => new Date(s).toLocaleString("de-DE", {weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
const wtag = (d) => new Date(d + "T12:00:00").toLocaleDateString("de-DE", {weekday: "short"});
const gueltig = (a) => `${wtag(a.gueltig_von)}–${wtag(a.gueltig_bis)} ${dmy(a.gueltig_von).slice(0, 6)}`;

async function marktWaehlen(h) {
  let gewaehlt = null;
  const r = await dialog(`<h2 style="margin-top:0">${esc(h.name)}-Markt wählen</h2>
    <p class="muted small">Die Preise gelten je Verkaufsregion, deshalb braucht es deinen Markt.</p>
    <div class="row"><input id="mq" placeholder="PLZ oder Ort" autocomplete="off"><button class="btn" type="button" id="ms">Suchen</button></div>
    <div id="mt" style="margin-top:8px"></div>`, [["abbrechen", "Abbrechen"], ["ok", "Übernehmen", "primary"]], (d) => {
    const suchen = () => guarded(async () => {
      const treffer = await api(`angebote/maerkte/${h.haendler}?q=${encodeURIComponent($("#mq", d).value)}`);
      $("#mt", d).innerHTML = treffer.length ? treffer.map((m) => `<label class="inline" style="display:flex;margin:4px 0">
        <input type="radio" name="markt" value="${esc(m.id)}"> ${esc(m.name)} <span class="muted small">${esc(m.adresse)}</span></label>`).join("")
        : `<p class="muted">Kein Markt gefunden.</p>`;
      $$("[name=markt]", d).forEach((x) => x.onchange = () => { gewaehlt = x.value; });
    });
    $("#ms", d).onclick = suchen;
    $("#mq", d).onkeydown = (ev) => { if (ev.key === "Enter") { ev.preventDefault(); suchen(); } };
  });
  if (r !== "ok" || !gewaehlt) return false;
  await put("einstellungen/angebote", {[`${h.haendler}_markt`]: gewaehlt});
  toast("Markt gespeichert");
  return true;
}

async function warten(el, vorher) {
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    const st = await api("angebote/status");
    if (st.some((h) => h.aktiv && h.letzter_abruf !== vorher[h.haendler])) return render(el);
  }
  toast("Abruf dauert ungewöhnlich lange", "fehler");
}

export default async function render(el) {
  const [status, liste, produkte] = await Promise.all([api("angebote/status"), api("angebote"), api("produkte")]);
  const filter = lokal("angebote.filter", "pruefen");
  const such = lokal("angebote.suche", "").toLowerCase();
  const passt = FILTER.find((f) => f[0] === filter)?.[2] ?? (() => true);
  const zeilen = liste.filter((a) => passt(a) && (!such || `${a.titel} ${a.produkt ?? ""}`.toLowerCase().includes(such)));
  const kiTreffer = zeilen.filter((a) => a.zuordnung === "ki");
  const unbearbeitet = liste.filter((a) => !a.zuordnung).length;
  const nachName = Object.fromEntries(produkte.map((p) => [p.name.toLowerCase(), p.id]));

  el.innerHTML = `<div class="card"><div class="kopf"><h2>Prospekte</h2></div>
    ${status.map((h) => `<div class="ang-status"><div><b>${esc(h.name)}</b>${h.aktiv ? ` <span class="muted tiny">Markt ${esc(h.markt)}</span>` : ""}
      <div class="small">${!h.aktiv ? `<span class="muted">nicht eingerichtet</span>`
        : h.kaputt ? `<span class="tag bad">kaputt</span> ${esc(h.fehler)}`
        : h.ok ? `<span class="tag good">ok</span> <span class="muted">${h.anzahl} Lebensmittel · KW ${esc(h.wochen.map((w) => w.split("-")[1]).join(", "))}</span>`
        : `<span class="muted">noch nie abgerufen</span>`}</div>
      ${h.letzter_abruf ? `<div class="muted tiny">zuletzt ${esc(wann(h.letzter_abruf))}${h.kaputt && h.letzter_erfolg ? ` · zuletzt ok ${esc(wann(h.letzter_erfolg))}` : ""}</div>` : ""}</div>
      <div class="aktionen">${h.aktiv ? `<button class="btn klein" data-abruf="${h.haendler}">Jetzt abrufen</button>` : ""}
        <button class="btn klein ${h.aktiv ? "" : "primary"}" data-markt="${h.haendler}">${h.aktiv ? "Markt ändern" : "Markt wählen"}</button></div></div>`).join("")}
    <p class="muted tiny">Abruf automatisch einmal am Tag, bis die aktuelle Woche da ist, ab Mittwoch auch die nächste. Kaputte Abrufe melden sich als HA-Hinweis.</p></div>
  <div class="card"><div class="kopf"><h2>Angebote (${liste.length})</h2><div class="aktionen">
      ${unbearbeitet ? `<button class="btn klein" id="ki">${unbearbeitet} der KI geben</button>` : ""}
      ${liste.some((a) => ["ki", "ki_keins"].includes(a.zuordnung)) ? `<button class="btn klein" id="kineu" title="Unbestätigte KI-Ergebnisse neu zuordnen lassen; Bestätigtes und Ignoriertes bleibt">KI neu zuordnen</button>` : ""}
      ${kiTreffer.length ? `<button class="btn klein primary" id="alle">${kiTreffer.length} KI-Treffer bestätigen</button>` : ""}</div></div>
    <div class="chips">${FILTER.map(([k, t, f]) => `<button class="chip ${k === filter ? "on" : ""}" data-f="${k}">${t} <span class="muted">${liste.filter(f).length}</span></button>`).join("")}</div>
    <input id="such" placeholder="Suchen …" value="${esc(such)}" style="margin:8px 0">
    <datalist id="angprod">${produkte.map((p) => `<option value="${esc(p.name)}">`).join("")}</datalist>
    ${zeilen.length ? zeilen.map((a) => `<div class="ang" data-id="${a.id}">
      ${a.bild ? `<img src="${esc(a.bild)}" alt="" loading="lazy">` : `<span></span>`}
      <div class="text">${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.titel)}</a>` : esc(a.titel)}
        <div class="muted tiny">${esc(a.markt)} · ${esc(a.menge_text ?? "")} · ${esc(gueltig(a))}${a.grundpreis ? " · " + esc(a.grundpreis) : ""}</div>
        ${a.ki_info ? `<div class="muted tiny">KI: ${esc(a.ki_info)}</div>` : ""}</div>
      <div class="preis"><b>${a.preis != null ? eur(a.preis) : esc(a.preis_text ?? "–")}</b>
        ${a.streichpreis ? `<div class="muted tiny"><s>${eur(a.streichpreis)}</s></div>` : ""}
        ${a.app_preis ? `<div class="tiny" title="mit Händler-App">App ${eur(a.app_preis)}</div>` : ""}</div>
      <div class="zuo">${a.zuordnung === "ki" ? `<span>✨ ${esc(a.produkt)}</span><button class="btn klein primary" data-ok title="bestätigen">✓</button>`
        : ["alias", "nutzer"].includes(a.zuordnung) ? `<span class="ok">✓ ${esc(a.produkt)}</span>`
        : a.zuordnung === "ignoriert" ? `<span class="muted">ignoriert</span>` : ""}
        <input list="angprod" data-wahl placeholder="${a.produkt ? "anderes Produkt …" : "Produkt …"}">
        ${a.zuordnung !== "ignoriert" ? `<button class="btn klein" data-weg title="passt zu keinem Produkt">✗</button>` : ""}</div></div>`).join("")
      : `<div class="leer">${liste.length ? "Nichts in diesem Filter." : "Noch keine Angebote. Markt wählen und abrufen."}</div>`}
    <p class="muted tiny">✓ merkt den Titel für die nächsten Wochen und schreibt Angebots- und Streichpreis in die Preistabelle. ✗ ignoriert den Titel künftig.</p></div>`;

  const neu = () => guarded(() => render(el));
  $$("[data-f]", el).forEach((b) => b.onclick = () => { merken("angebote.filter", b.dataset.f); neu(); });
  const s = $("#such", el);
  s.onchange = () => { merken("angebote.suche", s.value.trim()); neu(); };
  $$("[data-markt]", el).forEach((b) => b.onclick = () => guarded(async () => {
    if (await marktWaehlen(status.find((h) => h.haendler === b.dataset.markt))) neu();
  }));
  $$("[data-abruf]", el).forEach((b) => b.onclick = () => guarded(async () => {
    const vorher = Object.fromEntries(status.map((h) => [h.haendler, h.letzter_abruf]));
    await post("angebote/abrufen", {haendler: b.dataset.abruf});
    b.disabled = true; b.textContent = "Läuft …";
    toast("Abruf gestartet, KI ordnet danach zu");
    await warten(el, vorher);
  }));
  const ki = $("#ki", el);
  if (ki) ki.onclick = () => guarded(async () => {
    ki.disabled = true; ki.textContent = "KI arbeitet …";
    const r = await post("angebote/ki");
    toast(r.fehler ? `KI: ${r.fehler}` : `${r.treffer} von ${r.offen} zugeordnet`, r.fehler ? "fehler" : "");
    neu();
  });
  const kineu = $("#kineu", el);
  if (kineu) kineu.onclick = () => guarded(async () => {
    kineu.disabled = true; kineu.textContent = "KI arbeitet …";
    const r = await post("angebote/ki?neu=true");
    toast(r.fehler ? `KI: ${r.fehler}` : `${r.treffer} von ${r.offen} zugeordnet`, r.fehler ? "fehler" : "");
    neu();
  });
  const alle = $("#alle", el);
  if (alle) alle.onclick = () => guarded(async () => {
    meldeMassen(await post("angebote/bestaetigen", {ids: kiTreffer.map((a) => a.id)}), "bestätigt"); neu();
  });
  const zuordnen = (tr, pid) => guarded(async () => {
    const r = await post(`angebote/${tr.dataset.id}/zuordnen`, {produkt_id: pid});
    toast(pid ? `${r.produkt} gemerkt${r.weitere ? `, ${r.weitere} weitere` : ""}` : "Ignoriert"); neu();
  });
  $$("[data-ok]", el).forEach((b) => b.onclick = () => guarded(async () => {
    const tr = b.closest("[data-id]");
    meldeMassen(await post("angebote/bestaetigen", {ids: [+tr.dataset.id]}), "bestätigt"); neu();
  }));
  $$("[data-weg]", el).forEach((b) => b.onclick = () => zuordnen(b.closest("[data-id]"), null));
  $$("[data-wahl]", el).forEach((i) => i.onchange = () => {
    const pid = nachName[i.value.trim().toLowerCase()];
    if (pid) zuordnen(i.closest("[data-id]"), pid); else if (i.value.trim()) toast("Produkt nicht im Katalog", "fehler");
  });
}
