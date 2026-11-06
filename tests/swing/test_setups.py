from datetime import date, timedelta

from swing.setup_backtest import Trade, portfolio, simulate, summary, symbol_trades
from swing.setups import SetupConfig, find_setup, knife
from swing.zones import ZoneConfig, find_zones
from tests.swing.test_zones import make, saw

CFG = SetupConfig(fill_weeks=1, max_hold_days=5)


def sim(rows, mode="oco", entry=100.0, stop=97.0, target=106.0):
    return simulate(make([(100, 104, 103)] + rows), 0, entry, stop, target, mode, CFG)


def test_fill_then_target():
    filled, k, fill, j, ex, why = sim([(101, 103, 102), (99.5, 102, 101), (101, 106.5, 106)])
    assert (filled, k, fill, j, ex, why) == (True, 2, 100.0, 3, 106.0, "target")


def test_gap_below_entry_fills_at_the_open():
    b = make([(100, 104, 103), (95, 99, 98)])
    b.open[1] = 98.5
    assert simulate(b, 0, 100.0, 97.0, 106.0, "target", CFG)[2] == 98.5


def test_stop_wins_a_day_with_both_and_fill_day_checks_only_the_stop():
    assert sim([(99, 103, 101), (96, 107, 100)])[4:] == (97.0, "stop")      # both next day: the stop
    assert sim([(96.5, 107, 100)])[3:] == (1, 97.0, "stop")                 # fill day: stop only
    assert sim([(99, 107, 101), (101, 105, 104)])[5] == "end"               # target on the fill day ignored
    assert sim([(99, 103, 101)] + [(101, 105, 104)] * 6)[5] == "time"       # sold after max_hold_days


def test_target_only_ignores_the_stop_and_unfilled_expires():
    assert sim([(99, 103, 101), (90, 95, 92), (92, 107, 106)], mode="target")[4:] == (106.0, "target")
    filled, *_, why = sim([(101, 105, 104)] * 7)
    assert not filled and why == "unfilled"


def test_setup_on_a_range():
    b = make(saw(400) + [(101.0, 102.0, 101.5)])
    rep = find_zones(b, ZoneConfig())
    s = find_setup(b, rep, SetupConfig(), "Power", True)
    assert s is not None
    sup = rep.supports()[0]
    assert s.entry == round(min(sup.hi, rep.close), 2) and s.stop < sup.lo
    assert s.target >= rep.resistances()[0].lo - 0.01 and s.rr >= 2
    assert s.industry == "Power" and s.sector_ok is True


def test_knife_blocks_a_fall():
    b = make(saw(400) + [(101, 103, 102), (96, 98, 97), (93, 95, 94), (90, 92, 91)])
    assert knife(b, 1.5, SetupConfig())
    rep = find_zones(b, ZoneConfig())
    assert find_setup(b, rep, SetupConfig()) is None


def test_symbol_trades_twins_and_no_overlap():
    b = make(saw(900))
    tr = symbol_trades(b, ZoneConfig(), SetupConfig(), b.dates[300], b.dates[-1], "X", None, 0.0)
    real = [t for t in tr if not t.random]
    assert real and len(tr) == 2 * len(real)                                # one twin per trade
    oco = sorted((t for t in real if t.mode == "oco"), key=lambda t: t.asof)
    tgt = {t.asof: t for t in real if t.mode == "target"}
    for prev, nxt in zip(oco, oco[1:]):
        assert nxt.asof > max(prev.exit_day, tgt[prev.asof].exit_day)       # no overlapping trades
    filled = [t for t in oco if t.filled]
    assert filled and sum(t.ret_pct > 0 for t in filled) > 0.7 * len(filled)  # a clean range pays
    assert "random twins" in summary(tr, SetupConfig())


def _t(sym, day, industry, exit_in=10, score=70.0):
    d = date(2026, 1, 5) + timedelta(days=day)
    return Trade(sym, d, "oco", False, 100, 97, 106, score, 2.0, True, True, industry, 15000, True, d, 100,
                 d + timedelta(days=exit_in), 106, "target", 5, 5.75, 2.0)


def test_portfolio_limits():
    cfg = SetupConfig(max_open=3, max_per_sector=2)
    ts = [_t("A", 0, "Power"), _t("B", 0, "Power"), _t("C", 0, "Power"), _t("D", 0, "IT"), _t("E", 0, "IT"),
          _t("F", 20, "Power")]
    kept = portfolio(ts, cfg)
    assert [t.symbol for t in kept] == ["A", "B", "D", "F"]                 # C: 2 Power; E: 3 open
