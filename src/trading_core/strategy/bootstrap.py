"""Wires a full algo session (clock, feed, broker, journal, ...) and runs it."""

from __future__ import annotations

import logging
from datetime import date, time
from typing import Any

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


def run_algo(platform: PlatformConfig, db: Database, algo_id: str, *, mode: str | None = None,
             replay_date: date | None = None, overrides: dict[str, Any] | None = None,
             force_new: bool = False, clock: Clock | None = None,
             calendar: MarketCalendar | None = None) -> int:
    """Run ``algo_id`` for one day. Returns the run_id."""
    algo_cls, cfg = registry.load(algo_id, overrides)
    mode = mode or cfg.mode
    if mode != "paper":
        raise RuntimeError("Live trading is disabled in v1 — only --mode paper is supported.")

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

        feed = ReplayFeed(db, trade_date, clock)
        premarket_source = StoredPremarketSource(journal, trade_date)
    else:
        from trading_core.feed.live_feed import LiveFeed
        from trading_core.kite.client import KiteClient

        secrets = load_secrets(platform.root)
        kite = KiteClient.from_db(db, platform.kite, secrets, clock)
        feed = LiveFeed(secrets.api_key, kite.access_token, clock)
        premarket_source = KitePremarketSource(kite, clock)
    broker = PaperBroker(feed, charges, platform.paper, clock)

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
    Runner(algo).run()
    return run_id
