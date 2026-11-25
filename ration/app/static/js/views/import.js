import {$, $$, api, bestaetigen, dialog, esc, guarded, post, toast, gehe, importMerken, importText, importVergessen} from "../lib.js";
import {schritteEditor} from "../schritte.js";

// Rezept per Link: Vorschau läuft im Hintergrund (KI braucht bis ~1 min), Balken mit Fortschritt,
// dann Zuordnung der Zutaten prüfen und nach Grocy übernehmen. ?id=… öffnet einen laufenden Import.
export default async function render(el, {params}) {
  el.innerHTML = `<div class="card"><h2>Rezept per Link importieren</h2>
    <label>Link (z. B. HelloFresh, Chefkoch oder jede Seite mit Rezept-Schema)</label>
    <input id="url" type="url" placeholder="https://…" value="${esc(params.url ?? "")}">
    <details style="margin-top:8px"><summary>Seite blockt? Quelltext einfügen</summary>
      <textarea id="html" rows="4" placeholder="Seitenquelltext (Strg+U, alles kopieren)"></textarea></details>
    <label class="inline"><input id="ki" type="checkbox" checked> KI prüft Zuordnung und beschreibt neue Produkte</label>
    <button class="btn primary voll" id="laden">Vorschau laden</button>
    <p class="muted tiny">Übernommen werden Zutaten, Mengen, Zubereitungsschritte und, wo vorhanden, Titel- und Schrittbilder (lokal gespeichert). Der Link zur Quelle bleibt dabei.
      Die KI-Prüfung läuft im Hintergrund weiter, wenn du die Seite verlässt (Kreis oben rechts).</p></div>
    <div id="vor"></div>`;
  let aktiv = true;
  const box = $("#vor", el), knopf = $("#laden", el);

  // Stand abfragen, bis die Vorschau fertig ist; Balken zeigt Schritt und geschätzte Restzeit
  async function verfolgen(id) {
    knopf.disabled = true;
    try {
      while (aktiv) {
        const s = await api(`import/${id}`);
        if (!aktiv) return;
        if (s.status === "fertig") {
          importVergessen(id);
          vorschau(box, s, await api("produkte?nutzung=true"));
          return;
        }
        if (s.status === "fehler") {
          importVergessen(id);
          box.innerHTML = `<div class="card"><p class="over">Import fehlgeschlagen: ${esc(s.fehler)}</p></div>`;
          return;
        }
        if (s.url && !$("#url", el).value) $("#url", el).value = s.url;
        const rest = s.rest_s > 0 ? `noch ca. ${s.rest_s} s` : s.schritt === "KI" ? "gleich fertig …" : "";
        box.innerHTML = `<div class="card"><h2>Vorschau wird erstellt</h2>
          <div class="fortschritt" role="progressbar" aria-valuenow="${s.fortschritt}" aria-valuemin="0" aria-valuemax="100">
            <div style="width:${s.fortschritt}%"></div></div>
          <p class="muted small">${esc(importText(s))}${rest ? ` · ${rest}` : ""}</p></div>`;
        await new Promise((ok) => setTimeout(ok, 1000));
      }
    } finally { knopf.disabled = false; }
  }

  knopf.onclick = () => guarded(async () => {
    knopf.disabled = true;
    const s = await post("import/start", {url: $("#url", el).value, html: $("#html", el).value || null,
      ki: $("#ki", el).checked}).finally(() => { knopf.disabled = false; });
    importMerken(s.import_id);
    await verfolgen(s.import_id);
  });
  if (params.id) guarded(() => verfolgen(+params.id));
  return () => { aktiv = false; };
}

// Attribute eines neuen Produkts laut KI, kurz: „Gemüse · exakt · 10 Tage · Variante von Paprika“
const MODUS = {exakt: "exakt", menge: "Menge", fuellstand: "Füllstand", immer_da: "immer da"};
function attributText(z) {
  const a = z.attribute || {}, teile = [];
  if (a.kategorie) teile.push(a.kategorie);
  if (a.tracking_modus) teile.push(MODUS[a.tracking_modus] ?? a.tracking_modus);
  if (a.haltbarkeit_tage) teile.push(a.haltbarkeit_tage === -1 ? "unbegrenzt" : `${a.haltbarkeit_tage} Tage`);
  // geöffnet: 0/leer = Öffnen ändert das MHD nicht (trockene Vorräte)
  if (Object.keys(a).length) teile.push(a.haltbarkeit_offen_tage ? `geöffnet ${a.haltbarkeit_offen_tage} Tage` : "geöffnet: MHD bleibt");
  if (z.variante_von) teile.push(`unter ${z.variante_von.name}`);
  return teile.join(" · ");
}

// Farbe je Zeile: grün = passt sicher, gelb = Oberbegriff/ähnliche Sorte, blau = wird neu angelegt,
// rot = bitte ansehen (Menge fehlt, KI widerspricht), grau = weggelassen
const FARBEN = [["gruen", "passt"], ["gelb", "Oberbegriff / ähnliche Sorte"], ["blau", "neu"], ["rot", "prüfen"]];
const ART = {oberbegriff: "Oberbegriff", austauschbar: "ähnliche Sorte"};

function vorschau(box, v, produkte) {
  const pmap = Object.fromEntries(produkte.map((p) => [p.id, p]));
  // Arbeitsstand je Zeile: wert = produkt_id | "+neu" | "" (weglassen), manuell = vom Nutzer gewählt
  const wahl = Object.fromEntries(v.zutaten.map((z) => [z.index, {
    wert: z.aktion === "zuordnen" ? z.produkt_id : z.aktion === "neu" ? "+neu" : "",
    name: z.neu_name ?? z.name, menge: z.menge_bestand ?? "", manuell: false,
  }]));
  const farbe = (z) => {
    const w = wahl[z.index];
    if (!w.wert) return "grau";
    if (w.menge === "" || w.menge == null) return "rot";
    if (w.manuell) return w.wert === "+neu" ? "blau" : "gruen";
    if (z.status === "pruefen" || /KI meint/.test(z.hinweis)) return "rot";
    if (w.wert === "+neu") return "blau";
    return z.sicher || z.treffer === "gleich" ? "gruen" : "gelb";
  };
  const zielText = (z) => {
    const w = wahl[z.index];
    if (!w.wert) return "– weglassen –";
    if (w.wert === "+neu") return `+ neu: ${w.name}`;
    return pmap[w.wert]?.name ?? z.produkt ?? w.wert;
  };
  const art = (z) => {
    const w = wahl[z.index];
    if (w.manuell || !w.wert || w.wert === "+neu") return "";
    return z.sicher ? "" : ART[z.treffer] ?? (z.treffer === "gleich" ? "" : "unsicher");
  };

  const zeileHtml = (z) => {
    const w = wahl[z.index], f = farbe(z), attr = attributText(z);
    return `<div class="imp-zeile ${f}" data-zeile="${z.index}">
      <div class="text"><b>${esc(z.text)}</b>
        ${z.hinweis ? `<div class="muted tiny">${esc(z.hinweis)}</div>` : ""}
        ${z.pruefung && !w.manuell ? `<div class="muted tiny" title="Begründung der KI">KI: ${esc(z.pruefung)}</div>` : ""}</div>
      <div class="ziel">
        <button class="imp-wahl" data-wahl="${z.index}" title="Antippen: Produkt suchen oder ändern">${esc(zielText(z))}${art(z) ? `<span class="art">${art(z)}</span>` : ""}</button>
        ${w.wert ? `<input type="number" min="0" step="any" data-menge="${z.index}" value="${esc(w.menge)}" aria-label="Menge">
          <span class="muted tiny">${esc(pmap[w.wert]?.einheit ?? "g")}</span>` : ""}</div>
      ${w.wert === "+neu" ? `<div class="neu-name"><input data-name="${z.index}" value="${esc(w.name)}" aria-label="Name des neuen Produkts">
        ${attr && !w.manuell ? `<span class="muted tiny">${esc(attr)}</span>` : ""}</div>` : ""}</div>`;
  };
  const zaehlen = () => {
    const n = {};
    v.zutaten.forEach((z) => { const f = farbe(z); n[f] = (n[f] ?? 0) + 1; });
    return FARBEN.filter(([f]) => n[f]).map(([f, t]) => `<span><i class="imp-punkt ${f}"></i>${n[f]} ${t}</span>`).join("")
      + (n.grau ? `<span class="muted">${n.grau} weggelassen</span>` : "");
  };

  // Anleitung: Arbeitskopie im gespeicherten Format, Zutaten = Import-Zeilen
  const anl = v.anleitung;
  const schritte = (anl?.schritte ?? []).map((s) => ({...s, zutaten: s.zutaten.map((z) => ({zutat: z.zutat, anteil: z.anteil}))}));
  let anlGeaendert = false;
  const anlZutaten = () => v.zutaten.map((z) => {
    const w = wahl[z.index];
    return {index: z.index, name: w.wert === "+neu" ? w.name : w.wert ? (pmap[w.wert]?.name ?? z.name) : z.name,
      menge: w.menge, einheit: pmap[w.wert]?.einheit ?? "g", aus: !w.wert};
  });

  box.innerHTML = `<div class="card"><h2>Vorschau prüfen</h2>
    ${v.dublette ? `<p class="over">Dieses Rezept gibt es schon: <a href="#/rezept/${esc(v.dublette)}">ansehen</a>.</p>` : ""}
    <div class="row"><div style="flex:3"><label>Name</label><input id="iname" value="${esc(v.name)}"></div>
      <div><label>Portionen</label><input id="ipor" type="number" min="1" max="12" value="${v.portionen}"></div></div>
    ${v.bild || v.schritt_bilder ? `<div class="row" style="align-items:center;margin-top:10px">${v.bild ? `<img src="${esc(v.bild)}" alt="" class="import-bild" referrerpolicy="no-referrer">` : ""}
      <label class="inline"><input type="checkbox" id="ifotos" checked> Bilder übernehmen (${v.bild ? "Titelbild" : ""}${v.bild && v.schritt_bilder ? " + " : ""}${v.schritt_bilder ? `${v.schritt_bilder} Schrittbilder` : ""})</label></div>` : ""}</div>
  <div class="card"><h2>1 · Zutaten</h2>
    ${v.ki?.fehler ? `<p class="over small">KI nicht erreichbar, nur Code-Vorschlag: ${esc(v.ki.fehler)}</p>` : ""}
    <div class="imp-legende small" id="legende">${zaehlen()}</div>
    <p class="muted tiny">Produkt antippen zum Suchen: Steht z. B. Hirtenkäse bei dir unter Feta, dort auswählen. Die Wahl merkt sich die App für die nächsten Rezepte.</p>
    <div id="zeilen">${v.zutaten.map(zeileHtml).join("")}</div></div>
  <div class="card"><div class="kopf"><h2>2 · Anleitung</h2>
      <label class="inline"><input type="checkbox" id="ianl" ${schritte.length ? "checked" : ""}> als Kochanleitung übernehmen</label></div>
    <p class="muted small">${anl?.quelle === "ki" ? "Von der KI Schritt für Schritt umgeschrieben." : "Vorschlag aus den Originalschritten (ohne KI)."}
      ${anl?.fehler ? `<span class="over">KI-Fehler: ${esc(anl.fehler)}</span>` : ""}</p>
    ${anl?.hinweise?.length ? `<ul class="small">${anl.hinweise.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}
    <p class="muted tiny">Mengen im Text für ${v.portionen} Portion(en): Kochmodus und Rezeptbuch rechnen sie für andere Portionen um.</p>
    <div id="ischritte"></div>
    <details style="margin-top:10px"><summary>Originalschritte (Rezepttext, ${v.schritte.length})</summary>
      <textarea id="ischr" rows="${Math.min(Math.max(v.schritte.length + 1, 3), 14)}"
        placeholder="Quelle liefert keine Schritte, hier eintragen oder leer lassen">${esc(v.schritte.join("\n"))}</textarea></details></div>
  <div class="card"><button class="btn primary voll" id="iok">In Grocy übernehmen</button></div>`;

  const editor = schritteEditor($("#ischritte", box), schritte, {
    zutaten: anlZutaten, original: v.schritte, onDirty: () => { anlGeaendert = true; },
  });
  editor.zeichnen();

  const zeileNeu = (i) => {
    const z = v.zutaten.find((x) => x.index === i), alt = $(`[data-zeile="${i}"]`, box);
    alt.outerHTML = zeileHtml(z);
    binden($(`[data-zeile="${i}"]`, box));
    $("#legende", box).innerHTML = zaehlen();
  };
  const binden = (zeile) => {
    const i = +zeile.dataset.zeile, w = wahl[i];
    $("[data-wahl]", zeile).onclick = () => guarded(async () => {
      if (!await suchen(v.zutaten.find((x) => x.index === i), w, produkte)) return;
      zeileNeu(i); editor.zeichnen();
    });
    const m = $("[data-menge]", zeile);
    if (m) m.oninput = () => {
      w.menge = m.value === "" ? "" : +m.value;
      const f = farbe(v.zutaten.find((x) => x.index === i));
      zeile.className = `imp-zeile ${f}`; $("#legende", box).innerHTML = zaehlen();
    };
    const n = $("[data-name]", zeile);
    if (n) n.onchange = () => { w.name = n.value.trim() || w.name; $(`[data-wahl="${i}"]`, zeile).textContent = `+ neu: ${w.name}`; editor.zeichnen(); };
  };
  $$("[data-zeile]", box).forEach(binden);

  $("#iok", box).onclick = () => guarded(async () => {
    const zutaten = [];
    for (const z of v.zutaten) {
      const w = wahl[z.index];
      if (!w.wert) continue;
      if (w.menge === "" || w.menge == null) throw new Error(`Menge fehlt: ${z.text}`);
      // ki: Auswahl ist der unveränderte KI-Vorschlag (der Alias gilt dann nicht als sicher)
      const ki = !!z.ki && !w.manuell;
      zutaten.push(w.wert === "+neu" ? {index: z.index, neu_anlegen: w.name, menge: +w.menge, stueck_g: z.stueck_g,
          ki, attribute: w.manuell ? null : z.attribute ?? null, variante_von: w.manuell ? null : z.variante_von?.produkt_id ?? null}
        : {index: z.index, produkt_id: w.wert, menge: +w.menge, ki});
    }
    const mitAnl = $("#ianl", box).checked && schritte.length;
    if (mitAnl) {
      const f = editor.fehlend();
      if (f.length && !await bestaetigen(`${f.length} Zutat(en) stehen in keinem Anleitungsschritt (${f.map((z) => esc(z.name)).join(", ")}). Trotzdem übernehmen?`, "Übernehmen")) return;
    }
    const e = await post(`import/${v.import_id}/uebernehmen`, {name: $("#iname", box).value, portionen: +$("#ipor", box).value,
      zutaten, trotz_dublette: !!v.dublette, fotos: $("#ifotos", box)?.checked ?? false,
      schritte: $("#ischr", box).value.split("\n").map((t) => t.trim()).filter(Boolean),
      anleitung: mitAnl ? schritte : null, anleitung_quelle: anlGeaendert ? "manuell" : anl?.quelle ?? "code"});
    toast(`„${e.name}“ angelegt · ${e.zutaten} Zutaten` + (e.neue_produkte.length ? ` · ${e.neue_produkte.length} neue Produkte` : "")
      + (e.anleitung ? ` · Anleitung mit ${e.anleitung} Schritten` : "") + (e.fotos ? ` · ${e.fotos} Bilder werden geladen` : ""));
    gehe(`rezept/${e.rezept_id}`);
  });
}

// Suchfenster: alle Produkte mit Kategorie und Anzahl Rezepte, oben die Vorschläge zur Zutat.
// Gibt true zurück, wenn sich die Wahl geändert hat (w wird direkt geändert).
async function suchen(z, w, produkte) {
  let ergebnis = null;
  const vorschlag = [...new Set([z.produkt_id, ...z.alternativen.map((a) => a.produkt_id)].filter(Boolean))];
  const eintrag = (p) => `<button type="button" data-p="${esc(p.id)}" class="${p.id === w.wert ? "aktuell" : ""}">
    <span class="name">${esc(p.name)}${p.oberprodukt ? ` <span class="muted tiny">unter ${esc(p.oberprodukt)}</span>` : ""}</span>
    <span class="muted tiny">${esc(p.kategorie ?? "")}</span>
    <span class="muted tiny nw">${p.rezepte ? `${p.rezepte} Rezept${p.rezepte === 1 ? "" : "e"}` : "–"}</span></button>`;
  const liste = (q) => {
    const t = q.trim().toLowerCase();
    const treffer = produkte.filter((p) => !t || p.name.toLowerCase().includes(t) || (p.kategorie ?? "").toLowerCase().includes(t));
    const vor = t ? [] : vorschlag.map((id) => produkte.find((p) => p.id === id)).filter(Boolean);
    return (vor.length ? `<h4>Vorschläge</h4>${vor.map(eintrag).join("")}<h4>Alle Produkte</h4>` : "")
      + (treffer.length ? treffer.map(eintrag).join("") : `<p class="muted small" style="padding:8px 10px;margin:0">Nichts gefunden.</p>`);
  };
  const r = await dialog(`<h2>Produkt für „${esc(z.text)}“</h2>
    <input type="search" id="sq" placeholder="Suchen: Name oder Kategorie" value="" autocomplete="off">
    <div class="suche-liste" id="sl">${liste("")}</div>
    <div class="row" style="margin-top:10px;align-items:end">
      <div style="flex:1 1 200px"><label>oder neu anlegen als</label><input id="sneu" value="${esc(w.wert === "+neu" ? w.name : z.neu_name ?? z.name)}"></div>
      <button type="button" class="btn" id="sneuok">+ Neu anlegen</button></div>`,
  [["abbrechen", "Abbrechen"], ["weg", "Weglassen"]], (d) => {
    const q = $("#sq", d), sl = $("#sl", d);
    const klicks = () => $$("[data-p]", sl).forEach((b) => b.onclick = () => { ergebnis = {wert: b.dataset.p}; d.close("wahl"); });
    q.oninput = () => { sl.innerHTML = liste(q.value); klicks(); };
    q.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("[data-p]", sl)?.click(); } };
    klicks();
    $("#sneuok", d).onclick = () => { ergebnis = {wert: "+neu", name: $("#sneu", d).value.trim() || z.name}; d.close("wahl"); };
    setTimeout(() => q.focus(), 0);
  });
  if (r === "weg") ergebnis = {wert: ""};
  if (!ergebnis) return false;
  const vorher = w.wert, p = produkte.find((x) => x.id === ergebnis.wert);
  w.wert = ergebnis.wert;
  if (ergebnis.name) w.name = ergebnis.name;
  // Menge: bei gleicher Einheit bleibt sie, sonst neu eintragen (Code rechnet beim nächsten Import)
  const einheitAlt = vorher === "+neu" ? "g" : produkte.find((x) => x.id === vorher)?.einheit ?? "g";
  const einheitNeu = w.wert === "+neu" ? "g" : p?.einheit ?? "g";
  if (w.wert && einheitAlt !== einheitNeu) w.menge = "";
  else if (w.wert && w.menge === "" && z.menge_bestand != null) w.menge = z.menge_bestand;
  // unveränderter KI-/Code-Vorschlag zählt nicht als manuell (Farbe und Alias-Quelle bleiben)
  const original = z.aktion === "zuordnen" ? z.produkt_id : z.aktion === "neu" ? "+neu" : "";
  w.manuell = !(w.wert === original && (w.wert !== "+neu" || w.name === (z.neu_name ?? z.name)));
  return true;
}
