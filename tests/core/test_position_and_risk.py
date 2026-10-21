from datetime import datetime, time

import pytest

from tests.helpers import tick
from trading_core.broker.base import BUY, SELL
from trading_core.broker.charges import ChargeCalculator
from trading_core.clock import IST
from trading_core.config import ChargeRates
from trading_core.risk.engine import RiskContext, RiskEngine
from trading_core.risk.rules import KillSwitch, PnlStop, PnlTarget, StaleFeed, TimeExit
from trading_core.strategy.position import Leg, Position

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=IST)
CHARGES = ChargeCalculator(ChargeRates())


def condor() -> Position:
    legs = [
        Leg("LONG_PE", 1, "PE24400", BUY, 65, entry_price=2.0, entry_charges=24.0),
        Leg("LONG_CE", 2, "CE25600", BUY, 65, entry_price=2.0, entry_charges=24.0),
        Leg("SHORT_PE", 3, "PE24700", SELL, 65, entry_price=10.0, entry_charges=25.0),
        Leg("SHORT_CE", 4, "CE25300", SELL, 65, entry_price=10.0, entry_charges=25.0),
    ]
    return Position("IRON_CONDOR", "NIFTY", legs, status="OPEN")


def quotes(short_ask=8.0, long_bid=1.5):
    ticks = {1: tick(1, 1.6, long_bid, long_bid + 0.1), 2: tick(2, 1.6, long_bid, long_bid + 0.1),
             3: tick(3, 7.9, short_ask - 0.1, short_ask), 4: tick(4, 7.9, short_ask - 0.1, short_ask)}
    return ticks.get


def test_entry_credit_and_order_of_legs():
    pos = condor()
    assert pos.entry_credit_pts == 16.0
    assert [l.side for l in pos.entry_order()] == [BUY, BUY, SELL, SELL]
    assert [l.side for l in pos.exit_order()] == [SELL, SELL, BUY, BUY]


def test_mark_uses_executable_prices():
    m = condor().mark(quotes(short_ask=8.0, long_bid=1.5), CHARGES, NOW)
    # shorts bought back at ask 8.0 (+2 each), longs sold at bid 1.5 (-0.5 each) -> +3 pts x 65
    assert m.complete and m.gross_pnl == pytest.approx(195.0)
    assert m.net_premium == 13.0
    assert m.entry_charges == 98.0 and m.exit_charges > 80
    assert m.net_pnl == pytest.approx(195.0 - m.charges)


def test_missing_quote_makes_mark_incomplete():
    m = condor().mark(lambda tok: None if tok == 3 else quotes()(tok), CHARGES, NOW)
    assert not m.complete and m.net_premium is None


def engine(**kw):
    return RiskEngine([KillSwitch(), StaleFeed(10), PnlStop(300, kw.get("basis", "net")),
                       PnlTarget(500, kw.get("basis", "net")), TimeExit(time(15, 15))])


def ctx(mark, now=NOW, age=1.0, kill=False):
    return RiskContext(now=now, position=condor(), mark=mark, feed_age_s=age, kill_requested=kill)


def test_kill_switch_has_priority_over_everything():
    bad = condor().mark(quotes(short_ask=20.0), CHARGES, NOW)
    sig = engine().evaluate(ctx(bad, kill=True, age=None))
    assert sig.rule == "KILL_SWITCH"


def test_stale_feed_beats_stop():
    bad = condor().mark(quotes(short_ask=20.0), CHARGES, NOW)
    assert engine().evaluate(ctx(bad, age=12)).rule == "STALE_FEED"
    assert engine().evaluate(ctx(bad, age=None)).rule == "STALE_FEED"


def test_stop_and_target_on_net_basis():
    assert engine().evaluate(ctx(condor().mark(quotes(short_ask=13.0), CHARGES, NOW))).rule == "STOP_LOSS"
    assert engine().evaluate(ctx(condor().mark(quotes(short_ask=3.0), CHARGES, NOW))).rule == "TARGET"


def test_gross_basis_ignores_charges():
    # +3 pts = +195 gross, but net of ~₹180 charges it is ~+15
    m = condor().mark(quotes(short_ask=6.5, long_bid=1.5), CHARGES, NOW)   # gross +390
    assert engine(basis="gross").evaluate(ctx(m)) is None
    m2 = condor().mark(quotes(short_ask=5.5, long_bid=1.5), CHARGES, NOW)  # gross +520
    assert engine(basis="gross").evaluate(ctx(m2)).rule == "TARGET"
    assert engine(basis="net").evaluate(ctx(m2)) is None


def test_incomplete_mark_never_triggers_pnl_rules():
    m = condor().mark(lambda tok: None if tok == 3 else quotes(short_ask=30.0)(tok), CHARGES, NOW)
    assert engine().evaluate(ctx(m)) is None


def test_time_exit():
    m = condor().mark(quotes(), CHARGES, NOW)
    late = datetime(2026, 10, 23, 15, 15, tzinfo=IST)
    assert engine().evaluate(ctx(m, now=late)).rule == "TIME_EXIT"
    assert engine().evaluate(ctx(m)) is None
