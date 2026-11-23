import {$, $$, api, esc, fmt, fotoUrl, guarded, heute, nichtDa, nutriBadge, plusTage, post, put, toast, wt, ablaufText, REGLER} from "../lib.js";

// Planer-Chips: neutral → rechts (+1) → links (−1) → neutral. Werte dazwischen bleiben, bis man klickt.
const state = {regler: null, tage: null};
const kurzTag = (d) => d === heute() ? "heute" : d === plusTage(heute(), 1) ? "morgen"
  : new Date(d + "T12:00:00").toLocaleDateString("de-DE", {weekday: "short"});

export default async function render(el) {
  const [u, buch, einst, fuell, vor, dub, katalog, laeufe, sp] = await Promise.all([
    api("uebersicht"), api("rezeptbuch"), api("einstellungen"), api("fuellstand"),
    api("vorschlaege").catch(() => []), api("dubletten").catch(() => []), api("katalog/produkte"),
    api("plan/laeufe").catch(() => []), api("spontan").catch(() => ({rezepte: [], schwelle_pct: 95}))]);
  const rezept = Object.fromEntries(buch.map((r) => [r.id, r]));
  const tage = u.tage.slice(1);
  const frei = new Set(u.tage.filter((t) => !t.gericht).map((t) => t.datum));
  const kochtage = einst.planer.kochtage;
  const wd = (d) => (new Date(d + "T12:00:00").getDay() + 6) % 7;
  state.regler ??= {...einst.praeferenzen};
  state.tage ??= new Set(u.tage.slice(0, 7).map((t) => t.datum).filter((d) => frei.has(d) && kochtage.includes(wd(d))));
  const kochbar = buch.filter((r) => r.kochbar)
    .sort((a, b) => (b.favorit - a.favorit) || (b.bewertung - a.bewertung) || a.name.localeCompare(b.name, "de"));
  const knapp = fuell.filter((f) => f.stand === "knapp" || f.stand === "leer");
  const ohne = katalog.filter((p) => p.attribute_fehlen).length;
  const vorschlaege = new Set(vor.map((v) => `${v.typ}:${v.objekt_id}`)).size;
  const aufgaben = [
    ohne && [`${ohne} Produkte ohne Attribute`, "ki-vorschlaege"],
    vorschlaege && [`${vorschlaege} KI-Vorschläge prüfen`, "ki-vorschlaege"],
    dub.length && [`${dub.length} mögliche Dubletten`, "dubletten"],
    knapp.length && [`${knapp.length}× Füllstand knapp/leer`, "reste"],
  ].filter(Boolean);
  const h = u.heute;

  el.innerHTML = `<div class="start">
    <section class="card heute-karte ${h ? "" : "leer-heute"}" data-drop="${u.datum}">
      ${h ? `<div class="heute-bild">${h.foto ? `<img src="${fotoUrl(h.foto, false)}" alt="">` : `<span>🍽️</span>`}</div>
      <div class="heute-text">
        <div class="muted small">Heute · ${wt(u.datum)}</div>
        <h2 class="gross"><a href="#/rezept/${esc(h.rezept_id)}">${esc(h.name)}</a></h2>
        <div class="fakten">${h.portionen} Portionen${h.kcal ? ` · ${fmt(h.kcal, 0)} kcal` : ""}${h.protein ? ` · ${fmt(h.protein, 0)} g Protein` : ""}
          ${nutriBadge(h.nutri)}${h.status === "gekocht" ? ` <span class="tag good">✓ gekocht</span>` : ""}</div>
        ${h.fehlend.length ? `<p class="soon small">Fehlt: ${esc(h.fehlend.join(", "))}</p>` : h.status !== "gekocht" ? `<p class="ok small">Alles da.</p>` : ""}
        <div class="aktionen-zeile">
          ${h.status !== "gekocht" ? `<a class="btn primary" href="#/kochen">👩‍🍳 Kochen</a>` : ""}
          <button class="btn" data-schieben="${u.datum}" data-ziel="${plusTage(u.datum, 1)}">→ morgen</button>
          <button class="btn" data-waehlen="${u.datum}" title="Anderer Tag">⇄</button>
        </div>
        <div class="ziel-chips" data-fuer="${u.datum}"></div>
      </div>` : `<div class="heute-text">
        <div class="muted small">Heute · ${wt(u.datum)}</div>
        <h2 class="gross">Noch nichts geplant</h2>
        ${kochbar.length ? `<p class="muted">Jetzt kochbar, ein Klick plant es für heute:</p>
          <div class="chips">${kochbar.slice(0, 4).map((r) => `<button class="chip" data-heute="${esc(r.id)}" data-p="${r.portionen}">${esc(r.name)}</button>`).join("")}</div>`
          : `<p class="muted">Nichts komplett vorrätig. <a href="#/plan">Plan erstellen →</a></p>`}
      </div>`}
    </section>

    <section class="card"><div class="kopf"><h2>Nächste Tage</h2><div class="aktionen"><a class="btn klein" href="#/plan">Kalender →</a></div></div>
      <p class="muted tiny">Karte auf einen anderen Tag ziehen oder → / ⇄ tippen. Belegte Tage werden getauscht.</p>
      <div class="tage-raster">${tage.map((t) => tagKarte(t, u.tage)).join("")}</div>
    </section>

    <div class="grid">
      <section class="card" id="planer-karte"><div class="kopf"><h2>Plan vorschlagen</h2></div>
        <label>Tage</label><div class="chips">${u.tage.slice(0, 7).map((t) => `<button class="chip ${state.tage.has(t.datum) ? "on" : ""}" data-pt="${t.datum}"
          title="${t.gericht ? "schon geplant: " + esc(t.gericht.name) : "frei"}">${kurzTag(t.datum)}${t.gericht ? " •" : ""}</button>`).join("")}</div>
        <label>Wünsche</label><div class="chips">${REGLER.map(([k, t, l, r]) => reglerChip(k, t, l, r)).join("")}</div>
        <button class="btn primary voll" id="vorschlagen" ${state.tage.size ? "" : "disabled"}>${state.tage.size} ${state.tage.size === 1 ? "Tag" : "Tage"} vorschlagen</button>
        <p class="muted tiny">• = schon geplant, wird neu geplant, wenn gewählt. Mehr Optionen im <a href="#/plan">Planer</a>.</p>
        <div id="vorschlag"></div>
      </section>

      <section class="card"><div class="kopf"><h2>Läuft bald ab</h2><div class="aktionen"><a class="btn klein" href="#/inventar?filter=bald">Inventar →</a></div></div>
        ${u.laeuft_ab.length ? `<table>${u.laeuft_ab.slice(0, 8).map((b) => `<tr>
          <td><a href="#/rezepte?produkt=${esc(b.produkt_id)}" title="Rezepte damit">${esc(b.name)}</a></td>
          <td class="nw">${fmt(b.menge)} ${esc(b.einheit)}</td><td class="nw right">${ablaufText(b.ablauf, b.tage)}</td></tr>`).join("")}</table>
          ${u.laeuft_ab.length > 8 ? `<p class="muted tiny">… und ${u.laeuft_ab.length - 8} weitere</p>` : ""}`
          : `<p class="muted">Nichts in den nächsten ${u.bald_tage} Tagen. 👌</p>`}
        ${u.reste.length ? `<h3>Reste</h3><table>${u.reste.map((r) => `<tr><td>${esc(r.name.replace(/^Rest: /, ""))}</td>
          <td class="nw">${fmt(r.portionen)} P.</td><td class="nw right">${r.ablauf ? ablaufText(r.ablauf, Math.round((new Date(r.ablauf) - new Date(u.datum)) / 864e5)) : ""}</td></tr>`).join("")}</table>` : ""}
        <div class="row vorrat">
          <a class="kachel" href="#/inventar"><div class="zahl">${u.artikel}</div><div class="muted small">vorrätig</div></a>
          <a class="kachel" href="#/produkte?filter=nicht_da"><div class="zahl">${katalog.filter(nichtDa).length}</div><div class="muted small">nicht da</div></a>
          <a class="kachel" href="#/rezepte?kochbar=1"><div class="zahl">${kochbar.length}</div><div class="muted small">kochbar</div></a></div>
        ${aufgaben.length ? `<h3>Zu tun</h3><table>${aufgaben.map(([t, r]) =>
          `<tr class="klick" onclick="location.hash='#/${r}'"><td>${esc(t)}</td><td class="right muted">→</td></tr>`).join("")}</table>` : ""}
      </section>
    </div>

    ${sp.rezepte.length ? `<section class="card"><div class="kopf"><h2>Spontan kochbar</h2><div class="aktionen">
        <a class="btn klein" href="#/rezepte?kochbar=1">Alle kochbaren →</a></div></div>
      <p class="muted tiny">Mindestens ${sp.schwelle_pct} % der Zutaten (nach Menge) vorrätig, Basics wie Salz und Öl zählen nicht.
        Zuerst, was am vollständigsten ist und bald Ablaufendes verbraucht.</p>
      <div class="karten-raster">${sp.rezepte.map((r) => `<div class="rezeptkarte card">
        <a class="bild" href="#/rezept/${esc(r.rezept_id)}">${r.foto ? `<img src="${fotoUrl(r.foto)}" alt="" loading="lazy">` : `<span>🍽️</span>`}
          <span class="ecke">${nutriBadge(r.nutri)}</span></a>
        <div class="inhalt"><a href="#/rezept/${esc(r.rezept_id)}" class="karten-titel"><b>${esc(r.name)}</b></a>
          <div class="muted tiny">${Math.floor(r.abdeckung * 100)} % da${r.kcal ? ` · ${fmt(r.kcal, 0)} kcal` : ""}${r.dringend ? ` · <span class="soon">verbraucht Ablaufendes</span>` : ""}</div>
          ${r.fehlt.length ? `<div class="soon tiny">fehlt: ${esc(r.fehlt.map((f) => f.text).join(", "))}</div>` : ""}
          <div class="tag-aktionen"><button class="btn klein primary" data-statt="${esc(r.rezept_id)}"
            title="${h ? "Heute kochen, das geplante Gericht rückt auf den nächsten freien Tag" : "Für heute einplanen"}">Statt heute</button>
            <a class="btn klein" href="#/kochen/${esc(r.rezept_id)}" title="Zusätzlich kochen, Plan bleibt unverändert">Zusätzlich</a></div>
        </div></div>`).join("")}</div>
    </section>` : ""}
  </div>`;

  // Verschieben: Knopf, Tag-Auswahl oder Drag&Drop
  const schieben = (von, ziel) => guarded(async () => {
    const r = await post(`plan/${von}/verschieben`, {ziel});
    toast(r.getauscht ? `Getauscht mit ${kurzTag(ziel)}` : `Verschoben auf ${kurzTag(ziel)}`);
    render(el);
  });
  $$("[data-schieben]", el).forEach((b) => b.onclick = () => schieben(b.dataset.schieben, b.dataset.ziel));
  $$("[data-waehlen]", el).forEach((b) => b.onclick = () => {
    const box = $(`.ziel-chips[data-fuer="${b.dataset.waehlen}"]`, el);
    if (box.innerHTML) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="chips">${u.tage.filter((t) => t.datum !== b.dataset.waehlen).map((t) =>
      `<button class="chip" data-nach="${t.datum}">${kurzTag(t.datum)} ${Number(t.datum.slice(8))}.${t.gericht ? " ⇄" : ""}</button>`).join("")}</div>`;
    $$("[data-nach]", box).forEach((c) => c.onclick = () => schieben(b.dataset.waehlen, c.dataset.nach));
  });
  $$("[data-drag]", el).forEach((k) => k.ondragstart = (ev) => {
    ev.dataTransfer.setData("text/plain", k.dataset.drag); ev.dataTransfer.effectAllowed = "move";
  });
  $$("[data-drop]", el).forEach((z) => {
    z.ondragover = (ev) => { ev.preventDefault(); z.classList.add("drop-ziel"); };
    z.ondragleave = () => z.classList.remove("drop-ziel");
    z.ondrop = (ev) => {
      ev.preventDefault(); z.classList.remove("drop-ziel");
      const von = ev.dataTransfer.getData("text/plain");
      if (von && von !== z.dataset.drop) schieben(von, z.dataset.drop);
    };
  });
  $$("[data-statt]", el).forEach((b) => b.onclick = () => guarded(async () => {
    const r = await post("plan/spontan", {rezept_id: b.dataset.statt});
    toast(r.verschoben ? `Heute gekocht, bisheriges Gericht auf ${kurzTag(r.verschoben.datum)} ${Number(r.verschoben.datum.slice(8))}.` : "Für heute geplant");
    render(el);
  }));
  $$("[data-heute]", el).forEach((b) => b.onclick = () => guarded(async () => {
    await put(`plan/${u.datum}`, {rezept_id: b.dataset.heute, portionen: einst.planer.portionen || +b.dataset.p});
    toast("Für heute geplant"); render(el);
  }));

  // Planer
  const knopf = () => {
    const b = $("#vorschlagen", el), n = state.tage.size;
    b.disabled = !n; b.textContent = `${n} ${n === 1 ? "Tag" : "Tage"} vorschlagen`;
  };
  $$("[data-pt]", el).forEach((c) => c.onclick = () => {
    const d = c.dataset.pt;
    state.tage.has(d) ? state.tage.delete(d) : state.tage.add(d);
    c.classList.toggle("on", state.tage.has(d)); knopf();
  });
  $$("[data-regler]", el).forEach((c) => c.onclick = () => {
    const k = c.dataset.regler, v = state.regler[k];
    state.regler[k] = v > 0 ? -1 : v < 0 ? 0 : 1;
    const [, t, l, r] = REGLER.find(([x]) => x === k);
    c.textContent = state.regler[k] > 0 ? r : state.regler[k] < 0 ? l : t;
    c.classList.toggle("on", !!state.regler[k]);
  });
  $("#vorschlagen", el).onclick = () => guarded(async () => {
    const praeferenzen = Object.fromEntries(REGLER.map(([k]) => [k, state.regler[k]]));
    const daten = [...state.tage].sort();
    const ueberschreiben = daten.some((d) => !frei.has(d));
    const r = await post("plan/erzeugen", {daten, praeferenzen, ueberschreiben});
    toast(einst.planer.ki ? "Planer läuft, die KI wählt aus …" : "Planer läuft …");
    vorschlag(el, r.lauf_id, rezept);
  });
  const letzter = laeufe[0];
  if (letzter && ["laeuft", "fertig"].includes(letzter.status) && letzter.start >= u.datum) vorschlag(el, letzter.id, rezept);
}

function tagKarte(t, alle) {
  const g = t.gericht, morgen = plusTage(t.datum, 1);
  const kopf = `<div class="tag-kopf"><b>${kurzTag(t.datum)}</b> <span class="muted tiny">${wt(t.datum).split(", ")[1] ?? ""}</span></div>`;
  if (!g) return `<div class="tag-karte frei" data-drop="${t.datum}">${kopf}<a class="frei-plus" href="#/plan" title="Planen">＋</a></div>`;
  const imPlan = alle.some((x) => x.datum === morgen);
  return `<div class="tag-karte" data-drop="${t.datum}" data-drag="${t.datum}" draggable="true">
    ${kopf}
    <a class="tag-bild" href="#/rezept/${esc(g.rezept_id)}">${g.foto ? `<img src="${fotoUrl(g.foto)}" alt="" loading="lazy" draggable="false">` : `<span>🍽️</span>`}
      <span class="ecke">${nutriBadge(g.nutri)}</span></a>
    <div class="tag-name">${esc(g.name)}${g.status === "gekocht" ? " ✓" : ""}</div>
    <div class="muted tiny">${g.kcal ? `${fmt(g.kcal, 0)} kcal · ` : ""}${g.portionen} P.${g.fehlend.length ? ` · <span class="soon">${g.fehlend.length} fehlt</span>` : ""}</div>
    <div class="tag-aktionen">
      ${imPlan ? `<button class="btn klein" data-schieben="${t.datum}" data-ziel="${morgen}" title="Einen Tag später">→ ${kurzTag(morgen)}</button>` : ""}
      <button class="btn klein" data-waehlen="${t.datum}" title="Anderer Tag">⇄</button></div>
    <div class="ziel-chips" data-fuer="${t.datum}"></div>
  </div>`;
}

function reglerChip(k, titel, links, rechts) {
  const v = state.regler[k];
  const text = v > 0 ? rechts : v < 0 ? links : titel;
  return `<button class="chip ${v ? "on" : ""}" data-regler="${k}" title="${esc(titel)}: tippen wechselt neutral → ${esc(rechts)} → ${esc(links)}">${esc(text)}</button>`;
}

async function vorschlag(el, id, rezept) {
  const box = $("#vorschlag", el);
  if (!box || !document.body.contains(box)) return;
  const l = await api(`plan/laeufe/${id}`);
  if (l.status === "laeuft") {
    box.innerHTML = `<p class="muted">Plant gerade …</p>`;
    setTimeout(() => guarded(() => vorschlag(el, id, rezept)), 2500); return;
  }
  if (l.status === "fehler") { box.innerHTML = `<p class="over small">Fehlgeschlagen: ${esc(l.fehler)}</p>`; return; }
  if (l.status !== "fertig") { box.innerHTML = ""; return; }
  box.innerHTML = `<h3>Vorschlag</h3><div class="vorschlag-liste">${l.tage.map((t) => {
    const r = rezept[t.rezept_id] ?? {};
    return `<a class="vorschlag-zeile" href="#/rezept/${esc(t.rezept_id)}">
      <span class="mini-bild">${r.foto ? `<img src="${fotoUrl(r.foto)}" alt="" loading="lazy">` : "🍽️"}</span>
      <span class="grow"><span class="muted tiny">${kurzTag(t.datum)} ${Number(t.datum.slice(8))}.</span><br><b>${esc(t.name)}</b>
        <span class="muted tiny">${t.portionen} P.${r.kcal ? ` · ${fmt(r.kcal, 0)} kcal` : ""}</span></span>
      ${nutriBadge(r.nutri)}</a>`;
  }).join("")}</div>
  ${l.einkauf.length ? `<p class="muted small">Einkauf: ${l.einkauf.length} Positionen</p>` : `<p class="ok small">Nichts zu kaufen.</p>`}
  <button class="btn primary voll" id="uebernehmen">Übernehmen</button>`;
  $("#uebernehmen", box).onclick = () => guarded(async () => {
    const e = await post(`plan/laeufe/${l.id}/uebernehmen`);
    toast(`${e.uebernommen} Tage übernommen`); state.tage = null; render(el);
  });
}
