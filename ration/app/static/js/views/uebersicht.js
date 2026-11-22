import {api, esc, fmt, guarded, heute, nichtDa, plusTage, wt, ablaufText} from "../lib.js";

export default async function (el) {
  const bald = (await api("einstellungen").catch(() => null))?.anzeige?.bald_tage ?? 3;
  const [st, h, plan, rezepte, fuell, vor, dub, katalog] = await Promise.all([
    api(`status?tage=${bald}`), api("heute"), api("plan?tage=7"), api("rezepte"), api("fuellstand"),
    api("vorschlaege").catch(() => []), api("dubletten").catch(() => []), api("katalog/produkte")]);
  const name = Object.fromEntries(rezepte.map((r) => [r.id, r.name]));
  const byDay = Object.fromEntries(plan.map((p) => [p.datum, p]));
  const tage = [...Array(7)].map((_, i) => plusTage(heute(), i));
  const knapp = fuell.filter((f) => f.stand === "knapp" || f.stand === "leer");
  const ohne = katalog.filter((p) => p.attribute_fehlen).length;
  const vorschlaege = new Set(vor.map((v) => `${v.typ}:${v.objekt_id}`)).size;
  const aufgaben = [
    ohne && [`${ohne} Produkte ohne Attribute`, "ki-vorschlaege"],
    vorschlaege && [`${vorschlaege} KI-Vorschläge prüfen`, "ki-vorschlaege"],
    dub.length && [`${dub.length} mögliche Dubletten`, "dubletten"],
    knapp.length && [`${knapp.length}× Füllstand knapp/leer`, "reste"],
    tage.filter((d) => !byDay[d]).length && [`${tage.filter((d) => !byDay[d]).length} Tage ohne Plan`, "plan"],
  ].filter(Boolean);

  el.innerHTML = `<div class="grid">
    <div class="card"><div class="kopf"><h2>Heute</h2><div class="aktionen"><a class="btn klein" href="#/heute">Kochen →</a></div></div>
      ${h.rezept ? `<div class="zahl" style="font-size:20px">${esc(h.rezept.name)}</div>
        <p class="muted">${h.rezept.portionen} Portionen${h.status === "gekocht" ? " · ✓ gekocht" : ""}</p>`
        : `<p class="muted">Nichts geplant.</p><a class="btn" href="#/rezepte?kochbar=1">Was kann ich jetzt kochen?</a>`}</div>
    <div class="card"><div class="kopf"><h2>Läuft bald ab</h2><div class="aktionen"><a class="btn klein" href="#/inventar?filter=bald">Inventar →</a></div></div>
      ${st.laeuft_bald_ab.length ? `<table>${st.laeuft_bald_ab.slice(0, 8).map((b) => `<tr>
        <td><a href="#/rezepte?produkt=${esc(b.produkt_id)}" title="Rezepte damit">${esc(b.name)}</a></td>
        <td class="nw">${fmt(b.menge)} ${esc(b.einheit)}</td><td class="nw right">${ablaufText(b.ablauf, b.tage)}</td></tr>`).join("")}</table>`
        : `<p class="muted">Nichts in den nächsten ${bald} Tagen. 👌</p>`}</div>
    <div class="card"><div class="kopf"><h2>Diese Woche</h2><div class="aktionen"><a class="btn klein" href="#/plan">Planen →</a></div></div>
      <table>${tage.map((d) => `<tr><td class="nw muted">${wt(d)}</td><td>${byDay[d]
        ? `<a href="#/rezept/${esc(byDay[d].rezept_id)}">${esc(name[byDay[d].rezept_id] ?? byDay[d].rezept_id)}</a>${byDay[d].status === "gekocht" ? " ✓" : ""}`
        : `<span class="muted">–</span>`}</td></tr>`).join("")}</table></div>
    <div class="card"><h2>Zu tun</h2>${aufgaben.length ? `<table>${aufgaben.map(([t, r]) =>
        `<tr class="klick" onclick="location.hash='#/${r}'"><td>${esc(t)}</td><td class="right muted">→</td></tr>`).join("")}</table>`
      : `<p class="muted">Alles erledigt.</p>`}
      ${st.reste.length ? `<h3>Reste</h3><table>${st.reste.map((r) => `<tr><td>${esc(r.name.replace(/^Rest: /, ""))}</td>
        <td class="nw">${fmt(r.portionen)} P.</td></tr>`).join("")}</table>` : ""}</div>
    <div class="card"><h2>Vorrat</h2><div class="row">
      <a class="kachel" href="#/inventar"><div class="zahl">${st.artikel}</div><div class="muted small">Produkte vorrätig</div></a>
      <a class="kachel" href="#/produkte?filter=nicht_da"><div class="zahl">${katalog.filter(nichtDa).length}</div><div class="muted small">bekannt, nicht da</div></a>
      <a class="kachel" href="#/rezepte?kochbar=1"><div class="zahl" id="kochbar">…</div><div class="muted small">jetzt kochbar</div></a></div></div>
  </div>`;
  guarded(async () => {
    const buch = await api("rezeptbuch");
    const z = el.querySelector("#kochbar"); if (z) z.textContent = buch.filter((r) => r.kochbar).length;
  });
}
