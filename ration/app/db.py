"""SQLite der App. Hält nur, was Grocy nicht kann (siehe docs/konzept.md, Datenmodell)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer, String, create_engine
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


def make_engine(db_url: str) -> Engine:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    return engine
