import {$, $$, api, bestaetigen, esc, fmt, guarded, MODI, post, toast} from "../lib.js";

// Dubletten: Paare nebeneinander, Feld für Feld, mergen oder als „kein Duplikat“ merken
export default async function render(el, {params}) {
  const schwelle = params.schwelle ? +params.schwelle : null;
  const alle = await api("dubletten" + (schwelle ? `?schwelle=${schwelle}` : ""));
  const zeigen = Math.max(+(params.n ?? 15) || 15, 15), dub = alle.slice(0, zeigen);
  const FELDER = [["einheit", "Einheit"], ["bestand", "Bestand", (p) => `${fmt(p.bestand)} ${esc(p.einheit)}`],
    ["tracking_modus", "Modus", (p) => esc(MODI[p.tracking_modus] ?? "–")], ["kategorie", "Kategorie"],
    ["packung", "Packung", (p) => p.packung ? `${esc(p.packung.einheit)} ${fmt(p.packung.menge)}` : "–"],
    ["haltbarkeit_tage", "Haltbar (Tage)"], ["g_je_einheit", "g je Einheit"], ["parent_id", "Variante von"]];
  const wert = (p, [k, , f]) => f ? f(p) : esc(p[k] ?? "–");
  el.innerHTML = `<div class="card"><div class="kopf"><h2>Mögliche Dubletten <span class="muted">(${alle.length})</span></h2>
      <div class="aktionen"><label class="inline" style="margin:0">Empfindlichkeit
        <select id="schwelle">${[[null, "Standard"], [0.95, "streng"], [0.85, "normal"], [0.75, "locker"]].map(([v, t]) =>
          `<option value="${v ?? ""}" ${v === schwelle ? "selected" : ""}>${t}</option>`).join("")}</select></label></div></div>
    <p class="muted small">Beim Mergen wandern Bestand, Rezepte, Aliase und Packungen zum Produkt, das bleibt. Das andere wird in Grocy gelöscht.
      „Kein Duplikat“ merkt sich das Paar dauerhaft.</p></div>
    ${dub.length ? dub.map((k, i) => `<div class="card" data-i="${i}"><div class="kopf"><h3 style="margin:0">${esc(k.a.name)} ↔ ${esc(k.b.name)}</h3>
        <span class="tag ${k.score > 0.9 ? "bad" : "warn"}">${Math.round(k.score * 100)} % · ${esc(k.grund)}</span></div>
      <div class="tabelle"><table><thead><tr><th></th>
        <th><label class="inline" style="margin:0"><input type="radio" name="keep${i}" value="${esc(k.a.id)}" ${k.a.bestand >= k.b.bestand ? "checked" : ""}> behalten: <b>${esc(k.a.name)}</b></label></th>
        <th><label class="inline" style="margin:0"><input type="radio" name="keep${i}" value="${esc(k.b.id)}" ${k.a.bestand < k.b.bestand ? "checked" : ""}> behalten: <b>${esc(k.b.name)}</b></label></th></tr></thead>
      <tbody>${FELDER.map((f) => { const x = wert(k.a, f), y = wert(k.b, f);
        return `<tr><td class="muted nw">${f[1]}</td><td class="${x !== y ? "soon" : ""}">${x}</td><td class="${x !== y ? "soon" : ""}">${y}</td></tr>`; }).join("")}</tbody></table></div>
      <div class="row" style="margin-top:10px"><button class="btn primary" data-merge="${i}">Zusammenführen</button>
        <button class="btn" data-nein="${i}">Kein Duplikat</button>
        <a class="btn" href="#/produkt/${esc(k.a.id)}">${esc(k.a.name)} öffnen</a><a class="btn" href="#/produkt/${esc(k.b.id)}">${esc(k.b.name)} öffnen</a></div></div>`).join("")
      : `<div class="card"><div class="leer">Keine Dubletten gefunden. 🎉</div></div>`}
    ${alle.length > dub.length ? `<div class="card"><p class="muted">${dub.length} von ${alle.length} gezeigt, sortiert nach Ähnlichkeit.</p>
      <a class="btn" href="#/dubletten?n=${zeigen + 15}${schwelle ? `&schwelle=${schwelle}` : ""}">15 weitere</a></div>` : ""}`;
  $("#schwelle", el).onchange = (e) => { location.hash = "#/dubletten" + (e.target.value ? `?schwelle=${e.target.value}` : ""); };
  $$("[data-merge]", el).forEach((b) => b.onclick = () => guarded(async () => {
    const k = dub[+b.dataset.merge], behalten = $(`input[name=keep${b.dataset.merge}]:checked`, el).value;
    const weg = behalten === k.a.id ? k.b : k.a, bleibt = behalten === k.a.id ? k.a : k.b;
    if (!await bestaetigen(`„${esc(weg.name)}“ in „${esc(bleibt.name)}“ zusammenführen? „${esc(weg.name)}“ wird in Grocy gelöscht.`, "Zusammenführen", true)) return;
    const e = await post("dubletten/mergen", {behalten, entfernen: weg.id});
    toast(`„${e.entfernt}“ in „${e.behalten}“ zusammengeführt`); render(el, {params});
  }));
  $$("[data-nein]", el).forEach((b) => b.onclick = () => guarded(async () => {
    const k = dub[+b.dataset.nein];
    await post("dubletten/ignorieren", {behalten: k.a.id, entfernen: k.b.id});
    toast("Gemerkt: kein Duplikat"); render(el, {params});
  }));
}
