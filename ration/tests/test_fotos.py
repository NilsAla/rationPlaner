"""Rezeptfotos: Upload, Hauptbild, Schrittbilder aus HelloFresh-Seiten, Kochmodus."""

import base64
import io
import json

from PIL import Image

from app import fotos


def _png(farbe="red", groesse=(2400, 1200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", groesse, farbe).save(buf, "PNG")
    return buf.getvalue()


def test_upload_hauptbild_und_loeschen(client):
    r = client.post(
        "/api/v1/rezepte/paprika-pasta/fotos",
        json={"daten": "data:image/png;base64," + base64.b64encode(_png()).decode()},
    )
    assert r.status_code == 201, r.text
    f1 = r.json()
    assert f1["haupt"] is True and (f1["breite"], f1["hoehe"]) == (1600, 800)
    gross = client.get(f"/api/v1/fotos/{f1['id']}")
    assert gross.headers["content-type"] == "image/jpeg"
    klein = Image.open(io.BytesIO(client.get(f"/api/v1/fotos/{f1['id']}?klein=1").content))
    assert max(klein.size) == fotos.KLEIN_PX
    # Schrittfoto wird nicht Hauptbild, zweites Ergebnisfoto auch nicht automatisch
    f2 = client.post(
        "/api/v1/rezepte/paprika-pasta/fotos",
        json={"daten": base64.b64encode(_png("blue", (300, 300))).decode(), "schritt": 1},
    ).json()
    assert f2["haupt"] is False and f2["schritt"] == 1
    buch = {x["id"]: x for x in client.get("/api/v1/rezeptbuch").json()}
    assert buch["paprika-pasta"]["foto"] == f1["id"]
    assert client.patch(f"/api/v1/fotos/{f2['id']}", json={"haupt": True}).json()["haupt"]
    detail = client.get("/api/v1/rezepte/paprika-pasta").json()
    assert [f["id"] for f in detail["fotos"] if f["haupt"]] == [f2["id"]]
    # Kein Bild → 422
    kaputt = client.post(
        "/api/v1/rezepte/paprika-pasta/fotos", json={"daten": base64.b64encode(b"x" * 50).decode()}
    )
    assert kaputt.status_code == 422
    assert client.delete(f"/api/v1/fotos/{f2['id']}").status_code == 200
    assert client.get(f"/api/v1/fotos/{f2['id']}").status_code == 404
    # Hauptbild gelöscht → erstes Ergebnisfoto rückt nach
    assert client.get("/api/v1/rezepte/paprika-pasta/fotos").json()[0]["haupt"] is True


HF_HTML = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Recipe",
"name":"Test","image":"https://img.hellofresh.com/w_1200/hellofresh_s3/image/haupt.jpg",
"recipeIngredient":["200 g Nudeln"],
"recipeInstructions":[{"@type":"HowToStep","text":"Eins"},{"@type":"HowToStep","text":"Zwei"}]}
</script>
<script id="__NEXT_DATA__" type="application/json">""" + json.dumps(
    {"props": {"pageProps": {"recipe": {"steps": [
        {"index": 1, "instructions": "Eins", "images": [{"path": "/image/schritt1.jpg"}]},
        {"index": 2, "instructions": "Zwei", "images": []},
        {"index": 3, "instructions": "Drei", "images": [{"link": "https://img.x/s3.jpg"}]},
    ]}}}}
) + "</script></head></html>"  # fmt: skip


def test_bilder_aus_hellofresh_html():
    from app.rezept_import import json_ld_rezept

    haupt, schritte = fotos.bilder_aus_html(HF_HTML, json_ld_rezept(HF_HTML))
    assert haupt.endswith("/haupt.jpg")
    assert schritte == {0: fotos.HF_BILD_BASIS + "/image/schritt1.jpg", 2: "https://img.x/s3.jpg"}


def test_import_laedt_bilder_und_kochmodus_zeigt_sie(client, monkeypatch):
    geladen = []

    async def fake_laden(url):
        geladen.append(url)
        return _png("green", (800, 600))

    monkeypatch.setattr(fotos, "herunterladen", fake_laden)
    url = "https://www.hellofresh.de/recipes/test-0123456789abcdef01234567"
    v = client.post("/api/v1/import/vorschau", json={"url": url, "html": HF_HTML}).json()
    assert v["bild"].endswith("haupt.jpg") and v["schritt_bilder"] == 2
    e = client.post(
        f"/api/v1/import/{v['import_id']}/uebernehmen",
        json={"zutaten": [{"index": 0, "produkt_id": "nudeln", "menge": 200}]},
    ).json()
    assert e["fotos"] == 3 and len(geladen) == 3
    liste = client.get(f"/api/v1/rezepte/{e['rezept_id']}/fotos").json()
    assert sorted((f["schritt"], f["haupt"]) for f in liste if f["schritt"] is not None) == [
        (0, False),
        (2, False),
    ]
    a = client.get(f"/api/v1/kochen/anleitung/{e['rezept_id']}").json()
    # Code-Vorschlag: ein Schritt je Originalschritt, Schritt 1 hat das Bild von Original 0
    mit_bild = [s for s in a["schritte"] if s["fotos"]]
    assert len(mit_bild) == 1 and mit_bild[0]["original"] == [0]
    # Rezept löschen entfernt auch die Fotos
    client.delete(f"/api/v1/rezepte/{e['rezept_id']}")
    assert client.get(f"/api/v1/fotos/{liste[0]['id']}").status_code == 404
