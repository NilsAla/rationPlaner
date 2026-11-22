import {$, $$, KATEGORIEN, MODI, api, bestaetigen, del, dmy, esc, fmt, gehe, guarded, patch, post, put, titel, toast} from "../lib.js";

// Produkt-Editor: neu (ohne arg) oder bearbeiten, mit Packungen, Aliasen, Verwendung
export default async function render(el, {arg}) {
  const [p, liste, orte] = await Promise.all([arg ? api(`produkte/${arg}`) : null, api("produkte"), api("lagerorte")]);
  titel(p ? p.name : "Neues Produkt");
  const sel = (name, werte, wert, leer = "–") => `<select name="${name}"><option value="">${leer}</option>${werte.map(([k, t]) =>
    `<option value="${esc(k)}" ${k === wert ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  const num = (name, wert, attr = "") => `<input name="${name}" type="number" step="any" value="${wert ?? ""}" ${attr}>`;
  const e = p?.einheit ?? "g";

  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr))">
    <form class="card" id="form" style="grid-column:1/-1"><div class="kopf"><h2>${p ? esc(p.name) : "Neues Produkt"}</h2>
      ${p ? `<span class="muted small">Bestand ${fmt(p.bestand)} ${esc(e)} · ID ${esc(p.id)}</span>` : ""}</div>
      <div class="row"><div style="flex:2"><label>Name</label><input name="name" value="${esc(p?.name ?? "")}" required></div>
        <div><label>Einheit</label><input name="einheit" value="${esc(e)}" ${p ? "disabled title=\"Einheit lässt sich nach dem Anlegen nicht ändern\"" : ""}></div>
        <div><label>Kategorie</label><input name="kategorie" list="katl" value="${esc(p?.kategorie ?? "")}">
          <datalist id="katl">${KATEGORIEN.map((k) => `<option value="${esc(k)}">`).join("")}</datalist></div></div>
      <div class="row"><div><label>Tracking-Modus</label>${sel("tracking_modus", Object.entries(MODI), p?.tracking_modus, "nicht gesetzt")}</div>
        <div><label>Füllstand</label>${sel("fuellstand", [["voll", "voll"], ["knapp", "knapp"], ["leer", "leer"]], p?.fuellstand)}</div>
        <div><label>Lagerort</label>${sel("lagerort_id", orte.map((o) => [o.id, o.name]), p?.lagerort_id)}</div></div>
      <div class="row"><div><label>g je ${esc(e)}</label>${num("g_je_einheit", p?.g_je_einheit ?? (e === "g" ? 1 : ""), 'min="0"')}</div>
        <div><label>Toleranz %</label>${num("toleranz_pct", p?.toleranz_pct, 'min="0" max="100"')}</div>
        <div><label>Mindestbestand</label>${num("mindestbestand", p?.mindestbestand, 'min="0"')}</div></div>
      <div class="row"><div><label>Ablauf</label>${sel("ablauf_typ", [["mhd", "MHD"], ["verbrauchsdatum", "Verbrauchsdatum"]], p?.ablauf_typ)}</div>
        <div><label>Haltbar (Tage, −1 = nie)</label>${num("haltbarkeit_tage", p?.haltbarkeit_tage, 'min="-1"')}</div>
        <div><label>geöffnet</label>${num("haltbarkeit_offen_tage", p?.haltbarkeit_offen_tage, 'min="0"')}</div>
        <div><label>gefroren</label>${num("haltbarkeit_gefroren_tage", p?.haltbarkeit_gefroren_tage, 'min="0"')}</div>
        <div><label>aufgetaut</label>${num("haltbarkeit_aufgetaut_tage", p?.haltbarkeit_aufgetaut_tage, 'min="0"')}</div></div>
      <label>Variante von</label>${sel("parent_id", liste.filter((x) => x.id !== p?.id).map((x) => [x.id, x.name]), p?.parent_id, "keine")}
      <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">${p ? "Speichern" : "Anlegen"}</button>
        <a class="btn" href="#/produkte">Zurück</a><span class="grow"></span>
        ${p ? `<button class="btn danger" type="button" id="loeschen">Löschen</button>` : ""}</div></form>
    ${p ? `<div class="card"><h2>Packungen</h2>
      <p>${p.packung ? `Standard: <b>${esc(p.packung.einheit)} ${fmt(p.packung.menge)} ${esc(e)}</b>` : `<span class="muted">Keine Standard-Packung.</span>`}</p>
      <div class="row"><input id="se" placeholder="Einheit, z. B. Dose" value="${esc(p.packung?.einheit ?? "")}">
        <input id="sm" type="number" min="0" step="any" placeholder="Menge in ${esc(e)}" value="${p.packung?.menge ?? ""}">
        <button class="btn" id="sstd" style="flex:0 0 auto">Als Standard</button></div>
      <h3>Weitere Größen</h3>${p.weitere_packungen.length ? `<table>${p.weitere_packungen.map((w) => `<tr><td>${esc(w.einheit)} ${fmt(w.menge)} ${esc(e)}</td>
        <td class="muted tiny">${esc(w.quelle)}${w.gueltig_bis ? " bis " + dmy(w.gueltig_bis) : ""}</td>
        <td class="right"><button class="icon" data-pdel="${w.id}">✕</button></td></tr>`).join("")}</table>` : `<p class="muted">Keine.</p>`}
      <div class="row"><input id="we" placeholder="Einheit"><input id="wm" type="number" min="0" step="any" placeholder="Menge in ${esc(e)}">
        <button class="btn" id="wadd" style="flex:0 0 auto">+ Größe</button></div></div>
    <div class="card"><h2>Aliase</h2><p class="muted tiny">Zutatentexte aus Rezepten, die immer auf dieses Produkt zeigen.</p>
      ${p.aliase.length ? `<div class="chips">${p.aliase.map((a) => `<span class="chip">${esc(a)} <button class="icon" data-adel="${esc(a)}">✕</button></span>`).join("")}</div>` : `<p class="muted">Keine.</p>`}
      <div class="row" style="margin-top:8px"><input id="anew" placeholder="z. B. Spaghetti"><button class="btn" id="aadd" style="flex:0 0 auto">+ Alias</button></div></div>
    <div class="card"><h2>Verwendet in</h2>${p.rezepte.length ? `<table>${p.rezepte.map((r) => `<tr><td><a href="#/rezept/${esc(r.id)}">${esc(r.name)}</a></td>
        <td class="nw right muted">${fmt(r.menge)} ${esc(e)}</td></tr>`).join("")}</table>` : `<p class="muted">Keinem Rezept.</p>`}
      ${p.varianten.length ? `<h3>Varianten</h3><p>${p.varianten.map((v) => `<a href="#/produkt/${esc(v.id)}">${esc(v.name)}</a>`).join(", ")}</p>` : ""}
      ${p.bestand ? `<p><a href="#/inventar">Chargen im Inventar ansehen →</a></p>` : ""}</div>` : ""}</div>`;

  $("#form", el).onsubmit = (ev) => {
    ev.preventDefault();
    guarded(async () => {
      const fd = Object.fromEntries(new FormData(ev.target));
      const body = {name: fd.name.trim(), kategorie: fd.kategorie.trim() || undefined};
      for (const k of ["toleranz_pct", "g_je_einheit", "mindestbestand", "haltbarkeit_tage", "haltbarkeit_offen_tage", "haltbarkeit_gefroren_tage", "haltbarkeit_aufgetaut_tage"])
        if (fd[k] !== "") body[k] = +fd[k];
      for (const k of ["tracking_modus", "fuellstand", "ablauf_typ", "lagerort_id"]) if (fd[k]) body[k] = fd[k];
      if (p) body.parent_id = fd.parent_id; else if (fd.parent_id) body.parent_id = fd.parent_id;
      if (p) { await patch(`produkte/${p.id}`, body); toast("Produkt gespeichert"); render(el, {arg}); }
      else { const n = await post("produkte", {...body, einheit: fd.einheit.trim() || "g"}); toast("Produkt angelegt"); gehe(`produkt/${n.id}`); }
    });
  };
  if (!p) return;
  $("#loeschen", el).onclick = () => guarded(async () => {
    if (!await bestaetigen(`„${esc(p.name)}“ in Grocy löschen? Geht nur ohne Bestand, Varianten und Rezepte.`, "Löschen", true)) return;
    await del(`produkte/${p.id}`); toast("Produkt gelöscht"); gehe("produkte");
  });
  const neu = () => render(el, {arg});
  $("#sstd", el).onclick = () => guarded(async () => { await put(`produkte/${p.id}/packung`, {einheit: $("#se", el).value, menge: +$("#sm", el).value}); toast("Standard gespeichert"); neu(); });
  $("#wadd", el).onclick = () => guarded(async () => { await post(`produkte/${p.id}/packungen`, {einheit: $("#we", el).value, menge: +$("#wm", el).value}); neu(); });
  $$("[data-pdel]", el).forEach((b) => b.onclick = () => guarded(async () => { await del(`packungen/${b.dataset.pdel}`); neu(); }));
  $$("[data-adel]", el).forEach((b) => b.onclick = () => guarded(async () => { await del(`aliase/${encodeURIComponent(b.dataset.adel)}`); neu(); }));
  $("#aadd", el).onclick = () => guarded(async () => { await put("aliase", {text: $("#anew", el).value, produkt_id: p.id}); neu(); });
}
