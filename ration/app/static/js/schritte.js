import {$, $$, esc, fmt} from "./lib.js";

// Schritt-Editor der Kochanleitung: Titel, Text, Zutaten je Schritt, Timer, Reihenfolge.
// Gemeinsam für die Anleitungsseite und die Import-Prüfung. `schritte` im gespeicherten Format
// ({titel, text, zutaten: [{zutat, anteil}], timer_s, …}) wird direkt verändert.
export const leererSchritt = () => ({titel: "", text: "", zutaten: [], timer_s: null, timer_name: null, parallel: false,
  timer_geschaetzt: false, original: [], foto: null});

// opts.zutaten(): [{index, name, menge, einheit, aus}] (aus = weggelassen, Chip ausgegraut)
// opts.original: Originalschritte (Texte) für die Zuordnung „aus Original n“
// opts.fotoHtml(s) / opts.onFoto(s): optionaler Foto-Teil je Schritt (nur bei gespeicherten Rezepten)
export function schritteEditor(box, schritte, {zutaten, original = [], onDirty = () => {}, fotoHtml = null, onFoto = null}) {
  const aktiv = () => zutaten().filter((z) => !z.aus);
  const fehlend = () => {
    const drin = new Set(schritte.flatMap((s) => s.zutaten.map((z) => z.zutat)));
    return aktiv().filter((z) => !drin.has(z.index));
  };
  const fehlendHtml = () => {
    const f = fehlend();
    return f.length ? `<p class="small"><span class="tag warn">${f.length} ohne Schritt</span> ${f.map((z) => esc(z.name)).join(", ")}</p>`
      : `<p class="muted small">Alle Zutaten sind einem Schritt zugeordnet.</p>`;
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
    <div class="chips">${zutaten().map((z) => {
      const t = s.zutaten.find((x) => x.zutat === z.index);
      if (z.aus && !t) return "";
      return `<button class="chip ${t ? "on" : ""} ${z.aus ? "aus" : ""}" data-z="${z.index}"
        title="${z.aus ? "weggelassen" : `${esc(fmt(z.menge))} ${esc(z.einheit ?? "")}`}">${esc(z.name)}${
        t && t.anteil < 0.999 ? ` <span class="tiny">${Math.round(t.anteil * 100)} %</span>` : ""}</button>`;
    }).join("")}</div>
    <div class="row" style="align-items:end">
      <div style="flex:0 0 110px"><label>Timer (Min.)</label><input data-f="timer_min" type="number" min="0" max="360" step="0.5"
        value="${s.timer_s ? +(s.timer_s / 60).toFixed(1) : ""}"></div>
      <div style="flex:1 1 140px"><label>Timer-Name</label><input data-f="timer_name" value="${esc(s.timer_name ?? "")}" placeholder="z. B. Nudeln"></div>
      <label class="nw" style="flex:0 0 auto"><input type="checkbox" data-f="parallel" ${s.parallel ? "checked" : ""}> läuft nebenher</label>
      ${s.timer_geschaetzt ? `<span class="tag warn" title="Dauer steht nicht im Original">≈ geschätzt</span>` : ""}
    </div>
    <div class="anl-foto">${fotoHtml ? fotoHtml(s) : ""}
      ${original.length ? `<span class="muted tiny">Original:</span> <span class="chips">${original.map((o, n) =>
        `<button class="chip ${s.original.includes(n) ? "on" : ""}" data-o="${n}" title="${esc(o.slice(0, 120))}">${n + 1}</button>`).join("")}</span>` : ""}</div></div>`;

  const geaendert = () => onDirty();
  const zeichnen = () => {
    box.innerHTML = `<div data-fehlend>${fehlendHtml()}</div>
      <div>${schritte.map(schrittHtml).join("")}</div>
      <button class="btn klein" data-neu>＋ Schritt</button>`;
    binden();
  };

  const binden = () => {
    $$(".anl-schritt", box).forEach((el) => {
      const i = +el.dataset.i, s = schritte[i];
      $$("[data-f]", el).forEach((inp) => inp.addEventListener(inp.type === "checkbox" ? "change" : "input", () => {
        const f = inp.dataset.f;
        if (f === "parallel") s.parallel = inp.checked;
        else if (f === "timer_min") { const m = parseFloat(inp.value); s.timer_s = m > 0 ? Math.round(m * 60) : null; s.timer_geschaetzt = false; }
        else s[f] = inp.value;
        geaendert();
      }));
      $$("[data-z]", el).forEach((c) => c.onclick = () => {
        const idx = +c.dataset.z, pos = s.zutaten.findIndex((z) => z.zutat === idx);
        if (pos >= 0) s.zutaten.splice(pos, 1); else s.zutaten.push({zutat: idx, anteil: 1});
        c.classList.toggle("on", pos < 0);
        $("[data-fehlend]", box).innerHTML = fehlendHtml();
        geaendert();
      });
      $$("[data-o]", el).forEach((c) => c.onclick = () => {
        const o = +c.dataset.o, pos = s.original.indexOf(o);
        if (pos >= 0) s.original.splice(pos, 1); else s.original.push(o);
        s.original.sort((x, y) => x - y); geaendert(); zeichnen();
      });
      const foto = $("[data-foto]", el);
      if (foto && onFoto) foto.onclick = () => onFoto(s);
      $$("[data-m]", el).forEach((b) => b.onclick = () => {
        const j = i + +b.dataset.m;
        [schritte[i], schritte[j]] = [schritte[j], schritte[i]]; geaendert(); zeichnen();
      });
      $("[data-plus]", el).onclick = () => { schritte.splice(i + 1, 0, leererSchritt()); geaendert(); zeichnen(); };
      $("[data-x]", el).onclick = () => { schritte.splice(i, 1); geaendert(); zeichnen(); };
    });
    $("[data-neu]", box).onclick = () => { schritte.push(leererSchritt()); geaendert(); zeichnen(); };
  };

  return {zeichnen, fehlend, aktualisieren: () => { const f = $("[data-fehlend]", box); if (f) f.innerHTML = fehlendHtml(); }};
}
