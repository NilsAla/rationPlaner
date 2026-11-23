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
      <div class="bon-drop" id="bdrop" tabindex="0" title="Foto wählen, hierher ziehen oder Strg+V">
        <img id="bvor" alt="" hidden><span id="btext">📷 Foto wählen · Bild hierher ziehen · <b>Strg+V</b> fügt aus der Zwischenablage ein</span></div>
      <input id="bf" type="file" accept="image/*" capture="environment" hidden>
      <button class="btn primary voll" id="bup" disabled>Hochladen &amp; lesen</button>
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
  // Bon-Bild: Dateiauswahl, Drag & Drop oder Strg+V (Screenshot, kopiertes Foto)
  let bild = null, vorschau = null;
  const setzen = (f, quelle) => {
    if (!f || !f.type.startsWith("image/")) { toast("Kein Bild gefunden", "fehler"); return; }
    if (!["image/jpeg", "image/png", "image/webp"].includes(f.type)) { toast(`Bildformat ${f.type} wird nicht unterstützt (JPEG, PNG, WebP)`, "fehler"); return; }
    bild = f;
    if (vorschau) URL.revokeObjectURL(vorschau);
    vorschau = URL.createObjectURL(f);
    const img = $("#bvor", el); img.src = vorschau; img.hidden = false;
    $("#btext", el).innerHTML = `${esc(quelle)} · ${Math.round(f.size / 1024)} KB · <u>anderes Bild</u>`;
    $("#bup", el).disabled = false; $("#bup", el).focus();
  };
  const drop = $("#bdrop", el);
  drop.onclick = () => $("#bf", el).click();
  drop.onkeydown = (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); $("#bf", el).click(); } };
  $("#bf", el).onchange = (ev) => setzen(ev.target.files[0], ev.target.files[0]?.name ?? "Foto");
  drop.ondragover = (ev) => { ev.preventDefault(); drop.classList.add("ueber"); };
  drop.ondragleave = () => drop.classList.remove("ueber");
  drop.ondrop = (ev) => { ev.preventDefault(); drop.classList.remove("ueber"); setzen(ev.dataTransfer.files[0], "Eingefügt per Drag & Drop"); };
  const einfuegen = (ev) => {
    if (!el.isConnected || ev.target.closest?.("input:not([type=file]),textarea")) return;
    const f = [...(ev.clipboardData?.items ?? [])].find((i) => i.kind === "file" && i.type.startsWith("image/"))?.getAsFile();
    if (!f) return;
    ev.preventDefault();
    const name = f.name && f.name !== "image.png" ? f.name : `bon-${Date.now()}.${f.type.split("/")[1]}`;
    setzen(new File([f], name, {type: f.type}), "📋 Aus der Zwischenablage");
    drop.scrollIntoView({block: "nearest", behavior: "smooth"});
  };
  // interne Neu-Renders (nach dem Buchen) dürfen keinen zweiten Listener anhängen
  if (el._bonPaste) document.removeEventListener("paste", el._bonPaste);
  el._bonPaste = einfuegen;
  document.addEventListener("paste", einfuegen);
  $("#bup", el).onclick = () => guarded(async () => {
    const f = bild; if (!f) throw new Error("Erst ein Foto wählen oder per Strg+V einfügen");
    const fd = new FormData(); fd.append("datei", f);
    const r = await api("bon", {method: "POST", body: fd});
    toast("Hochgeladen, wird gelesen …"); bonZeigen($("#bdet", el), r.id, produkte, () => render(el, {params}));
  });
  const aufraeumen = () => { document.removeEventListener("paste", einfuegen); el._bonPaste = null; if (vorschau) URL.revokeObjectURL(vorschau); };
  $$("[data-bon]", el).forEach((tr) => tr.onclick = () => guarded(() => bonZeigen($("#bdet", el), +tr.dataset.bon, produkte, () => render(el, {params}))));
  if (params.bon) guarded(() => bonZeigen($("#bdet", el), +params.bon, produkte, () => render(el, {params: {}})));
  return aufraeumen;
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
  const kats = (await api("ausgaben/kategorien").catch(() => [])).filter((k) => k !== "Pfand");
  const katSel = (z) => kats.length ? `<select data-bk="${z.index}" class="bk" title="Ausgaben-Kategorie (nur Auswertung)">${
    [`<option value="">${esc(z.kategorie ?? "Kategorie …")}</option>`, ...kats.map((k) => `<option ${k === z.kategorie ? "selected" : ""}>${esc(k)}</option>`)].join("")}</select>` : "";
  const opts = (z) => `<option value="+neu" ${z.aktion === "neu" ? "selected" : ""}>+ neues Produkt</option>` +
    produkte.map((p) => `<option value="${esc(p.id)}" ${p.id === z.produkt_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const zeilen = bon.positionen.filter((z) => z.aktion !== "weglassen");
  const ignoriert = bon.positionen.filter((z) => z.aktion === "weglassen");
  const TYP = {pfand: "Pfand", leergut: "Leergut", rabatt: "Rabatt", storno: "Storno", artikel: "storniert", non_food: "storniert"};
  const pr = bon.pruefung;
  const summenHinweis = !pr ? "" : Math.abs(pr.abweichung) <= 0.05
    ? `<p class="small"><span class="tag good">Summe stimmt</span> ${eur(pr.berechnet)} nachgerechnet aus allen Zeilen.</p>`
    : `<p class="small"><span class="tag bad">Summe weicht ab</span> Bon ${eur(bon.summe)}, nachgerechnet ${eur(pr.berechnet)}
        (Differenz ${eur(pr.abweichung)}). Vermutlich fehlt eine Zeile oder ein Betrag ist falsch gelesen, bitte mit dem Bon vergleichen.</p>`;
  box.innerHTML = `<div class="card"><div class="kopf"><h2>${esc(bon.markt ?? "Bon")} <span class="muted">· ${dmy(bon.datum)} · ${eur(bon.summe ?? 0)}</span></h2></div>
    <p class="muted small">Haken = wird gebucht. Vorausgewählt sind bekannte Bon-Texte und sichere KI-Treffer. Rabatte und Stornos sind schon verrechnet.
      Alle Zeilen (auch ohne Haken) landen mit ihrer Kategorie in den <a href="#/ausgaben">Ausgaben</a>, nicht im Inventar.</p>
    ${summenHinweis}
    ${ignoriert.length ? `<details class="small"><summary>${ignoriert.length} Zeilen ohne Buchung (Pfand, Leergut, Rabatt, Storno)</summary>
      <table>${ignoriert.map((z) => `<tr><td><span class="tag">${esc(TYP[z.typ] ?? z.typ)}</span></td><td>${esc(z.text)}</td>
        <td class="muted tiny">${esc(z.hinweis)}</td><td class="right nw">${z.betrag != null ? eur(z.betrag) : ""}</td></tr>`).join("")}</table></details>` : ""}
    <div class="tabelle"><table><thead><tr><th class="check"><input type="checkbox" id="balle" checked></th><th>Bon-Text</th><th>Produkt</th><th>Menge</th><th>Preis</th></tr></thead>
    <tbody>${zeilen.map((z) => `<tr><td class="check"><input type="checkbox" data-bc="${z.index}" ${z.vorausgewaehlt ? "checked" : ""}></td>
      <td>${esc(z.text)} <span class="tag ${z.quelle === "non_food" ? "" : z.status === "sicher" ? "good" : z.status === "vorschlag" ? "warn" : "bad"}">${esc(z.quelle === "non_food" ? "Non-Food" : z.quelle)}</span>
        ${z.annahme ? ` <span class="tag warn" title="Menge steht nicht auf dem Bon">Annahme</span>` : ""}
        ${z.zweifel ? ` <span class="tag bad" title="Anzahl oder Gewicht passt nicht zum Preis">prüfen</span>` : ""}
        ${z.hinweis ? `<div class="muted tiny">${esc(z.hinweis)}</div>` : ""}${katSel(z)}</td>
      <td><select data-bp="${z.index}">${opts(z)}</select>
        <span data-bnbox="${z.index}" style="display:${z.aktion === "neu" ? "flex" : "none"};gap:4px;margin-top:4px">
          <input data-bn="${z.index}" value="${esc(z.neu_name ?? "")}">
          <select data-bu="${z.index}" style="width:auto" title="Bestandseinheit des neuen Produkts">${["g", "ml", "Stück"].map((u) =>
            `<option ${u === (z.einheit ?? "g") ? "selected" : ""}>${u}</option>`).join("")}</select></span></td>
      <td class="nw"><input data-bm="${z.index}" type="number" min="0" step="any" value="${z.menge ?? ""}" style="width:90px">
        <span class="muted tiny" data-be="${z.index}">${esc(z.einheit ?? "g")}</span></td>
      <td class="nw right">${z.preis != null ? eur(z.preis) : ""}${z.rabatt ? `<div class="muted tiny">−${eur(z.rabatt)} Rabatt</div>` : ""}</td></tr>`).join("")}</tbody></table></div>
    <div class="row" style="margin-top:10px"><button class="btn primary" id="bbuchen">Ausgewählte buchen</button>
      <button class="btn danger" id="bweg">Bon verwerfen</button></div></div>`;
  $$("[data-bu]", box).forEach((u) => u.onchange = () => { $(`[data-be="${u.dataset.bu}"]`, box).textContent = u.value; });
  $("#balle", box).onchange = (e) => $$("[data-bc]", box).forEach((c) => { c.checked = e.target.checked; });
  $$("[data-bp]", box).forEach((sel) => sel.onchange = () => {
    const i = sel.dataset.bp;
    $(`[data-bnbox="${i}"]`, box).style.display = sel.value === "+neu" ? "flex" : "none";
    $(`[data-be="${i}"]`, box).textContent = einheit[sel.value] ?? $(`[data-bu="${i}"]`, box).value;
    $(`[data-bc="${i}"]`, box).checked = true;
  });
  $("#bbuchen", box).onclick = () => guarded(async () => {
    const positionen = [];
    for (const z of zeilen) {
      if (!$(`[data-bc="${z.index}"]`, box).checked) continue;
      const sel = $(`[data-bp="${z.index}"]`, box).value, menge = $(`[data-bm="${z.index}"]`, box).value;
      if (!menge) throw new Error(`Menge fehlt: ${z.text}`);
      positionen.push({index: z.index, menge: +menge, preis: z.preis,
        ...(sel === "+neu" ? {neu_anlegen: $(`[data-bn="${z.index}"]`, box).value, einheit: $(`[data-bu="${z.index}"]`, box).value}
          : {produkt_id: sel})});
    }
    const kategorien = Object.fromEntries($$("[data-bk]", box).filter((s) => s.value).map((s) => [s.dataset.bk, s.value]));
    const e = await post(`bon/${id}/buchen`, {positionen, kategorien});
    toast(`${e.gebucht.length} Positionen eingebucht` + (e.neue_produkte.length ? ` · ${e.neue_produkte.length} neue Produkte` : ""));
    fertig();
  });
  $("#bweg", box).onclick = () => guarded(async () => { await post(`bon/${id}/verwerfen`); toast("Bon verworfen"); fertig(); });
}
