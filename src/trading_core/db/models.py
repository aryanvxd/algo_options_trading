"""Database schema shared by every algo.

Conventions
- Timestamps are IST. They are stored without an offset (SQLite has no tz type) and come back
  tz-aware via ``ISTDateTime``.
- Prices are in points, money in rupees.
- Every trading row carries ``algo_id`` and ``mode`` (paper/live) so algos share one DB.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from trading_core.clock import IST


class ISTDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(IST).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=IST) if value is not None else None


class Base(DeclarativeBase):
    type_annotation_map = {datetime: ISTDateTime(), dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------- reference data

class BrokerSession(Base):
    __tablename__ = "broker_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(20), default="zerodha")
    user_id: Mapped[str | None] = mapped_column(String(40))
    access_token: Mapped[str] = mapped_column(String(200))
    login_at: Mapped[datetime]
    valid_until: Mapped[datetime]


class Instrument(Base):
    """Daily snapshot of the instruments we care about (configured option chains + NSE indices)."""
    __tablename__ = "instruments"
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    instrument_token: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    exchange_token: Mapped[int | None] = mapped_column(BigInteger)
    tradingsymbol: Mapped[str] = mapped_column(String(60))
    name: Mapped[str | None] = mapped_column(String(60))
    exchange: Mapped[str] = mapped_column(String(10))
    segment: Mapped[str] = mapped_column(String(20))
    instrument_type: Mapped[str] = mapped_column(String(10))
    strike: Mapped[float | None] = mapped_column(Float)
    expiry: Mapped[date | None] = mapped_column(Date)
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    tick_size: Mapped[float] = mapped_column(Float, default=0.05)

    __table_args__ = (
        Index("ix_instruments_chain", "as_of_date", "name", "expiry", "instrument_type", "strike"),
        Index("ix_instruments_symbol", "as_of_date", "exchange", "tradingsymbol"),
    )


class MarketHoliday(Base):
    __tablename__ = "market_holidays"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(10), primary_key=True, default="NSE")
    description: Mapped[str | None] = mapped_column(String(100))


# ---------------------------------------------------------------- algos and runs

class Algo(Base):
    __tablename__ = "algos"
    algo_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(20), default="0.1.0")
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]


class AlgoRun(Base):
    """One per algo per trading day (source=live). Replays create extra rows with source=replay."""
    __tablename__ = "algo_runs"
    run_id: Mapped[int] = mapped_column(primary_key=True)
    algo_id: Mapped[str] = mapped_column(ForeignKey("algos.algo_id"))
    trade_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(10))            # paper | live
    source: Mapped[str] = mapped_column(String(10))          # live | replay
    status: Mapped[str] = mapped_column(String(12))          # STARTED SKIPPED IN_TRADE DONE ERROR
    skip_reason: Mapped[str | None] = mapped_column(Text)
    config_snapshot: Mapped[dict[str, Any]]
    config_hash: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]

    __table_args__ = (Index("ix_runs_algo_date", "algo_id", "trade_date"),)


class RunCheck(Base):
    __tablename__ = "run_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("algo_runs.run_id"), index=True)
    ts: Mapped[datetime]
    check_name: Mapped[str] = mapped_column(String(40))
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[dict[str, Any] | None]


class MarketBreadth(Base):
    __tablename__ = "market_breadth"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("algo_runs.run_id"), index=True)
    captured_at: Mapped[datetime]
    index_symbol: Mapped[str] = mapped_column(String(40))
    index_spot: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    gap_pct: Mapped[float | None] = mapped_column(Float)
    vix: Mapped[float | None] = mapped_column(Float)
    advances: Mapped[int] = mapped_column(Integer, default=0)
    declines: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    gainers: Mapped[list[Any] | None]
    losers: Mapped[list[Any] | None]


# ---------------------------------------------------------------- trading

class Position(Base):
    __tablename__ = "positions"
    position_id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("algo_runs.run_id"), index=True)
    algo_id: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(10))
    structure: Mapped[str] = mapped_column(String(30))
    underlying: Mapped[str] = mapped_column(String(30))
    expiry: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10))          # OPENING OPEN CLOSED FAILED
    entry_time: Mapped[datetime | None]
    exit_time: Mapped[datetime | None]
    entry_credit_pts: Mapped[float | None] = mapped_column(Float)
    exit_debit_pts: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(40))
    gross_pnl: Mapped[float | None] = mapped_column(Float)
    charges: Mapped[float | None] = mapped_column(Float)
    net_pnl: Mapped[float | None] = mapped_column(Float)
    params: Mapped[dict[str, Any] | None]
    capital_required: Mapped[float | None] = mapped_column(Float)   # margin blocked, or premium paid
    capital_source: Mapped[str | None] = mapped_column(String(20))  # kite_margin | estimate | premium
    spot_at_entry: Mapped[float | None] = mapped_column(Float)
    spot_at_exit: Mapped[float | None] = mapped_column(Float)

    legs: Mapped[list["Leg"]] = relationship(back_populates="position", order_by="Leg.leg_id")


class Leg(Base):
    __tablename__ = "legs"
    leg_id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.position_id"), index=True)
    role: Mapped[str] = mapped_column(String(20))            # SHORT_CE, LONG_PE, ...
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    tradingsymbol: Mapped[str] = mapped_column(String(60))
    exchange: Mapped[str] = mapped_column(String(10), default="NFO")
    option_type: Mapped[str | None] = mapped_column(String(4))
    strike: Mapped[float | None] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(4))             # BUY | SELL (entry side)
    qty: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)

    position: Mapped[Position] = relationship(back_populates="legs")


class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.leg_id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("algo_runs.run_id"), index=True)
    algo_id: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(10))
    purpose: Mapped[str] = mapped_column(String(10))         # ENTRY | EXIT
    broker_order_id: Mapped[str | None] = mapped_column(String(40))
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    tradingsymbol: Mapped[str] = mapped_column(String(60))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(10))
    limit_price: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12))          # PENDING OPEN COMPLETE REJECTED CANCELLED
    reject_reason: Mapped[str | None] = mapped_column(Text)
    tag: Mapped[str | None] = mapped_column(String(20))
    placed_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Fill(Base):
    __tablename__ = "fills"
    fill_id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer)
    filled_at: Mapped[datetime]
    bid_at_fill: Mapped[float | None] = mapped_column(Float)
    ask_at_fill: Mapped[float | None] = mapped_column(Float)
    slippage_pts: Mapped[float | None] = mapped_column(Float)


class OrderCharge(Base):
    __tablename__ = "order_charges"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), primary_key=True)
    brokerage: Mapped[float] = mapped_column(Float)
    gst: Mapped[float] = mapped_column(Float)
    stt: Mapped[float] = mapped_column(Float)
    exchange_txn: Mapped[float] = mapped_column(Float)
    sebi: Mapped[float] = mapped_column(Float)
    stamp: Mapped[float] = mapped_column(Float)
    total: Mapped[float] = mapped_column(Float)


# ---------------------------------------------------------------- monitoring and control

class LiveState(Base):
    """One row per run, overwritten every second; the dashboard's live view."""
    __tablename__ = "live_state"
    run_id: Mapped[int] = mapped_column(ForeignKey("algo_runs.run_id"), primary_key=True)
    updated_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(20))
    spot: Mapped[float | None] = mapped_column(Float)
    vix: Mapped[float | None] = mapped_column(Float)
    net_premium: Mapped[float | None] = mapped_column(Float)
    gross_pnl: Mapped[float | None] = mapped_column(Float)
    net_pnl: Mapped[float | None] = mapped_column(Float)
    legs: Mapped[list[Any] | None]
    levels: Mapped[dict[str, Any] | None]
    feed_ok: Mapped[bool] = mapped_column(Boolean, default=True)


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.position_id"))
    ts: Mapped[datetime]
    spot: Mapped[float | None] = mapped_column(Float)
    vix: Mapped[float | None] = mapped_column(Float)
    net_premium: Mapped[float | None] = mapped_column(Float)
    gross_pnl: Mapped[float | None] = mapped_column(Float)
    net_pnl: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (Index("ix_possnap_pos_ts", "position_id", "ts"),)


class TickSnapshot(Base):
    __tablename__ = "tick_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("algo_runs.run_id"))
    ts: Mapped[datetime]
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    ltp: Mapped[float | None] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    bid_qty: Mapped[int | None] = mapped_column(Integer)
    ask_qty: Mapped[int | None] = mapped_column(Integer)
    oi: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_ticks_run_token_ts", "run_id", "instrument_token", "ts"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("algo_runs.run_id"), index=True)
    algo_id: Mapped[str | None] = mapped_column(String(40))
    ts: Mapped[datetime]
    level: Mapped[str] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None]


class ControlCommand(Base):
    __tablename__ = "control_commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    algo_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("algo_runs.run_id"))
    command: Mapped[str] = mapped_column(String(20))         # KILL | PAUSE | RESUME
    issued_at: Mapped[datetime]
    issued_by: Mapped[str | None] = mapped_column(String(40))
    consumed_at: Mapped[datetime | None]


class DailySummary(Base):
    __tablename__ = "daily_summary"
    run_id: Mapped[int] = mapped_column(ForeignKey("algo_runs.run_id"), primary_key=True)
    algo_id: Mapped[str] = mapped_column(String(40))
    trade_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(10))
    traded: Mapped[bool] = mapped_column(Boolean)
    entry_credit_pts: Mapped[float | None] = mapped_column(Float)
    max_profit: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    gross_pnl: Mapped[float | None] = mapped_column(Float)
    charges: Mapped[float | None] = mapped_column(Float)
    net_pnl: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_summary_algo_date", "algo_id", "trade_date"),)


class JournalNote(Base):
    """Your own notes on a strategy's day or trade, written from the dashboard. Algos never read it."""
    __tablename__ = "journal_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    algo_id: Mapped[str] = mapped_column(String(40))
    trade_date: Mapped[date] = mapped_column(Date)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.position_id"))   # None = the whole day
    text: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[Any] | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    __table_args__ = (Index("ix_notes_algo_date", "algo_id", "trade_date"),)


# ---------------------------------------------------------------- market calendar

class CalendarEvent(Base):
    """An economic release, company results date or other scheduled market event (see
    ``trading_core.market.events``). Upserted by (source, event_key), so refetching never duplicates."""
    __tablename__ = "calendar_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))            # forexfactory | nse_results | india
    event_key: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(12))          # economic | results
    ts: Mapped[datetime]                                       # IST; midnight when only the date is known
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    country: Mapped[str | None] = mapped_column(String(8))     # IN, US, EU, ... (results: IN)
    title: Mapped[str] = mapped_column(Text)
    impact: Mapped[str | None] = mapped_column(String(10))     # High | Medium | Low | Holiday
    forecast: Mapped[str | None] = mapped_column(String(40))
    previous: Mapped[str | None] = mapped_column(String(40))
    actual: Mapped[str | None] = mapped_column(String(40))
    symbol: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[dict[str, Any] | None]
    fetched_at: Mapped[datetime]

    __table_args__ = (Index("ix_calendar_key", "source", "event_key", unique=True),
                      Index("ix_calendar_ts", "ts"))


class CalendarSource(Base):
    """When each calendar source was last fetched, and how it went."""
    __tablename__ = "calendar_sources"
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    fetched_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(10))            # OK | FAILED
    rows: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- market data (Parquet store manifest)

class MarketDataFile(Base):
    """One row per Parquet file in data/market: what was downloaded/exported, from where, and whether
    it checked out. Makes downloads resumable and lets the dashboard show coverage."""
    __tablename__ = "market_data_files"
    dataset: Mapped[str] = mapped_column(String(30), primary_key=True)        # options_1m, index_1m, ...
    partition_key: Mapped[str] = mapped_column(String(40), primary_key=True)  # underlying or symbol
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), primary_key=True)         # kite, nse, recorder, groww ...
    path: Mapped[str] = mapped_column(Text)
    rows: Mapped[int] = mapped_column(Integer)
    contracts: Mapped[int | None] = mapped_column(Integer)
    first_ts: Mapped[datetime | None]
    last_ts: Mapped[datetime | None]
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))                           # OK PARTIAL FAILED EMPTY
    validated_against: Mapped[str | None] = mapped_column(String(30))
    max_close_diff: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    written_at: Mapped[datetime]

    __table_args__ = (Index("ix_mdf_dataset_date", "dataset", "trade_date"),)
