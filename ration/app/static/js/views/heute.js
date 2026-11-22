import {$, $$, api, esc, fmt, guarded, heute, post, put, toast} from "../lib.js";

export default async function render(el) {
  const h = await api("heute");
  if (!h.rezept) {
    const buch = (await api("rezeptbuch")).filter((r) => r.kochbar);
    el.innerHTML = `<div class="card"><h2>Heute ist nichts geplant</h2>
      <p class="muted">Im <a href="#/plan">Wochenplan</a> eintragen oder direkt eins der kochbaren Rezepte nehmen:</p>
      ${buch.length ? `<table>${buch.slice(0, 10).map((r) => `<tr><td><a href="#/rezept/${esc(r.id)}">${esc(r.name)}</a></td>
        <td class="right"><button class="btn klein" data-plan="${esc(r.id)}" data-p="${r.portionen}">Heute kochen</button></td></tr>`).join("")}</table>`
        : `<p class="muted">Gerade ist kein Rezept komplett vorrätig.</p>`}</div>`;
    $$("[data-plan]", el).forEach((b) => b.onclick = () => guarded(async () => {
      await put(`plan/${heute()}`, {rezept_id: b.dataset.plan, portionen: +b.dataset.p});
      render(el);
    }));
    return;
  }
  const r = h.rezept;
  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr))">
    <div class="card"><div class="kopf"><h2>${esc(r.name)}</h2><div class="aktionen">
      <a class="btn klein" href="#/rezept/${esc(r.id)}">Rezept</a></div></div>
      <p class="muted">${r.portionen} Portion(en)${h.status === "gekocht" ? " · ✓ gekocht" : ""}</p>
      <h3>Zutaten</h3><ul>${r.zutaten.map((z) => `<li>${fmt(z.menge)} ${esc(z.einheit)} ${esc(z.name)}</li>`).join("")}</ul></div>
    <div class="card"><h2>Zubereitung</h2>${r.schritte.length ? `<ol class="schritte">${r.schritte.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`
      : `<p class="muted">Keine Schritte hinterlegt.</p>`}</div>
    <div class="card"><h2>Gekocht</h2><div class="row">
      <div><label>Gekocht (Portionen)</label><input id="pg" type="number" min="1" max="12" value="${r.portionen}"></div>
      <div><label>Gegessen (Portionen)</label><input id="pe" type="number" min="0" max="12" value="${Math.min(r.portionen, 1)}"></div></div>
      <p class="muted small">Was übrig bleibt, wird als Rest eingebucht.</p>
      <button class="btn primary voll" id="kochen" ${h.status === "gekocht" ? "disabled" : ""}>Gekocht buchen</button>
      <div id="nach"></div></div></div>`;
  $("#kochen", el).onclick = () => guarded(async () => {
    const e = await post("gekocht", {rezept_id: r.id, portionen_gekocht: +$("#pg", el).value, portionen_gegessen: +$("#pe", el).value});
    const fehl = e.fehlend.map((p) => `${p.name} ${fmt(p.menge)} ${p.einheit}`).join(", ");
    toast(`${e.abgebucht.length} Zutaten abgebucht` + (e.rest ? ` · ${fmt(e.rest.menge)} Portion(en) Rest` : "") + (fehl ? ` · fehlte: ${fehl}` : ""));
    $("#kochen", el).disabled = true;
    nachDemKochen($("#nach", el), e);
  });
}

function nachDemKochen(box, e) {
  box.innerHTML = `<h3>Wie war's?</h3><div class="chips">
      <button class="chip" data-b="1">👍 gern wieder</button><button class="chip" data-b="0">😐 ok</button>
      <button class="chip" data-b="-1">👎 eher nicht</button></div>
    ${e.fuellstand_fragen.length ? `<h3>Wie voll ist noch …?</h3><table>${e.fuellstand_fragen.map((f) => `<tr><td>${esc(f.name)}</td>
      <td class="right nw">${["voll", "knapp", "leer"].map((st) => `<button class="btn klein" data-fs="${esc(f.produkt_id)}" data-st="${st}">${st}</button>`).join(" ")}</td></tr>`).join("")}</table>` : ""}`;
  $$("[data-b]", box).forEach((b) => b.onclick = () => guarded(async () => {
    await put(`kochvorgaenge/${e.kochvorgang_id}/bewertung`, {bewertung: +b.dataset.b});
    $$("[data-b]", box).forEach((x) => x.classList.toggle("on", x === b));
    toast("Danke, fließt in die Planung ein");
  }));
  $$("[data-fs]", box).forEach((b) => b.onclick = () => guarded(async () => {
    await put(`fuellstand/${b.dataset.fs}`, {stand: b.dataset.st});
    b.closest("tr").remove(); toast("Füllstand gespeichert");
  }));
}
