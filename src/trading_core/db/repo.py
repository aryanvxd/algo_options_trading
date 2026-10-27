"""Journal: every DB write an algo makes, plus the reads the runner and dashboard need.

Each method opens its own short transaction, so a crash never leaves half-written state behind
and other processes (dashboard, other algos) see updates promptly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import delete, insert, select, update

from trading_core.broker.base import OrderRequest, OrderResult
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.feed.base import Tick
from trading_core.strategy.position import Leg, Mark, Position


def config_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


class Journal:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ algos / runs

    def register_algo(self, algo_id: str, name: str, version: str, description: str, now: datetime) -> None:
        with self.db.session() as s:
            row = s.get(m.Algo, algo_id)
            if row is None:
                s.add(m.Algo(algo_id=algo_id, name=name, version=version, description=description,
                             enabled=True, created_at=now))
            else:
                row.name, row.version, row.description = name, version, description

    def find_run(self, algo_id: str, trade_date: date, mode: str, source: str = "live") -> m.AlgoRun | None:
        with self.db.session() as s:
            return s.scalars(
                select(m.AlgoRun)
                .where(m.AlgoRun.algo_id == algo_id, m.AlgoRun.trade_date == trade_date,
                       m.AlgoRun.mode == mode, m.AlgoRun.source == source)
                .order_by(m.AlgoRun.run_id.desc())
            ).first()

    def create_run(self, algo_id: str, trade_date: date, mode: str, source: str,
                   config_snapshot: dict[str, Any], now: datetime) -> int:
        with self.db.session() as s:
            run = m.AlgoRun(algo_id=algo_id, trade_date=trade_date, mode=mode, source=source, status="STARTED",
                            config_snapshot=config_snapshot, config_hash=config_hash(config_snapshot),
                            started_at=now)
            s.add(run)
            s.flush()
            return run.run_id

    def get_run(self, run_id: int) -> m.AlgoRun | None:
        with self.db.session() as s:
            return s.get(m.AlgoRun, run_id)

    def set_run_status(self, run_id: int, status: str, now: datetime, skip_reason: str | None = None,
                       ended: bool = False) -> None:
        values: dict[str, Any] = {"status": status}
        if skip_reason is not None:
            values["skip_reason"] = skip_reason
        if ended:
            values["ended_at"] = now
        with self.db.session() as s:
            s.execute(update(m.AlgoRun).where(m.AlgoRun.run_id == run_id).values(**values))

    def add_check(self, run_id: int, now: datetime, name: str, passed: bool, value: float | None = None,
                  threshold: float | None = None, detail: dict[str, Any] | None = None) -> None:
        with self.db.session() as s:
            s.add(m.RunCheck(run_id=run_id, ts=now, check_name=name, value=value, threshold=threshold,
                             passed=passed, detail=detail))

    def add_breadth(self, run_id: int, snap: Any) -> None:
        """``snap`` is a ``market.premarket.PremarketSnapshot``."""
        with self.db.session() as s:
            s.add(m.MarketBreadth(
                run_id=run_id, captured_at=snap.captured_at, index_symbol=snap.index_symbol,
                index_spot=snap.index_spot, prev_close=snap.prev_close, gap_pct=snap.gap_pct, vix=snap.vix,
                advances=snap.advances, declines=snap.declines, unchanged=snap.unchanged,
                gainers=snap.gainers, losers=snap.losers,
            ))

    def latest_breadth(self, trade_date: date) -> m.MarketBreadth | None:
        """Most recent pre-open snapshot recorded by a live run on ``trade_date`` (used by replays)."""
        with self.db.session() as s:
            return s.scalars(
                select(m.MarketBreadth).join(m.AlgoRun, m.AlgoRun.run_id == m.MarketBreadth.run_id)
                .where(m.AlgoRun.trade_date == trade_date, m.AlgoRun.source == "live")
                .order_by(m.MarketBreadth.id.desc())
            ).first()

    def event(self, run_id: int | None, algo_id: str | None, now: datetime, type_: str, message: str,
              level: str = "INFO", data: dict[str, Any] | None = None) -> None:
        with self.db.session() as s:
            s.add(m.Event(run_id=run_id, algo_id=algo_id, ts=now, level=level, type=type_, message=message,
                          data=data))

    # ------------------------------------------------------------ positions / orders

    def create_position(self, run_id: int, algo_id: str, mode: str, pos: Position) -> None:
        with self.db.session() as s:
            row = m.Position(run_id=run_id, algo_id=algo_id, mode=mode, structure=pos.structure,
                             underlying=pos.underlying, expiry=pos.expiry, status=pos.status, params=pos.params)
            s.add(row)
            s.flush()
            pos.position_id = row.position_id
            for leg in pos.legs:
                lr = m.Leg(position_id=row.position_id, role=leg.role, instrument_token=leg.instrument_token,
                           tradingsymbol=leg.tradingsymbol, exchange=leg.exchange, option_type=leg.option_type,
                           strike=leg.strike, side=leg.side, qty=leg.qty)
                s.add(lr)
                s.flush()
                leg.leg_id = lr.leg_id

    def update_position(self, pos: Position) -> None:
        with self.db.session() as s:
            row = s.get(m.Position, pos.position_id)
            row.status = pos.status
            row.entry_time = pos.entry_time
            row.exit_time = pos.exit_time
            row.exit_reason = pos.exit_reason
            row.entry_credit_pts = pos.entry_credit_pts
            row.exit_debit_pts = pos.exit_debit_pts
            row.capital_required, row.capital_source = pos.capital_required, pos.capital_source
            row.spot_at_entry, row.spot_at_exit = pos.spot_at_entry, pos.spot_at_exit
            if pos.status == "CLOSED":
                row.gross_pnl = pos.realized_gross
                row.charges = pos.total_charges
                row.net_pnl = round(pos.realized_gross - pos.total_charges, 2)
            for leg in pos.legs:
                lr = s.get(m.Leg, leg.leg_id)
                lr.entry_price, lr.exit_price = leg.entry_price, leg.exit_price
                lr.pnl = leg.realized_pnl

    def positions_for_run(self, run_id: int) -> list[Position]:
        """Rebuild domain positions from the DB (used to resume after a restart)."""
        out: list[Position] = []
        with self.db.session() as s:
            rows = s.scalars(select(m.Position).where(m.Position.run_id == run_id)
                             .order_by(m.Position.position_id)).all()
            for r in rows:
                charges = self._leg_charges(s, [l.leg_id for l in r.legs])
                legs = [Leg(role=l.role, instrument_token=l.instrument_token, tradingsymbol=l.tradingsymbol,
                            side=l.side, qty=l.qty, exchange=l.exchange, option_type=l.option_type,
                            strike=l.strike, leg_id=l.leg_id, entry_price=l.entry_price, exit_price=l.exit_price,
                            entry_charges=charges.get((l.leg_id, "ENTRY"), 0.0),
                            exit_charges=charges.get((l.leg_id, "EXIT"), 0.0))
                        for l in r.legs]
                out.append(Position(structure=r.structure, underlying=r.underlying, legs=legs, expiry=r.expiry,
                                    params=r.params or {}, position_id=r.position_id, status=r.status,
                                    entry_time=r.entry_time, exit_time=r.exit_time, exit_reason=r.exit_reason,
                                    capital_required=r.capital_required, capital_source=r.capital_source,
                                    spot_at_entry=r.spot_at_entry, spot_at_exit=r.spot_at_exit))
        return out

    @staticmethod
    def _leg_charges(s, leg_ids: list[int]) -> dict[tuple[int, str], float]:
        if not leg_ids:
            return {}
        rows = s.execute(
            select(m.Order.leg_id, m.Order.purpose, m.OrderCharge.total)
            .join(m.OrderCharge, m.OrderCharge.order_id == m.Order.order_id)
            .where(m.Order.leg_id.in_(leg_ids), m.Order.status == "COMPLETE")
        ).all()
        out: dict[tuple[int, str], float] = {}
        for leg_id, purpose, total in rows:
            out[(leg_id, purpose)] = out.get((leg_id, purpose), 0.0) + total
        return out

    def record_order(self, run_id: int, algo_id: str, mode: str, purpose: str, leg_id: int | None,
                     req: OrderRequest, res: OrderResult, placed_at: datetime) -> None:
        with self.db.session() as s:
            s.add(m.Order(order_id=res.order_id, leg_id=leg_id, run_id=run_id, algo_id=algo_id, mode=mode,
                          purpose=purpose, broker_order_id=res.broker_order_id,
                          instrument_token=req.instrument_token, tradingsymbol=req.tradingsymbol, side=req.side,
                          qty=req.qty, order_type=req.order_type, limit_price=req.limit_price, status=res.status,
                          reject_reason=res.reject_reason, tag=req.tag, placed_at=placed_at,
                          updated_at=res.filled_at or placed_at))
            s.flush()
            if res.filled:
                s.add(m.Fill(order_id=res.order_id, price=res.avg_price, qty=res.filled_qty,
                             filled_at=res.filled_at or placed_at, bid_at_fill=res.bid, ask_at_fill=res.ask,
                             slippage_pts=res.slippage_pts))
                c = res.charges
                s.add(m.OrderCharge(order_id=res.order_id, brokerage=c.brokerage, gst=c.gst, stt=c.stt,
                                    exchange_txn=c.exchange_txn, sebi=c.sebi, stamp=c.stamp, total=c.total))

    # ------------------------------------------------------------ monitoring

    def upsert_live_state(self, run_id: int, now: datetime, status: str, spot: float | None, vix: float | None,
                          mark: Mark | None, levels: dict[str, Any] | None, feed_ok: bool) -> None:
        values = dict(updated_at=now, status=status, spot=spot, vix=vix, feed_ok=feed_ok, levels=levels,
                      net_premium=mark.net_premium if mark else None,
                      gross_pnl=mark.gross_pnl if mark else None,
                      net_pnl=mark.net_pnl if mark else None,
                      legs=[asdict(l) for l in mark.legs] if mark else None)
        with self.db.session() as s:
            row = s.get(m.LiveState, run_id)
            if row is None:
                s.add(m.LiveState(run_id=run_id, **values))
            else:
                for k, v in values.items():
                    setattr(row, k, v)

    def add_position_snapshots(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            with self.db.session() as s:
                s.execute(insert(m.PositionSnapshot), rows)

    def add_ticks(self, run_id: int, ticks: Iterable[Tick], ts: datetime | None = None) -> int:
        """Store ticks. ``ts`` overrides each tick's own time with the recording time, so a replay
        sees the book exactly as it was at that moment."""
        rows = [dict(run_id=run_id, ts=ts or t.ts, instrument_token=t.instrument_token, ltp=t.ltp, bid=t.bid,
                     ask=t.ask, bid_qty=t.bid_qty, ask_qty=t.ask_qty, oi=t.oi, volume=t.volume) for t in ticks]
        if rows:
            with self.db.session() as s:
                s.execute(insert(m.TickSnapshot), rows)
        return len(rows)

    def pending_commands(self, algo_id: str, run_id: int, now: datetime) -> list[str]:
        """Return and consume unconsumed commands for this algo (either run-specific or algo-wide)."""
        with self.db.session() as s:
            rows = s.scalars(
                select(m.ControlCommand)
                .where(m.ControlCommand.algo_id == algo_id, m.ControlCommand.consumed_at.is_(None),
                       (m.ControlCommand.run_id.is_(None)) | (m.ControlCommand.run_id == run_id))
                .order_by(m.ControlCommand.id)
            ).all()
            for r in rows:
                r.consumed_at = now
            return [r.command for r in rows]

    def issue_command(self, algo_id: str, command: str, now: datetime, run_id: int | None = None,
                      issued_by: str = "cli") -> None:
        with self.db.session() as s:
            s.add(m.ControlCommand(algo_id=algo_id, run_id=run_id, command=command, issued_at=now,
                                   issued_by=issued_by))

    def write_daily_summary(self, row: dict[str, Any]) -> None:
        with self.db.session() as s:
            s.execute(delete(m.DailySummary).where(m.DailySummary.run_id == row["run_id"]))
            s.add(m.DailySummary(**row))

    def daily_summaries(self, algo_id: str, limit: int = 30) -> list[m.DailySummary]:
        with self.db.session() as s:
            return list(s.scalars(
                select(m.DailySummary).where(m.DailySummary.algo_id == algo_id)
                .order_by(m.DailySummary.trade_date.desc(), m.DailySummary.run_id.desc()).limit(limit)
            ).all())
