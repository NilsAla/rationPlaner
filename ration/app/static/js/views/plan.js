import {$, $$, api, del, dialog, dmy, esc, eur, fmt, fotoUrl, guarded, heute, plusTage, post, put, toast, wt, REGLER, WOCHENTAGE} from "../lib.js";

// Wochenplan: Kalender mit + (Rezept suchen), × (entfernen), Antippen (ändern) und Ziehen
// (verschieben, auf belegtem Tag tauschen; Handy: lange drücken). Darunter der Planer, der
// für ausgewählte Tage einen Vorschlag macht.
const state = {auswahl: null, monat: 0, personen: {}, gewaehlt: null};
// Handy: kompakter Monatskalender (Fotos statt Namen) und darunter der gewählte Tag
const KOMPAKT = "(max-width: 700px)";
const wdOf = (d) => (new Date(d + "T12:00:00").getDay() + 6) % 7;
// Personen eines Tages: gewählt, sonst Standard je Wochentag, sonst Portionen-Standard
const personenStandard = (planer, d) => planer.personen_je_wochentag?.[String(wdOf(d))] ?? planer.portionen;
const tagLang = (d) => new Date(d + "T12:00:00").toLocaleDateString("de-DE", {weekday: "long", day: "numeric", month: "long"});

export default async function render(el, ctx = {}) {
  const ein = ctx.params?.rezept ? {id: ctx.params.rezept, portionen: +ctx.params.portionen || null} : null;
  const h = heute();
  const montag = plusTage(h, -wdOf(h) + state.monat * 28);
  const [einst, plan, buch, laeufe] = await Promise.all([
    api("einstellungen"), api(`plan?von=${montag}&tage=35`), api("rezeptbuch"), api("plan/laeufe")]);
  const rezept = Object.fromEntries(buch.map((r) => [r.id, r]));
  const name = Object.fromEntries(buch.map((r) => [r.id, r.name]));
  const byDay = Object.fromEntries(plan.map((p) => [p.datum, p]));
  const tage = [...Array(35)].map((_, i) => plusTage(montag, i));
  const kochtage = einst.planer.kochtage;
  const zukunft = [...Array(14)].map((_, i) => plusTage(h, i));
  if (!state.auswahl) state.auswahl = new Set(zukunft.slice(0, 7).filter((d) => kochtage.includes(wdOf(d)) && !byDay[d]));
  const prefs = einst.praeferenzen;
  const alleTags = [...new Set(buch.flatMap((r) => r.tags))].sort();
  const neu = () => render(el, ctx);

  const zelle = (d) => {
    const p = byDay[d], vergangen = d < h, r = p && rezept[p.rezept_id];
    const cls = ["tag-zelle", vergangen ? "vergangen" : "", d === h ? "heute" : "", p ? "belegt" : "frei",
      d === state.gewaehlt ? "gewaehlt" : "",
      !kochtage.includes(wdOf(d)) ? "aus" : "", d.slice(8) === "01" ? "monatsanfang" : ""].join(" ");
    return `<div class="${cls}" data-tag="${d}">
      <div class="tz-kopf"><span class="wt-kurz">${WOCHENTAGE[wdOf(d)]}</span><span class="nr">${Number(d.slice(8))}.${
        d.slice(8) === "01" || d === tage[0] ? " " + new Date(d + "T12:00:00").toLocaleDateString("de-DE", {month: "short"}) : ""}</span>
        ${d === h ? `<span class="heute-tag">heute</span>` : ""}</div>
      ${p ? `<div class="gericht ${p.status === "gekocht" ? "gekocht" : ""}" data-g="${d}" ${vergangen ? "" : `title="Antippen: ändern · Ziehen: verschieben"`}>
          ${r?.foto ? `<img src="${fotoUrl(r.foto)}" alt="" draggable="false">` : `<span class="g-ico">🍽️</span>`}
          <span class="g-kurz">${esc((name[p.rezept_id] ?? "?").split(/[\s-]/)[0])}</span>
          <span class="g-text"><span class="g-name">${esc(name[p.rezept_id] ?? "?")}</span><span class="g-p">${p.portionen} P.${p.status === "gekocht" ? " · gekocht" : ""}</span></span>
          ${vergangen ? "" : `<button class="g-weg" data-weg="${d}" title="Entfernen" aria-label="Entfernen">×</button>`}</div>`
        : vergangen ? "" : `<button class="plus" data-plus="${d}" aria-label="Gericht hinzufügen">${ein ? "＋ hier einplanen" : "＋"}</button>`}
    </div>`;
  };

  el.innerHTML = `${ein ? `<div class="card" style="border-color:var(--accent)"><b>„${esc(name[ein.id] ?? ein.id)}“ einplanen:</b>
      <span class="muted">Tag antippen.</span> <a class="btn klein" href="#/plan">Abbrechen</a></div>` : ""}
    <div class="card"><div class="kopf"><h2>Kalender</h2><div class="aktionen">
      <button class="btn klein" data-m="-1" aria-label="Zurück">◀</button><button class="btn klein" data-m="0">heute</button><button class="btn klein" data-m="1" aria-label="Weiter">▶</button></div></div>
    <p class="muted small hinweis-gross">＋ fügt ein Gericht hinzu, × entfernt es. Gericht ziehen zum Verschieben, auf einen belegten Tag zum Tauschen.</p>
    <p class="muted small hinweis-klein">Tag antippen · ＋ fügt hinzu · Gericht lange drücken und ziehen zum Verschieben.</p>
    <div class="kalender ${state.monat === 0 ? "ab-heute" : ""} ${ein ? "einplanen" : ""}">${WOCHENTAGE.map((w) => `<div class="wt">${w}</div>`).join("")}
      ${tage.map(zelle).join("")}</div>
    <div id="tagdetail"></div></div>

    <div class="grid">
    <div class="card"><h2>Plan vorschlagen</h2>
      <p class="muted small">Tage wählen, für die der Planer Gerichte vorschlägt. Belegte Tage (·) bleiben fest, außer du planst sie neu.</p>
      <div class="chips" style="margin-bottom:8px">
        <button class="chip" data-schnell="7">nächste 7 Tage</button><button class="chip" data-schnell="werktage">Mo–Fr</button>
        <button class="chip" data-schnell="wochenende">Wochenende</button><button class="chip" data-schnell="frei">alle freien</button>
        <button class="chip" data-schnell="leer">keine</button></div>
      <div class="tage-wahl">${zukunft.map((d) => `<button class="tag-chip ${state.auswahl.has(d) ? "on" : ""} ${byDay[d] ? "belegt" : ""}" data-wahl="${d}">
        <small>${WOCHENTAGE[wdOf(d)]}</small><b>${Number(d.slice(8))}</b>${byDay[d] ? "<i>·</i>" : ""}</button>`).join("")}</div>
      ${state.auswahl.size ? `<label>Personen je Tag</label><div class="personen-liste">${[...state.auswahl].sort().map((d) =>
        `<label class="person-tag"><span>${wt(d)}</span><input type="number" min="1" max="12" data-pers="${d}"
          value="${state.personen[d] ?? personenStandard(einst.planer, d)}"></label>`).join("")}</div>` : ""}
      <details style="margin-top:10px"><summary>Wünsche und Optionen</summary>
      <div class="row"><div><label>Max. ungetestete</label><input id="mu" type="number" min="0" max="7" value="${einst.planer.max_ungetestet}"></div></div>
      ${REGLER.map(([k, t, l, r]) => `<div class="regler"><span class="name">${t}</span>
        <input type="range" min="-1" max="1" step="0.25" id="r_${k}" value="${prefs[k]}">
        <div class="enden"><span>${l}</span><span>${r}</span></div></div>`).join("")}
      ${alleTags.length ? `<label>Nie planen (Tags)</label><div class="chips">${alleTags.map((t) =>
        `<button class="chip ${prefs.ausschluss_tags.includes(t) ? "on" : ""}" data-aus="${esc(t)}">${esc(t)}</button>`).join("")}</div>` : ""}
      <label class="inline"><input type="checkbox" id="ki" ${einst.planer.ki ? "checked" : ""}> KI wählt aus (sonst nur der Code)</label>
      <label class="inline"><input type="checkbox" id="ueb"> Schon geplante Tage in der Auswahl neu planen</label>
      <label class="inline"><input type="checkbox" id="std"> Regler als Standard speichern</label></details>
      <button class="btn primary voll" id="start" ${state.auswahl.size ? "" : "disabled"}>Plan für ${state.auswahl.size} ${state.auswahl.size === 1 ? "Tag" : "Tage"} vorschlagen</button>
      <p class="muted tiny">Aufwand je Wochentag und Standard-Kochtage stellst du in den <a href="#/einstellungen">Einstellungen</a> ein.</p></div>
    <div id="ergebnis"></div></div>`;

  // --- Kalender: hinzufügen, entfernen, ändern, verschieben ---------------------------
  const kal = $(".kalender", el);
  $$("[data-plus]", el).forEach((b) => b.onclick = () => guarded(async () => {
    const d = b.dataset.plus;
    if (ein) {
      await put(`plan/${d}`, {rezept_id: ein.id, portionen: ein.portionen ?? personenStandard(einst.planer, d)});
      toast(`Eingeplant am ${wt(d)}`); location.hash = "#/plan"; return;
    }
    const w = await rezeptWaehlen(d, buch, personenStandard(einst.planer, d));
    if (!w) return;
    await put(`plan/${d}`, {rezept_id: w.id, portionen: w.portionen});
    toast(`${name[w.id]} am ${wt(d)}`); neu();
  }));
  $$("[data-weg]", el).forEach((b) => b.onclick = (ev) => guarded(async () => {
    ev.stopPropagation();
    const d = b.dataset.weg;
    await del(`plan/${d}`);
    toast(`${name[byDay[d].rezept_id] ?? "Gericht"} am ${wt(d)} entfernt`); neu();
  }));
  // Handy: gewählter Tag unter dem Kalender (Name, Portionen, Kochen, Ändern, Entfernen)
  const kompakt = () => matchMedia(KOMPAKT).matches;
  if (!state.gewaehlt || !tage.includes(state.gewaehlt)) state.gewaehlt = tage.includes(h) ? h : tage[0];
  const detail = () => {
    const box = $("#tagdetail", el), d = state.gewaehlt, p = byDay[d], r = p && rezept[p.rezept_id];
    $$(".tag-zelle", kal).forEach((z) => z.classList.toggle("gewaehlt", z.dataset.tag === d));
    box.innerHTML = `<div class="tagdetail">
      <div class="td-kopf"><b>${esc(tagLang(d))}</b>${d === h ? ` <span class="heute-tag">heute</span>` : ""}</div>
      ${p ? `<div class="td-gericht">
          <a href="#/rezept/${esc(p.rezept_id)}" class="td-bild">${r?.foto ? `<img src="${fotoUrl(r.foto)}" alt="">` : "🍽️"}</a>
          <div class="td-text"><a href="#/rezept/${esc(p.rezept_id)}"><b>${esc(name[p.rezept_id] ?? "?")}</b></a>
            <span class="muted small">${p.portionen} Portionen${p.status === "gekocht" ? " · gekocht" : ""}${r && !r.kochbar ? ` · <span class="soon">${r.fehlend.length} fehlt</span>` : r ? ` · <span class="ok">alles da</span>` : ""}</span></div></div>
        ${d >= h ? `<div class="td-aktionen">
          ${d === h && p.status !== "gekocht" ? `<a class="btn primary" href="#/kochen">👩‍🍳 Kochen</a>` : ""}
          <button class="btn" data-td="aendern">✎ Ändern</button><button class="btn danger" data-td="weg">× Entfernen</button></div>` : ""}`
      : d >= h ? `<button class="btn primary voll" data-td="plus" style="margin-top:6px">＋ Gericht hinzufügen</button>`
      : `<p class="muted small">Nichts geplant.</p>`}</div>`;
    $$("[data-td]", box).forEach((b) => b.onclick = () => guarded(async () => {
      const a = b.dataset.td;
      if (a === "plus") return $(`[data-plus="${d}"]`, kal)?.click();
      if (a === "weg") { await del(`plan/${d}`); toast(`${name[p.rezept_id] ?? "Gericht"} am ${wt(d)} entfernt`); return neu(); }
      if (await tagBearbeiten(d, p, buch, name, zukunft)) neu();
    }));
  };
  detail();
  // Handy: Antippen eines Tages wählt ihn (leere Zukunftstage öffnen direkt die Suche über ＋)
  kal.addEventListener("click", (e) => {
    if (!kompakt() || e.target.closest("button,[data-g]")) return;
    const z = e.target.closest("[data-tag]");
    if (z) { state.gewaehlt = z.dataset.tag; detail(); }
  });
  ziehen(kal, {
    klick: (d) => guarded(async () => {
      if (kompakt()) { state.gewaehlt = d; detail(); return; }
      if (d < h) { location.hash = `#/rezept/${byDay[d].rezept_id}`; return; }
      if (await tagBearbeiten(d, byDay[d], buch, name, zukunft)) neu();
    }),
    ablegen: (von, ziel) => guarded(async () => {
      const r = await post(`plan/${von}/verschieben`, {ziel});
      toast(r.getauscht ? `Getauscht: ${wt(von)} ⇄ ${wt(ziel)}` : `Verschoben auf ${wt(ziel)}`); neu();
    }),
    darf: (d) => d >= h,
  });

  matchMedia(KOMPAKT).addEventListener("change", detail, {signal: abbruch.signal});

  // --- Planer ------------------------------------------------------------------------
  $$("[data-pers]", el).forEach((i) => i.onchange = () => { state.personen[i.dataset.pers] = +i.value; });
  $$("[data-m]", el).forEach((b) => b.onclick = () => { state.monat = +b.dataset.m === 0 ? 0 : state.monat + +b.dataset.m; neu(); });
  $$("[data-wahl]", el).forEach((b) => b.onclick = () => {
    const d = b.dataset.wahl;
    state.auswahl.has(d) ? state.auswahl.delete(d) : state.auswahl.add(d);
    neu();
  });
  $$("[data-schnell]", el).forEach((b) => b.onclick = () => {
    const art = b.dataset.schnell, woche = zukunft.slice(0, 7);
    state.auswahl = new Set(art === "leer" ? [] : art === "7" ? woche.filter((d) => !byDay[d])
      : art === "werktage" ? woche.filter((d) => wdOf(d) < 5 && !byDay[d])
      : art === "wochenende" ? woche.filter((d) => wdOf(d) >= 5 && !byDay[d])
      : zukunft.filter((d) => !byDay[d] && kochtage.includes(wdOf(d))));
    neu();
  });
  const ausschluss = new Set(prefs.ausschluss_tags);
  $$("[data-aus]", el).forEach((b) => b.onclick = () => {
    ausschluss.has(b.dataset.aus) ? ausschluss.delete(b.dataset.aus) : ausschluss.add(b.dataset.aus);
    b.classList.toggle("on");
  });
  $("#start", el).onclick = () => guarded(async () => {
    const praeferenzen = Object.fromEntries(REGLER.map(([k]) => [k, +$(`#r_${k}`, el).value]));
    praeferenzen.ausschluss_tags = [...ausschluss];
    if ($("#std", el).checked) await put("einstellungen/praeferenzen", praeferenzen);
    const personen = Object.fromEntries($$("[data-pers]", el).map((i) => [i.dataset.pers, Math.min(12, Math.max(1, +i.value || 1))]));
    await post("plan/erzeugen", {daten: [...state.auswahl].sort(), personen,
      max_ungetestet: +$("#mu", el).value, ki: $("#ki", el).checked, ueberschreiben: $("#ueb", el).checked, praeferenzen});
    toast($("#ki", el).checked ? "Planer läuft, die KI wählt aus …" : "Planer läuft …");
    ergebnis(el, null, name, ctx);
  });
  ergebnis(el, laeufe[0], name, ctx);
  return () => abbruch?.abort();
}

// Rezept suchen und wählen: kochbare zuerst, Portionen einstellbar. → {id, portionen} | null
export async function rezeptWaehlen(datum, buch, portionen, aktuell = null) {
  let wahl = null, filter = "";
  const sortiert = [...buch].sort((a, b) => (b.kochbar - a.kochbar) || (b.favorit ?? 0) - (a.favorit ?? 0) || a.name.localeCompare(b.name, "de"));
  const eintrag = (r) => `<button type="button" class="rw-zeile ${r.id === aktuell ? "aktuell" : ""}" data-r="${esc(r.id)}">
    ${r.foto ? `<img src="${fotoUrl(r.foto)}" alt="" loading="lazy">` : `<span class="g-ico">🍽️</span>`}
    <span class="rw-text"><b>${esc(r.name)}</b><small class="muted">${r.kochbar ? `<span class="ok">● alles da</span>` : `${r.fehlend.length} fehlt`}${
      r.gekocht ? ` · ${r.gekocht}× gekocht` : " · neu"}${r.aufwand ? ` · ${esc(r.aufwand)}` : ""}</small></span></button>`;
  const liste = () => {
    const q = filter.trim().toLowerCase();
    const t = sortiert.filter((r) => !q || r.name.toLowerCase().includes(q) || r.tags.some((x) => x.toLowerCase().includes(q)));
    return t.length ? t.slice(0, 80).map(eintrag).join("") : `<p class="muted small" style="padding:10px">Kein Rezept gefunden.</p>`;
  };
  await dialog(`<h2>${esc(tagLang(datum))}</h2>
    <input type="search" id="rq" placeholder="Rezept suchen (Name oder Tag) …" autocomplete="off">
    <div class="row" style="align-items:center;margin-top:8px"><span class="muted small" style="flex:0 0 auto">Portionen</span>
      <div class="stepper"><button type="button" data-pm="-1">−</button><input id="rp" type="number" min="1" max="12" value="${portionen}"><button type="button" data-pm="1">+</button></div></div>
    <div class="suche-liste rw-liste" id="rl">${liste()}</div>`, [["abbrechen", "Abbrechen"]], (d) => {
    const q = $("#rq", d), rl = $("#rl", d), rp = $("#rp", d);
    const klicks = () => $$("[data-r]", rl).forEach((b) => b.onclick = () => {
      wahl = {id: b.dataset.r, portionen: Math.min(12, Math.max(1, +rp.value || 1))}; d.close("wahl");
    });
    q.oninput = () => { filter = q.value; rl.innerHTML = liste(); klicks(); };
    q.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("[data-r]", rl)?.click(); } };
    $$("[data-pm]", d).forEach((b) => b.onclick = () => { rp.value = Math.min(12, Math.max(1, (+rp.value || 1) + +b.dataset.pm)); });
    klicks();
    setTimeout(() => q.focus(), 0);
  });
  return wahl;
}

// Belegten Tag bearbeiten: anderes Rezept, Portionen, verschieben, KI-Tausch, entfernen.
// Gibt true zurück, wenn sich etwas geändert hat.
async function tagBearbeiten(datum, eintrag, buch, name, zukunft) {
  const r = await dialog(`<h2>${esc(tagLang(datum))}</h2>
    <p><a href="#/rezept/${esc(eintrag.rezept_id)}"><b>${esc(name[eintrag.rezept_id] ?? "?")}</b></a></p>
    <div class="row" style="align-items:end">
      <div><label>Portionen</label><div class="stepper"><button type="button" data-pm="-1">−</button><input id="dp" type="number" min="1" max="12" value="${eintrag.portionen}"><button type="button" data-pm="1">+</button></div></div>
      <div><label>Tag</label><select id="dt">${zukunft.map((d) => `<option value="${d}" ${d === datum ? "selected" : ""}>${esc(wt(d))}</option>`).join("")}</select></div></div>
    <p class="muted tiny">Liegt am Zieltag schon etwas, werden beide getauscht.</p>`,
  [["abbrechen", "Abbrechen"], ["weg", "Entfernen"], ["andere", "Anderes Rezept"], ["tausch", "⇄ KI-Vorschlag"], ["ok", "Speichern", "primary"]],
  (d) => $$("[data-pm]", d).forEach((b) => b.onclick = () => { const i = $("#dp", d); i.value = Math.min(12, Math.max(1, (+i.value || 1) + +b.dataset.pm)); }));
  const d = $("#dialog");
  if (r === "ok") {
    const portionen = Math.min(12, Math.max(1, +$("#dp", d).value || 1)), ziel = $("#dt", d).value;
    if (portionen !== eintrag.portionen) await put(`plan/${datum}`, {rezept_id: eintrag.rezept_id, portionen});
    if (ziel !== datum) {
      const e = await post(`plan/${datum}/verschieben`, {ziel});
      toast(e.getauscht ? `Getauscht mit ${wt(ziel)}` : `Verschoben auf ${wt(ziel)}`);
    }
    return true;
  }
  if (r === "weg") { await del(`plan/${datum}`); toast("Entfernt"); return true; }
  if (r === "andere") {
    const w = await rezeptWaehlen(datum, buch, eintrag.portionen, eintrag.rezept_id);
    if (!w) return false;
    await put(`plan/${datum}`, {rezept_id: w.id, portionen: w.portionen});
    return true;
  }
  if (r === "tausch") { const e = await post(`plan/${datum}/tauschen`, {}); toast(`${e.name} · ${e.begruendung}`); return true; }
  return false;
}

// Ziehen mit Maus und Finger (Pointer Events statt HTML5-Drag, das auf Touch nicht geht).
// Maus: ab 5 px Bewegung. Finger: 300 ms lange drücken, sonst scrollt die Seite normal.
let abbruch = null;  // Listener am window: beim Neuzeichnen und Verlassen der Seite lösen
function ziehen(kal, {klick, ablegen, darf}) {
  abbruch?.abort();
  abbruch = new AbortController();
  const signal = abbruch.signal;
  let z = null;
  const ziel = (x, y) => {
    const t = document.elementFromPoint(x, y)?.closest("[data-tag]");
    return t && kal.contains(t) && darf(t.dataset.tag) ? t : null;
  };
  const start = () => {
    z.aktiv = true;
    const r = z.el.getBoundingClientRect();
    z.dx = z.x0 - r.left; z.dy = z.y0 - r.top;
    z.geist = z.el.cloneNode(true);
    Object.assign(z.geist.style, {width: r.width + "px", left: r.left + "px", top: r.top + "px"});
    z.geist.classList.add("geist");
    document.body.append(z.geist);
    z.el.classList.add("zieht");
    navigator.vibrate?.(15);
    z.scroll = setInterval(() => {
      if (!z) return;
      if (z.y < 90) window.scrollBy(0, -12); else if (z.y > innerHeight - 110) window.scrollBy(0, 12);
    }, 16);
  };
  const ende = (ok) => {
    if (!z) return;
    clearTimeout(z.timer); clearInterval(z.scroll);
    const {aktiv, von, el, geist, x, y} = z;
    z = null;
    geist?.remove(); el.classList.remove("zieht");
    $$(".drop-ziel", kal).forEach((t) => t.classList.remove("drop-ziel"));
    if (!ok) return;
    if (aktiv) { const t = ziel(x, y); if (t && t.dataset.tag !== von) ablegen(von, t.dataset.tag); }
    else klick(von);
  };
  kal.addEventListener("pointerdown", (e) => {
    const g = e.target.closest("[data-g]");
    if (!g || e.target.closest("button,a") || (e.pointerType === "mouse" && e.button !== 0)) return;
    const von = g.dataset.g;
    z = {el: g, von, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, touch: e.pointerType !== "mouse", aktiv: false};
    if (!darf(von)) return;  // vergangene Tage: nur antippen
    if (z.touch) z.timer = setTimeout(() => z && !z.aktiv && !z.abgebrochen && start(), 300);
  }, {signal});
  window.addEventListener("pointermove", (e) => {
    if (!z) return;
    z.x = e.clientX; z.y = e.clientY;
    const weg = Math.hypot(z.x - z.x0, z.y - z.y0);
    if (!z.aktiv) {
      if (z.touch && weg > 10) { clearTimeout(z.timer); z.abgebrochen = true; }  // Finger scrollt
      else if (!z.touch && weg > 5 && darf(z.von)) start();
      return;
    }
    z.geist.style.transform = `translate(${z.x - z.x0}px, ${z.y - z.y0}px) rotate(-2deg)`;
    const t = ziel(z.x, z.y);
    $$(".drop-ziel", kal).forEach((x) => x !== t && x.classList.remove("drop-ziel"));
    t?.classList.add("drop-ziel");
  }, {signal});
  window.addEventListener("pointerup", () => { if (z) ende(!z.abgebrochen); }, {signal});
  window.addEventListener("pointercancel", () => ende(false), {signal});
  // Während des Ziehens darf der Finger nicht scrollen
  kal.addEventListener("touchmove", (e) => { if (z?.aktiv) e.preventDefault(); }, {passive: false, signal});
  kal.addEventListener("contextmenu", (e) => { if (e.target.closest("[data-g]")) e.preventDefault(); }, {signal});
}

async function ergebnis(el, letzter, name, ctx) {
  const box = $("#ergebnis", el);
  if (!box) return;
  let lauf = null;
  if (!letzter) lauf = (await api("plan/laeufe"))[0];
  lauf = lauf ?? letzter;
  if (!lauf) { box.innerHTML = `<div class="card"><h2>Vorschlag</h2><p class="muted">Noch kein Vorschlag.</p></div>`; return; }
  const l = await api(`plan/laeufe/${lauf.id}`);
  if (l.status === "laeuft") {
    box.innerHTML = `<div class="card"><h2>Vorschlag</h2><p class="muted">Plant gerade …</p></div>`;
    setTimeout(() => guarded(() => ergebnis(el, null, name, ctx)), 2500); return;
  }
  if (l.status === "fehler") { box.innerHTML = `<div class="card"><h2>Vorschlag</h2><p class="over">Fehlgeschlagen: ${esc(l.fehler)}</p></div>`; return; }
  const ki = l.ki?.aktiv ? (l.ki.fehler ? `<p class="over small">KI nicht erreichbar (${esc(l.ki.fehler)}), der Code hat geplant.</p>`
    : `<p class="muted small">KI hat ausgewählt (${l.ki.runden} ${l.ki.runden === 1 ? "Runde" : "Runden"})${l.ki.probleme.length ? `, offen: ${esc(l.ki.probleme.join("; "))}` : ""}.</p>`) : "";
  box.innerHTML = `<div class="card"><div class="kopf"><h2>Vorschlag vom ${dmy(l.start)}</h2>
      <span class="tag ${l.status === "uebernommen" ? "good" : ""}">${esc(l.status)}</span></div>${ki}
    <table>${l.tage.map((t) => `<tr><td class="nw muted">${wt(t.datum)}</td><td><a href="#/rezept/${esc(t.rezept_id)}"><b>${esc(t.name)}</b></a>
      <span class="muted">· ${t.portionen} P.</span> <span class="tag">${esc(t.quelle)}</span>
      ${t.begruendung ? `<div class="muted tiny">${esc(t.begruendung)}</div>` : ""}
      ${t.skaliert.map((x) => `<div class="muted tiny">${esc(x.name)}: ${fmt(x.genommen)} statt ${fmt(x.rezept)} ${esc(x.einheit)}</div>`).join("")}
      ${t.hinweise.map((x) => `<div class="soon tiny">${esc(x)}</div>`).join("")}</td></tr>`).join("")}</table>
    ${l.ohne_rezept?.length ? `<p class="over small">Ohne Rezept: ${l.ohne_rezept.map(wt).join(", ")}</p>` : ""}
    <h3>Einkauf${l.kosten != null ? ` · ca. ${eur(l.kosten)}` : ""}${l.kosten_unbekannt ? ` <span class="muted tiny">(${l.kosten_unbekannt} ohne Preis)</span>` : ""}</h3>
    ${l.einkauf.length ? `<ul>${l.einkauf.map((e) => `<li>${esc(e.text)}${e.preis != null ? ` <span class="muted">${eur(e.preis)}</span>` : ""}</li>`).join("")}</ul>` : `<p class="muted">Nichts zu kaufen.</p>`}
    ${l.verderb.length ? `<h3>Verdirbt trotzdem</h3><ul>${l.verderb.map((v) => `<li class="over">${esc(v.name)} ${fmt(v.menge)} ${esc(v.einheit)} am ${dmy(v.ablauf)}</li>`).join("")}</ul>` : ""}
    ${l.ueberhang.length ? `<p class="muted small">Bleibt übrig: ${l.ueberhang.map((v) => `${esc(v.name)} ${fmt(v.menge)} ${esc(v.einheit)}`).join(", ")}</p>` : ""}
    ${l.status === "fertig" ? `<button class="btn primary voll" id="uebernehmen">In den Wochenplan übernehmen</button>` : ""}</div>`;
  const b = $("#uebernehmen", box);
  if (b) b.onclick = () => guarded(async () => {
    const e = await post(`plan/laeufe/${l.id}/uebernehmen`);
    toast(`${e.uebernommen} Tage übernommen`); state.auswahl = null; render(el, ctx);
  });
}
