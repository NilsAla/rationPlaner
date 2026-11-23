import {$, api, bestaetigen, esc, gehe, guarded, post, tabelle, toast, zeit} from "../lib.js";

// Übersicht der Kochanleitungen: Status je Rezept, KI-Umschreiben einzeln/alle, Freigabe
const STATUS = {
  keine: `<span class="tag">keine</span>`,
  entwurf: `<span class="tag warn">Entwurf</span>`,
  geprueft: `<span class="tag good">freigegeben</span>`,
};
const QUELLE = {ki: "✨ KI", code: "Code", manuell: "von Hand"};

export default async function render(el) {
  let timer = null, aktiv = true;
  const laden = async () => {
    const [liste, laeufe] = await Promise.all([api("kochen/anleitungen"), api("kochen/migrieren")]);
    if (!aktiv) return;
    const lauf = laeufe[0];
    const fehlen = liste.filter((r) => r.zutaten && (r.status === "keine" || r.veraltet)).length;
    const entwuerfe = liste.filter((r) => r.status === "entwurf").length;
    el.innerHTML = `<div class="card"><div class="kopf"><h2>Kochanleitungen</h2><div class="aktionen">
        <button class="btn primary" id="alle" ${fehlen ? "" : "disabled"}>✨ Alle fehlenden umschreiben (${fehlen})</button>
        <button class="btn" id="alle_frei" ${entwuerfe ? "" : "disabled"}>✓ Alle Entwürfe freigeben (${entwuerfe})</button></div></div>
      <p class="muted small">Die KI ordnet die Schritte wie beim echten Kochen (Wasser aufsetzen, schneiden, während es kocht …) und ordnet jedem Schritt seine
        Zutaten zu. Mengen rechnet der Code. Ergebnisse landen als Entwurf in der App, das Rezept in Grocy bleibt unverändert.
        Ein KI-Aufruf je Rezept. Scheitert die KI, bleibt die bisherige Anleitung stehen und der nächste Lauf versucht es erneut.</p>
      <div id="lauf">${lauf ? laufHtml(lauf) : ""}</div>
      <div id="tab" style="margin-top:10px"></div></div>`;
    tabelle($("#tab", el), {
      zeilen: liste, id: (r) => r.rezept_id, sortierung: "name", auswahl: () => {}, leer: "Keine Rezepte.",
      onKlick: (r) => gehe(`rezept/${r.rezept_id}/anleitung`),
      aktionen: `<button class="btn klein" data-akt="ki">✨ Mit KI umschreiben</button>
        <button class="btn klein" data-akt="frei">✓ Freigeben</button>`,
      spalten: [
        {key: "name", titel: "Rezept", wert: (r) => `<b>${esc(r.name)}</b>${r.hinweise.length
          ? `<div class="muted tiny" title="${esc(r.hinweise.join("\n"))}">⚠ ${esc(r.hinweise[0])}</div>` : ""}`},
        {key: "status", titel: "Status", klasse: "nw", sort: (r) => ({keine: 0, entwurf: 1, geprueft: 2})[r.status],
          wert: (r) => STATUS[r.status] + (r.veraltet ? ` <span class="tag bad" title="Rezept seit dem Umschreiben geändert">veraltet</span>` : "")},
        {key: "quelle", titel: "Quelle", klasse: "nw", wert: (r) => esc(QUELLE[r.quelle] ?? "–")},
        {key: "schritte", titel: "Schritte", klasse: "nw right", wert: (r) => r.status === "keine"
          ? `<span class="muted">${r.original_schritte} im Original</span>` : `${r.schritte} <span class="muted tiny">(Original ${r.original_schritte})</span>`},
        {key: "koch", titel: "", klasse: "nw right", sort: () => 0,
          wert: (r) => `<a class="btn klein" href="#/kochen/${esc(r.rezept_id)}?test=1" title="Testlauf">🧪</a>
            <a class="btn klein" href="#/kochen/${esc(r.rezept_id)}" title="Kochmodus">👩‍🍳</a>`},
      ],
    });
    $("#tab", el).onclick = (ev) => {
      const b = ev.target.closest("[data-akt]");
      if (!b) return;
      const ids = [...$("#tab", el)._tabelle.gewaehlt];
      const mitZutaten = ids.filter((id) => liste.find((r) => r.rezept_id === id)?.zutaten);
      guarded(() => b.dataset.akt === "ki" ? umschreiben(mitZutaten, false) : freigeben(ids));
    };
    $("#alle", el).onclick = () => guarded(() => umschreiben(null, true, fehlen));
    $("#alle_frei", el).onclick = () => guarded(() => freigeben(liste.filter((r) => r.status === "entwurf").map((r) => r.rezept_id)));
    clearTimeout(timer);
    if (lauf?.status === "laeuft") timer = setTimeout(() => guarded(laden), 3000);
  };

  const laufHtml = (l) => {
    const pct = Math.round(100 * l.fertig / Math.max(l.gesamt, 1));
    const fehler = l.fehlgeschlagen?.length ?? 0;
    if (l.status === "laeuft") {
      return `<div class="card" style="background:var(--accent-soft)"><b>KI schreibt um … ${l.fertig} von ${l.gesamt}</b>
        <div class="fortschritt"><div style="width:${pct}%"></div></div>
        <p class="muted tiny">Läuft im Hintergrund weiter, du kannst die Seite verlassen. Am Ende kommt eine Benachrichtigung.</p></div>`;
    }
    return `<p class="small muted">Letzter Lauf ${zeit(l.angelegt)}: ${l.status === "fehler" ? `<span class="tag bad">abgebrochen</span> ${esc(l.fehler ?? "")}`
      : `${l.fertig - fehler} von ${l.gesamt} umgeschrieben`}${fehler ? `, ${fehler} fehlgeschlagen (${esc(l.fehlgeschlagen[0].fehler)})` : ""}.</p>`;
  };

  const umschreiben = async (ids, nurFehlende, anzahl) => {
    if (ids && !ids.length) { toast("Keins der gewählten Rezepte hat Zutaten", "fehler"); return; }
    const n = anzahl ?? ids.length;
    if (!await bestaetigen(`${n} Rezept(e) per KI umschreiben? Das sind ${n} KI-Aufrufe. Vorhandene Anleitungen der Auswahl werden als neuer Entwurf ersetzt.`, "✨ Umschreiben")) return;
    const r = await post("kochen/migrieren", ids ? {rezept_ids: ids, nur_fehlende: nurFehlende} : {nur_fehlende: true});
    toast(`Lauf gestartet: ${r.rezepte} Rezept(e)`);
    $("#tab", el)._tabelle?.gewaehlt.clear();
    await laden();
  };

  const freigeben = async (ids) => {
    if (!ids.length) return;
    const r = await post("kochen/anleitungen/freigeben", {ids});
    toast(`${r.ok.length} freigegeben${r.fehler.length ? `, ${r.fehler.length} ohne Anleitung übersprungen` : ""}`);
    await laden();
  };

  await laden();
  return () => { aktiv = false; clearTimeout(timer); };
}
