import {$$, api, dmy, esc, eur, guarded, lokal, merken, patch, post, toast} from "../lib.js";

// Ausgaben aus Kassenbons: Monat, Kategorien, geplant vs. spontan, Verlauf. Nichts davon im Inventar.
const monatName = (m) => new Date(m + "-15").toLocaleDateString("de-DE", {month: "long", year: "numeric"});
const plusMonat = (m, n) => { const d = new Date(m + "-15"); d.setMonth(d.getMonth() + n); return d.toISOString().slice(0, 7); };
const balken = (wert, max, titel) => `<div class="aus-balken" title="${esc(titel)}"><span style="width:${max ? Math.max(1, wert / max * 100) : 0}%"></span></div>`;

export default async function render(el) {
  const jetzt = new Date().toISOString().slice(0, 7);
  const m = lokal("ausgaben.monat", jetzt);
  const [r, zeilen, kats] = await Promise.all([api(`ausgaben?monat=${m}`), api(`ausgaben/zeilen?monat=${m}`), api("ausgaben/kategorien")]);
  const diff = r.summe - r.vormonat, waren = r.geplant + r.spontan;
  const maxKat = Math.max(0, ...r.kategorien.map((k) => k.summe)), maxV = Math.max(0, ...r.verlauf.map((v) => v.summe));
  const anteil = waren > 0 ? Math.round(r.geplant / waren * 100) : 0;
  el.innerHTML = `<div class="card"><div class="kopf"><h2>${esc(monatName(m))}</h2><div class="aktionen">
      <button class="btn klein" data-m="-1" aria-label="Vormonat">◀</button><button class="btn klein" data-m="0">aktuell</button>
      <button class="btn klein" data-m="1" aria-label="Nächster Monat" ${m >= jetzt ? "disabled" : ""}>▶</button></div></div>
    <div class="row">
      <div class="kachel"><div class="zahl">${eur(r.summe)}</div><div class="muted small">${r.bons} ${r.bons === 1 ? "Einkauf" : "Einkäufe"}</div></div>
      <div class="kachel"><div class="zahl">${r.vormonat ? `${diff >= 0 ? "+" : "−"}${eur(Math.abs(diff))}` : "–"}</div><div class="muted small">zum Vormonat (${eur(r.vormonat)})</div></div>
      <div class="kachel"><div class="zahl">${waren ? anteil + " %" : "–"}</div><div class="muted small">geplant</div></div></div>
    ${waren ? `<div class="aus-split" role="img" aria-label="Geplant ${eur(r.geplant)}, spontan ${eur(r.spontan)}">
        <span class="geplant" style="flex:${r.geplant}" title="Geplant: ${eur(r.geplant)}"></span><span class="spontan" style="flex:${r.spontan}" title="Spontan: ${eur(r.spontan)}"></span></div>
      <div class="aus-legende small"><span><i class="geplant"></i>Geplant ${eur(r.geplant)}</span><span><i class="spontan"></i>Spontan ${eur(r.spontan)}</span>
        ${r.pfand ? `<span class="muted">Pfand netto ${eur(r.pfand)}</span>` : ""}</div>
      <p class="muted tiny">Geplant = Produkt stand auf der Einkaufsliste der 7 Tage davor oder wird für ein Gericht der 7 Tage danach gebraucht.</p>` : ""}
    </div>
    ${r.bons ? `<div class="grid">
      <div class="card"><h2>Nach Kategorie</h2><table class="aus-tab">${r.kategorien.map((k) => `<tr><td>${esc(k.kategorie)}</td>
        <td class="grow">${balken(k.summe, maxKat, `${k.kategorie}: ${eur(k.summe)}`)}</td><td class="right nw">${eur(k.summe)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2>Spontankäufe</h2>${r.top_spontan.length ? `<table>${r.top_spontan.map((t) => `<tr><td>${esc(t.text)}<div class="muted tiny">${esc(t.kategorie)} · ${dmy(t.datum)}</div></td>
        <td class="right nw">${eur(t.betrag)}</td></tr>`).join("")}</table>` : `<p class="muted">Keine.</p>`}
        <h3>Nach Markt</h3><table>${r.maerkte.map((x) => `<tr><td>${esc(x.markt)}</td><td class="right nw">${eur(x.summe)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2>Verlauf</h2><div class="aus-verlauf">${r.verlauf.map((v) => `<div class="spalte ${v.monat === m ? "an" : ""}" title="${esc(monatName(v.monat))}: ${eur(v.summe)}">
          <span class="wert tiny">${v.summe ? Math.round(v.summe) : ""}</span><span class="saeule" style="height:${maxV ? v.summe / maxV * 100 : 0}%"></span>
          <span class="tiny muted">${new Date(v.monat + "-15").toLocaleDateString("de-DE", {month: "short"})}</span></div>`).join("")}</div>
        <p class="muted tiny">Euro je Monat, letzte 6 Monate.</p></div></div>
    <div class="card"><div class="kopf"><h2>Alle Zeilen (${zeilen.length})</h2></div>
      <p class="muted tiny">Kategorie ändern merkt sie für diesen Bon-Text. Häkchen = geplant.</p>
      <div class="tabelle"><table><thead><tr><th>Bon-Text · Kategorie</th><th title="geplant">Gepl.</th><th class="right">Betrag</th></tr></thead><tbody>
      ${zeilen.map((z) => `<tr><td>${esc(z.text)}<div class="muted tiny">${dmy(z.datum)}${z.markt ? " · " + esc(z.markt) : ""}</div>
        ${z.typ === "pfand" || z.typ === "leergut" ? `<span class="muted">Pfand</span>` : `<select data-kat="${z.id}" class="bk">${kats.filter((k) => k !== "Pfand").map((k) =>
          `<option ${k === z.kategorie ? "selected" : ""}>${esc(k)}</option>`).join("")}</select>`}</td>
        <td class="check">${z.typ === "pfand" || z.typ === "leergut" ? "" : `<input type="checkbox" data-gep="${z.id}" ${z.geplant ? "checked" : ""}>`}</td>
        <td class="right nw">${eur(z.betrag)}</td></tr>`).join("")}</tbody></table></div></div>`
    : `<div class="card"><div class="leer">Keine Bons in diesem Monat. Ausgaben entstehen beim Buchen eines Kassenbons, auch für Zeilen, die nicht ins Inventar gehen.</div>
        <button class="btn klein" id="neu">Bereits gebuchte Bons übernehmen</button></div>`}`;
  $$("[data-m]", el).forEach((b) => b.onclick = () => { merken("ausgaben.monat", +b.dataset.m ? plusMonat(m, +b.dataset.m) : jetzt); guarded(() => render(el)); });
  $$("[data-kat]", el).forEach((s) => s.onchange = () => guarded(async () => { await patch(`ausgaben/${s.dataset.kat}`, {kategorie: s.value}); toast("Kategorie gemerkt"); render(el); }));
  $$("[data-gep]", el).forEach((c) => c.onchange = () => guarded(async () => { await patch(`ausgaben/${c.dataset.gep}`, {geplant: c.checked}); render(el); }));
  const neu = el.querySelector("#neu");
  if (neu) neu.onclick = () => guarded(async () => { const e = await post("ausgaben/neu-berechnen"); toast(`${e.zeilen} Zeilen übernommen`); render(el); });
}
