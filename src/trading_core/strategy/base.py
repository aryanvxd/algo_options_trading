"""BaseAlgo: the lifecycle every algo follows, and the shared position-management loop.

An algo subclass implements only what is specific to it:
  day_check()      decide if today is a trade day           (raise SkipDay to stop)
  premarket()      pre-open filters                          (raise SkipDay to stop)
  start_feed()     subscribe the instruments it needs
  build_entry()    return a Position to open, None to retry next loop, or raise SkipDay
  build_exit_rules()  ordered list of ExitRule (first match wins)

Everything else — order execution, mark-to-market, exits, snapshots, live state, kill switch,
daily summary — is handled here, identically for every algo.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_core.broker.base import Broker
from trading_core.broker.charges import ChargeCalculator
from trading_core.clock import Clock
from trading_core.config import PlatformConfig
from trading_core.db.repo import Journal
from trading_core.feed.base import MarketFeed
from trading_core.kite.instruments import InstrumentStore
from trading_core.market.calendar import MarketCalendar
from trading_core.market.premarket import PremarketSource
from trading_core.reporting.daily_report import write_report
from trading_core.risk.engine import ExitRule, ExitSignal, RiskContext, RiskEngine
from trading_core.strategy.execution import LegExecutor
from trading_core.strategy.position import Mark, Position


class ScheduleConfig(BaseModel):
    day_check: time = time(8, 15)
    premarket: time = time(9, 8)
    start_feed: time = time(9, 15)
    entry: time = time(9, 20)
    entry_deadline: time = time(9, 35)     # stop retrying entry after this
    square_off: time = time(15, 15)
    report: time = time(15, 35)


class BaseAlgoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algo_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    mode: Literal["paper", "live"] = "paper"
    lots: int = Field(1, ge=1)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)


class SkipDay(Exception):
    """Raised by a lifecycle hook to stop trading for the day (reason is journaled)."""


@dataclass
class AlgoContext:
    run_id: int
    algo_id: str
    mode: str                      # paper | live
    source: str                    # live | replay
    trade_date: date
    platform: PlatformConfig
    clock: Clock
    feed: MarketFeed
    broker: Broker
    journal: Journal
    calendar: MarketCalendar
    instruments: InstrumentStore
    premarket_source: PremarketSource
    charges: ChargeCalculator
    kite: Any = None               # KiteClient in live sessions, None in replays

    @property
    def is_replay(self) -> bool:
        return self.source == "replay"


ENTRY_RETRY_S = 30

# Algo states
IDLE, READY, ENTRY_PENDING, IN_TRADE, EXITING, DONE, SKIPPED = (
    "IDLE", "READY", "ENTRY_PENDING", "IN_TRADE", "EXITING", "DONE", "SKIPPED")


class BaseAlgo(ABC):
    config_model: ClassVar[type[BaseAlgoConfig]] = BaseAlgoConfig
    structure: ClassVar[str] = "CUSTOM"

    def __init__(self, ctx: AlgoContext, cfg: BaseAlgoConfig):
        self.ctx = ctx
        self.cfg = cfg
        self.log = logging.getLogger(f"algo.{ctx.algo_id}")
        self.state = IDLE
        self.position: Position | None = None
        self.executor = LegExecutor(ctx)
        self._risk: RiskEngine | None = None
        self.kill_requested = False
        self.kill_reason: str | None = None
        self.paused = False
        self.exit_signal: ExitSignal | None = None
        self.last_mark: Mark | None = None
        self.max_net: float | None = None
        self.min_net: float | None = None
        self.skip_reason: str | None = None
        self.notes: list[str] = []
        self._entry_wait_reason: str | None = None
        self._entry_retry_at: datetime | None = None
        self._last_snapshot: datetime | None = None
        self._snapshots: list[dict[str, Any]] = []

    # ------------------------------------------------------------ hooks for subclasses

    def day_check(self) -> None:
        """Raise SkipDay if today is not a trade day."""

    def premarket(self) -> None:
        """Raise SkipDay if pre-open conditions fail."""

    def start_feed(self) -> None:
        """Subscribe instruments on ``self.ctx.feed``."""

    @abstractmethod
    def build_entry(self) -> Position | None:
        """Return the position to open now, None to retry next loop, or raise SkipDay."""

    @abstractmethod
    def build_exit_rules(self) -> list[ExitRule]: ...

    def spot(self) -> float | None:
        return None

    def vix(self) -> float | None:
        return None

    def levels(self) -> dict[str, Any] | None:
        """Price/P&L levels for the dashboard (entry credit, stop, target, ...)."""
        return None

    # ------------------------------------------------------------ helpers

    @property
    def risk(self) -> RiskEngine:
        # Built lazily: rules may depend on facts learned during the day (e.g. lot size).
        if self._risk is None:
            self._risk = RiskEngine(self.build_exit_rules())
        return self._risk

    @property
    def active(self) -> bool:
        return self.state in (ENTRY_PENDING, IN_TRADE, EXITING)

    @property
    def finished(self) -> bool:
        return self.state in (DONE, SKIPPED)

    def check(self, name: str, passed: bool, value: float | None = None, threshold: float | None = None,
              **detail: Any) -> bool:
        self.ctx.journal.add_check(self.ctx.run_id, self.ctx.clock.now(), name, passed, value, threshold,
                                   detail or None)
        self.log.info("check %-12s %s value=%s threshold=%s %s", name, "PASS" if passed else "FAIL", value,
                      threshold, detail or "")
        return passed

    def event(self, type_: str, message: str, level: str = "INFO", **data: Any) -> None:
        getattr(self.log, level.lower(), self.log.info)(message)
        self.ctx.journal.event(self.ctx.run_id, self.ctx.algo_id, self.ctx.clock.now(), type_, message,
                               level=level, data=data or None)

    # ------------------------------------------------------------ lifecycle (called by Runner)

    def mark_ready(self) -> None:
        if self.state == IDLE:
            self.state = READY

    def skip(self, reason: str) -> None:
        if self.finished:
            return
        self.state = SKIPPED
        self.skip_reason = reason
        self.ctx.journal.set_run_status(self.ctx.run_id, "SKIPPED", self.ctx.clock.now(), skip_reason=reason)
        self.event("SKIP", f"skipping day: {reason}")

    def begin_entry(self) -> None:
        if self.state != READY:
            return
        if self.kill_requested or self.paused:
            self.notes.append("entry blocked by kill/pause")
            self.state = DONE
            return
        self.state = ENTRY_PENDING

    def request_kill(self, reason: str = "manual") -> None:
        if self.kill_requested:
            return
        self.kill_requested, self.kill_reason = True, reason
        self.event("KILL_SWITCH", f"kill requested: {reason}", level="WARNING")
        if self.state in (IDLE, READY, ENTRY_PENDING):
            self.notes.append(f"killed before entry: {reason}")
            self.state = DONE

    def on_error(self, exc: BaseException, where: str) -> None:
        self.log.exception("error in %s", where)
        self.ctx.journal.event(self.ctx.run_id, self.ctx.algo_id, self.ctx.clock.now(), "ERROR",
                               f"{where}: {exc!r}", level="ERROR")
        if self.position is not None and not self.position.is_flat:
            self.request_kill(f"error in {where}")

    def on_tick(self, now: datetime) -> None:
        if self.state == ENTRY_PENDING:
            if now.time() > self.cfg.schedule.entry_deadline:
                self.skip(f"no entry by {self.cfg.schedule.entry_deadline:%H:%M}"
                          + (f" (last: {self._entry_wait_reason})" if self._entry_wait_reason else ""))
            elif not self.paused and (self._entry_retry_at is None or now >= self._entry_retry_at):
                self._try_entry(now)
        elif self.state in (IN_TRADE, EXITING):
            self._monitor(now)

    def square_off(self) -> None:
        if self.state == ENTRY_PENDING:
            self.skip("square-off time reached without entry")
        elif self.state == IN_TRADE:
            self._start_exit(ExitSignal("TIME_EXIT", "scheduled square-off"))
            self._monitor(self.ctx.clock.now())

    def restore(self, positions: list[Position]) -> None:
        """Resume after a restart: pick up the day's position from the DB."""
        traded = [p for p in positions if any(l.entry_price is not None for l in p.legs)]
        if not traded:
            return
        pos = traded[-1]
        self.position = pos
        if pos.is_flat:
            self.state = DONE
            self.event("RESUME", f"resumed: position {pos.position_id} already closed")
        elif pos.status == "OPEN":
            self.state = IN_TRADE
            self.event("RESUME", f"resumed open position {pos.position_id}", level="WARNING")
        else:
            self.state = IN_TRADE
            self._start_exit(ExitSignal("RESTART_PARTIAL", "restart found a partially opened position"))

    # ------------------------------------------------------------ entry / exit internals

    def _try_entry(self, now: datetime) -> None:
        try:
            pos = self.build_entry()
        except SkipDay as e:
            self.skip(str(e))
            return
        if pos is None:
            return
        pos.status = "OPENING"
        self.ctx.journal.create_position(self.ctx.run_id, self.ctx.algo_id, self.ctx.mode, pos)
        ok = self.executor.open(pos)
        pos.entry_time = self.ctx.clock.now()
        if ok:
            pos.status = "OPEN"
            self.position = pos
            self.ctx.journal.update_position(pos)
            self.ctx.journal.set_run_status(self.ctx.run_id, "IN_TRADE", now)
            self.state = IN_TRADE
            self.event("ENTRY", f"entered {pos.structure}: credit {pos.entry_credit_pts} pts, "
                                f"entry charges ₹{sum(l.entry_charges for l in pos.legs):.2f}",
                       credit=pos.entry_credit_pts, legs={l.role: l.entry_price for l in pos.legs})
            return
        filled_any = any(l.entry_price is not None for l in pos.legs)
        pos.status, pos.exit_reason, pos.exit_time = "FAILED", "ENTRY_FAILED", self.ctx.clock.now()
        self.ctx.journal.update_position(pos)
        if filled_any:
            # Money was spent on a partial fill + unwind: that is today's one trade.
            self.position = pos
            self.state = DONE
            self.event("ENTRY_FAILED", "entry failed after partial fill; legs unwound", level="WARNING")
        else:
            # Nothing filled, nothing spent: try again shortly instead of hammering every loop.
            self._entry_wait_reason = "order rejected"
            self._entry_retry_at = now + timedelta(seconds=ENTRY_RETRY_S)

    def _start_exit(self, signal: ExitSignal) -> None:
        if self.state != IN_TRADE:
            return
        self.exit_signal = signal
        self.state = EXITING
        mark = self.last_mark
        self.event("EXIT_TRIGGER", f"{signal.rule}: {signal.reason}", rule=signal.rule,
                   net_pnl=mark.net_pnl if mark else None, net_premium=mark.net_premium if mark else None)

    def _monitor(self, now: datetime) -> None:
        pos = self.position
        assert pos is not None
        mark = pos.mark(self.ctx.feed.latest, self.ctx.charges, now)
        self.last_mark = mark
        if mark.complete:
            self.max_net = mark.net_pnl if self.max_net is None else max(self.max_net, mark.net_pnl)
            self.min_net = mark.net_pnl if self.min_net is None else min(self.min_net, mark.net_pnl)

        if self.state == IN_TRADE:
            signal = self.risk.evaluate(RiskContext(
                now=now, position=pos, mark=mark, feed_age_s=self.ctx.feed.last_tick_age(now),
                kill_requested=self.kill_requested, kill_reason=self.kill_reason))
            if signal:
                self._start_exit(signal)

        self._maybe_snapshot(now, mark)

        if self.state == EXITING and self.executor.close(pos):
            pos.status = "CLOSED"
            pos.exit_time = self.ctx.clock.now()
            pos.exit_reason = self.exit_signal.rule if self.exit_signal else "UNKNOWN"
            self.ctx.journal.update_position(pos)
            net = round(pos.realized_gross - pos.total_charges, 2)
            self.event("EXIT", f"closed {pos.structure} ({pos.exit_reason}): gross ₹{pos.realized_gross:.2f}, "
                               f"charges ₹{pos.total_charges:.2f}, net ₹{net:.2f}",
                       gross=pos.realized_gross, charges=pos.total_charges, net=net)
            self.flush_snapshots()
            self.state = DONE

    def _maybe_snapshot(self, now: datetime, mark: Mark) -> None:
        interval = self.ctx.platform.runtime.position_snapshot_interval_s
        if self._last_snapshot is not None and (now - self._last_snapshot).total_seconds() < interval:
            return
        self._last_snapshot = now
        self._snapshots.append(dict(position_id=self.position.position_id, ts=now, spot=self.spot(),
                                    vix=self.vix(), net_premium=mark.net_premium, gross_pnl=mark.gross_pnl,
                                    net_pnl=mark.net_pnl if mark.complete else None))
        if len(self._snapshots) >= 60:
            self.flush_snapshots()

    def flush_snapshots(self) -> None:
        if self._snapshots:
            self.ctx.journal.add_position_snapshots(self._snapshots)
            self._snapshots = []

    # ------------------------------------------------------------ live state / report

    def write_live_state(self, now: datetime) -> None:
        age = self.ctx.feed.last_tick_age(now)
        feed_ok = age is not None and age <= 10
        self.ctx.journal.upsert_live_state(self.ctx.run_id, now, self.state, self.spot(), self.vix(),
                                           self.last_mark if self.position else None, self.levels(), feed_ok)

    def summary(self) -> dict[str, Any]:
        pos = self.position
        traded = pos is not None and any(l.entry_price is not None for l in pos.legs)
        closed = traded and pos.is_flat
        notes = list(self.notes)
        if self.skip_reason:
            notes.insert(0, f"skipped: {self.skip_reason}")
        if traded and not closed:
            notes.append("position still open at report time")
        return dict(
            run_id=self.ctx.run_id, algo_id=self.ctx.algo_id, trade_date=self.ctx.trade_date, mode=self.ctx.mode,
            source=self.ctx.source, traded=traded,
            entry_credit_pts=pos.entry_credit_pts if traded else None,
            max_profit=self.max_net, max_drawdown=self.min_net,
            gross_pnl=pos.realized_gross if closed else None,
            charges=pos.total_charges if traded else None,
            net_pnl=round(pos.realized_gross - pos.total_charges, 2) if closed else None,
            exit_reason=pos.exit_reason if traded else None,
            notes="; ".join(notes) or None,
        )

    def report(self) -> Path:
        self.flush_snapshots()
        summary = self.summary()
        self.ctx.journal.write_daily_summary(summary)
        now = self.ctx.clock.now()
        if self.state != SKIPPED:
            self.state = DONE
        self.ctx.journal.set_run_status(self.ctx.run_id, "SKIPPED" if self.skip_reason else "DONE", now,
                                        ended=True)
        path = write_report(self.ctx.platform.reports_dir, summary, self.position)
        self.log.info("report written to %s", path)
        return path
