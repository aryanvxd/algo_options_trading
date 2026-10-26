from datetime import date

from sqlalchemy import delete, func, select

from tests.helpers import seed_day
from trading_core.db import models as m
from trading_core.market.calendar import MarketCalendar
from trading_core.marketdata.duck import MarketDB
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.sources.recorder import export_day, prune_sqlite_ticks
from trading_core.marketdata.store import MarketStore
from trading_core.strategy.bootstrap import run_algo

DAY, EXPIRY = date(2026, 10, 23), date(2026, 10, 27)
LOOSE = {"exits": {"pnl_basis": "gross", "stop_loss_points": 77, "target_points": 4.6,  # ≈ ₹5,000 / ₹300 on 1 lot
                   "stale_feed_seconds": 60}}


def count(db, model):
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_export_prune_then_replay_from_parquet(platform, db):
    seed_day(db, platform, DAY, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: max(0.05, 1.0 - 2.0 * f),
             interval_s=60)
    store, manifest = MarketStore(platform.marketdata.dir), Manifest(db)
    ticks_before = count(db, m.TickSnapshot)

    stats = export_day(db, store, manifest, DAY)
    assert stats["ticks"] == ticks_before and stats["unknown"] == 0
    mdb = MarketDB(store)
    assert mdb.rows("SELECT count(*) FROM options_ticks")[0][0] == stats["options"]
    assert mdb.rows("SELECT count(DISTINCT symbol) FROM index_ticks")[0][0] == 2
    assert mdb.rows("SELECT min(bid) > 0, count(DISTINCT strike) FROM options_ticks")[0] == (True, 29)

    # nothing is pruned inside the keep window; everything once past it
    assert prune_sqlite_ticks(db, manifest, DAY, keep_days=7) == 0
    assert prune_sqlite_ticks(db, manifest, date(2026, 11, 30), keep_days=7) == ticks_before
    with db.session() as s:                       # also lose the SQLite instrument snapshot
        s.execute(delete(m.Instrument))

    run_id = run_algo(platform, db, "iron_condor", replay_date=DAY, overrides=LOOSE, calendar=MarketCalendar([]))
    with db.session() as s:
        pos = s.scalars(select(m.Position).where(m.Position.run_id == run_id)).one()
    assert pos.status == "CLOSED" and pos.exit_reason == "TARGET"
