import {$, $$, KATEGORIEN, api, dmy, esc, eur, fmt, guarded, post, toast, wt} from "../lib.js";

// Einkauf für den geplanten Zeitraum: nach Kategorie gruppiert, mit Kosten, Verderb und Überhang
export default async function render(el, {params}) {
  const tage = Math.min(Math.max(+(params.tage ?? 7) || 7, 1), 14);
  const [sim, katalog] = await Promise.all([api(`plan/simulation?tage=${tage}`), api("katalog/produkte")]);
  const kat = Object.fromEntries(katalog.map((p) => [p.id, p.kategorie || "Sonstiges"]));
  const gruppen = {};
  sim.einkauf.forEach((e) => (gruppen[kat[e.produkt_id] ?? "Sonstiges"] ??= []).push(e));
  const rang = (k) => { const i = KATEGORIEN.indexOf(k); return i < 0 ? 99 : i; };
  const reihen = Object.keys(gruppen).sort((a, b) => rang(a) - rang(b) || a.localeCompare(b, "de"));

  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr))">
    <div class="card"><div class="kopf"><h2>Einkaufsliste</h2><div class="aktionen">
        <select id="tage">${[3, 5, 7, 10, 14].map((t) => `<option value="${t}" ${t === tage ? "selected" : ""}>${t} Tage</option>`).join("")}</select></div></div>
      <p class="muted small">${sim.tage.length} geplante Gerichte${sim.kosten != null ? ` · ca. <b>${eur(sim.kosten)}</b>` : ""}${sim.kosten_unbekannt ? ` (${sim.kosten_unbekannt} ohne Preis)` : ""}.
        Gerundet auf ganze Packungen, mit Toleranz.</p>
      ${sim.einkauf.length ? reihen.map((k) => `<h3>${esc(k)}</h3><table>${gruppen[k].map((e, i) => `<tr>
          <td class="check"><input type="checkbox" data-hak="${esc(k)}-${i}"></td><td>${esc(e.text)}</td>
          <td class="nw right muted">${e.preis != null ? eur(e.preis) : ""}</td></tr>`).join("")}</table>`).join("")
        : `<div class="leer">Nichts zu kaufen. 🎉</div>`}
      ${sim.einkauf.length ? `<div class="row" style="margin-top:12px"><button class="btn primary" id="ha">→ HA-Einkaufsliste</button>
        <button class="btn" id="kopie">📋 Kopieren</button></div>
        <p class="muted tiny">Die Haken hier sind nur zum Abhaken im Laden, sie buchen nichts ein.</p>` : ""}</div>
    <div class="card"><h2>Geplant</h2>${sim.tage.length ? `<table>${sim.tage.map((t) => `<tr><td class="nw muted">${wt(t.datum)}</td>
        <td><a href="#/rezept/${esc(t.rezept_id)}">${esc(t.name)}</a> <span class="muted tiny">${t.portionen} P.</span>
        ${t.zukauf.length ? `<div class="muted tiny">kauft: ${t.zukauf.map(esc).join(", ")}</div>` : ""}
        ${t.hinweise.map((h) => `<div class="soon tiny">${esc(h)}</div>`).join("")}</td></tr>`).join("")}</table>`
        : `<p class="muted">Nichts geplant. <a href="#/plan">Zum Wochenplan</a></p>`}
      ${sim.verderb.length ? `<h3>Verdirbt trotzdem</h3><ul>${sim.verderb.map((v) => `<li class="over">${esc(v.name)} ${fmt(v.menge)} ${esc(v.einheit)} am ${dmy(v.ablauf)}</li>`).join("")}</ul>` : ""}
      ${sim.ueberhang.length ? `<h3>Bleibt übrig</h3><p class="muted small">${sim.ueberhang.map((v) => `${esc(v.name)} ${fmt(v.menge)} ${esc(v.einheit)}`).join(", ")}</p>` : ""}</div></div>`;

  $("#tage", el).onchange = (e) => { location.hash = `#/einkauf?tage=${e.target.value}`; };
  $$("[data-hak]", el).forEach((c) => c.onchange = () => { c.closest("tr").style.opacity = c.checked ? .45 : 1; });
  if (!sim.einkauf.length) return;
  $("#ha", el).onclick = () => guarded(async () => {
    const e = await post("einkaufsliste", {tage});
    toast(e.hinzugefuegt.length ? `${e.hinzugefuegt.length} Einträge hinzugefügt` + (e.schon_auf_liste.length ? `, ${e.schon_auf_liste.length} standen schon drauf` : "")
      : "Alles steht schon auf der Liste");
  });
  $("#kopie", el).onclick = () => guarded(async () => {
    await navigator.clipboard.writeText(reihen.map((k) => `${k}:\n` + gruppen[k].map((e) => `- ${e.text}`).join("\n")).join("\n\n"));
    toast("In die Zwischenablage kopiert");
  });
}
