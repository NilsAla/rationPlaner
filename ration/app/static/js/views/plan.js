import {$, $$, api, del, dialog, dmy, esc, eur, fmt, guarded, heute, plusTage, post, put, toast, wt, REGLER, WOCHENTAGE} from "../lib.js";

const state = {auswahl: null, monat: 0};

export default async function render(el, ctx = {}) {
  const ein = ctx.params?.rezept ? {id: ctx.params.rezept, portionen: +ctx.params.portionen || null} : null;
  const h = heute();
  const montag = plusTage(h, -((new Date(h + "T12:00:00").getDay() + 6) % 7) + state.monat * 28);
  const [einst, plan, rezepte, buch, laeufe] = await Promise.all([
    api("einstellungen"), api(`plan?von=${montag}&tage=${Math.max(35, 28 - state.monat * 28)}`), api("rezepte"), api("rezeptbuch"), api("plan/laeufe")]);
  const name = Object.fromEntries(rezepte.map((r) => [r.id, r.name]));
  const byDay = Object.fromEntries(plan.map((p) => [p.datum, p]));
  const tage = [...Array(35)].map((_, i) => plusTage(montag, i));
  const kochtage = einst.planer.kochtage;
  if (!state.auswahl) {
    state.auswahl = new Set([...Array(7)].map((_, i) => plusTage(h, i))
      .filter((d) => kochtage.includes((new Date(d + "T12:00:00").getDay() + 6) % 7) && !byDay[d]));
  }
  const prefs = einst.praeferenzen;
  const alleTags = [...new Set(buch.flatMap((r) => r.tags))].sort();

  el.innerHTML = `${ein ? `<div class="card" style="border-color:var(--accent)"><b>„${esc(name[ein.id] ?? ein.id)}“ einplanen:</b>
      <span class="muted">Tag im Kalender anklicken.</span> <a class="btn klein" href="#/plan">Abbrechen</a></div>` : ""}
    <div class="card"><div class="kopf"><h2>Kalender</h2><div class="aktionen">
      <button class="btn klein" data-m="-1">◀</button><button class="btn klein" data-m="0">heute</button><button class="btn klein" data-m="1">▶</button></div></div>
    <p class="muted small">Tage anklicken, an denen gekocht werden soll (grün). ✎ trägt ein Rezept direkt ein. Schon geplante Tage bleiben beim Vorschlag fest.</p>
    <div class="chips" style="margin-bottom:10px">
      <button class="chip" data-schnell="7">nächste 7 Tage</button><button class="chip" data-schnell="werktage">Mo–Fr</button>
      <button class="chip" data-schnell="wochenende">Wochenende</button><button class="chip" data-schnell="frei">alle freien Tage</button>
      <button class="chip" data-schnell="leer">keine</button></div>
    <div class="kalender">${WOCHENTAGE.map((w) => `<div class="wt">${w}</div>`).join("")}
      ${tage.map((d) => {
        const p = byDay[d], vergangen = d < h, wd = (new Date(d + "T12:00:00").getDay() + 6) % 7;
        const cls = [vergangen ? "vergangen" : "", d === h ? "heute" : "", state.auswahl.has(d) ? "koch" : "",
          !kochtage.includes(wd) && !p ? "aus" : ""].join(" ");
        return `<div class="tag-zelle ${cls}" data-tag="${d}">
          <span class="nr">${Number(d.slice(8))}.${d.slice(8) === "01" || d === tage[0] ? " " + new Date(d + "T12:00:00").toLocaleDateString("de-DE", {month: "short"}) : ""}</span>
          ${p ? `<span class="rz ${p.status === "gekocht" ? "gekocht" : ""}">${esc(name[p.rezept_id] ?? "?")}</span><span class="tiny muted">${p.portionen} P.</span>` : ""}
          ${state.auswahl.has(d) ? `<span class="haken">✓</span>` : ""}
          ${!vergangen ? `<button class="icon tiny" data-edit="${d}" title="Rezept eintragen" style="margin-top:auto;align-self:flex-end;font-size:13px;padding:0 4px">✎</button>` : ""}
        </div>`;
      }).join("")}</div></div>

    <div class="grid">
    <div class="card"><h2>Plan vorschlagen <span class="muted">(${state.auswahl.size} Tage)</span></h2>
      <div class="row"><div><label>Portionen je Tag</label><input id="por" type="number" min="1" max="6" value="${einst.planer.portionen}"></div>
        <div><label>Max. ungetestete</label><input id="mu" type="number" min="0" max="7" value="${einst.planer.max_ungetestet}"></div></div>
      ${REGLER.map(([k, t, l, r]) => `<div class="regler"><span class="name">${t}</span>
        <input type="range" min="-1" max="1" step="0.25" id="r_${k}" value="${prefs[k]}">
        <div class="enden"><span>${l}</span><span>${r}</span></div></div>`).join("")}
      ${alleTags.length ? `<label>Nie planen (Tags)</label><div class="chips">${alleTags.map((t) =>
        `<button class="chip ${prefs.ausschluss_tags.includes(t) ? "on" : ""}" data-aus="${esc(t)}">${esc(t)}</button>`).join("")}</div>` : ""}
      <label class="inline"><input type="checkbox" id="ki" ${einst.planer.ki ? "checked" : ""}> KI wählt aus (sonst nur der Code)</label>
      <label class="inline"><input type="checkbox" id="ueb"> Schon geplante Tage in der Auswahl neu planen</label>
      <label class="inline"><input type="checkbox" id="std"> Regler als Standard speichern</label>
      <button class="btn primary voll" id="start" ${state.auswahl.size ? "" : "disabled"}>Plan für ${state.auswahl.size} Tage vorschlagen</button>
      <p class="muted tiny">Aufwand je Wochentag und Standard-Kochtage stellst du in den <a href="#/einstellungen">Einstellungen</a> ein.</p></div>
    <div id="ergebnis"></div></div>`;

  $$("[data-m]", el).forEach((b) => b.onclick = () => { state.monat = +b.dataset.m === 0 ? 0 : state.monat + +b.dataset.m; render(el, ctx); });
  $$("[data-tag]", el).forEach((z) => z.onclick = (ev) => {
    if (ev.target.closest("[data-edit]") || z.classList.contains("vergangen")) return;
    const d = z.dataset.tag;
    if (ein) {
      guarded(async () => {
        await put(`plan/${d}`, {rezept_id: ein.id, portionen: ein.portionen ?? einst.planer.portionen});
        toast(`Eingeplant am ${wt(d)}`); location.hash = "#/plan";
      });
      return;
    }
    state.auswahl.has(d) ? state.auswahl.delete(d) : state.auswahl.add(d);
    render(el, ctx);
  });
  $$("[data-edit]", el).forEach((b) => b.onclick = () => guarded(() => tagBearbeiten(el, b.dataset.edit, byDay[b.dataset.edit], buch, ctx)));
  $$("[data-schnell]", el).forEach((b) => b.onclick = () => {
    const art = b.dataset.schnell, zukunft = [...Array(28)].map((_, i) => plusTage(h, i));
    const wdOf = (d) => (new Date(d + "T12:00:00").getDay() + 6) % 7;
    state.auswahl = new Set(art === "leer" ? [] : art === "7" ? zukunft.slice(0, 7).filter((d) => !byDay[d])
      : art === "werktage" ? zukunft.slice(0, 7).filter((d) => wdOf(d) < 5 && !byDay[d])
      : art === "wochenende" ? zukunft.slice(0, 7).filter((d) => wdOf(d) >= 5 && !byDay[d])
      : zukunft.slice(0, 14).filter((d) => !byDay[d] && kochtage.includes(wdOf(d))));
    render(el, ctx);
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
    await post("plan/erzeugen", {daten: [...state.auswahl].sort(), portionen: +$("#por", el).value,
      max_ungetestet: +$("#mu", el).value, ki: $("#ki", el).checked, ueberschreiben: $("#ueb", el).checked, praeferenzen});
    toast($("#ki", el).checked ? "Planer läuft, die KI wählt aus …" : "Planer läuft …");
    ergebnis(el, null, name, ctx);
  });
  ergebnis(el, laeufe[0], name, ctx);
}

async function tagBearbeiten(el, datum, eintrag, buch, ctx) {
  const sortiert = [...buch].sort((a, b) => (b.kochbar - a.kochbar) || a.name.localeCompare(b.name, "de"));
  const r = await dialog(`<h2>${wt(datum)}</h2>
    <label>Rezept</label><select id="dr"><option value="">– nichts –</option>${sortiert.map((x) =>
      `<option value="${esc(x.id)}" data-p="${x.portionen}" ${x.id === eintrag?.rezept_id ? "selected" : ""}>${x.kochbar ? "● " : ""}${esc(x.name)}</option>`).join("")}</select>
    <label>Portionen</label><input id="dp" type="number" min="1" max="12" value="${eintrag?.portionen ?? 2}">
    <p class="muted tiny">● = alles vorrätig</p>`,
  [["abbrechen", "Abbrechen"], ...(eintrag ? [["tausch", "⇄ Vorschlag"], ["weg", "Entfernen"]] : []), ["ok", "Speichern", "primary"]],
  (d) => { $("#dr", d).onchange = () => { const o = $("#dr", d).selectedOptions[0]; if (o?.dataset.p) $("#dp", d).value = o.dataset.p; }; });
  const d = $("#dialog");
  if (r === "ok") {
    const rid = $("#dr", d).value;
    if (rid) await put(`plan/${datum}`, {rezept_id: rid, portionen: +$("#dp", d).value}); else await del(`plan/${datum}`);
  } else if (r === "weg") await del(`plan/${datum}`);
  else if (r === "tausch") { const e = await post(`plan/${datum}/tauschen`, {}); toast(`${e.name} · ${e.begruendung}`); }
  else return;
  render(el, ctx);
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
