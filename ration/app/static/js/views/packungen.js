import {$, $$, api, dmy, esc, fmt, formDialog, guarded, patch, post, put, tabelle, toast} from "../lib.js";

// Packungsgrößen aller Produkte auf einen Blick, schnell setzen per Dialog
export default async function render(el) {
  const liste = await api("packungen");
  let nurOhne = false, q = "";
  el.innerHTML = `<div class="card"><div class="werkzeug"><input type="search" id="q" placeholder="Produkt suchen …">
      <button class="chip" id="ohne">ohne Standard-Packung <span class="muted">${liste.filter((p) => !p.standard).length}</span></button></div>
    <p class="muted tiny">Standard = meistgekaufte Größe (in Grocy). Weitere Größen nutzt die Einkaufsliste, um mit wenig Überhang einzukaufen,
      bei Gleichstand die Größe, die du laut Bons wirklich kaufst. Neue Größen vom Bon sind erst Vorschläge
      (<a href="#/ki-vorschlaege">prüfen</a>). Gängige Größen und Standard lässt du unter <a href="#/ki-vorschlaege">KI-Kategorisierung</a> analysieren.</p>
    <div id="liste"></div></div>`;
  const tab = $("#liste", el);
  const zeichnen = () => tabelle(tab, {
    zeilen: liste.filter((p) => (!nurOhne || !p.standard) && (!q || p.name.toLowerCase().includes(q))),
    id: (p) => p.produkt_id, sortierung: "name", leer: "Keine Produkte.",
    onKlick: (p) => guarded(() => bearbeiten(p, () => render(el))),
    spalten: [
      {key: "name", titel: "Produkt", wert: (p) => `<a href="#/produkt/${esc(p.produkt_id)}">${esc(p.name)}</a>`},
      {key: "standard", titel: "Standard", sort: (p) => p.standard?.menge ?? -1,
        wert: (p) => p.standard ? `${esc(p.standard.einheit)} ${fmt(p.standard.menge)} ${esc(p.einheit)}` : `<span class="muted">–</span>`},
      {key: "gekauft", titel: "Gekauft", sort: (p) => p.weitere.reduce((s, w) => s + w.haeufigkeit, 0),
        wert: (p) => p.weitere.filter((w) => w.haeufigkeit).sort((a, b) => b.haeufigkeit - a.haeufigkeit)
          .map((w) => `<span class="nw">${fmt(w.menge)} ${esc(p.einheit)} × ${w.haeufigkeit}</span>`).join(", ")
          + (p.weitere.some((w) => w.zuletzt) ? `<div class="muted tiny">zuletzt ${dmy(p.weitere.map((w) => w.zuletzt).filter(Boolean).sort().at(-1))}</div>` : "")},
      {key: "weitere", titel: "Weitere Größen", sort: (p) => p.weitere.length,
        wert: (p) => p.weitere.filter((w) => !p.standard || Math.abs(w.menge - p.standard.menge) > 1e-6).map((w) =>
          `<span class="tag ${w.bestaetigt ? "" : "warn"}" title="${esc([w.marke, w.haendler, w.ean].filter(Boolean).join(" · "))}">${esc(w.einheit)} ${fmt(w.menge)} ${esc(p.einheit)}${
            w.marke ? ` · ${esc(w.marke)}` : ""}${w.quelle === "angebot" ? " · Angebot" : ""}${w.bestaetigt ? "" : " · Vorschlag"}</span>`).join(" ")},
    ],
  });
  $("#q", el).oninput = (e) => { q = e.target.value.toLowerCase(); zeichnen(); };
  $("#ohne", el).onclick = (e) => { nurOhne = !nurOhne; e.currentTarget.classList.toggle("on"); zeichnen(); };
  zeichnen();
}

async function bearbeiten(p, fertig) {
  const w = await formDialog(`<h2>${esc(p.name)}</h2>
    <label>Standard-Packung</label><div class="row"><input name="se" placeholder="Einheit, z. B. Dose" value="${esc(p.standard?.einheit ?? "")}">
      <input name="sm" type="number" min="0" step="any" placeholder="Menge in ${esc(p.einheit)}" value="${p.standard?.menge ?? ""}"></div>
    ${p.weitere.length ? `<label>Varianten</label><table>${p.weitere.map((w) => `<tr data-var="${w.id}"><td class="nw">${esc(w.einheit)} ${fmt(w.menge)}
        ${w.bestaetigt ? "" : `<span class="tag warn">Vorschlag</span>`}<div class="muted tiny">${w.haeufigkeit}× gekauft${w.zuletzt ? `, zuletzt ${dmy(w.zuletzt)}` : ""}</div></td>
      <td><input data-f="marke" placeholder="Marke" value="${esc(w.marke ?? "")}"></td><td><input data-f="haendler" placeholder="Händler" value="${esc(w.haendler ?? "")}"></td>
      <td><input data-f="ean" placeholder="EAN" inputmode="numeric" value="${esc(w.ean ?? "")}"></td></tr>`).join("")}</table>` : ""}
    <label>Weitere Größe hinzufügen (optional)</label><div class="row"><input name="we" placeholder="Einheit">
      <input name="wm" type="number" min="0" step="any" placeholder="Menge in ${esc(p.einheit)}"></div>
    <p class="muted tiny">Weitere Größen entfernen: auf der <a href="#/produkt/${esc(p.produkt_id)}">Produktseite</a>.</p>`);
  if (!w) return;
  for (const v of p.weitere) {
    const tr = $(`[data-var="${v.id}"]`, document.querySelector("#dialog"));
    if (!tr) continue;
    const neu = Object.fromEntries($$("[data-f]", tr).map((i) => [i.dataset.f, i.value.trim()]));
    if (["marke", "haendler", "ean"].some((k) => (v[k] ?? "") !== neu[k])) await patch(`packungen/${v.id}`, neu);
  }
  if (w.se && w.sm && (w.se !== p.standard?.einheit || +w.sm !== p.standard?.menge)) await put(`produkte/${p.produkt_id}/packung`, {einheit: w.se, menge: +w.sm});
  if (w.we && w.wm) await post(`produkte/${p.produkt_id}/packungen`, {einheit: w.we, menge: +w.wm});
  toast("Gespeichert"); fertig();
}
