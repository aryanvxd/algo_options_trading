"""Wires a full algo session (clock, feed, broker, journal, ...) and runs it."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterator

from trading_core.broker.charges import ChargeCalculator
from trading_core.broker.paper import PaperBroker
from trading_core.clock import Clock, LiveClock, SimClock, ist
from trading_core.config import PlatformConfig, load_secrets
from trading_core.db.engine import Database
from trading_core.db.migrate import holidays
from trading_core.db.repo import Journal
from trading_core.kite.instruments import InstrumentStore
from trading_core.market.calendar import MarketCalendar
from trading_core.market.premarket import KitePremarketSource, StoredPremarketSource
from trading_core.strategy import registry
from trading_core.strategy.base import AlgoContext
from trading_core.strategy.runner import Runner

log = logging.getLogger(__name__)

REPLAY_START = time(8, 0)


class AlreadyRan(RuntimeError):
    pass


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_instance(path: Path) -> Iterator[None]:
    """Hold an OS file lock for the life of a live run.

    A second ``algo run`` for the same algo would resume the same run with its own feed and broker
    and fight the first one over the position. The OS drops the lock when the process ends, even if
    the window is closed or the process is killed, so a stale lock can never block the next day.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning(f"another `algo run` for this algo is already running (lock: {path}). "
                                 "Close it first, or use `algo kill` to stop it.") from None
        # Who holds the lock, for the dashboard (it checks the process, never the lock itself).
        pid_file = path.with_suffix(".pid")
        pid_file.write_text(json.dumps(dict(pid=os.getpid(), started_at=datetime.now().astimezone().isoformat())))
        try:
            yield
        finally:
            pid_file.unlink(missing_ok=True)
    finally:
        f.close()


def run_algo(platform: PlatformConfig, db: Database, algo_id: str, *, mode: str | None = None,
             replay_date: date | None = None, overrides: dict[str, Any] | None = None,
             force_new: bool = False, clock: Clock | None = None,
             calendar: MarketCalendar | None = None, feed: Any = None) -> int:
    """Run ``algo_id`` for one day. Returns the run_id. A replay may be given its ``feed`` (a
    backtest's rebuilt day); otherwise it reads the ticks recorded on ``replay_date``."""
    algo_cls, cfg = registry.load(algo_id, overrides)
    mode = mode or cfg.mode
    if mode != "paper":
        raise RuntimeError("Live trading is disabled in v1 — only --mode paper is supported.")
    # Replays never touch a live run, so any number of them may run alongside one.
    lock = nullcontext() if replay_date else single_instance(platform.data_dir / "locks" / f"{algo_id}.lock")
    with lock:
        return _run(platform, db, algo_id, algo_cls, cfg, mode, replay_date, force_new, clock, calendar, feed)


def _run(platform: PlatformConfig, db: Database, algo_id: str, algo_cls: type, cfg: Any, mode: str,
         replay_date: date | None, force_new: bool, clock: Clock | None, calendar: MarketCalendar | None,
         feed: Any = None) -> int:
    source = "replay" if replay_date else "live"
    clock = clock or (SimClock(ist(replay_date, REPLAY_START)) if replay_date else LiveClock())
    trade_date = replay_date or clock.today()

    journal = Journal(db)
    journal.register_algo(algo_id, cfg.name, cfg.version, cfg.description, clock.now())
    calendar = calendar or MarketCalendar(holidays(db))
    instruments = InstrumentStore(db, platform.instruments)
    charges = ChargeCalculator(platform.charges)

    kite = None
    if replay_date:
        from trading_core.feed.replay_feed import ReplayFeed
        from trading_core.marketdata.store import MarketStore

        feed = feed or ReplayFeed(db, trade_date, clock, store=MarketStore(platform.marketdata.dir))
        premarket_source = StoredPremarketSource(journal, trade_date)
    else:
        from trading_core.feed.live_feed import LiveFeed
        from trading_core.kite.client import KiteClient

        secrets = load_secrets(platform.root)
        kite = KiteClient.from_db(db, platform.kite, secrets, clock)
        feed = LiveFeed(secrets.api_key, kite.access_token, clock)
        premarket_source = KitePremarketSource(kite, clock)
    broker = PaperBroker(feed, charges, platform.paper, clock)

    if not replay_date and platform.calendar_autofetch:
        refresh_calendar(platform, db, clock, calendar)

    resumed = False
    existing = None if (replay_date or force_new) else journal.find_run(algo_id, trade_date, mode, "live")
    if existing is not None:
        if existing.status in ("DONE", "SKIPPED"):
            raise AlreadyRan(f"{algo_id} already finished for {trade_date} (run {existing.run_id}, "
                             f"{existing.status}). One run per day; use --force-new to override.")
        run_id, resumed = existing.run_id, True
        log.warning("resuming run %s (status %s)", run_id, existing.status)
    else:
        run_id = journal.create_run(algo_id, trade_date, mode, source, cfg.model_dump(mode="json"), clock.now())

    ctx = AlgoContext(run_id=run_id, algo_id=algo_id, mode=mode, source=source, trade_date=trade_date,
                      platform=platform, clock=clock, feed=feed, broker=broker, journal=journal,
                      calendar=calendar, instruments=instruments, premarket_source=premarket_source,
                      charges=charges, kite=kite)
    algo = algo_cls(ctx, cfg)
    if resumed:
        algo.restore(journal.positions_for_run(run_id))
    if cfg.carry_overnight and algo.position is None:
        algo.adopt(journal.open_positions_before(algo_id, mode, source, trade_date))
    Runner(algo).run()
    if source == "live":
        export_recorded_day(platform, db, trade_date, clock.today())
    return run_id


def refresh_calendar(platform: PlatformConfig, db: Database, clock: Clock, calendar: MarketCalendar) -> None:
    """Fetch the market calendar if it hasn't been today (the algos' event checks read it). Never fatal."""
    from trading_core.market.events import refresh

    try:
        log.info("market calendar: %s", refresh(db, platform, clock.now(), calendar))
    except Exception:  # noqa: BLE001 - a calendar outage must not stop trading
        log.exception("market calendar refresh failed")


def export_recorded_day(platform: PlatformConfig, db: Database, day: date, today: date) -> None:
    """Move the day's recorded ticks to Parquet and prune old ones from SQLite. Never fatal."""
    from trading_core.marketdata.manifest import Manifest
    from trading_core.marketdata.sources.recorder import export_day, prune_sqlite_ticks
    from trading_core.marketdata.store import MarketStore

    try:
        manifest = Manifest(db)
        stats = export_day(db, MarketStore(platform.marketdata.dir), manifest, day)
        pruned = prune_sqlite_ticks(db, manifest, today, platform.marketdata.sqlite_tick_keep_days)
        log.info("recorder export %s: %s; pruned %d old tick rows from SQLite", day, stats, pruned)
    except Exception:  # noqa: BLE001 - the trading day is already done; don't fail on housekeeping
        log.exception("recorder export failed for %s (run `algo data export-recorder` later)", day)
