import {$, $$, api, dmy, esc, eur, guarded, post, toast} from "../lib.js";

// Einbuchen von Hand (mit Mehrfachzeilen) und Kassenbon per Foto
export default async function render(el, {params}) {
  const [produkte, orte, bons] = await Promise.all([api("produkte"), api("lagerorte"), api("bon")]);
  const einheit = Object.fromEntries(produkte.map((p) => [p.id, p.einheit]));
  const zeile = () => `<tr data-ez><td><input data-p list="plist" placeholder="Produkt …"></td>
    <td style="width:100px"><input data-m type="number" min="0" step="any" placeholder="Menge"></td><td class="muted tiny" data-e style="width:36px"></td>
    <td style="width:150px"><input data-d type="date"></td>
    <td style="width:150px"><select data-l><option value="">Standard</option>${orte.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("")}</select></td>
    <td style="width:100px"><input data-pr type="number" min="0" step="0.01" placeholder="€"></td>
    <td style="width:36px"><button class="icon" data-weg>✕</button></td></tr>`;
  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr))">
    <div class="card" style="grid-column:1/-1"><div class="kopf"><h2>Von Hand einbuchen</h2>
      <div class="aktionen"><a class="btn klein" href="#/produkt-neu">+ Neues Produkt</a></div></div>
      <p class="muted tiny">MHD leer = Standard-Haltbarkeit des Produkts. Preis optional (Gesamtpreis), fließt in die Kostenschätzung.</p>
      <datalist id="plist">${produkte.map((p) => `<option value="${esc(p.name)}">`).join("")}</datalist>
      <div class="tabelle"><table><thead><tr><th>Produkt</th><th>Menge</th><th></th><th>MHD</th><th>Lagerort</th><th>Preis</th><th></th></tr></thead>
        <tbody id="ez">${zeile()}</tbody></table></div>
      <div class="row" style="margin-top:8px"><button class="btn" id="mehr">+ Zeile</button><button class="btn primary" id="buchen">Alles einbuchen</button></div></div>
    <div class="card"><h2>Kassenbon fotografieren</h2>
      <input id="bf" type="file" accept="image/*" capture="environment">
      <button class="btn primary voll" id="bup">Hochladen &amp; lesen</button>
      <p class="muted tiny">Die KI liest den Bon im Hintergrund (ca. 30 s). Gebucht wird erst nach deiner Prüfung.</p>
      <h3>Letzte Bons</h3>${bons.length ? `<table>${bons.map((b) => `<tr class="klick" data-bon="${b.id}">
        <td><b>${esc(b.markt ?? "Bon " + b.id)}</b></td><td class="nw muted">${dmy(b.datum ?? b.angelegt)}</td>
        <td class="nw right">${b.summe ? eur(b.summe) : ""}</td>
        <td class="right"><span class="tag ${b.status === "fehler" ? "bad" : b.status === "review" ? "warn" : b.status === "gebucht" ? "good" : ""}">${esc(b.status)}</span></td></tr>`).join("")}</table>`
        : `<p class="muted">Noch keine.</p>`}</div>
    <div id="bdet" style="grid-column:1/-1"></div></div>`;

  const binden = () => {
    $$("[data-weg]", el).forEach((b) => b.onclick = () => { if ($$("[data-ez]", el).length > 1) b.closest("tr").remove(); });
    $$("[data-ez] [data-p]", el).forEach((i) => i.oninput = () => {
      const p = produkte.find((x) => x.name === i.value);
      i.closest("tr").querySelector("[data-e]").textContent = p ? einheit[p.id] : "";
    });
  };
  binden();
  $("#mehr", el).onclick = () => { $("#ez", el).insertAdjacentHTML("beforeend", zeile()); binden(); };
  $("#buchen", el).onclick = () => guarded(async () => {
    const zeilen = $$("[data-ez]", el).filter((tr) => $("[data-p]", tr).value.trim());
    if (!zeilen.length) throw new Error("Mindestens ein Produkt angeben");
    const auftraege = zeilen.map((tr) => {
      const name = $("[data-p]", tr).value.trim(), p = produkte.find((x) => x.name.toLowerCase() === name.toLowerCase());
      if (!p) throw new Error(`Unbekanntes Produkt: ${name}. Erst anlegen.`);
      const menge = +$("[data-m]", tr).value;
      if (!menge) throw new Error(`Menge fehlt: ${name}`);
      return {produkt_id: p.id, menge, mhd: $("[data-d]", tr).value || null, lagerort_id: $("[data-l]", tr).value || null,
        preis: $("[data-pr]", tr).value ? +$("[data-pr]", tr).value : null};
    });
    for (const a of auftraege) await post("bestand", a);
    toast(`${auftraege.length} Position(en) eingebucht`); render(el, {params});
  });
  $("#bup", el).onclick = () => guarded(async () => {
    const f = $("#bf", el).files[0]; if (!f) throw new Error("Erst ein Foto wählen");
    const fd = new FormData(); fd.append("datei", f);
    const r = await api("bon", {method: "POST", body: fd});
    toast("Hochgeladen, wird gelesen …"); bonZeigen($("#bdet", el), r.id, produkte, () => render(el, {params}));
  });
  $$("[data-bon]", el).forEach((tr) => tr.onclick = () => guarded(() => bonZeigen($("#bdet", el), +tr.dataset.bon, produkte, () => render(el, {params}))));
  if (params.bon) guarded(() => bonZeigen($("#bdet", el), +params.bon, produkte, () => render(el, {params: {}})));
}

async function bonZeigen(box, id, produkte, fertig) {
  const bon = await api(`bon/${id}`);
  if (!box.isConnected) return;
  if (bon.status === "verarbeitung") {
    box.innerHTML = `<div class="card"><h2>Bon ${id}</h2><p class="muted">Wird gelesen …</p></div>`;
    setTimeout(() => guarded(() => bonZeigen(box, id, produkte, fertig)), 3000); return;
  }
  if (bon.status !== "review") {
    box.innerHTML = `<div class="card"><h2>${esc(bon.markt ?? "Bon " + id)}</h2><p class="${bon.status === "fehler" ? "over" : "muted"}">
      ${esc(bon.status)}${bon.fehler ? ": " + esc(bon.fehler) : ""}</p></div>`; return;
  }
  const einheit = Object.fromEntries(produkte.map((p) => [p.id, p.einheit]));
  const opts = (z) => `<option value="+neu" ${z.aktion === "neu" ? "selected" : ""}>+ neues Produkt</option>` +
    produkte.map((p) => `<option value="${esc(p.id)}" ${p.id === z.produkt_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const zeilen = bon.positionen.filter((z) => z.aktion !== "weglassen");
  box.innerHTML = `<div class="card"><div class="kopf"><h2>${esc(bon.markt ?? "Bon")} <span class="muted">· ${dmy(bon.datum)} · ${eur(bon.summe ?? 0)}</span></h2></div>
    <p class="muted small">Haken = wird gebucht. Vorausgewählt sind bekannte Bon-Texte und sichere KI-Treffer.
      ${bon.positionen.length - zeilen.length} Zeilen (Pfand, Rabatt …) ignoriert.</p>
    <div class="tabelle"><table><thead><tr><th class="check"><input type="checkbox" id="balle" checked></th><th>Bon-Text</th><th>Produkt</th><th>Menge</th><th>Preis</th></tr></thead>
    <tbody>${zeilen.map((z) => `<tr><td class="check"><input type="checkbox" data-bc="${z.index}" ${z.vorausgewaehlt ? "checked" : ""}></td>
      <td>${esc(z.text)} <span class="tag ${z.status === "sicher" ? "good" : z.status === "vorschlag" ? "warn" : "bad"}">${esc(z.quelle)}</span>
        ${z.hinweis ? `<div class="muted tiny">${esc(z.hinweis)}</div>` : ""}</td>
      <td><select data-bp="${z.index}">${opts(z)}</select>
        <input data-bn="${z.index}" value="${esc(z.neu_name ?? "")}" style="display:${z.aktion === "neu" ? "block" : "none"};margin-top:4px"></td>
      <td class="nw"><input data-bm="${z.index}" type="number" min="0" step="any" value="${z.menge ?? ""}" style="width:90px">
        <span class="muted tiny" data-be="${z.index}">${esc(z.einheit ?? "g")}</span></td>
      <td class="nw right">${z.preis != null ? eur(z.preis) : ""}</td></tr>`).join("")}</tbody></table></div>
    <div class="row" style="margin-top:10px"><button class="btn primary" id="bbuchen">Ausgewählte buchen</button>
      <button class="btn danger" id="bweg">Bon verwerfen</button></div></div>`;
  $("#balle", box).onchange = (e) => $$("[data-bc]", box).forEach((c) => { c.checked = e.target.checked; });
  $$("[data-bp]", box).forEach((sel) => sel.onchange = () => {
    const i = sel.dataset.bp;
    $(`[data-bn="${i}"]`, box).style.display = sel.value === "+neu" ? "block" : "none";
    $(`[data-be="${i}"]`, box).textContent = einheit[sel.value] ?? "g";
    $(`[data-bc="${i}"]`, box).checked = true;
  });
  $("#bbuchen", box).onclick = () => guarded(async () => {
    const positionen = [];
    for (const z of zeilen) {
      if (!$(`[data-bc="${z.index}"]`, box).checked) continue;
      const sel = $(`[data-bp="${z.index}"]`, box).value, menge = $(`[data-bm="${z.index}"]`, box).value;
      if (!menge) throw new Error(`Menge fehlt: ${z.text}`);
      positionen.push({index: z.index, menge: +menge, preis: z.preis,
        ...(sel === "+neu" ? {neu_anlegen: $(`[data-bn="${z.index}"]`, box).value} : {produkt_id: sel})});
    }
    const e = await post(`bon/${id}/buchen`, {positionen});
    toast(`${e.gebucht.length} Positionen eingebucht` + (e.neue_produkte.length ? ` · ${e.neue_produkte.length} neue Produkte` : ""));
    fertig();
  });
  $("#bweg", box).onclick = () => guarded(async () => { await post(`bon/${id}/verwerfen`); toast("Bon verworfen"); fertig(); });
}
