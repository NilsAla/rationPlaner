import {$, $$, api, esc, fmt, guarded, post, gehe, importMerken, zeit} from "../lib.js";

// Rezept von der KI erfinden lassen: Wunsch → Entwurf → Änderungswünsche → Import-Vorschau.
// Nach Grocy geht erst etwas über die normale Import-Vorschau (Zuordnung prüfen, übernehmen).
// ?id=… öffnet einen Entwurf.
const AUFWAND = [["schnell", "schnell (≤ 25 min)"], ["normal", "normal"], ["aufwaendig", "aufwändig"]];
const BEISPIELE = ["statt Sahne Crème fraîche", "schärfer", "vegetarisch", "weniger Schritte", "für 4 Portionen"];

// Abgleich mit dem Katalog (nur Code, die Import-Vorschau entscheidet endgültig):
// grün = aus unserer DB, gelb = ähnliches Produkt, blau = wird neu angelegt, grau = weggelassen
const ABGLEICH = {db: ["gruen", "aus unserer DB"], vorschlag: ["gelb", "ähnliches Produkt"], neu: ["blau", "neu"], weglassen: ["grau", "weggelassen"]};
function abgleichHtml(zutaten, abgleich) {
  const n = {};
  abgleich.forEach((a) => { n[a.status] = (n[a.status] ?? 0) + 1; });
  const legende = Object.entries(ABGLEICH).filter(([k]) => n[k])
    .map(([k, [f, t]]) => `<span><i class="imp-punkt ${f}"></i>${n[k]} ${t}</span>`).join("");
  const ziel = (a) => {
    if (!a) return "";
    if (a.status === "neu") return `<span class="tag">+ neu: ${esc(a.neu_name)}</span>`;
    if (a.status === "weglassen") return `<span class="muted tiny">${esc(a.hinweis || "weggelassen")}</span>`;
    const vorrat = a.immer_da ? "immer da" : a.bestand > 0 ? `im Vorrat ${fmt(a.bestand)} ${esc(a.einheit)}` : "nicht im Vorrat";
    return `→ <b>${esc(a.produkt)}</b>${a.status === "vorschlag" ? ` <span class="tag">ähnlich</span>` : ""} <span class="muted tiny">${vorrat}</span>`;
  };
  return `<div class="imp-legende small">${legende}</div>
    <ul class="gen-zutaten">${zutaten.map((z, i) => { const a = abgleich[i];
      return `<li><i class="imp-punkt ${ABGLEICH[a?.status]?.[0] ?? "grau"}"></i>${esc(z)} ${ziel(a)}</li>`; }).join("")}</ul>
    <p class="muted tiny">Vorab-Abgleich mit dem Katalog. In der Import-Vorschau prüft zusätzlich die KI, dort kannst du jede Zeile ändern.</p>`;
}

export default async function render(el, {params}) {
  let aktiv = true;
  const info = await api("generieren");
  el.innerHTML = `<div class="card"><h2>Rezept generieren</h2>
    ${info.ki_fehlt ? `<p class="over small">KI für „Rezept generieren“ nicht eingerichtet: ${esc(info.ki_fehlt)} (Einstellungen → KI)</p>` : ""}
    <label>Was möchtest du kochen?</label>
    <textarea id="gprompt" rows="3" placeholder="z. B. cremige Pasta mit Pilzen und Spinat, ohne Fleisch"></textarea>
    <div class="row">
      <div><label>Portionen</label><input id="gpor" type="number" min="1" max="12" value="2"></div>
      <div style="flex:2"><label>Aufwand</label><select id="gauf">${AUFWAND.map(([k, t]) =>
        `<option value="${k}" ${k === "normal" ? "selected" : ""}>${t}</option>`).join("")}</select></div></div>
    <label class="inline"><input id="gvorrat" type="checkbox"> Vorrat nutzen (bald Ablaufendes bevorzugen)</label>
    <button class="btn primary voll" id="gstart" ${info.ki_fehlt ? "disabled" : ""}>Rezept erfinden</button>
    <p class="muted tiny">Die KI schreibt Zutaten und Kochanleitung. Danach kannst du Änderungen wünschen;
      erst „Übernehmen“ öffnet die Import-Vorschau, dort prüfst du die Produkte wie beim Link-Import.</p></div>
    <div id="gbox"></div>
    ${info.entwuerfe.length ? `<div class="card"><h2>Letzte Entwürfe</h2><div class="chips">${info.entwuerfe.map((e) =>
      `<a class="chip" href="#/generieren?id=${e.id}" title="${esc(e.prompt)}">${esc(e.name)}${e.versionen > 1 ? ` · v${e.versionen}` : ""}${e.import_id ? " ✓" : ""}</a>`).join("")}</div></div>` : ""}`;
  const box = $("#gbox", el);

  // Wartebalken: der Aufruf läuft synchron, Dauer geschätzt aus den letzten echten Läufen
  async function mitBalken(text, fn) {
    const start = Date.now(), dauer = Math.max(5, info.erwartet_s || 30) * 1000;
    const knoepfe = $$("button", el);
    knoepfe.forEach((b) => { b.dataset.war = b.disabled; b.disabled = true; });
    const balken = document.createElement("div");
    balken.className = "card";
    box.prepend(balken);
    const zeichnen = () => {
      const t = Date.now() - start, pct = Math.min(95, 95 * t / dauer), rest = Math.round((dauer - t) / 1000);
      balken.innerHTML = `<h2>${esc(text)}</h2><div class="fortschritt"><div style="width:${pct}%"></div></div>
        <p class="muted small">${rest > 0 ? `noch ca. ${rest} s` : "gleich fertig …"}</p>`;
    };
    zeichnen();
    const timer = setInterval(zeichnen, 1000);
    try { return await fn(); } finally {
      clearInterval(timer); balken.remove();
      knoepfe.forEach((b) => { b.disabled = b.dataset.war === "true"; });
    }
  }

  function zeigen(e, version = e.versionen.length - 1) {
    if (!aktiv) return;
    const v = e.versionen[version], r = v.rezept;
    box.innerHTML = `<div class="card">
      <div class="kopf"><h2>${esc(r.name)}</h2><span class="tag">${r.portionen} Portion${r.portionen === 1 ? "" : "en"}</span></div>
      <p class="muted small">Wunsch: ${esc(e.prompt)}</p>
      ${e.versionen.length > 1 ? `<div class="chips" style="margin-bottom:10px">${e.versionen.map((x, i) =>
        `<button class="chip ${i === version ? "on" : ""}" data-v="${i}" title="${esc(x.wunsch ?? "erster Entwurf")}">v${i + 1}${x.wunsch ? `: ${esc(x.wunsch.slice(0, 30))}` : ""}</button>`).join("")}</div>` : ""}
      ${r.aenderung && v.wunsch ? `<p class="small">✏️ ${esc(r.aenderung)}</p>` : ""}
      <h3>Zutaten</h3>${abgleichHtml(r.zutaten, v.abgleich ?? [])}
      <h3>Anleitung</h3><ol>${r.schritte.map((s) => `<li><b>${esc(s.titel)}</b>${s.timer_s ? ` <span class="tag">⏱ ${Math.round(s.timer_s / 60)} min</span>` : ""}${s.parallel ? ` <span class="tag">nebenher</span>` : ""}
        <div class="small">${esc(s.text)}</div></li>`).join("")}</ol>
      ${r.hinweise?.length ? `<ul class="small muted">${r.hinweise.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}
      <p class="muted tiny">v${version + 1} vom ${esc(zeit(v.angelegt))}</p></div>
    <div class="card"><h2>Ändern</h2>
      <textarea id="gwunsch" rows="2" placeholder="z. B. statt Sahne Crème fraîche"></textarea>
      <div class="chips" style="margin:6px 0 10px">${BEISPIELE.map((b) => `<button class="chip" data-bsp="${esc(b)}">${esc(b)}</button>`).join("")}</div>
      <button class="btn voll" id="gaendern">Änderung anwenden${version < e.versionen.length - 1 ? ` (auf v${version + 1})` : ""}</button></div>
    <div class="card">${e.import_id ? `<p class="muted small">Schon in den Import gegeben: <a href="#/import?id=${e.import_id}">Vorschau öffnen</a>.</p>` : ""}
      <button class="btn primary voll" id="gok">Übernehmen: v${version + 1} prüfen und anlegen</button></div>`;
    $$("[data-v]", box).forEach((b) => b.onclick = () => zeigen(e, +b.dataset.v));
    $$("[data-bsp]", box).forEach((b) => b.onclick = () => {
      const w = $("#gwunsch", box);
      w.value = w.value.trim() ? `${w.value.trim()}, ${b.dataset.bsp}` : b.dataset.bsp;
    });
    $("#gaendern", box).onclick = () => guarded(async () => {
      const wunsch = $("#gwunsch", box).value.trim();
      if (!wunsch) throw new Error("Was soll sich ändern?");
      const neu = await mitBalken("Rezept wird überarbeitet", () =>
        post(`generieren/${e.id}/ueberarbeiten`, {wunsch, version}));
      zeigen(neu);
    });
    $("#gok", box).onclick = () => guarded(async () => {
      const s = await post(`generieren/${e.id}/import`, {version});
      importMerken(s.import_id);
      gehe(`import?id=${s.import_id}`);
    });
  }

  $("#gstart", el).onclick = () => guarded(async () => {
    const prompt = $("#gprompt", el).value.trim();
    if (prompt.length < 3) throw new Error("Bitte kurz beschreiben, was du kochen möchtest");
    const e = await mitBalken("Rezept wird erfunden", () => post("generieren", {
      prompt, portionen: +$("#gpor", el).value || 2, aufwand: $("#gauf", el).value, vorrat: $("#gvorrat", el).checked,
    }));
    history.replaceState(null, "", `#/generieren?id=${e.id}`);
    zeigen(e);
  });
  if (params.id) guarded(async () => zeigen(await api(`generieren/${+params.id}`)));
  return () => { aktiv = false; };
}
