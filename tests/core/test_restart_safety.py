"""What happens when `algo run` is started twice, or restarted mid-day (27 Oct 2026: a second run
resumed the open trade at 10:36 and closed it at once as STALE_FEED)."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from tests.helpers import FakeFeed, seed_day, tick
from trading_core.clock import IST, SimClock, ist
from trading_core.config import KiteSecrets
from trading_core.db import models as m
from trading_core.feed.live_feed import LiveFeed
from trading_core.market.calendar import MarketCalendar
from trading_core.risk.rules import StaleFeed
from trading_core.strategy.bootstrap import AlreadyRunning, run_algo, single_instance

TRADE_DATE = date(2026, 10, 23)
EXPIRY = date(2026, 10, 27)
CAL = MarketCalendar([])


def test_second_lock_on_the_same_file_is_refused_until_the_first_is_released(tmp_path):
    path = tmp_path / "locks" / "iron_condor.lock"
    with single_instance(path):
        with pytest.raises(AlreadyRunning):
            with single_instance(path):
                pass
        with single_instance(tmp_path / "locks" / "other_algo.lock"):    # other algos are not blocked
            pass
    with single_instance(path):                                          # released on exit
        pass


def test_live_run_refuses_to_start_while_another_holds_the_lock(platform, db):
    with single_instance(platform.data_dir / "locks" / "iron_condor.lock"):
        with pytest.raises(AlreadyRunning):
            run_algo(platform, db, "iron_condor", clock=SimClock(ist(TRADE_DATE, time(10, 36))), calendar=CAL)
    with db.session() as s:
        assert s.scalars(select(m.AlgoRun)).first() is None        # refused before touching the journal


def test_live_feed_counts_from_the_connect_until_the_first_tick(monkeypatch):
    clock = SimClock(datetime(2026, 10, 27, 10, 36, tzinfo=IST))
    feed = LiveFeed("key", "token", clock)
    monkeypatch.setattr(feed.conn, "start", lambda: None)
    rule = StaleFeed(10)
    stale = lambda: rule.check(SimpleNamespace(feed_age_s=feed.last_tick_age(clock.now())))  # noqa: E731

    assert feed.last_tick_age(clock.now()) is None                 # not started: still "never"
    feed.start()
    clock.sleep(3)
    assert feed.last_tick_age(clock.now()) == 3 and stale() is None   # restart grace, no exit
    feed.table.update([tick(256265, 22800, when=clock.now())])
    clock.sleep(2)
    assert feed.last_tick_age(clock.now()) == 2                    # ticking: measured from the tick
    clock.sleep(10)
    assert stale() is not None                                     # a real gap still exits


def test_live_feed_with_no_tick_at_all_goes_stale_after_the_limit(monkeypatch):
    clock = SimClock(datetime(2026, 10, 27, 10, 36, tzinfo=IST))
    feed = LiveFeed("key", "token", clock)
    monkeypatch.setattr(feed.conn, "start", lambda: None)
    feed.start()
    clock.sleep(11)
    assert StaleFeed(10).check(SimpleNamespace(feed_age_s=feed.last_tick_age(clock.now())))


def test_restart_after_the_trade_closed_writes_the_report_at_once(platform, db, monkeypatch):
    """The window was closed after the exit but before 15:35: rerunning finishes the day at once."""
    seed_day(db, platform, TRADE_DATE, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    run_id = run_algo(platform, db, "iron_condor", replay_date=TRADE_DATE, calendar=CAL,
                      overrides={"exits": {"pnl_basis": "gross", "stop_loss_points": 77, "target_points": 77,
                                           "stale_feed_seconds": 60}})
    with db.session() as s:     # make it look like a live run that was closed before its report
        run = s.get(m.AlgoRun, run_id)
        run.source, run.status, run.ended_at = "live", "IN_TRADE", None
        s.execute(delete(m.DailySummary))
        orders = s.scalar(select(func.count()).select_from(m.Order))

    import trading_core.feed.live_feed as live_feed
    import trading_core.kite.client as kite_client
    import trading_core.strategy.bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "load_secrets", lambda root: KiteSecrets(api_key="k", api_secret="s"))
    monkeypatch.setattr(kite_client.KiteClient, "from_db",
                        classmethod(lambda cls, *a: SimpleNamespace(access_token="t")))
    monkeypatch.setattr(live_feed, "LiveFeed", lambda *a: FakeFeed(age=None))
    restart = ist(TRADE_DATE, time(14, 11))

    assert run_algo(platform, db, "iron_condor", clock=SimClock(restart), calendar=CAL) == run_id
    with db.session() as s:
        run = s.get(m.AlgoRun, run_id)
        pos = s.scalars(select(m.Position).where(m.Position.run_id == run_id)).one()
        summary = s.get(m.DailySummary, run_id)
        assert run.status == "DONE" and run.ended_at.replace(tzinfo=None) < restart.replace(tzinfo=None) + \
            timedelta(minutes=1)                                   # did not wait for 15:35
        assert summary.net_pnl == pytest.approx(pos.net_pnl) and summary.exit_reason == pos.exit_reason
        assert s.scalar(select(func.count()).select_from(m.Order)) == orders   # nothing re-traded
