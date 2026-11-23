import {$, $$, api, esc, fmt, guarded, kamera, lokal, merken, post, toast} from "../lib.js";

// Vorhandenes per Barcode einbuchen: scannen (Kamera, Foto oder Eingabe) → prüfen → einbuchen.
// Unbekannte EANs fragt die App bei Open Food Facts an; nichts wird ohne Bestätigung gesetzt.
const FORMATE = ["ean_13", "ean_8", "upc_a", "upc_e"];
let liste = lokal("scan.liste", []);
const sichern = () => merken("scan.liste", liste);

export default async function render(el) {
  const [produkte, orte] = await Promise.all([api("produkte"), api("lagerorte")]);
  const live = "BarcodeDetector" in window && !!navigator.mediaDevices?.getUserMedia;
  const zeichnen = () => {
    el.innerHTML = `<div class="card"><h2>Barcode scannen</h2>
      <p class="muted small">Produkt in die Kamera halten. Bekannte Barcodes erkennt die App sofort, unbekannte schlägt sie über
        Open Food Facts nach (Name, Marke, Packungsgröße). Eingebucht wird erst unten mit „Einbuchen“.</p>
      <div class="row"><button class="btn primary" id="scan">📷 ${live ? "Scannen" : "Barcode fotografieren"}</button>
        <form id="manuell" style="flex:2 1 260px;display:flex;gap:6px"><input id="ean" inputmode="numeric" pattern="[0-9]*" placeholder="oder EAN eintippen" style="flex:1;min-width:0">
          <button class="btn" style="flex:0 0 auto">Suchen</button></form></div>
      <label class="inline"><input type="checkbox" id="mehrfach" ${lokal("scan.mehrfach", true) ? "checked" : ""}> Nach jedem Treffer weiter scannen</label>
      ${live ? "" : `<p class="muted tiny">Dieser Browser erkennt Barcodes nicht live (z. B. iPhone). Foto vom Barcode machen, der Server liest ihn.</p>`}
      <input id="bf" type="file" accept="image/*" capture="environment" hidden></div>
    <div class="card"><div class="kopf"><h2>Gescannt (${liste.length})</h2>
      ${liste.length ? `<div class="aktionen"><button class="btn klein danger" id="leeren">Liste leeren</button></div>` : ""}</div>
      ${liste.length ? `<div class="scan-liste">${liste.map(karte).join("")}</div>
        <button class="btn primary voll" id="buchen">${liste.length} ${liste.length === 1 ? "Eintrag" : "Einträge"} einbuchen</button>`
        : `<div class="leer">Noch nichts gescannt.</div>`}</div>`;
    binden();
  };

  const karte = (x, i) => {
    const g = x.info, off = g.off, p = g.bekannt;
    const einheit = x.produkt_id ? (produkte.find((q) => q.id === x.produkt_id)?.einheit ?? "") : x.einheit;
    return `<div class="scan-karte" data-i="${i}">
      <div class="scan-bild">${off?.bild ? `<img src="${esc(off.bild)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "🏷️"}</div>
      <div class="scan-inhalt">
        <div><b>${esc(p?.name ?? off?.name ?? "Unbekannt")}</b> ${p ? `<span class="tag good">bekannt</span>` : off ? `<span class="tag">Open Food Facts</span>` : `<span class="tag warn">nicht gefunden</span>`}</div>
        <div class="muted tiny">${esc(g.ean)}${off?.marke ? ` · ${esc(off.marke)}` : ""}${off?.quantity ? ` · ${esc(off.quantity)}` : ""}</div>
        <div class="scan-felder">
          <select data-f="produkt_id"><option value="">+ neues Produkt</option>${
            [...g.vorschlaege.map((v) => ({id: v.produkt_id, name: `≈ ${v.name}`})), ...produkte].map((q) =>
              `<option value="${esc(q.id)}" ${q.id === x.produkt_id ? "selected" : ""}>${esc(q.name)}</option>`).join("")}</select>
          ${x.produkt_id ? "" : `<input data-f="neu_name" value="${esc(x.neu_name ?? "")}" placeholder="Name">
            <select data-f="einheit">${["g", "ml", "Stück"].map((u) => `<option ${u === x.einheit ? "selected" : ""}>${u}</option>`).join("")}</select>`}
        </div>
        <div class="scan-felder">
          <span class="nw"><button class="btn klein" data-plus="-1">−</button> <b>${fmt(x.anzahl)}</b> <button class="btn klein" data-plus="1">+</button> ×</span>
          <span class="nw"><input data-f="packung_menge" type="number" min="0" step="any" value="${x.packung_menge ?? ""}" placeholder="Inhalt" style="width:90px"> ${esc(einheit)}</span>
          <input data-f="mhd" type="date" value="${esc(x.mhd ?? "")}" title="MHD (leer = Standard-Haltbarkeit)" style="width:auto">
          <select data-f="lagerort_id" style="width:auto"><option value="">Standard-Ort</option>${orte.map((o) =>
            `<option value="${esc(o.id)}" ${o.id === x.lagerort_id ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select>
          <button class="btn klein" data-weg title="Entfernen">✕</button>
        </div>
        ${x.produkt_id || x.packung_menge ? "" : `<div class="soon tiny">Packungsinhalt fehlt, sonst wird je Stück gebucht.</div>`}
      </div></div>`;
  };

  const hinzu = async (ean) => {
    ean = String(ean).replace(/\D/g, "");
    const da = liste.find((x) => x.info.ean === ean);
    if (da) { da.anzahl += 1; sichern(); zeichnen(); toast(`${da.info.bekannt?.name ?? da.info.off?.name ?? ean}: ${da.anzahl}×`); return true; }
    const info = await api(`barcode/${ean}`);
    const p = info.bekannt, off = info.off;
    liste.unshift({
      info, anzahl: 1, produkt_id: p?.produkt_id ?? null,
      neu_name: off?.name ?? "", einheit: off?.einheit ?? "g", marke: off?.marke ?? null,
      packung_menge: info.groesse?.menge ?? (p ? null : off?.menge ?? null), mhd: null, lagerort_id: null,
    });
    sichern(); zeichnen();
    toast(p ? `Bekannt: ${p.name}` : off ? `Gefunden: ${off.name}` : `${ean} unbekannt, bitte Produkt wählen`);
    navigator.vibrate?.(40);
    return true;
  };

  const scannen = async () => {
    try {
      if (live) {
        const code = await kamera({titel: "Barcode ins Feld halten", scan: FORMATE});
        if (!code) return;
        await hinzu(code);
      } else {
        const f = await kamera({titel: "Barcode groß und scharf fotografieren"});
        if (!f) return;
        await ausBild(f);
      }
      if ($("#mehrfach", el)?.checked) setTimeout(() => guarded(scannen), 400);
    } catch (e) {
      if (e?.name === "NotAllowedError" || /Kamera|HTTPS|unterstützt/.test(e?.message ?? "")) {
        toast(`${e.message || e}. Nimm die Kamera des Geräts …`); $("#bf", el).click();
      } else throw e;
    }
  };
  const ausBild = async (f) => {
    const daten = await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(f); });
    const r = await post("barcode/lesen", {daten});
    for (const c of r.codes) await hinzu(c);
  };

  const binden = () => {
    $("#scan", el).onclick = () => guarded(scannen);
    $("#bf", el).onchange = (ev) => guarded(() => ev.target.files[0] && ausBild(ev.target.files[0]));
    $("#manuell", el).onsubmit = (ev) => { ev.preventDefault(); const v = $("#ean", el).value; if (v) guarded(() => hinzu(v)); };
    $("#mehrfach", el).onchange = (ev) => merken("scan.mehrfach", ev.target.checked);
    const l = $("#leeren", el); if (l) l.onclick = () => { liste = []; sichern(); zeichnen(); };
    $$(".scan-karte", el).forEach((k) => {
      const x = liste[+k.dataset.i];
      $$("[data-f]", k).forEach((i) => i.onchange = () => {
        const f = i.dataset.f;
        x[f] = i.type === "number" ? (i.value ? +i.value : null) : i.value || null;
        if (f === "produkt_id") {
          const q = produkte.find((p) => p.id === x.produkt_id);
          if (q?.einheit && x.info.off?.menge && q.einheit === x.info.off.einheit) x.packung_menge = x.info.off.menge;
        }
        sichern(); if (["produkt_id", "packung_menge"].includes(f)) zeichnen();
      });
      $$("[data-plus]", k).forEach((b) => b.onclick = () => { x.anzahl = Math.max(1, x.anzahl + +b.dataset.plus); sichern(); zeichnen(); });
      $("[data-weg]", k).onclick = () => { liste.splice(+k.dataset.i, 1); sichern(); zeichnen(); };
    });
    const b = $("#buchen", el);
    if (b) b.onclick = () => guarded(async () => {
      const fehlt = liste.find((x) => !x.produkt_id && !x.neu_name?.trim());
      if (fehlt) throw new Error(`Name fehlt für ${fehlt.info.ean}`);
      const r = await post("barcode/einbuchen", {eintraege: liste.map((x) => ({
        ean: x.info.ean, anzahl: x.anzahl, packung_menge: x.packung_menge || null, marke: x.marke, mhd: x.mhd || null,
        lagerort_id: x.lagerort_id || null, ...(x.produkt_id ? {produkt_id: x.produkt_id} : {neu_name: x.neu_name.trim(), einheit: x.einheit})}))});
      const ok = new Set(r.gebucht.map((g) => g.ean));
      liste = liste.filter((x) => !ok.has(x.info.ean)); sichern(); zeichnen();
      toast(`${r.gebucht.length} eingebucht` + (r.neue_produkte.length ? ` · ${r.neue_produkte.length} neu` : "")
        + (r.fehler.length ? ` · Fehler: ${r.fehler.map((f) => f.fehler).join("; ")}` : ""), r.fehler.length ? "fehler" : "");
    });
  };
  zeichnen();
}
