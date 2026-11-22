"""SQLite der App. Hält nur, was Grocy nicht kann (siehe docs/konzept.md, Datenmodell)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, Integer, String, create_engine
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


def make_engine(db_url: str) -> Engine:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    return engine
