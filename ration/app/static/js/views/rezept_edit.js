import {$, $$, AUFWAND, QUELLEN, api, quelleInfo, esc, guarded, patch, post, toast, gehe, titel} from "../lib.js";

// Rezept anlegen (ohne arg) oder bearbeiten. Mengen in Bestandseinheit für die Basis-Portionen.
export default async function render(el, {arg}) {
  const [r, produkte, buch] = await Promise.all([
    arg ? api(`rezepte/${arg}`) : null, api("produkte"), api("rezeptbuch")]);
  titel(r ? `${r.name} bearbeiten` : "Neues Rezept");
  const tags = [...new Set(buch.flatMap((x) => x.tags))].sort();
  const einheit = Object.fromEntries(produkte.map((p) => [p.id, p.einheit]));
  const opt = (sel) => `<option value="">– Produkt –</option>` + produkte.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === sel ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const zeile = (z = {}) => `<tr data-zrow><td><select data-zp>${opt(z.produkt_id)}</select></td>
    <td style="width:110px"><input data-zm type="number" min="0" step="any" value="${z.menge_basis ?? ""}"></td>
    <td style="width:40px" class="muted tiny" data-ze>${esc(einheit[z.produkt_id] ?? "")}</td>
    <td><input data-zn placeholder="Notiz (optional)" value="${esc(z.notiz ?? "")}"></td>
    <td style="width:36px"><button class="icon" data-zdel title="Entfernen">✕</button></td></tr>`;

  el.innerHTML = `<div class="card">
    <div class="row"><div style="flex:3"><label>Name</label><input id="name" value="${esc(r?.name ?? "")}" required></div>
      <div><label>Portionen</label><input id="por" type="number" min="1" max="24" value="${r?.portionen_basis ?? 2}"></div></div>
    <div class="row"><div><label>Status</label><select id="stat">${["ungetestet", "getestet"].map((s) =>
        `<option ${s === (r?.status ?? "ungetestet") ? "selected" : ""}>${s}</option>`).join("")}</select></div>
      <div><label>Aufwand</label><select id="auf"><option value="">–</option>${Object.entries(AUFWAND).map(([k, t]) =>
        `<option value="${k}" ${k === r?.aufwand ? "selected" : ""}>${t}</option>`).join("")}</select></div></div>
    <div class="row"><div><label>Quelle</label><input id="quelle" list="quellliste" value="${esc(r ? quelleInfo(r.quelle).label : "manuell")}"
        placeholder="manuell, HelloFresh, Chefkoch, Website …">
      <datalist id="quellliste">${Object.values(QUELLEN).map((t) => `<option value="${esc(t)}">`).join("")}</datalist></div>
      <div style="flex:2"><label>Link zum Original (optional)</label><input id="url" type="url" placeholder="https://…" value="${esc(r?.url ?? "")}"></div></div>
    <label>Tags (mit Komma)</label><input id="tags" list="tagliste" value="${esc((r?.tags ?? []).join(", "))}">
    <datalist id="tagliste">${tags.map((t) => `<option value="${esc(t)}">`).join("")}</datalist>
    <div class="chips" style="margin-top:6px">${tags.slice(0, 20).map((t) => `<button class="chip" data-tagadd="${esc(t)}">+ ${esc(t)}</button>`).join("")}</div>
    <h3>Zutaten</h3><p class="muted tiny">Menge in der Bestandseinheit des Produkts, für die oben angegebenen Portionen.
      Fehlt ein Produkt? <a href="#/produkt-neu">Neu anlegen</a></p>
    <div class="tabelle"><table id="zut"><tbody>${(r?.zutaten ?? []).map(zeile).join("") || zeile()}</tbody></table></div>
    <button class="btn klein" id="zadd" style="margin-top:6px">+ Zutat</button>
    <h3>Zubereitung</h3><textarea id="schr" rows="10" placeholder="Ein Schritt pro Zeile">${esc((r?.schritte ?? []).join("\n"))}</textarea>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="save">${r ? "Speichern" : "Anlegen"}</button>
      <a class="btn" href="${r ? `#/rezept/${esc(r.id)}` : "#/rezepte"}">Abbrechen</a></div></div>`;

  const binden = () => {
    $$("[data-zdel]", el).forEach((b) => b.onclick = () => b.closest("tr").remove());
    $$("[data-zp]", el).forEach((s) => s.onchange = () => { s.closest("tr").querySelector("[data-ze]").textContent = einheit[s.value] ?? ""; });
  };
  binden();
  $("#zadd", el).onclick = () => { $("#zut tbody", el).insertAdjacentHTML("beforeend", zeile()); binden(); };
  $$("[data-tagadd]", el).forEach((b) => b.onclick = () => {
    const t = $("#tags", el), liste = t.value.split(",").map((x) => x.trim()).filter(Boolean);
    if (!liste.includes(b.dataset.tagadd)) t.value = [...liste, b.dataset.tagadd].join(", ");
  });
  $("#save", el).onclick = () => guarded(async () => {
    const zutaten = $$("[data-zrow]", el).map((tr) => ({produkt_id: $("[data-zp]", tr).value, menge: +$("[data-zm]", tr).value,
      notiz: $("[data-zn]", tr).value.trim() || null})).filter((z) => z.produkt_id);
    if (zutaten.some((z) => !z.menge)) throw new Error("Bei jeder Zutat eine Menge angeben");
    const body = {name: $("#name", el).value.trim(), portionen: +$("#por", el).value, zutaten, status: $("#stat", el).value,
      tags: $("#tags", el).value.split(",").map((t) => t.trim()).filter(Boolean),
      schritte: $("#schr", el).value.split("\n").map((t) => t.trim()).filter(Boolean)};
    if ($("#auf", el).value) body.aufwand = $("#auf", el).value;
    const ql = $("#quelle", el).value.trim();
    body.quelle = Object.entries(QUELLEN).find(([, t]) => t.toLowerCase() === ql.toLowerCase())?.[0] ?? ql;
    body.url = $("#url", el).value.trim();
    if (!body.name) throw new Error("Name fehlt");
    const id = r ? (await patch(`rezepte/${r.id}`, body), r.id) : (await post("rezepte", body)).id;
    toast(r ? "Rezept gespeichert" : "Rezept angelegt"); gehe(`rezept/${id}`);
  });
}
