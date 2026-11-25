import {$, $$, api, bestaetigen, del, dialog, esc, fmt, fotoHochladen, fotoUrl, guarded, post, put, titel, toast} from "../lib.js";
import {schritteEditor} from "../schritte.js";

// Editor für die Kochanleitung eines Rezepts: Schritte, Zutaten je Schritt, Timer, Reihenfolge
const STATUS = {keine: "noch keine", entwurf: "Entwurf", geprueft: "freigegeben"};
const QUELLE = {ki: "KI", code: "Code-Vorschlag", manuell: "von Hand"};

export default async function render(el, {arg}) {
  const [a0, rezept, fotos0] = await Promise.all([api(`kochen/anleitung/${arg}`), api(`rezepte/${arg}`), api(`rezepte/${arg}/fotos`)]);
  let fotos = fotos0;
  let a = a0, dirty = false;
  // Arbeitskopie im gespeicherten Format
  let schritte = [];
  const uebernehmen = (x) => {
    a = x;
    schritte = x.schritte.map((s) => ({
      titel: s.titel, text: s.text, timer_s: s.timer_s, timer_name: s.timer_name, parallel: !!s.parallel,
      timer_geschaetzt: !!s.timer_geschaetzt, original: s.original ?? [], foto: s.foto ?? null, zutaten: s.zutaten.map((z) => ({zutat: z.index, anteil: z.anteil})),
    }));
    dirty = false;
  };
  uebernehmen(a0);
  titel(`Anleitung: ${a.name}`);

  const markDirty = () => { dirty = true; $("#dirty", el).hidden = false; };

  // Schrittfoto: ausdrücklich gewählt, sonst die Fotos der zugeordneten Originalschritte
  const schrittFotos = (s) => s.foto ? [s.foto]
    : fotos.filter((f) => f.schritt != null && s.original.includes(f.schritt)).map((f) => f.id).slice(0, 3);
  const fotoHtml = (s) => {
    const ids = schrittFotos(s);
    return `${ids.map((id) => `<img src="${fotoUrl(id)}" alt="" class="schritt-mini">`).join("")}
      <button class="btn klein" data-foto>🖼 ${s.foto ? "Foto ändern" : ids.length ? "Foto (aus Original)" : "Foto zuordnen"}</button>`;
  };

  const zeichnen = () => {
    el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(420px,100%),1fr))">
      <div class="card" style="grid-column:1/-1"><div class="kopf"><h2>${esc(a.name)}</h2><div class="aktionen">
          <a class="btn klein" href="#/rezept/${esc(a.rezept_id)}">← Rezept</a>
          <a class="btn klein" href="#/kochen/${esc(a.rezept_id)}?test=1" title="Ohne HA-Timer und Buchung">🧪 Testlauf</a>
          <a class="btn klein primary" href="#/kochen/${esc(a.rezept_id)}">👩‍🍳 Kochmodus</a></div></div>
        <p class="muted small">Status: <b>${STATUS[a.status] ?? a.status}</b>${a.gespeichert ? ` · Quelle: ${QUELLE[a.quelle] ?? a.quelle}` : " · Vorschlag aus den Originalschritten, noch nicht gespeichert"}
          ${a.veraltet ? ` · <span class="tag warn">Rezept seitdem geändert</span>` : ""}
          <span id="dirty" class="tag" ${dirty ? "" : "hidden"}>ungespeichert</span></p>
        ${a.hinweise.length ? `<ul class="small">${a.hinweise.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn primary" id="speichern">Speichern</button>
          <button class="btn" id="freigeben">✓ ${a.status === "geprueft" ? "Speichern (freigegeben)" : "Speichern und freigeben"}</button>
          <button class="btn" id="ki">✨ Mit KI neu schreiben</button>
          <button class="btn" id="code">Code-Vorschlag</button>
          ${a.gespeichert ? `<button class="btn danger" id="loeschen">Anleitung löschen</button>` : ""}</div>
        <p class="muted tiny">Mengen rechnet der Code aus Rezept × Portionen × Anteil. Steht eine Zutat in mehreren Schritten, gilt jeweils die volle Menge (später weiterbearbeitet).</p></div>
      <div class="card"><h2>Schritte</h2>
        <p class="muted tiny">Mengen im Text für ${a.portionen_basis} Portion(en) schreiben („250 g Hähnchen“, „2 EL Öl“, „1 Zwiebel“): Kochmodus und Rezeptbuch rechnen sie für andere Portionen mit um. Zeiten, °C und cm bleiben.</p>
        <div id="liste"></div></div>
      <div class="card"><h2>Original</h2>
        ${rezept.schritte.length ? `<ol class="schritte">${rezept.schritte.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : `<p class="muted">Keine Originalschritte.</p>`}
        <h3>Zutaten (${a.portionen_basis} P.)</h3><table>${a.zutaten.map((z) => `<tr><td>${esc(z.name)}${z.notiz ? ` <span class="muted tiny">${esc(z.notiz)}</span>` : ""}</td>
          <td class="nw right">${fmt(z.menge)} ${esc(z.einheit)}</td></tr>`).join("")}</table></div></div>`;
    editor = schritteEditor($("#liste", el), schritte, {
      zutaten: () => a.zutaten, original: rezept.schritte, onDirty: markDirty,
      fotoHtml, onFoto: (s) => guarded(() => fotoWaehlen(s)),
    });
    editor.zeichnen();
    binden();
  };

  let editor;
  const binden = () => {
    $("#speichern", el).onclick = () => speichern(a.status === "geprueft" && !dirty ? "geprueft" : "entwurf");
    $("#freigeben", el).onclick = () => speichern("geprueft");
    $("#ki", el).onclick = () => neu(true);
    $("#code", el).onclick = () => neu(false);
    const l = $("#loeschen", el);
    if (l) l.onclick = () => guarded(async () => {
      if (!await bestaetigen("Gespeicherte Anleitung löschen? Das Rezept in Grocy bleibt unverändert.", "Löschen", true)) return;
      await del(`kochen/anleitung/${a.rezept_id}`); toast("Anleitung gelöscht");
      uebernehmen(await api(`kochen/anleitung/${a.rezept_id}`)); zeichnen();
    });
  };

  const fotoWaehlen = async (s) => {
    let gewaehlt = s.foto;
    const r = await dialog(`<h2>Foto für den Schritt</h2>
      ${fotos.length ? `<div class="fotogrid">${fotos.map((f) => `<figure><img src="${fotoUrl(f.id)}" alt="" data-wahl-foto="${f.id}"
        class="${f.id === s.foto ? "gewaehlt-foto" : ""}" style="cursor:pointer"><figcaption>${f.haupt ? "Hauptbild" : f.schritt != null ? `Original ${f.schritt + 1}` : esc(f.quelle)}</figcaption></figure>`).join("")}</div>`
        : `<p class="muted">Zu diesem Rezept gibt es noch keine Fotos.</p>`}
      <p class="muted tiny">Ohne Auswahl zeigt der Kochmodus die Fotos der zugeordneten Originalschritte.</p>`,
    [["abbrechen", "Abbrechen"], ["upload", "📷 Hochladen"], ["kein", "Kein eigenes"], ["ok", "Übernehmen", "primary"]],
    (d) => $$("[data-wahl-foto]", d).forEach((img) => img.onclick = () => {
      $$("[data-wahl-foto]", d).forEach((x) => x.classList.remove("gewaehlt-foto"));
      img.classList.add("gewaehlt-foto"); gewaehlt = +img.dataset.wahlFoto;
    }));
    if (r === "upload") {
      const f = await fotoHochladen(a.rezept_id);
      if (!f) return;
      fotos = [...fotos, f]; s.foto = f.id;
    } else if (r === "kein") s.foto = null;
    else if (r === "ok") s.foto = gewaehlt ?? null;
    else return;
    markDirty(); zeichnen();
  };

  const speichern = (status) => guarded(async () => {
    const leere = schritte.filter((s) => !s.titel.trim() && !s.text.trim()).length;
    if (!schritte.length || leere === schritte.length) { toast("Mindestens ein Schritt mit Text", "fehler"); return; }
    const f = editor.fehlend();
    if (f.length && !await bestaetigen(`${f.length} Zutat(en) stehen in keinem Schritt (${f.map((z) => esc(z.name)).join(", ")}). Trotzdem speichern?`, "Speichern")) return;
    uebernehmen(await put(`kochen/anleitung/${a.rezept_id}`, {schritte, status}));
    toast(status === "geprueft" ? "Gespeichert und freigegeben" : "Gespeichert");
    zeichnen();
  });

  const neu = (ki) => guarded(async () => {
    const text = ki ? "Anleitung per KI neu schreiben lassen (ein KI-Aufruf)? Das Ergebnis wird als Entwurf gespeichert."
      : "Code-Vorschlag aus den Originalschritten erzeugen und als Entwurf speichern?";
    if (!await bestaetigen(text + (dirty || a.gespeichert ? " Die aktuelle Anleitung wird ersetzt." : ""), ki ? "✨ Umschreiben" : "Erzeugen")) return;
    const knopf = $(ki ? "#ki" : "#code", el);
    knopf.disabled = true; knopf.textContent = ki ? "KI schreibt …" : "…";
    try {
      uebernehmen(await post(`kochen/anleitung/${a.rezept_id}/generieren`, {ki}));
      toast(a.quelle === "ki" ? "KI-Entwurf gespeichert, bitte prüfen" : "Entwurf gespeichert");
    } finally { zeichnen(); }
  });

  zeichnen();
  const warnen = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", warnen);
  return () => window.removeEventListener("beforeunload", warnen);
}
