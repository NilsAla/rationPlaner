"""SQLite der App. Hält nur, was Grocy nicht kann (siehe docs/konzept.md, Datenmodell)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PlanEintrag(Base):
    """Ein Tag im Wochenplan. Vorerst manuell befüllt, später vom Planer."""

    __tablename__ = "wochenplan"

    datum: Mapped[date] = mapped_column(Date, primary_key=True)
    rezept_id: Mapped[str] = mapped_column(String)
    portionen: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="geplant")  # geplant/gekocht/uebersprungen


class Packung(Base):
    """Weitere Packungsgröße eines Produkts. Die Standard-Packung liegt in Grocy."""

    __tablename__ = "packung"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    produkt_id: Mapped[str] = mapped_column(String, index=True)
    einheit: Mapped[str] = mapped_column(String)  # z. B. "Dose"
    menge: Mapped[float] = mapped_column(Float)  # in Bestandseinheit des Produkts
    quelle: Mapped[str] = mapped_column(String, default="manuell")  # manuell/bon/angebot/llm
    gueltig_bis: Mapped[date | None] = mapped_column(Date, nullable=True)  # Angebot


class RezeptMeta(Base):
    """Was Grocy am Rezept nicht kann: Herkunft (für Dubletten beim Import)."""

    __tablename__ = "rezept_meta"

    rezept_id: Mapped[str] = mapped_column(String, primary_key=True)
    quelle: Mapped[str | None] = mapped_column(String, nullable=True)  # z. B. hellofresh
    quelle_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)


class ZutatAlias(Base):
    """Zutatentext aus Importen (normalisiert) → Produkt. Entsteht aus bestätigten Reviews."""

    __tablename__ = "zutat_alias"

    text: Mapped[str] = mapped_column(String, primary_key=True)
    produkt_id: Mapped[str] = mapped_column(String)


class RezeptImport(Base):
    """Ein Import-Vorgang: gelesenes Rezept, wartet auf Review."""

    __tablename__ = "rezept_import"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String)
    quelle_id: Mapped[str] = mapped_column(String, index=True)
    roh: Mapped[dict] = mapped_column(JSON)
    rezept_id: Mapped[str | None] = mapped_column(String, nullable=True)  # nach Übernahme
    angelegt: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def make_engine(db_url: str) -> Engine:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    return engine


class DubletteEntscheidung(Base):
    """Produktpaar mit Entscheidung „nicht mergen“. Wird nie wieder vorgeschlagen."""

    __tablename__ = "dublette_entscheidung"

    produkt_a: Mapped[str] = mapped_column(String, primary_key=True)  # kleinere ID
    produkt_b: Mapped[str] = mapped_column(String, primary_key=True)
    entscheidung: Mapped[str] = mapped_column(String, default="nicht_mergen")
    datum: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProduktMerge(Base):
    """Zusammengeführte Produkte: alte ID → neue ID, damit alte Verweise auflösbar bleiben."""

    __tablename__ = "produkt_merge"

    alt_id: Mapped[str] = mapped_column(String, primary_key=True)
    neu_id: Mapped[str] = mapped_column(String)
    alt_name: Mapped[str] = mapped_column(String)
    datum: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FuellstandAbfrage(Base):
    """Wann zuletzt nach dem Füllstand eines Produkts gefragt wurde (höchstens alle 14 Tage)."""

    __tablename__ = "fuellstand_abfrage"

    produkt_id: Mapped[str] = mapped_column(String, primary_key=True)
    zuletzt: Mapped[date] = mapped_column(Date)


class BonImport(Base):
    """Ein Kassenbon: Foto, LLM-Rohantwort, Positionen mit Vorschlag, Review-Status."""

    __tablename__ = "bon_import"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datei: Mapped[str] = mapped_column(String)  # relativ zu <media_dir>/ration/bons
    status: Mapped[str] = mapped_column(
        String, default="verarbeitung"
    )  # +review/gebucht/fehler/verworfen
    markt: Mapped[str | None] = mapped_column(String, nullable=True)
    datum: Mapped[date | None] = mapped_column(Date, nullable=True)
    summe: Mapped[float | None] = mapped_column(Float, nullable=True)
    roh: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # LLM-Antwort
    positionen: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fehler: Mapped[str | None] = mapped_column(String, nullable=True)
    angelegt: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BonAlias(Base):
    """Bon-Text je Markt → Produkt. Entsteht aus jeder bestätigten Position."""

    __tablename__ = "bon_alias"

    markt: Mapped[str] = mapped_column(String, primary_key=True)  # normalisiert, "" = unbekannt
    text: Mapped[str] = mapped_column(String, primary_key=True)  # normalisiert
    produkt_id: Mapped[str] = mapped_column(String)
    menge: Mapped[float | None] = mapped_column(Float, nullable=True)  # je Stück, Bestandseinheit


class Preis(Base):
    """Gezahlter oder angebotener Preis. Quelle bon/angebot/streichpreis."""

    __tablename__ = "preis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    produkt_id: Mapped[str] = mapped_column(String, index=True)
    preis: Mapped[float] = mapped_column(Float)  # gesamt, EUR
    menge: Mapped[float] = mapped_column(Float)  # in Bestandseinheit
    markt: Mapped[str | None] = mapped_column(String, nullable=True)
    datum: Mapped[date] = mapped_column(Date)
    quelle: Mapped[str] = mapped_column(String, default="bon")
