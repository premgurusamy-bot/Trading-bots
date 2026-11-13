"""Persistence (SQLAlchemy 2). SQLite by default; PostgreSQL-ready (no SQLite-only types).
Schema changes go through Alembic migrations (backend/alembic)."""
from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path

from sqlalchemy import (create_engine, String, Integer, Float, Boolean, DateTime, Date, Text, JSON, ForeignKey,
                        UniqueConstraint, Index, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings
from app.core.types import IST


class Base(DeclarativeBase):
    pass


def now_ist() -> datetime:
    return datetime.now(IST)


class InstrumentMasterVersion(Base):
    __tablename__ = "instrument_master_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    columns: Mapped[dict] = mapped_column(JSON)


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_version_id: Mapped[int] = mapped_column(ForeignKey("instrument_master_versions.id"))
    security_id: Mapped[str] = mapped_column(String(32))
    exchange_segment: Mapped[str] = mapped_column(String(16))
    instrument: Mapped[str] = mapped_column(String(16))
    underlying: Mapped[str] = mapped_column(String(32))
    trading_symbol: Mapped[str] = mapped_column(String(64))
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    lot_size: Mapped[int] = mapped_column(Integer)
    tick_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (Index("ix_instr_lookup", "underlying", "instrument", "expiry", "strike", "option_type"),
                      Index("ix_instr_secid", "exchange_segment", "security_id"))


class CandleRow(Base):
    __tablename__ = "candles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    tf: Mapped[str] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)
    __table_args__ = (UniqueConstraint("symbol", "tf", "ts", name="uq_candle"),)


class ChainSnapshotRow(Base):
    __tablename__ = "chain_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index: Mapped[str] = mapped_column(String(16))
    expiry: Mapped[date] = mapped_column(Date)
    underlying: Mapped[float] = mapped_column(Float)
    feed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)        # sanitized raw payload
    normalized: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_chain_idx_time", "index", "received_at"),)


class DecisionSnapshot(Base):
    """Full input snapshot of an engine evaluation that produced a signal or a rejected candidate:
    candles, chain, Greeks (with source/calculation metadata), S/R levels, params and version."""
    __tablename__ = "decision_snapshots"
    ref: Mapped[str] = mapped_column(String(32), primary_key=True)
    index: Mapped[str] = mapped_column(String(16))
    candle_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    strategy_version: Mapped[str] = mapped_column(String(16))
    params_hash: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[dict] = mapped_column(JSON)
    levels: Mapped[list] = mapped_column(JSON)
    evaluation: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)       # idempotency key
    index: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    setup: Mapped[str] = mapped_column(String(2))
    state: Mapped[str] = mapped_column(String(12))
    score: Mapped[int] = mapped_column(Integer)
    day: Mapped[date] = mapped_column(Date)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)
    trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (Index("ix_signal_day", "day", "index"),)


class SignalEvent(Base):
    __tablename__ = "signal_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(128), index=True)
    from_state: Mapped[str | None] = mapped_column(String(12), nullable=True)
    to_state: Mapped[str] = mapped_column(String(12))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("signal_id", "to_state", name="uq_signal_event"),)


class ResearchLog(Base):
    """Rejected / invalidated / expired candidates — kept OUT of the execution journal."""
    __tablename__ = "research_log"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    index: Mapped[str] = mapped_column(String(16))
    day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))          # REJECTED | CANCELLED | EXPIRED
    score: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[list] = mapped_column(JSON)
    data: Mapped[dict] = mapped_column(JSON)
    snapshot_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    premium_high_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_low_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(96), unique=True)     # idempotency key
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trade_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(8))            # PAPER | LIVE
    side: Mapped[str] = mapped_column(String(4))
    security_id: Mapped[str] = mapped_column(String(32))
    exchange_segment: Mapped[str] = mapped_column(String(16))
    qty: Mapped[int] = mapped_column(Integer)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_type: Mapped[str] = mapped_column(String(12))
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    purpose: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)


class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    fill_id: Mapped[str] = mapped_column(String(96), unique=True)
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TradeRow(Base):
    __tablename__ = "trades"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(128), unique=True)       # no duplicate execution
    index: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(8))
    day: Mapped[date] = mapped_column(Date)
    direction: Mapped[str] = mapped_column(String(8))
    setup: Mapped[str] = mapped_column(String(2))
    state: Mapped[str] = mapped_column(String(16))         # PENDING_ENTRY | OPEN | CLOSING | CLOSED | ENTRY_FAILED
    option: Mapped[dict] = mapped_column(JSON)
    lots: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer)
    open_qty: Mapped[int] = mapped_column(Integer)
    entry_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_plan: Mapped[dict] = mapped_column(JSON)
    sizing: Mapped[dict] = mapped_column(JSON)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    charges: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    premium_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    underlying_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    underlying_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    journal: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecureExitRow(Base):
    __tablename__ = "secure_exit_state"
    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)


class DailyStateRow(Base):
    __tablename__ = "daily_state"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)


class SettingRow(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)
    level: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ist)
    kind: Mapped[str] = mapped_column(String(32))          # "technical_only" | "full"
    config: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    results: Mapped[dict] = mapped_column(JSON)


def make_engine(url: str | None = None):
    url = url or settings.database_url
    if url.startswith("sqlite:///"):
        Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, future=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=FULL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


def make_session_factory(eng):
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


def run_migrations(url: str | None = None) -> None:
    """Apply Alembic migrations up to head (used at startup and by setup scripts)."""
    from alembic import command
    from alembic.config import Config
    from app.config import BACKEND_DIR
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url or settings.database_url)
    url2 = url or settings.database_url
    if url2.startswith("sqlite:///"):
        Path(url2.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(cfg, "head")
