import {$, $$, api, bestaetigen, esc, fmt, formDialog, gehe, guarded, heute, patch, post, titel, toast} from "../lib.js";
import {nachDemKochen} from "./heute.js";

// Timer-Name aus dem Schritttitel, an einer Wortgrenze gekürzt
const kurz = (t) => { t = String(t ?? "").trim(); return t.length <= 24 ? t : t.slice(0, 24).replace(/\s+\S*$/, "") + " …"; };

// Kochmodus: ein Schritt groß, Zutaten abhaken, Timer per Knopf (HA-Timer, sonst Browser),
// Stand liegt als Kochsitzung auf dem Server, damit Tablet und Handy dasselbe sehen.

const mmss = (s) => {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
};
const dauerText = (s) => s >= 3600 ? `${Math.floor(s / 3600)} Std${s % 3600 ? ` ${Math.round(s % 3600 / 60)} Min` : ""}`
  : s % 60 ? `${mmss(s)} Min` : `${s / 60} Min`;

let audio = null;
const audioEntsperren = () => { try { audio ??= new AudioContext(); audio.resume?.(); } catch { /* egal */ } };
function klingeln() {
  try {
    audioEntsperren();
    for (let k = 0; k < 3; k++) {
      const o = audio.createOscillator(), g = audio.createGain(), t = audio.currentTime + k * 0.35;
      o.frequency.value = 880; o.connect(g); g.connect(audio.destination);
      g.gain.setValueAtTime(0.25, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.3);
      o.start(t); o.stop(t + 0.3);
    }
  } catch { /* ohne Ton */ }
  navigator.vibrate?.([300, 150, 300, 150, 300]);
}

export default async function render(el, {arg, params}) {
  if (!arg) return auswahl(el);
  // Testlauf (?test=1): Sitzung nur im Browser, nur Browser-Timer, keine Buchung, nichts gespeichert
  const test = params.test === "1";
  let zeitraffer = test;
  const opts = {rezept_id: arg, neu: params.neu === "1"};
  if (params.portionen) opts.portionen = +params.portionen;
  let sitz;
  if (test) {
    const r = await api(`rezepte/${encodeURIComponent(arg)}${params.portionen ? `?portionen=${+params.portionen}` : ""}`);
    sitz = {id: null, rezept_id: arg, portionen: r.portionen, schritt: 0, erledigt: [], abgehakt: [], timer: [], aktiv: true};
  } else {
    sitz = await post("kochen/sitzung", opts);
    if (params.neu || params.portionen) history.replaceState(null, "", `#/kochen/${encodeURIComponent(arg)}`);
  }
  let [anl, einst, timerInfo] = await Promise.all([api(`kochen/anleitung/${encodeURIComponent(arg)}?portionen=${sitz.portionen}`),
    api("einstellungen"), test ? {timer: []} : api("kochen/timer").catch(() => ({timer: []}))]);
  titel(`${test ? "Testlauf" : "Kochen"}: ${anl.name}`);
  document.body.classList.add("kochmodus");
  const pflicht = einst.kochen?.zutaten_pflicht !== false;
  const geklingelt = new Set(), weg = new Set();
  let stand = "";

  const N = () => anl.schritte.length;
  const speichern = async (felder) => { sitz = test ? {...sitz, ...felder} : await patch(`kochen/sitzung/${sitz.id}`, felder); };

  // --- Timer -------------------------------------------------------------------------------
  const timerZustand = (e) => {
    const jetzt = Date.now();
    if (e.entity_id.startsWith("browser:")) {
      if (e.pausiert_rest != null) return {rest: e.pausiert_rest, art: "pausiert"};
      const rest = (Date.parse(e.ende) - jetzt) / 1000;
      return {rest, art: rest <= 0 ? "klingelt" : "laeuft"};
    }
    const ha = timerInfo.timer.find((t) => t.entity_id === e.entity_id);
    if (ha?.state === "paused") return {rest: ha.rest_s ?? 0, art: "pausiert"};
    if (ha?.state === "active") {
      const rest = (Date.parse(ha.ende || e.ende) - jetzt) / 1000;
      return {rest, art: rest <= 0 ? "klingelt" : "laeuft"};
    }
    // idle: abgelaufen oder in HA abgebrochen
    return Date.parse(e.ende) <= jetzt + 3000 ? {rest: 0, art: "klingelt"} : {rest: 0, art: "weg"};
  };
  const timerZeichnen = () => {
    const tl = $("#tl", el);
    if (!tl) return;
    const liste = sitz.timer.filter((e) => !weg.has(e.entity_id)).map((e) => ({e, ...timerZustand(e)}));
    for (const t of liste) {
      if (t.art === "klingelt" && !geklingelt.has(t.e.entity_id + t.e.ende)) {
        geklingelt.add(t.e.entity_id + t.e.ende); klingeln(); toast(`⏰ ${t.e.name} ist fertig!`);
      }
      if (t.art === "weg") weg.add(t.e.entity_id);
    }
    tl.innerHTML = liste.filter((t) => t.art !== "weg").map(({e, rest, art}) => `<div class="timer-chip ${art}">
      <span>${esc(e.name)}</span><span class="zeit">${art === "klingelt" ? "fertig!" : mmss(rest)}</span>
      ${art === "klingelt" ? `<button class="btn primary klein" data-t="ok" data-id="${esc(e.entity_id)}">OK</button>`
        : `<button class="btn klein" data-t="${art === "pausiert" ? "weiter" : "pause"}" data-id="${esc(e.entity_id)}">${art === "pausiert" ? "▶" : "⏸"}</button>
           <button class="btn klein" data-t="weg" data-id="${esc(e.entity_id)}" title="Abbrechen">✕</button>`}</div>`).join("");
  };
  const browserTimer = async (sek, name) => {
    const e = {entity_id: `browser:${Date.now()}`, name, ende: new Date(Date.now() + sek * 1000).toISOString(), schritt: sitz.schritt, dauer_s: sek};
    await speichern({timer: [...sitz.timer, e]});
  };
  const timerStarten = async (sek, name) => {
    audioEntsperren();
    if (test) {
      const echt = zeitraffer ? Math.max(3, Math.round(sek / 60)) : sek;
      await browserTimer(echt, name);
      toast(`🧪 ⏱ ${name}: ${dauerText(sek)}${zeitraffer ? ` im Zeitraffer (${echt} s)` : ""}, kein HA-Timer`);
    } else {
      try {
        const r = await post("kochen/timer", {sekunden: sek, name, sitzung_id: sitz.id, schritt: sitz.schritt});
        toast(`⏱ ${name}: ${dauerText(sek)} auf ${r.timer_name}`);
      } catch (err) {
        await browserTimer(sek, name);
        toast(`⏱ ${name} läuft hier im Browser (${err.message})`);
      }
    }
    await abgleichen(true);
  };
  const timerAktion = async (art, id) => {
    const e = sitz.timer.find((x) => x.entity_id === id);
    if (!e) return;
    const ohne = sitz.timer.filter((x) => x.entity_id !== id);
    if (id.startsWith("browser:")) {
      if (art === "ok" || art === "weg") await speichern({timer: ohne});
      if (art === "pause") await speichern({timer: [...ohne, {...e, pausiert_rest: timerZustand(e).rest}]});
      if (art === "weiter") { const {pausiert_rest: r, ...rest} = e; await speichern({timer: [...ohne, {...rest, ende: new Date(Date.now() + r * 1000).toISOString()}]}); }
    } else {
      if (art === "ok") await speichern({timer: ohne});
      else await post(`kochen/timer/${id}/${{pause: "pausieren", weiter: "fortsetzen", weg: "abbrechen"}[art]}`);
    }
    await abgleichen(true);
  };

  // --- Abgleich mit dem Server (anderes Gerät, HA-Timer) --------------------------------------
  const abgleichen = async (erzwingen = false) => {
    if (!el.isConnected) return;
    if (test) { if (erzwingen) zeichnen(); else timerZeichnen(); return; }
    const [s, t] = await Promise.all([api(`kochen/sitzung/${sitz.id}`), api("kochen/timer").catch(() => timerInfo)]);
    timerInfo = t;
    const neu = JSON.stringify([s.schritt, s.abgehakt, s.erledigt, s.aktiv]);
    sitz = s;
    if (erzwingen || neu !== stand) { if (!$("#dialog").open) zeichnen(); } else timerZeichnen();
  };

  // --- Darstellung ---------------------------------------------------------------------------
  const zeichnen = () => {
    stand = JSON.stringify([sitz.schritt, sitz.abgehakt, sitz.erledigt, sitz.aktiv]);
    const i = Math.min(sitz.schritt, N()), s = anl.schritte[i], drin = new Set(sitz.abgehakt);
    const pct = N() ? Math.round(100 * Math.min(i, N()) / N()) : 0;
    const zutatDrin = (idx) => anl.schritte.every((x, k) => !x.zutaten.some((z) => z.index === idx) || drin.has(`${k}:${idx}`));
    const testBanner = test ? `<div class="card small testlauf"><span><b>🧪 Testlauf</b> · keine HA-Timer, keine Buchung, nichts wird gespeichert.</span>
      <label class="inline" style="margin:0 0 0 8px"><input type="checkbox" id="raffer" ${zeitraffer ? "checked" : ""}> ⏩ Zeitraffer (1 Min = 1 s)</label>
      <a class="btn klein" href="#/kochen/${esc(anl.rezept_id)}?portionen=${sitz.portionen}">Echt kochen</a></div>` : "";
    const banner = testBanner + (anl.status === "keine" || anl.status === "entwurf" || anl.veraltet || anl.hinweise.length ? `<div class="card small">
      ${anl.status === "keine" ? `Diese Anleitung kommt automatisch aus den Originalschritten.
        ${test ? "" : `<button class="btn klein primary" id="ki">✨ Mit KI in Koch-Schritte umschreiben</button>`}`
        : anl.status === "entwurf" ? `<span class="tag warn">${anl.quelle === "ki" ? "KI-Entwurf" : "Entwurf"}</span> noch nicht geprüft · <a href="#/rezept/${esc(anl.rezept_id)}/anleitung">prüfen</a>` : ""}
      ${anl.veraltet ? `<div class="soon">Das Rezept wurde seitdem geändert, die Anleitung ist evtl. veraltet.</div>` : ""}
      ${anl.hinweise.map((h) => `<div class="muted tiny">${esc(h)}</div>`).join("")}</div>` : "");
    el.innerHTML = `<div class="koch-kopf"><h2>${esc(anl.name)}</h2><span class="muted">${sitz.portionen} Portion(en)</span>
        <div class="fortschritt"><div style="width:${pct}%"></div></div><span class="muted small">${Math.min(i + 1, N())}/${N()}</span>
        <a class="btn klein" href="#/rezept/${esc(anl.rezept_id)}/anleitung" title="Anleitung bearbeiten">✎</a>
        <button class="btn klein" id="neustart" title="Von vorn">↺</button></div>
      <div class="timerleiste" id="tl"></div>${banner}
      ${i < N() ? `<div class="koch-grid"><div class="card schritt-gross">
          <div class="nr">Schritt ${i + 1} von ${N()}${s.parallel ? ` · <span class="tag good">läuft nebenher</span>` : ""}</div>
          <h2>${esc(s.titel)}</h2>${s.text && s.text !== s.titel ? `<p class="text">${esc(s.text)}</p>` : ""}
          ${s.zutaten.map((z) => `<label class="zutat-check ${drin.has(`${i}:${z.index}`) ? "drin" : ""}">
            <input type="checkbox" data-z="${i}:${z.index}" ${drin.has(`${i}:${z.index}`) ? "checked" : ""}>
            <span class="menge">${fmt(z.menge)} ${esc(z.einheit)}</span><span class="was">${esc(z.name)}${z.notiz ? ` <span class="muted small">(${esc(z.notiz)})</span>` : ""}${
              z.frueher ? ` <span class="muted small">aus Schritt ${z.frueher}</span>` : ""}</span></label>`).join("")}
          <div class="row" style="margin-top:6px">
            ${s.timer_s ? `<button class="btn primary gross" id="timer">⏱ ${s.timer_geschaetzt ? "≈ " : ""}${dauerText(s.timer_s)}${s.timer_name ? ` ${esc(s.timer_name)}` : ""} starten</button>` : ""}
            <button class="btn gross" id="eigener">+ Timer</button></div>
          <div class="koch-nav"><button class="btn" id="zurueck" ${i === 0 ? "disabled" : ""}>◀ Zurück</button>
            <button class="btn primary" id="weiter">${i === N() - 1 ? "Fertig ✓" : "Weiter ▶"}</button></div>
          ${anl.schritte[i + 1] ? `<div class="naechster">Als Nächstes: <b>${esc(anl.schritte[i + 1].titel)}</b></div>` : ""}</div>
        <div><div class="card"><h2>Schritte</h2><ol class="schrittliste">${anl.schritte.map((x, k) => `<li data-s="${k}"
            class="${k === i ? "akt" : sitz.erledigt.includes(k) ? "fertig" : ""}"><span class="n">${k + 1}</span><span>${esc(x.titel)}${x.timer_s ? " ⏱" : ""}</span></li>`).join("")}</ol></div>
          <div class="card"><h2>Alle Zutaten</h2><table>${anl.zutaten.map((z) => `<tr class="${zutatDrin(z.index) ? "muted" : ""}">
            <td>${zutatDrin(z.index) ? "✓" : ""}</td><td>${esc(z.name)}</td><td class="right nw">${fmt(z.menge)} ${esc(z.einheit)}</td></tr>`).join("")}</table>
            ${anl.fehlend.length ? `<p class="soon tiny">In keinem Schritt: ${anl.fehlend.map(esc).join(", ")}</p>` : ""}</div></div></div>`
      : test ? `<div class="card schritt-gross" id="fertig"><h2>Testlauf fertig 🧪</h2>
          <p class="muted small">Echt gekocht würde „Gekocht buchen“ das hier abbuchen (Rezept × ${sitz.portionen} Portion(en)). Gebucht wurde nichts.</p>
          <div id="vorschau" class="muted small">Lade Bestand …</div>
          <div class="row" style="margin-top:10px"><button class="btn" id="nochmal">↺ Nochmal testen</button>
            <a class="btn primary gross" href="#/kochen/${esc(anl.rezept_id)}?portionen=${sitz.portionen}">👩‍🍳 Jetzt echt kochen</a>
            <button class="btn" id="zurueck">◀ Zurück</button></div></div>`
      : `<div class="card schritt-gross" id="fertig"><h2>Fertig! 🎉</h2>
          <div class="row"><div><label>Gekocht (Portionen)</label><input id="pg" type="number" min="1" max="24" value="${sitz.portionen}"></div>
            <div><label>Gegessen (Portionen)</label><input id="pe" type="number" min="0" max="24" value="${Math.min(sitz.portionen, 1)}"></div></div>
          <p class="muted small">Bucht die Zutaten ab, der Rest wird als Rest eingebucht.</p>
          <div class="row"><button class="btn primary gross" id="buchen">Gekocht buchen</button>
            <button class="btn" id="ohne">Ohne Buchen beenden</button><button class="btn" id="zurueck">◀ Zurück</button></div>
          <div id="nach"></div></div>`}`;
    binden();
    timerZeichnen();
  };

  const binden = () => {
    $("#tl", el).onclick = (ev) => { const b = ev.target.closest("[data-t]"); if (b) guarded(() => timerAktion(b.dataset.t, b.dataset.id)); };
    const raffer = $("#raffer", el);
    if (raffer) raffer.onchange = () => { zeitraffer = raffer.checked; };
    const nochmal = $("#nochmal", el);
    if (nochmal) nochmal.onclick = () => guarded(async () => { await speichern({schritt: 0, erledigt: [], abgehakt: [], timer: []}); zeichnen(); });
    const vorschau = $("#vorschau", el);
    if (vorschau) guarded(async () => {
      const r = await api(`rezepte/${encodeURIComponent(anl.rezept_id)}?portionen=${sitz.portionen}`);
      vorschau.innerHTML = `<table>${r.zutaten.map((z) => `<tr><td><span class="dot ${z.ampel}"></span>${esc(z.name)}</td>
        <td class="right nw">${fmt(z.menge)} ${esc(z.einheit)}</td><td class="right nw muted tiny">da: ${fmt(z.bestand)}</td></tr>`).join("")}</table>`;
    });
    $("#neustart", el).onclick = () => guarded(async () => {
      if (test) { await speichern({schritt: 0, erledigt: [], abgehakt: [], timer: []}); zeichnen(); return; }
      if (!await bestaetigen("Von vorn anfangen? Abgehakte Zutaten und Schritte werden zurückgesetzt.", "Neu starten")) return;
      sitz = await post("kochen/sitzung", {rezept_id: anl.rezept_id, portionen: sitz.portionen, neu: true}); zeichnen();
    });
    const ki = $("#ki", el);
    if (ki) ki.onclick = () => guarded(async () => {
      ki.disabled = true; ki.textContent = "KI schreibt um … (dauert etwa 20 s)";
      try {
        await post(`kochen/anleitung/${encodeURIComponent(anl.rezept_id)}/generieren`, {ki: true});
        anl = await api(`kochen/anleitung/${encodeURIComponent(anl.rezept_id)}?portionen=${sitz.portionen}`);
        await speichern({schritt: 0, erledigt: [], abgehakt: []});
        toast(anl.quelle === "ki" ? `In ${N()} Schritte umgeschrieben` : "KI nicht erreichbar, Code-Vorschlag bleibt");
      } finally { zeichnen(); }
    });
    $$("[data-z]", el).forEach((c) => c.onchange = () => guarded(async () => {
      audioEntsperren();
      const neu = new Set(sitz.abgehakt);
      c.checked ? neu.add(c.dataset.z) : neu.delete(c.dataset.z);
      c.closest(".zutat-check").classList.toggle("drin", c.checked);
      await speichern({abgehakt: [...neu]}); zeichnen();
    }));
    $$("[data-s]", el).forEach((li) => li.onclick = () => guarded(async () => { await speichern({schritt: +li.dataset.s}); zeichnen(); }));
    const t = $("#timer", el), s = anl.schritte[sitz.schritt];
    if (t) t.onclick = () => guarded(() => timerStarten(s.timer_s, s.timer_name || kurz(s.titel)));
    const eig = $("#eigener", el);
    if (eig) eig.onclick = () => guarded(async () => {
      audioEntsperren();
      const w = await formDialog(`<h3>Timer stellen</h3><div class="row"><div><label>Minuten</label><input name="min" type="number" min="0.5" step="0.5" value="${s?.timer_s ? s.timer_s / 60 : 5}" required autofocus></div>
        <div><label>Name</label><input name="name" value="${esc(s?.timer_name || kurz(s?.titel) || "Timer")}"></div></div>
        <div class="chips">${[1, 3, 5, 8, 10, 12, 15, 20, 30].map((m) => `<button type="button" class="chip" onclick="this.form.min.value=${m}">${m}′</button>`).join("")}</div>`, "Starten");
      if (w && +w.min > 0) await timerStarten(Math.round(+w.min * 60), w.name || "Timer");
    });
    const zur = $("#zurueck", el);
    if (zur) zur.onclick = () => guarded(async () => { await speichern({schritt: Math.max(0, Math.min(sitz.schritt, N()) - 1)}); zeichnen(); });
    const wei = $("#weiter", el);
    if (wei) wei.onclick = () => guarded(weiter);
    const buchen = $("#buchen", el);
    if (buchen) buchen.onclick = () => guarded(async () => {
      const e = await post("gekocht", {rezept_id: anl.rezept_id, portionen_gekocht: +$("#pg", el).value, portionen_gegessen: +$("#pe", el).value});
      const fehl = e.fehlend.map((p) => `${p.name} ${fmt(p.menge)} ${p.einheit}`).join(", ");
      toast(`${e.abgebucht.length} Zutaten abgebucht` + (e.rest ? ` · ${fmt(e.rest.menge)} Portion(en) Rest` : "") + (fehl ? ` · fehlte: ${fehl}` : ""));
      buchen.disabled = true; $("#ohne", el).textContent = "Schließen";
      await speichern({aktiv: false});
      nachDemKochen($("#nach", el), e);
    });
    const ohne = $("#ohne", el);
    if (ohne) ohne.onclick = () => guarded(async () => { await speichern({aktiv: false}); gehe(""); });
  };

  const weiter = async () => {
    const i = sitz.schritt, s = anl.schritte[i];
    const offen = s.zutaten.filter((z) => !sitz.abgehakt.includes(`${i}:${z.index}`));
    if (pflicht && offen.length && !await bestaetigen(`Noch nicht abgehakt: <b>${offen.map((z) => esc(z.name)).join(", ")}</b>. Trotzdem weiter?`, "Trotzdem weiter")) return;
    await speichern({schritt: i + 1, erledigt: [...new Set([...sitz.erledigt, i])]});
    zeichnen(); window.scrollTo(0, 0);
  };

  // --- Rahmen: Takt, Abgleich, Tastatur, Bildschirm an ------------------------------------------
  zeichnen();
  const takt = setInterval(timerZeichnen, 1000);
  const abgleich = setInterval(() => guarded(() => abgleichen()), 4000);
  const tasten = (ev) => {
    if (ev.target.closest("input,textarea,select") || $("#dialog").open) return;
    if (ev.key === "ArrowRight") $("#weiter", el)?.click();
    if (ev.key === "ArrowLeft") $("#zurueck", el)?.click();
  };
  document.addEventListener("keydown", tasten);
  let sperre = null;
  const wach = async () => { try { if (document.visibilityState === "visible") sperre = await navigator.wakeLock?.request("screen"); } catch { /* egal */ } };
  wach();
  document.addEventListener("visibilitychange", wach);
  return () => {
    clearInterval(takt); clearInterval(abgleich);
    document.removeEventListener("keydown", tasten);
    document.removeEventListener("visibilitychange", wach);
    sperre?.release?.().catch(() => {});
    document.body.classList.remove("kochmodus");
  };
}

async function auswahl(el) {
  const [laufend, h, buch] = await Promise.all([api("kochen/sitzungen"), api("heute"), api("rezeptbuch")]);
  const name = Object.fromEntries(buch.map((r) => [r.id, r.name]));
  const kochbar = buch.filter((r) => r.kochbar);
  el.innerHTML = `<div class="grid">
    ${laufend.length ? `<div class="card"><h2>Weiterkochen</h2><table>${laufend.map((k) => `<tr><td><b>${esc(name[k.rezept_id] ?? k.rezept_id)}</b>
      <div class="muted tiny">Schritt ${k.schritt + 1} · ${k.portionen} P.${k.timer.length ? ` · ⏱ ${k.timer.length}` : ""}</div></td>
      <td class="right"><a class="btn primary klein" href="#/kochen/${esc(k.rezept_id)}">Weiter</a></td></tr>`).join("")}</table></div>` : ""}
    <div class="card"><h2>Heute</h2>${h.rezept ? `<div class="zahl" style="font-size:20px">${esc(h.rezept.name)}</div>
        <p class="muted">${h.rezept.portionen} Portion(en)${h.status === "gekocht" ? " · ✓ gekocht" : ""}</p>
        <a class="btn primary gross" href="#/kochen/${esc(h.rezept.id)}?portionen=${h.rezept.portionen}">👩‍🍳 Kochmodus starten</a>`
      : `<p class="muted">Heute ist nichts geplant (${esc(heute())}).</p>`}</div>
    <div class="card"><h2>Jetzt kochbar</h2>${kochbar.length ? `<table>${kochbar.slice(0, 12).map((r) => `<tr><td>${esc(r.name)}</td>
      <td class="right nw"><a class="btn klein" href="#/kochen/${esc(r.id)}?test=1" title="Testlauf ohne Timer und Buchung">🧪</a>
        <a class="btn klein" href="#/kochen/${esc(r.id)}">Kochen</a></td></tr>`).join("")}</table>`
      : `<p class="muted">Nichts komplett vorrätig. <a href="#/rezepte">Rezeptbuch</a></p>`}</div></div>`;
}
