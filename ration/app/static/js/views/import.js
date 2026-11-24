import {$, $$, api, esc, guarded, post, toast, gehe} from "../lib.js";

// Rezept per Link: Vorschau mit Zuordnung der Zutaten, dann Übernahme nach Grocy
export default async function render(el, {params}) {
  el.innerHTML = `<div class="card"><h2>Rezept per Link importieren</h2>
    <label>Link (z. B. HelloFresh, Chefkoch oder jede Seite mit Rezept-Schema)</label>
    <input id="url" type="url" placeholder="https://…" value="${esc(params.url ?? "")}">
    <details style="margin-top:8px"><summary>Seite blockt? Quelltext einfügen</summary>
      <textarea id="html" rows="4" placeholder="Seitenquelltext (Strg+U, alles kopieren)"></textarea></details>
    <label class="inline"><input id="ki" type="checkbox" checked> KI prüft Zuordnung und beschreibt neue Produkte</label>
    <button class="btn primary voll" id="laden">Vorschau laden</button>
    <p class="muted tiny">Übernommen werden Zutaten, Mengen, Zubereitungsschritte und, wo vorhanden, Titel- und Schrittbilder (lokal gespeichert). Der Link zur Quelle bleibt dabei.</p></div>
    <div id="vor"></div>`;
  $("#laden", el).onclick = () => guarded(async () => {
    const b = $("#laden", el); b.disabled = true; b.textContent = $("#ki", el).checked ? "Lädt, KI ordnet zu …" : "Lädt …";
    try {
      const [v, produkte] = await Promise.all([post("import/vorschau", {url: $("#url", el).value, html: $("#html", el).value || null,
        ki: $("#ki", el).checked}), api("produkte")]);
      vorschau($("#vor", el), v, produkte);
    } finally { b.disabled = false; b.textContent = "Vorschau laden"; }
  });
}

// Attribute eines neuen Produkts laut KI, kurz: „Gemüse · exakt · 10 Tage · Variante von Paprika“
const MODUS = {exakt: "exakt", menge: "Menge", fuellstand: "Füllstand", immer_da: "immer da"};
function attributText(z) {
  const a = z.attribute || {}, teile = [];
  if (a.kategorie) teile.push(a.kategorie);
  if (a.tracking_modus) teile.push(MODUS[a.tracking_modus] ?? a.tracking_modus);
  if (a.haltbarkeit_tage) teile.push(a.haltbarkeit_tage === -1 ? "unbegrenzt" : `${a.haltbarkeit_tage} Tage`);
  if (z.variante_von) teile.push(`Variante von ${z.variante_von.name}`);
  return teile.join(" · ");
}

function vorschau(box, v, produkte) {
  const einheit = Object.fromEntries(produkte.map((p) => [p.id, p.einheit]));
  const wert = (z) => z.aktion === "zuordnen" ? z.produkt_id : z.aktion === "neu" ? "+neu" : "";
  const opts = (z) => {
    const sel = wert(z), o = (val, txt) => `<option value="${esc(val)}" ${val === sel ? "selected" : ""}>${esc(txt)}</option>`;
    const alt = z.alternativen.map((a) => a.produkt_id);
    return o("+neu", "+ neues Produkt") + o("", "– weglassen –") +
      (alt.length ? `<optgroup label="Ähnlich">${z.alternativen.map((a) => o(a.produkt_id, a.name)).join("")}</optgroup>` : "") +
      `<optgroup label="Alle Produkte">${produkte.filter((p) => !alt.includes(p.id)).map((p) => o(p.id, p.name)).join("")}</optgroup>`;
  };
  const zaehl = (f) => v.zutaten.filter(f).length;
  const pruefen = zaehl((z) => z.status === "pruefen" && z.aktion !== "weglassen");
  box.innerHTML = `<div class="card"><h2>Vorschau</h2>
    ${v.dublette ? `<p class="over">Dieses Rezept gibt es schon: <a href="#/rezept/${esc(v.dublette)}">ansehen</a>.</p>` : ""}
    <p class="muted">${zaehl((z) => z.aktion === "zuordnen")} vorhanden · ${zaehl((z) => z.aktion === "neu")} neu ·
      ${zaehl((z) => z.aktion === "weglassen")} weggelassen${pruefen ? ` · <span class="over">${pruefen} prüfen</span>` : ""}</p>
    ${v.ki?.fehler ? `<p class="over small">KI nicht erreichbar, nur Code-Vorschlag: ${esc(v.ki.fehler)}</p>` : ""}
    ${v.ki?.geaendert ? `<p class="muted small">${v.ki.geaendert} Zutaten von der KI ergänzt, bitte prüfen.</p>` : ""}
    <div class="row"><div style="flex:3"><label>Name</label><input id="iname" value="${esc(v.name)}"></div>
      <div><label>Portionen</label><input id="ipor" type="number" min="1" max="12" value="${v.portionen}"></div></div>
    <h3>Zutaten</h3><div class="tabelle"><table><thead><tr><th>Zutat laut Rezept</th><th>Produkt</th><th>Menge</th><th></th></tr></thead>
    <tbody>${v.zutaten.map((z) => `<tr><td>${esc(z.text)} ${z.status !== "ok" ? `<span class="tag ${z.status === "pruefen" ? "bad" : "warn"}">${esc(z.status)}</span>` : ""}
        ${z.hinweis ? `<div class="muted tiny">${esc(z.hinweis)}</div>` : ""}</td>
      <td><select data-zp="${z.index}">${opts(z)}</select>
        <input data-zn="${z.index}" value="${esc(z.neu_name ?? z.name)}" style="display:${z.aktion === "neu" ? "block" : "none"};margin-top:4px">
        ${attributText(z) ? `<div class="muted tiny" data-za="${z.index}" style="display:${z.aktion === "neu" ? "block" : "none"}">neu: ${esc(attributText(z))}</div>` : ""}</td>
      <td style="width:100px"><input data-zm="${z.index}" type="number" min="0" step="any" value="${z.menge_bestand ?? ""}"></td>
      <td class="muted tiny" data-ze="${z.index}">${esc(einheit[z.produkt_id] ?? "g")}</td></tr>`).join("")}</tbody></table></div>
    <h3>Zubereitung <span class="muted tiny">(${v.schritte.length} Schritte, ein Schritt pro Zeile)</span></h3>
    <textarea id="ischr" rows="${Math.min(Math.max(v.schritte.length + 1, 3), 14)}"
      placeholder="Quelle liefert keine Schritte, hier eintragen oder leer lassen">${esc(v.schritte.join("\n"))}</textarea>
    ${v.bild || v.schritt_bilder ? `<div class="row" style="align-items:center;margin-top:10px">${v.bild ? `<img src="${esc(v.bild)}" alt="" class="import-bild" referrerpolicy="no-referrer">` : ""}
      <label class="inline"><input type="checkbox" id="ifotos" checked> Bilder übernehmen (${v.bild ? "Titelbild" : ""}${v.bild && v.schritt_bilder ? " + " : ""}${v.schritt_bilder ? `${v.schritt_bilder} Schrittbilder` : ""})</label></div>` : ""}
    <button class="btn primary voll" id="iok">In Grocy übernehmen</button></div>`;
  $$("[data-zp]", box).forEach((sel) => sel.onchange = () => {
    const i = sel.dataset.zp;
    $(`[data-zn="${i}"]`, box).style.display = sel.value === "+neu" ? "block" : "none";
    const za = $(`[data-za="${i}"]`, box);
    if (za) za.style.display = sel.value === "+neu" ? "block" : "none";
    $(`[data-ze="${i}"]`, box).textContent = einheit[sel.value] ?? "g";
  });
  $("#iok", box).onclick = () => guarded(async () => {
    const zutaten = [];
    for (const sel of $$("[data-zp]", box)) {
      const i = +sel.dataset.zp, menge = $(`[data-zm="${i}"]`, box).value, z = v.zutaten.find((x) => x.index === i);
      if (!sel.value) continue;
      if (menge === "") throw new Error(`Menge fehlt: ${z.text}`);
      // ki: Auswahl ist der unveränderte KI-Vorschlag (der Alias gilt dann nicht als sicher)
      const ki = !!z.ki && sel.value === wert(z);
      zutaten.push(sel.value === "+neu" ? {index: i, neu_anlegen: $(`[data-zn="${i}"]`, box).value, menge: +menge, stueck_g: z.stueck_g,
          ki, attribute: z.attribute ?? null, variante_von: z.variante_von?.produkt_id ?? null}
        : {index: i, produkt_id: sel.value, menge: +menge, ki});
    }
    const e = await post(`import/${v.import_id}/uebernehmen`, {name: $("#iname", box).value, portionen: +$("#ipor", box).value,
      zutaten, trotz_dublette: !!v.dublette, fotos: $("#ifotos", box)?.checked ?? false, schritte: $("#ischr", box).value.split("\n").map((t) => t.trim()).filter(Boolean)});
    toast(`„${e.name}“ angelegt · ${e.zutaten} Zutaten` + (e.neue_produkte.length ? ` · ${e.neue_produkte.length} neue Produkte` : "")
      + (e.fotos ? ` · ${e.fotos} Bilder werden geladen` : ""));
    gehe(`rezept/${e.rezept_id}`);
  });
}
