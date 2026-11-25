"""Angebote: Penny-Abruf (gemockt), Mengen-Parser, KI-Zuordnung mit ID-Prüfung, Bestätigen,
Healthcheck und Zeitplan. Antworten sind nachgebaut, keine echten Prospektdaten."""

from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import angebote
from app.angebote import (
    Angebot,
    AngebotAbruf,
    angebot_menge,
    faellig,
    penny_angebot,
    penny_kategorien,
    woche_von,
)
from app.db import Packung, Preis
from app.main import create_app
from app.settings import Settings

HEUTE = date.today()
WOCHE = woche_von(HEUTE)
NAECHSTE = woche_von(HEUTE + timedelta(days=7))


def seite(naechste: bool = False) -> str:
    teile = [
        f'<div data-current-week="{WOCHE}" data-next-week="{NAECHSTE}"></div>',
        '<div class="tabs__content--current-week">',
        '<section id="ab-montag--kuehlregal" class="x" data-category-type="food" '
        'data-period-category="false" data-category-name="kuehlregal">',
        '<section id="ab-donnerstag--obst-und-gemuese1" data-period-category="false"\n'
        'data-category-name="obst-und-gemuese1">',
        '<section id="ab-montag--weitere" data-period-category="false" '
        'data-category-name="weitere-angebote">',
        '<section id="ab-montag--black-week" data-category-type="nonfood" '
        'data-period-category="false" data-category-name="black-week">',
        '<section id="ab-montag--drogerie-und-haushalt1" data-category-type="food" '
        'data-period-category="false" data-category-name="drogerie-und-haushalt1">',
        '<section id="ab-montag--menu" data-period-category="true" data-category-name="menu">',
        "</div>",
    ]
    if naechste:
        teile += [
            '<div class="tabs__content--next-week">',
            '<section id="ab-montag--kuehlregal" data-period-category="false" '
            'data-category-name="kuehlregal">',
        ]
    return "\n".join(teile)


def kachel(uuid: str, title: str, quantity: str, price: str, **extra) -> dict:
    return {"uuid": uuid, "title": title, "quantity": quantity, "price": price, "type": "food",
            "primaryType": "offer", "linkHref": f"/angebote/x/{uuid}",
            "imageRendition": {"tileSm": f"https://cdn.example/{uuid}.png"}, **extra}  # fmt: skip


KATEGORIEN = {
    "kuehlregal": [
        kachel(
            "u1",
            "ALMETTE Frischkäse*",
            "je 150 g",
            "1.11",
            listPrice="1.99",
            benefitPrice="0.99²",
            basePrice="(1 kg = 7.40)",
        ),  # fmt: skip
        kachel("u2", "BARILLA Spaghetti", "je 1.000-g-Packung", "1.49²", crossOutPrice="1.89"),
        {"primaryType": "contentTeaser", "uuid": "t1"},
    ],
    "obst-und-gemuese1": [
        kachel("u3", "Paprika rot", "je Stück", "0.69"),
        kachel("u2", "BARILLA Spaghetti", "je 1.000-g-Packung", "1.49²"),  # doppelt
    ],
    "weitere-angebote": [
        {
            **kachel(
                "u9",
                "Topf-Set",
                "je Stück",
                "99.99",
                originalPrice="299.00",
                originalPriceType="uvp",
            ),
            "type": "non-food",
        },  # fmt: skip
    ],
}


class PennyFake:
    def __init__(self):
        self.markt_da = True
        self.naechste = False
        self.aufrufe: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.aufrufe.append(url)
        assert "rationPlaner" in request.headers["user-agent"]
        if url.endswith("/.rest/market"):
            maerkte = [{"wwIdent": "4711", "sellingRegion": "R1", "zipCode": "48282",
                        "city": "Emsdetten", "marketName": "Penny Test",
                        "nextWeekSellingRegion": "R2" if self.naechste else ""}]  # fmt: skip
            return httpx.Response(200, json=maerkte if self.markt_da else [])
        if url.endswith("/angebote"):
            return httpx.Response(200, text=seite(self.naechste))
        if "/.rest/offers/by-category/" in url:
            kat = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"offerTiles": KATEGORIEN.get(kat, [])})
        return httpx.Response(404)


@pytest.fixture
def penny():
    return PennyFake()


@pytest.fixture
def a_client(fake_backend, fake_ha, tmp_path, penny):
    app = create_app(
        inventory=fake_backend,
        ha=fake_ha,
        app_settings=Settings(data_dir=tmp_path, ai_task_entity="ai_task.test"),
        db_url=f"sqlite:///{tmp_path / 'a.db'}",
        angebote_http=httpx.AsyncClient(transport=httpx.MockTransport(penny)),
    )
    with TestClient(app) as c:
        c.put("/api/v1/einstellungen/angebote", json={"penny_markt": "4711"})
        yield c


def ki_antwort(*zuordnungen: tuple[int, str, str | None]) -> dict:
    return {"zuordnungen": [{"index": i, "sorte": "x", "passung": p, "produkt_id": pid}
                            for i, p, pid in zuordnungen]}  # fmt: skip


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("je 150 g", (150.0, "g", False)),
        ("je 0,75 l", (750.0, "ml", False)),
        ("je 0,7 I", (700.0, "ml", False)),
        ("je 1.000 g", (1000.0, "g", False)),
        ("je 1.200-g-Packung", (1200.0, "g", False)),
        ("je 500-g-Schale", (500.0, "g", False)),
        ("je 750-g-Großpackg.", (750.0, "g", False)),
        ("je 1,5-kg-Packg.", (1500.0, "g", False)),
        ("je 16 x 11 g", (176.0, "g", False)),
        ("je kg", (1000.0, "g", True)),
        ("je Stück ", (1.0, "Stück", False)),
        ("je 8er-Packung", (8.0, "Stück", False)),
        ("je 6 Stück", (6.0, "Stück", False)),
        ("je 2-Stück-Packung", (2.0, "Stück", False)),
        ("je 3 x 100 Blatt", None),
        ("z. B. 650 g", (650.0, "g", False)),
        ("je Set", None),
        (None, None),
    ],
)
def test_menge(text, erwartet):
    assert angebot_menge(text) == erwartet


def test_kategorien_und_wochen():
    k = penny_kategorien(seite(naechste=True))
    assert k[WOCHE] == [
        ("ab-montag", "kuehlregal"),
        ("ab-donnerstag", "obst-und-gemuese1"),
        ("ab-montag", "weitere-angebote"),
    ]
    assert k[NAECHSTE] == [("ab-montag", "kuehlregal")]


def test_angebot_preise_und_gueltigkeit():
    a = penny_angebot(KATEGORIEN["kuehlregal"][0], "2026-48", "ab-donnerstag", "kuehlregal")
    assert a is not None
    assert (a["titel"], a["preis"], a["streichpreis"], a["app_preis"]) == (
        "ALMETTE Frischkäse", 1.11, 1.99, 0.99,
    )  # fmt: skip
    assert (a["gueltig_von"], a["gueltig_bis"]) == (date(2026, 11, 26), date(2026, 11, 28))
    uvp = penny_angebot(KATEGORIEN["weitere-angebote"][0], "2026-48", "ab-montag", "w")
    assert uvp["streichpreis"] is None and uvp["typ"] == "nonfood"  # UVP ist kein Normalpreis
    rabatt = penny_angebot(kachel("r", "X", "je 100 g", "30% Rabatt"), "2026-48", "ab-montag", "k")
    assert rabatt["preis"] is None and rabatt["preis_text"] == "30% Rabatt"


def test_abruf_ki_zuordnung_und_liste(a_client, fake_ha, penny):
    # Reihenfolge an die KI: u1 Frischkäse, u2 Spaghetti, u3 Paprika (nur Lebensmittel)
    fake_ha.ki_antwort = ki_antwort((0, "gleich", "gibtsnicht"), (1, "variante", "nudeln"),
                                    (2, "gleich", "paprika"))  # fmt: skip
    r = a_client.post("/api/v1/angebote/abrufen", json={})
    assert r.json() == {"gestartet": ["penny"]}
    assert any("region=R1" in u for u in penny.aufrufe)
    assert not any("drogerie" in u or "black-week" in u for u in penny.aufrufe)
    liste = {a["titel"]: a for a in a_client.get("/api/v1/angebote").json()}
    assert set(liste) == {"ALMETTE Frischkäse", "BARILLA Spaghetti", "Paprika rot"}
    assert liste["ALMETTE Frischkäse"]["zuordnung"] == "ki_keins"  # unbekannte ID verworfen
    assert liste["BARILLA Spaghetti"]["produkt"] == "Nudeln"
    assert liste["BARILLA Spaghetti"]["zuordnung"] == "ki"
    assert liste["Paprika rot"]["gueltig_von"] == str(angebote.wochentag(WOCHE, 3))
    assert len(a_client.get("/api/v1/angebote?nonfood=true").json()) == 4
    assert (
        "Paprika rot" in fake_ha.ki_anfragen[-1]
        and "paprika|Paprika rot" in (fake_ha.ki_anfragen[-1])
    )
    st = a_client.get("/api/v1/angebote/status").json()[0]
    assert st["ok"] and not st["kaputt"] and st["anzahl"] == 3 and st["wochen"] == [WOCHE]


def test_bestaetigen_schreibt_preise_packung_und_alias(a_client, fake_ha):
    fake_ha.ki_antwort = ki_antwort((1, "gleich", "nudeln"), (2, "gleich", "paprika"))
    a_client.post("/api/v1/angebote/abrufen", json={})
    liste = {a["titel"]: a for a in a_client.get("/api/v1/angebote").json()}
    ids = [liste["BARILLA Spaghetti"]["id"], liste["Paprika rot"]["id"]]
    r = a_client.post("/api/v1/angebote/bestaetigen", json={"ids": ids})
    assert r.json()["ok"] == ids
    engine = a_client.app.state.engine
    with Session(engine) as s:
        preise = {(p.produkt_id, p.quelle): (p.preis, p.menge) for p in s.scalars(select(Preis))}
        packs = [(p.produkt_id, p.menge, p.quelle) for p in s.scalars(select(Packung))]
    assert preise[("nudeln", "angebot")] == (1.49, 1000.0)
    assert preise[("nudeln", "streichpreis")] == (1.89, 1000.0)
    assert preise[("paprika", "angebot")] == (0.69, 160.0)  # je Stück à 160 g
    assert ("nudeln", 1000.0, "angebot") in packs  # 1 kg statt der 500-g-Standardpackung
    assert not any(p[0] == "paprika" for p in packs)  # 160 g = Standard, keine neue Größe

    # Umentscheiden: ignorieren entfernt Preise und Angebots-Packung
    a_client.post(f"/api/v1/angebote/{ids[0]}/zuordnen", json={"produkt_id": None})
    with Session(engine) as s:
        assert not s.scalars(select(Preis).where(Preis.produkt_id == "nudeln")).all()
        assert not s.scalars(select(Packung).where(Packung.produkt_id == "nudeln")).all()

    # Nächster Abruf: gemerkte Titel ohne KI
    fake_ha.ki_antwort = ki_antwort()
    with Session(engine) as s:
        for a in s.scalars(select(Angebot)):
            a.zuordnung, a.produkt_id = None, None
        s.query(AngebotAbruf).delete()
        s.commit()
    a_client.post("/api/v1/angebote/abrufen", json={})
    liste = {a["titel"]: a for a in a_client.get("/api/v1/angebote").json()}
    assert liste["Paprika rot"]["zuordnung"] == "alias"
    assert liste["BARILLA Spaghetti"]["zuordnung"] == "ignoriert"


def test_planer_overlay(a_client, fake_ha):
    fake_ha.ki_antwort = ki_antwort((1, "gleich", "nudeln"))
    a_client.post("/api/v1/angebote/abrufen", json={})
    with Session(a_client.app.state.engine) as s:
        aktiv = angebote.aktiv_je_produkt(s, HEUTE, HEUTE + timedelta(days=7))
    assert set(aktiv) == {"nudeln"}


def test_healthcheck_kaputt_meldet_einmal(a_client, fake_ha, penny):
    penny.markt_da = False
    a_client.post("/api/v1/angebote/abrufen", json={})
    a_client.post("/api/v1/angebote/abrufen", json={})
    st = a_client.get("/api/v1/angebote/status").json()[0]
    assert st["kaputt"] and "4711 nicht gefunden" in st["fehler"]
    assert [t for t, _ in fake_ha.hinweise] == ["Angebote Penny kaputt"]


def test_ohne_markt_kein_abruf(a_client):
    a_client.put("/api/v1/einstellungen/angebote", json={"penny_markt": ""})
    assert a_client.post("/api/v1/angebote/abrufen", json={}).status_code == 422
    assert (
        a_client.put("/api/v1/einstellungen/angebote", json={"penny_markt": "x1"}).status_code
        == 422
    )


def test_marktsuche(a_client):
    r = a_client.get("/api/v1/angebote/maerkte/penny?q=4828").json()
    assert r[0]["id"] == "4711" and "Emsdetten" in r[0]["adresse"]


def test_faellig(tmp_path):
    from app.db import make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'f.db'}")
    montag = datetime.combine(date.fromisocalendar(2026, 48, 1), datetime.min.time()).replace(
        hour=8
    )
    with Session(engine) as s:
        assert faellig(s, "penny", montag)
        s.add(AngebotAbruf(haendler="penny", zeit=montag, ok=True, wochen="2026-48", anzahl=5))
        s.commit()
        assert not faellig(s, "penny", montag.replace(hour=20))  # einmal am Tag
        assert not faellig(s, "penny", montag + timedelta(days=1))  # Di: Woche ist da
        assert faellig(s, "penny", montag + timedelta(days=2))  # Mi: nächste Woche fehlt
        s.add(AngebotAbruf(haendler="penny", zeit=montag + timedelta(days=2), ok=True,
                           wochen="2026-48,2026-49", anzahl=5))  # fmt: skip
        s.commit()
        assert not faellig(s, "penny", montag + timedelta(days=3))


def test_ki_guard_mit_live_fehlgriffen():
    """Live 2026-11-25 (gpt-4o-mini): Namen statt IDs und Fehlgriffe quer durch Kategorien."""
    from app.angebote import ki_pruefen
    from app.inventory.base import Product

    produkte = {p.id: p for p in [
        Product(id="323", name="Butter", unit="g", category="Milchprodukte"),
        Product(id="383", name="Gnocchi", unit="g", category="Nudeln & Reis"),
        Product(id="331", name="Tomate", unit="g", category="Gemüse"),
        Product(id="398", name="Kürbiskerne", unit="g", category="Nüsse & Saaten"),
        Product(id="320", name="Olivenöl", unit="ml", category="Öl & Essig"),
        Product(id="404", name="Reis", unit="g"),
    ]}  # fmt: skip

    def e(sorte, kat, passung, pid):
        return {"sorte": sorte, "kategorie": kat, "passung": passung, "produkt_id": pid}

    p, info = ki_pruefen(e("Mandeln", "Nüsse & Saaten", "gleich", "323"), produkte)
    assert p is None and "Nüsse & Saaten ≠ Milchprodukte" in info
    assert ki_pruefen(e("Gnocchi", "Nudeln & Reis", "gleich", "Gnocchi"), produkte)[0].id == "383"
    assert ki_pruefen(e("Romatomaten", "Gemüse", "variante", "tomate"), produkte)[0].id == "331"
    p, info = ki_pruefen(e("Hackfleisch", "Fleisch", "gleich", "Gemischtes Hack"), produkte)
    assert p is None and "unbekannt" in info
    assert ki_pruefen(e("Mandeln", "Nüsse & Saaten", "keins", None), produkte) == (None, "Mandeln")
    # Produkt ohne Kategorie: KI-Kategorie kann nichts widerlegen
    assert ki_pruefen(e("Expressreis", "Nudeln & Reis", "variante", "404"), produkte)[0].id == "404"


def test_ki_neu_fragt_nur_unbestaetigte(a_client, fake_ha):
    fake_ha.ki_antwort = ki_antwort((1, "gleich", "nudeln"))
    a_client.post("/api/v1/angebote/abrufen", json={})
    liste = {a["titel"]: a for a in a_client.get("/api/v1/angebote").json()}
    a_client.post(
        f"/api/v1/angebote/{liste['Paprika rot']['id']}/zuordnen", json={"produkt_id": None}
    )
    fake_ha.ki_antwort = ki_antwort((0, "gleich", "nudeln"))
    r = a_client.post("/api/v1/angebote/ki?neu=true").json()
    assert r["offen"] == 2  # Frischkäse und Spaghetti, nicht die ignorierte Paprika
    liste = {a["titel"]: a for a in a_client.get("/api/v1/angebote").json()}
    assert liste["Paprika rot"]["zuordnung"] == "ignoriert"


def kl_kachel(bild: str, titel: str, sub: str, menge: str, preis: str, alt: str = "",
              karte: str = "") -> str:  # fmt: skip
    return (
        f'<a class="k-product-tile" href="#"><img class="k-image" '
        f'src="https://kaufland.media.schwarz/is/image/schwarz/{bild}" alt="">'
        f'<div class="k-product-tile__title">{titel}</div>'
        f'<div class="k-product-tile__subtitle">{sub} </div>'
        f'<div class="k-product-tile__unit-price">{menge}</div>'
        f'<div class="k-product-tile__base-price">(1 kg = 1.00)</div>'
        '<div class="k-product-tile__pricetags-normal">'
        f'<div class="k-price-tag__price">{preis} </div>'
        + (f'<span class="k-price-tag__old-price-line-through">{alt}</span>' if alt else "")
        + '</div><div class="k-product-tile__pricetags-loyalty">'
        + (f'<div class="k-price-tag__price">{karte} </div>' if karte else "")
        + "</div></a>"
    )


def kl_seite(abschnitte: list[tuple[str, str, list[str]]]) -> str:
    return "".join(
        f'<div id="{sid}" class="k-product-section"><div class="k-product-section__subheadline">'
        f"{gueltig}</div>{''.join(kacheln)}</div>"
        for sid, gueltig, kacheln in abschnitte
    )


BUTTER = kl_kachel("b1", "WEIHENSTEPHAN", "Butter", "je 250-g-Packg.", "1.29", "2.99", "1.15")
WOCHE48 = "Gültig vom 23.11. bis 28.11."
KL_JETZT = kl_seite([
    ("cat_0001_TopArticle", "Angebote gültig am 25.11.", [BUTTER]),
    ("cat_03_Molkereiprodukte__Fette", WOCHE48, [
        BUTTER,
        kl_kachel("b2", "SÖBBEKE", "Bioland Natur&shy;joghurt", "je 400-g-Becher", "1.29"),
    ]),
    ("cat_09_Drogerie__Tiernahrung", WOCHE48, [kl_kachel("d1", "NIVEA", "Creme", "1 l", "2.49")]),
])  # fmt: skip
KL_VORWERBUNG = kl_seite([("cat_Vorwerbung", "Gültig vom 30.11. bis 05.12.",
                           [kl_kachel("v1", "X", "Y", "je kg", "1.99")])])  # fmt: skip


def test_kaufland_seite():
    w = angebote.kaufland_seite(KL_JETZT, date(2026, 11, 25))
    assert list(w) == ["2026-48"]
    butter, joghurt = w["2026-48"]  # Top-Abschnitt und Drogerie fallen weg, Butter nur einmal
    assert (butter["titel"], butter["preis"], butter["streichpreis"], butter["app_preis"]) == (
        "WEIHENSTEPHAN Butter", 1.29, 2.99, 1.15,
    )  # fmt: skip
    assert (butter["menge"], butter["einheit"], butter["kategorie"]) == (
        250.0,
        "g",
        "Molkereiprodukte, Fette",
    )
    assert (butter["gueltig_von"], butter["gueltig_bis"]) == (
        date(2026, 11, 23),
        date(2026, 11, 28),
    )
    assert joghurt["titel"] == "SÖBBEKE Bioland Naturjoghurt" and joghurt["streichpreis"] is None
    assert angebote.kaufland_seite(KL_VORWERBUNG, date(2026, 11, 25)) == {}
    # Jahreswechsel: „29.12. bis 03.01.“
    neujahr = kl_seite([("cat_06_Grundnahrungsmittel", "Gültig vom 29.12. bis 03.01.",
                         [kl_kachel("n1", "A", "Reis", "je 1-kg-Packg.", "1.99")])])  # fmt: skip
    a = angebote.kaufland_seite(neujahr, date(2026, 12, 28))["2026-53"][0]
    assert (a["gueltig_von"], a["gueltig_bis"]) == (date(2026, 12, 29), date(2027, 1, 3))


def test_kaufland_abruf_mit_filiale(fake_backend, fake_ha, tmp_path):
    cookies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.klstorefinder.json"):
            filiale = {"n": "DE8360", "cn": "Kaufland Emsdetten", "sn": "Katthagen 6-8",
                       "pc": "48282", "t": "Emsdetten"}  # fmt: skip
            return httpx.Response(200, json=[filiale])
        cookies.append(request.headers.get("cookie"))
        woche = request.url.params["kloffer-week"]
        return httpx.Response(200, text=KL_JETZT if woche == "current" else KL_VORWERBUNG)

    app = create_app(
        inventory=fake_backend, ha=fake_ha,
        app_settings=Settings(data_dir=tmp_path, ai_task_entity="ai_task.test"),
        db_url=f"sqlite:///{tmp_path / 'k.db'}",
        angebote_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )  # fmt: skip
    fake_ha.ki_antwort = ki_antwort()
    with TestClient(app) as c:
        assert c.get("/api/v1/angebote/maerkte/kaufland?q=Emsdetten").json()[0]["id"] == "DE8360"
        assert (
            c.put("/api/v1/einstellungen/angebote", json={"kaufland_markt": "8360"}).json()[
                "kaufland_markt"
            ]
            == "DE8360"
        )
        assert (
            c.put("/api/v1/einstellungen/angebote", json={"kaufland_markt": "x"}).status_code == 422
        )
        assert c.post("/api/v1/angebote/abrufen", json={"haendler": "kaufland"}).status_code == 200
        st = {h["haendler"]: h for h in c.get("/api/v1/angebote/status").json()}
        liste = c.get("/api/v1/angebote?alle=true").json()
    assert cookies == ["x-aem-variant=DE8360"] * 2
    assert st["kaufland"]["ok"] and st["kaufland"]["anzahl"] == 2 and not st["penny"]["aktiv"]
    assert {a["markt"] for a in liste} == {"Kaufland"}
