import {$, $$, AUFWAND, REGLER, WOCHENTAGE, api, bestaetigen, del, esc, guarded, lokal, merken, post, put, toast} from "../lib.js";

// Einstellungen: Wochenrhythmus, Planer-Vorgaben, Präferenzen, Anzeige; HA-Optionen nur lesend
export default async function render(el) {
  const [e, buch, kt, ks] = await Promise.all([api("einstellungen"), api("rezeptbuch"),
    api("kochen/timer").catch((err) => ({timer: [], automatisch: true, fehler: err.message})),
    api("ki/status").catch(() => ({jobs: [], entitaeten: [], openai_key: false}))]);
  const kiE = e.ki;
  const {planer, praeferenzen: pr, anzeige, kochen: ko, ernaehrung: er, optionen: o} = e;
  const tags = [...new Set([...buch.flatMap((r) => r.tags), ...pr.ausschluss_tags])].sort((a, b) => a.localeCompare(b, "de"));
  const theme = lokal("theme", "auto");
  const ja = (x) => x ? `<span class="ok">gesetzt</span>` : `<span class="muted">nicht gesetzt</span>`;

  el.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr))">
    <div class="card"><div class="kopf"><h2>Wochenrhythmus</h2><div class="aktionen"><button class="btn klein" data-reset="planer,praeferenzen">Standard</button></div></div>
      <p class="muted small">An welchen Tagen gekocht wird (Vorauswahl im Kalender) und wie viel Aufwand der Tag verträgt.</p>
      <table>${WOCHENTAGE.map((w, i) => `<tr><td><label class="inline" style="margin:0"><input type="checkbox" data-kt="${i}" ${planer.kochtage.includes(i) ? "checked" : ""}> ${w}</label></td>
        <td><select data-auf="${i}">${Object.entries(AUFWAND).map(([k, t]) => `<option value="${k}" ${pr.aufwand_je_tag[String(i)] === k ? "selected" : ""}>bis ${t}</option>`).join("")}</select></td></tr>`).join("")}</table>
      <div class="chips" style="margin-top:8px"><button class="chip" data-preset="werktag">Werktags schnell</button>
        <button class="chip" data-preset="alle">Alles erlaubt</button></div></div>
    <div class="card"><h2>Planer</h2>
      <div class="row"><div><label>Portionen je Tag</label><input id="por" type="number" min="1" max="6" value="${planer.portionen}"></div>
        <div><label>Max. ungetestete je Plan</label><input id="mu" type="number" min="0" max="7" value="${planer.max_ungetestet}"></div></div>
      <label class="inline"><input type="checkbox" id="ki" ${planer.ki ? "checked" : ""}> KI wählt aus (sonst nur der Code)</label>
      <p class="muted tiny">Die KI sieht nur die vom Code vorsortierten Kandidaten und gibt IDs zurück. Mengen, Einkauf und Verderb rechnet immer der Code.</p>
      <h3>Präferenzen</h3>
      ${REGLER.map(([k, t, l, r]) => `<div class="regler"><span class="name">${t}</span>
        <input type="range" min="-1" max="1" step="0.25" id="r_${k}" value="${pr[k]}"><div class="enden"><span>${l}</span><span>${r}</span></div></div>`).join("")}
      ${tags.length ? `<label>Nie planen (Tags)</label><div class="chips">${tags.map((t) =>
        `<button class="chip ${pr.ausschluss_tags.includes(t) ? "on" : ""}" data-aus="${esc(t)}">${esc(t)}</button>`).join("")}</div>` : ""}
      <button class="btn primary voll" id="speichern">Speichern</button></div>
    <div class="card"><h2>Ernährung</h2>
      <p class="muted small">Ziel für <b>eine</b> Mahlzeit (das geplante Gericht des Tages). Rezepte zeigen, wie gut eine Portion passt.</p>
      <div class="row"><div><label>kcal je Mahlzeit</label><input id="ekcal" type="number" min="100" max="3000" step="50" value="${er.kcal_mahlzeit}"></div>
        <div><label>Protein mind. (g)</label><input id="eprot" type="number" min="0" max="200" value="${er.protein_mahlzeit}"></div></div>
      <label class="inline"><input type="checkbox" id="eplan" ${er.im_planer ? "checked" : ""}> Ziel im Wochenplaner berücksichtigen</label>
      <p class="muted tiny">Nährwerte je 100 g pflegst du am Produkt oder lässt sie unter KI-Vorschläge schätzen. Rezepte, bei denen weniger als 80 % des Gewichts Werte haben, bleiben im Planer neutral.</p>
      <button class="btn voll" id="ern">Speichern</button></div>
    <div class="card"><h2>Anzeige</h2>
      <label>„Läuft bald ab“ in den nächsten … Tagen</label><input id="bald" type="number" min="1" max="14" value="${anzeige.bald_tage}">
      <label>Farbschema</label><div class="chips">${[["auto", "🖥️ System"], ["light", "☀️ Hell"], ["dark", "🌙 Dunkel"]].map(([k, t]) =>
        `<button class="chip ${theme === k ? "on" : ""}" data-theme="${k}">${t}</button>`).join("")}</div>
      <button class="btn voll" id="anz">Speichern</button>
      <h3>Lokal</h3><p class="muted tiny">Filter, Sortierung und eingeklappte Menügruppen merkt sich der Browser.</p>
      <button class="btn klein" id="lokal">Gemerkte Ansicht zurücksetzen</button></div>
    <div class="card"><h2>Kochmodus</h2>
      <label class="inline"><input type="checkbox" id="zp" ${ko.zutaten_pflicht ? "checked" : ""}> Vor „Weiter“ nachfragen, wenn Zutaten des Schritts nicht abgehakt sind</label>
      <h3>Timer</h3>${kt.timer.length ? `<table>${kt.timer.map((t) => `<tr><td>${esc(t.name)}</td><td class="muted tiny">${esc(t.entity_id)}</td>
        <td class="right">${t.state === "idle" ? `<span class="muted">frei</span>` : esc(t.state)}</td></tr>`).join("")}</table>`
        : `<p class="muted small">${kt.fehler ? `HA nicht erreichbar (${esc(kt.fehler)}).` : "Keine Timer gefunden."} Der Kochmodus nimmt dann Browser-Timer.</p>`}
      <p class="muted tiny">${kt.automatisch ? "Automatisch erkannt (Küche/kitchen/koch im Namen). " : ""}Festlegen über die App-Option <code>timer_entities</code>
        in Home Assistant. Der Kochmodus startet jeweils den ersten freien Timer.</p></div>
    <div class="card" style="grid-column:1/-1"><h2>KI</h2>
      <p class="muted small">Standard für alle Aufgaben, je Aufgabe überschreibbar. <b>HA</b> nutzt <code>ai_task.generate_data</code> (Tokens geschätzt, ≈),
        <b>OpenAI direkt</b> die App-Option <code>openai_api_key</code> (Key ${ja(ks.openai_key)}, echte Tokens).</p>
      <div class="row"><div><label>Standard</label><select id="kimodus"><option value="ha" ${kiE.modus === "ha" ? "selected" : ""}>HA (ai_task)</option>
          <option value="openai" ${kiE.modus === "openai" ? "selected" : ""}>OpenAI direkt</option></select></div>
        <div><label>OpenAI-Modell (Standard)</label><input id="kimodell" value="${esc(kiE.openai_modell)}" placeholder="gpt-4.1-mini"></div>
        <div><label>AI-Task-Entität (Standard)</label><input value="${esc(o.ai_task_entity || "–")}" disabled title="App-Option ai_task_entity"></div></div>
      <datalist id="ki-ent">${ks.entitaeten.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
      <div class="tabelle"><table><thead><tr><th>Aufgabe</th><th>Modus</th><th>Entität (HA)</th><th>Modell (OpenAI)</th><th>Status</th></tr></thead><tbody>
        ${ks.jobs.map((j) => { const c = kiE.jobs[j.job] ?? {}; return `<tr data-job="${j.job}"><td>${esc(j.name)}</td>
          <td><select data-k="modus"><option value="">Standard</option><option value="ha" ${c.modus === "ha" ? "selected" : ""}>HA</option>
            <option value="openai" ${c.modus === "openai" ? "selected" : ""}>OpenAI</option></select></td>
          <td><input data-k="entitaet" list="ki-ent" value="${esc(c.entitaet ?? "")}" placeholder="Standard"></td>
          <td><input data-k="modell" value="${esc(c.modell ?? "")}" placeholder="Standard"></td>
          <td class="tiny">${j.fehlt ? `<span class="over">${esc(j.fehlt)}</span>` : `<span class="ok">✓</span> ${esc(j.backend === "openai" ? j.modell : j.entitaet)}`}</td></tr>`; }).join("")}
      </tbody></table></div>
      <h3>Preise <span class="muted tiny">(€ je 1 Mio. Tokens, je Modell oder AI-Task-Entität)</span></h3>
      <div class="tabelle"><table><thead><tr><th>Modell / Entität</th><th>Input</th><th>Output</th><th></th></tr></thead><tbody id="preise">
        ${[...Object.entries(kiE.preise), ["", {input: "", output: ""}]].map(([k, p]) => preisZeile(k, p)).join("")}</tbody></table></div>
      <p class="muted tiny">Ohne Preis zeigt das KI-Protokoll nur Tokens. Preise der Anbieter ändern sich, bitte selbst pflegen.</p>
      <button class="btn primary voll" id="kispeichern">KI-Einstellungen speichern</button></div>
    <div class="card"><h2>App-Optionen <span class="muted tiny">(nur lesen)</span></h2>
      <p class="muted tiny">Änderbar in Home Assistant unter Einstellungen → Apps → ration → Konfiguration.</p>
      <table>
        <tr><td class="muted">Version</td><td>${esc(o.version)} · API ${esc(o.api_version)}</td></tr>
        <tr><td class="muted">Grocy</td><td>${esc(o.grocy_url ?? "–")} · Key ${ja(o.grocy_api_key)}</td></tr>
        <tr><td class="muted">KI-Entität</td><td>${esc(o.ai_task_entity ?? "–")}</td></tr>
        <tr><td class="muted">OpenAI-Key</td><td>${ja(o.openai_api_key)}</td></tr>
        <tr><td class="muted">Einkaufsliste</td><td>${esc(o.todo_entity ?? "–")}</td></tr>
        <tr><td class="muted">Timer-Entitäten</td><td>${o.timer_entities?.length ? o.timer_entities.map(esc).join(", ") : `<span class="muted">automatisch</span>`}</td></tr>
        <tr><td class="muted">Benachrichtigung</td><td>${esc(o.notify_service ?? "–")}</td></tr>
        <tr><td class="muted">Toleranz-Standard</td><td>${esc(o.toleranz_default_pct)} %</td></tr>
        <tr><td class="muted">Bon-Konfidenz</td><td>${esc(o.bon_konfidenz_schwelle)}</td></tr>
        <tr><td class="muted">Dubletten-Schwelle</td><td>${esc(o.dubletten_schwelle)}</td></tr>
        <tr><td class="muted">API-Token</td><td>${ja(o.api_token)}</td></tr></table>
      <h3>Testdaten</h3><p class="muted tiny">Legt 2 Testrezepte samt Produkten in Grocy an (idempotent, löscht nichts).</p>
      <button class="btn klein" id="test">Testrezepte anlegen</button></div></div>`;

  const ausschluss = new Set(pr.ausschluss_tags);
  $$("[data-aus]", el).forEach((b) => b.onclick = () => {
    ausschluss.has(b.dataset.aus) ? ausschluss.delete(b.dataset.aus) : ausschluss.add(b.dataset.aus); b.classList.toggle("on");
  });
  $$("[data-preset]", el).forEach((b) => b.onclick = () => $$("[data-auf]", el).forEach((s) => {
    s.value = b.dataset.preset === "alle" || +s.dataset.auf >= 5 ? "aufwaendig" : "schnell";
  }));
  $("#speichern", el).onclick = () => guarded(async () => {
    await put("einstellungen/planer", {portionen: +$("#por", el).value, max_ungetestet: +$("#mu", el).value, ki: $("#ki", el).checked,
      kochtage: $$("[data-kt]", el).filter((c) => c.checked).map((c) => +c.dataset.kt)});
    await put("einstellungen/praeferenzen", {...Object.fromEntries(REGLER.map(([k]) => [k, +$(`#r_${k}`, el).value])),
      ausschluss_tags: [...ausschluss], aufwand_je_tag: Object.fromEntries($$("[data-auf]", el).map((s) => [s.dataset.auf, s.value]))});
    toast("Einstellungen gespeichert");
  });
  // Wochenrhythmus-Felder speichern mit demselben Knopf; Änderungen dort sofort übernehmen wäre zu viel Lärm im Protokoll
  $$("[data-kt],[data-auf]", el).forEach((x) => x.addEventListener("change", () => $("#speichern", el).classList.add("primary")));
  $$("[data-theme]", el).forEach((b) => b.onclick = () => {
    const t = b.dataset.theme; merken("theme", t);
    if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
    $$("[data-theme]", el).forEach((x) => x.classList.toggle("on", x === b));
  });
  $("#zp", el).onchange = (ev) => guarded(async () => { await put("einstellungen/kochen", {zutaten_pflicht: ev.target.checked}); toast("Gespeichert"); });
  $("#ern", el).onclick = () => guarded(async () => {
    await put("einstellungen/ernaehrung", {kcal_mahlzeit: +$("#ekcal", el).value, protein_mahlzeit: +$("#eprot", el).value, im_planer: $("#eplan", el).checked});
    toast("Gespeichert");
  });
  $("#anz", el).onclick = () => guarded(async () => { await put("einstellungen/anzeige", {bald_tage: +$("#bald", el).value}); toast("Gespeichert"); });
  $("#lokal", el).onclick = () => {
    try { Object.keys(localStorage).filter((k) => k.startsWith("ration.") && k !== "ration.theme").forEach((k) => localStorage.removeItem(k)); } catch { /* egal */ }
    toast("Ansicht zurückgesetzt");
  };
  $$("[data-reset]", el).forEach((b) => b.onclick = () => guarded(async () => {
    if (!await bestaetigen("Wochenrhythmus, Planer-Vorgaben und Präferenzen auf Standard zurücksetzen?", "Zurücksetzen")) return;
    for (const g of b.dataset.reset.split(",")) await del(`einstellungen/${g}`);
    toast("Zurückgesetzt"); render(el);
  }));
  const neueZeile = () => {
    const leer = $$("#preise tr", el).filter((tr) => !$("[data-p=k]", tr).value.trim());
    if (!leer.length) $("#preise", el).insertAdjacentHTML("beforeend", preisZeile("", {input: "", output: ""}));
    $$("#preise [data-p]", el).forEach((i) => i.oninput = neueZeile);
    $$("#preise [data-weg]", el).forEach((b) => b.onclick = () => { b.closest("tr").remove(); neueZeile(); });
  };
  neueZeile();
  $("#kispeichern", el).onclick = () => guarded(async () => {
    const jobs = Object.fromEntries($$("[data-job]", el).map((tr) => [tr.dataset.job,
      Object.fromEntries($$("[data-k]", tr).map((i) => [i.dataset.k, i.value.trim()]))]));
    const preise = Object.fromEntries($$("#preise tr", el).map((tr) => [$("[data-p=k]", tr).value.trim(),
      {input: +$("[data-p=i]", tr).value || 0, output: +$("[data-p=o]", tr).value || 0}]).filter(([k]) => k));
    await put("einstellungen/ki", {modus: $("#kimodus", el).value, openai_modell: $("#kimodell", el).value.trim(), jobs, preise});
    toast("KI-Einstellungen gespeichert"); render(el);
  });
  $("#test", el).onclick = () => guarded(async () => {
    const r = await post("testdaten"); toast(`${r.rezepte.filter((x) => x.neu).length} Testrezepte angelegt`);
  });
}

const preisZeile = (k, p) => `<tr><td><input data-p="k" list="ki-ent" value="${esc(k)}" placeholder="z. B. gpt-4.1-mini"></td>
  <td><input data-p="i" type="number" min="0" step="0.01" value="${p.input}" style="width:90px"></td>
  <td><input data-p="o" type="number" min="0" step="0.01" value="${p.output}" style="width:90px"></td>
  <td><button class="btn klein" data-weg title="Entfernen">✕</button></td></tr>`;
