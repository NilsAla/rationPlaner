import {$$, api, dmy, esc, fmt, guarded, heute, post, put, toast} from "../lib.js";

// Reste aus Gekocht und Produkte im Modus Füllstand
export default async function render(el) {
  const [reste, fuell] = await Promise.all([api("reste"), api("fuellstand")]);
  const h = heute();
  const tage = (d) => d ? Math.round((new Date(d + "T12:00:00") - new Date(h + "T12:00:00")) / 864e5) : null;
  const klasse = (d) => { const t = tage(d); return t == null ? "" : t < 0 ? "over" : t <= 1 ? "soon" : ""; };
  const reihenfolge = {leer: 0, knapp: 1, voll: 2};
  el.innerHTML = `<div class="grid">
    <div class="card"><h2>Reste</h2>${reste.length ? `<table>${reste.map((r) => `<tr>
        <td>${esc(r.name.replace(/^Rest: /, ""))}</td><td class="nw">${fmt(r.portionen)} P.</td>
        <td class="nw ${klasse(r.ablauf)}">${r.ablauf ? dmy(r.ablauf) : "–"}</td>
        <td class="nw right"><button class="btn klein" data-gegessen="${esc(r.produkt_id)}">1 gegessen</button>
          <button class="btn klein danger" data-weg="${esc(r.produkt_id)}" data-m="${r.portionen}">weg</button></td></tr>`).join("")}</table>`
      : `<p class="muted">Keine Reste. Beim <a href="#/heute">Kochen</a> wird übrig Gebliebenes automatisch eingebucht.</p>`}</div>
    <div class="card"><h2>Füllstand</h2>${fuell.length ? `<table>${[...fuell].sort((a, b) => reihenfolge[a.stand] - reihenfolge[b.stand] || a.name.localeCompare(b.name, "de"))
        .map((f) => `<tr><td><a href="#/produkt/${esc(f.produkt_id)}">${esc(f.name)}</a></td>
        <td class="nw right">${["voll", "knapp", "leer"].map((st) => `<button class="chip ${f.stand === st ? "on" : ""}" data-fs="${esc(f.produkt_id)}" data-st="${st}">${st}</button>`).join(" ")}</td></tr>`).join("")}</table>
      <p class="muted tiny">knapp und leer kommen beim nächsten Einkauf auf die Liste.</p>`
      : `<p class="muted">Keine Produkte im Modus Füllstand. Setzen lässt sich das unter <a href="#/produkte">Produkte</a>.</p>`}</div></div>`;
  $$("[data-gegessen]", el).forEach((b) => b.onclick = () => guarded(async () => {
    await post("bestand/abbuchen", {produkt_id: b.dataset.gegessen, menge: 1}); toast("Guten Appetit"); render(el);
  }));
  $$("[data-weg]", el).forEach((b) => b.onclick = () => guarded(async () => {
    await post("bestand/abbuchen", {produkt_id: b.dataset.weg, menge: +b.dataset.m, verdorben: true}); toast("Als verdorben ausgebucht"); render(el);
  }));
  $$("[data-fs]", el).forEach((b) => b.onclick = () => guarded(async () => {
    await put(`fuellstand/${b.dataset.fs}`, {stand: b.dataset.st}); render(el);
  }));
}
