import {$, api, esc, fmt, formDialog, guarded, post, put, tabelle, toast} from "../lib.js";

// Packungsgrößen aller Produkte auf einen Blick, schnell setzen per Dialog
export default async function render(el) {
  const liste = await api("packungen");
  let nurOhne = false, q = "";
  el.innerHTML = `<div class="card"><div class="werkzeug"><input type="search" id="q" placeholder="Produkt suchen …">
      <button class="chip" id="ohne">ohne Standard-Packung <span class="muted">${liste.filter((p) => !p.standard).length}</span></button></div>
    <p class="muted tiny">Standard = meistgekaufte Größe (in Grocy). Weitere Größen nutzt die Einkaufsliste, um mit wenig Überhang einzukaufen.</p>
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
      {key: "weitere", titel: "Weitere", sort: (p) => p.weitere.length,
        wert: (p) => p.weitere.map((w) => `<span class="tag">${fmt(w.menge)} ${esc(p.einheit)}${w.quelle !== "manuell" ? ` · ${esc(w.quelle)}` : ""}</span>`).join(" ")},
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
    <label>Weitere Größe hinzufügen (optional)</label><div class="row"><input name="we" placeholder="Einheit">
      <input name="wm" type="number" min="0" step="any" placeholder="Menge in ${esc(p.einheit)}"></div>
    <p class="muted tiny">Weitere Größen entfernen: auf der <a href="#/produkt/${esc(p.produkt_id)}">Produktseite</a>.</p>`);
  if (!w) return;
  if (w.se && w.sm && (w.se !== p.standard?.einheit || +w.sm !== p.standard?.menge)) await put(`produkte/${p.produkt_id}/packung`, {einheit: w.se, menge: +w.sm});
  if (w.we && w.wm) await post(`produkte/${p.produkt_id}/packungen`, {einheit: w.we, menge: +w.wm});
  toast("Gespeichert"); fertig();
}
