import {$, $$, api, bestaetigen, del, dialog, esc, fmt, fotoHochladen, fotoUrl, gehe, guarded, post, put, titel, toast} from "../lib.js";

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

  const fehlend = () => {
    const drin = new Set(schritte.flatMap((s) => s.zutaten.map((z) => z.zutat)));
    return a.zutaten.filter((z) => !drin.has(z.index));
  };
  const fehlendHtml = () => {
    const f = fehlend();
    return f.length ? `<p class="small"><span class="tag warn">${f.length} ohne Schritt</span> ${f.map((z) => esc(z.name)).join(", ")}</p>`
      : `<p class="muted small">Alle Zutaten sind einem Schritt zugeordnet.</p>`;
  };
  const markDirty = () => { dirty = true; $("#dirty", el).hidden = false; };

  // Schrittfoto: ausdrücklich gewählt, sonst die Fotos der zugeordneten Originalschritte
  const schrittFotos = (s) => s.foto ? [s.foto]
    : fotos.filter((f) => f.schritt != null && s.original.includes(f.schritt)).map((f) => f.id).slice(0, 3);
  const fotoHtml = (s) => {
    const ids = schrittFotos(s);
    return `<div class="anl-foto">${ids.map((id) => `<img src="${fotoUrl(id)}" alt="" class="schritt-mini">`).join("")}
      <button class="btn klein" data-foto>🖼 ${s.foto ? "Foto ändern" : ids.length ? "Foto (aus Original)" : "Foto zuordnen"}</button>
      ${rezept.schritte.length ? `<span class="muted tiny">Original:</span> <span class="chips">${rezept.schritte.map((o, n) =>
        `<button class="chip ${s.original.includes(n) ? "on" : ""}" data-o="${n}" title="${esc(o.slice(0, 120))}">${n + 1}</button>`).join("")}</span>` : ""}</div>`;
  };

  const schrittHtml = (s, i) => `<div class="anl-schritt" data-i="${i}">
    <div class="kopf"><b>${i + 1}.</b>
      <input data-f="titel" value="${esc(s.titel)}" placeholder="Titel, z. B. Wasser aufsetzen" aria-label="Titel">
      <div class="aktionen">
        <button class="btn klein" data-m="-1" ${i === 0 ? "disabled" : ""} title="nach oben">↑</button>
        <button class="btn klein" data-m="1" ${i === schritte.length - 1 ? "disabled" : ""} title="nach unten">↓</button>
        <button class="btn klein" data-plus title="Schritt darunter einfügen">＋</button>
        <button class="btn klein danger" data-x title="Schritt löschen">✕</button></div></div>
    <textarea data-f="text" rows="2" aria-label="Text">${esc(s.text)}</textarea>
    <div class="chips">${a.zutaten.map((z) => {
      const t = s.zutaten.find((x) => x.zutat === z.index);
      return `<button class="chip ${t ? "on" : ""}" data-z="${z.index}" title="${esc(fmt(z.menge))} ${esc(z.einheit)}">${esc(z.name)}${
        t && t.anteil < 0.999 ? ` <span class="tiny">${Math.round(t.anteil * 100)} %</span>` : ""}</button>`;
    }).join("")}</div>
    <div class="row" style="align-items:end">
      <div style="flex:0 0 110px"><label>Timer (Min.)</label><input data-f="timer_min" type="number" min="0" max="360" step="0.5"
        value="${s.timer_s ? +(s.timer_s / 60).toFixed(1) : ""}"></div>
      <div style="flex:1 1 140px"><label>Timer-Name</label><input data-f="timer_name" value="${esc(s.timer_name ?? "")}" placeholder="z. B. Nudeln"></div>
      <label class="nw" style="flex:0 0 auto"><input type="checkbox" data-f="parallel" ${s.parallel ? "checked" : ""}> läuft nebenher</label>
      ${s.timer_geschaetzt ? `<span class="tag warn" title="Dauer steht nicht im Original">≈ geschätzt</span>` : ""}
    </div>${fotoHtml(s)}</div>`;

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
      <div class="card"><div class="kopf"><h2>Schritte (${schritte.length})</h2>
        <div class="aktionen"><button class="btn klein" id="neu">＋ Schritt</button></div></div>
        <div id="fehlend">${fehlendHtml()}</div>
        <p class="muted tiny">Mengen im Text für ${a.portionen_basis} Portion(en) schreiben („250 g Hähnchen“, „2 EL Öl“, „1 Zwiebel“): Kochmodus und Rezeptbuch rechnen sie für andere Portionen mit um. Zeiten, °C und cm bleiben.</p>
        <div id="liste">${schritte.map(schrittHtml).join("")}</div></div>
      <div class="card"><h2>Original</h2>
        ${rezept.schritte.length ? `<ol class="schritte">${rezept.schritte.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : `<p class="muted">Keine Originalschritte.</p>`}
        <h3>Zutaten (${a.portionen_basis} P.)</h3><table>${a.zutaten.map((z) => `<tr><td>${esc(z.name)}${z.notiz ? ` <span class="muted tiny">${esc(z.notiz)}</span>` : ""}</td>
          <td class="nw right">${fmt(z.menge)} ${esc(z.einheit)}</td></tr>`).join("")}</table></div></div>`;
    binden();
  };

  const binden = () => {
    $$(".anl-schritt", el).forEach((box) => {
      const i = +box.dataset.i, s = schritte[i];
      $$("[data-f]", box).forEach((inp) => inp.addEventListener(inp.type === "checkbox" ? "change" : "input", () => {
        const f = inp.dataset.f;
        if (f === "parallel") s.parallel = inp.checked;
        else if (f === "timer_min") { const m = parseFloat(inp.value); s.timer_s = m > 0 ? Math.round(m * 60) : null; s.timer_geschaetzt = false; }
        else s[f] = inp.value;
        markDirty();
      }));
      $$("[data-z]", box).forEach((c) => c.onclick = () => {
        const idx = +c.dataset.z, pos = s.zutaten.findIndex((z) => z.zutat === idx);
        if (pos >= 0) s.zutaten.splice(pos, 1); else s.zutaten.push({zutat: idx, anteil: 1});
        c.classList.toggle("on", pos < 0);
        $("#fehlend", el).innerHTML = fehlendHtml();
        markDirty();
      });
      $$("[data-o]", box).forEach((c) => c.onclick = () => {
        const o = +c.dataset.o, pos = s.original.indexOf(o);
        if (pos >= 0) s.original.splice(pos, 1); else s.original.push(o);
        s.original.sort((x, y) => x - y); markDirty(); zeichnen();
      });
      $("[data-foto]", box).onclick = () => guarded(() => fotoWaehlen(s));
      $$("[data-m]", box).forEach((b) => b.onclick = () => {
        const j = i + +b.dataset.m;
        [schritte[i], schritte[j]] = [schritte[j], schritte[i]]; dirty = true; zeichnen();
      });
      $("[data-plus]", box).onclick = () => { schritte.splice(i + 1, 0, leer()); dirty = true; zeichnen(); };
      $("[data-x]", box).onclick = () => { schritte.splice(i, 1); dirty = true; zeichnen(); };
    });
    $("#neu", el).onclick = () => { schritte.push(leer()); dirty = true; zeichnen(); };
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

  const leer = () => ({titel: "", text: "", zutaten: [], timer_s: null, timer_name: null, parallel: false, timer_geschaetzt: false,
    original: [], foto: null});

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
    const f = fehlend();
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
