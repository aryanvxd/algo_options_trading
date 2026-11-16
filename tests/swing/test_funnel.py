from dataclasses import replace
from datetime import datetime

import numpy as np

from swing import fundamentals, funnel, tracking, watchlists
from swing.settings import FundRule, SwingConfig, load_swing_config
from swing.universe import SwingIndex, SwingUniverse
from swing.zones import find_zones
from tests.swing.test_zones import make, saw
from trading_core.clock import IST

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=IST)


def named(b, symbol):
    return replace(b, symbol=symbol)


def setup_world(cfg: SwingConfig):
    """One sector index sitting on support with a rising trend, two member stocks on support."""
    rows = [(lo + i * 0.02, hi + i * 0.02, c + i * 0.02) for i, (lo, hi, c) in enumerate(saw(400))]
    near = rows[-20][2]                                  # back at the bottom of the swing
    index = named(make(rows + [(near - 0.5, near + 0.5, near)]), "NIFTY TEST")
    stock = named(make(saw(400) + [(101.0, 102.0, 101.5)]), "AAA")
    other = named(make(saw(400) + [(101.0, 102.0, 101.6)]), "BBB")
    u = SwingUniverse("t", ["AAA", "BBB"], {"AAA": "Power", "BBB": "Power"},
                      [SwingIndex("NIFTY TEST", "sector", ["AAA", "BBB"])])
    ib, sb = {"NIFTY TEST": index}, {"AAA": stock, "BBB": other}
    reps = {k: find_zones(b, cfg.zones) for k, b in {**ib, **sb}.items()}
    return u, ib, sb, reps


def test_rsi_bounds_and_direction():
    up = np.arange(1, 40, dtype=float)
    assert funnel.rsi(up, 14)[-1] == 100.0
    r = funnel.rsi(np.array([10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11], dtype=float), 14)
    assert np.isnan(r[:14]).all() and 0 < r[-1] < 100


def test_funnel_stages_and_ranking():
    cfg = load_swing_config({"funnel": {"sector_need_trend": False, "top_n": 1}, "setups": {"max_per_sector": 3}})
    u, ib, sb, reps = setup_world(cfg)
    out = funnel.build(u, ib, {k: reps[k] for k in ib}, sb, {k: reps[k] for k in sb}, {"AAA": 50, "BBB": 50}, None,
                       cfg, None, NOW)
    assert out["available"] and out["funnel"]["sectors"] == 1
    sector = out["sectors"][0]
    assert sector["passed"] and sector["state"] in (funnel.LOOK_FOR_LONGS, funnel.SUPPORT_DOWNTREND)
    assert out["funnel"]["plans"] == 2 and out["funnel"]["top"] == 1
    p = funnel.top(out)[0]
    assert p["rank"] == 1 and p["sector"] == "NIFTY TEST" and p["sector_ok"]
    assert p["stop"] < p["zone"][0] and p["target"] > p["entry"] and p["rr"] >= cfg.setups.min_rr
    assert p["qty"] == int(p["amount"] // p["entry"]) and p["score"] == p["tech"]      # no news/fund data: tech only
    assert round(p["zone"][0] - p["stop"], 1) == round(cfg.funnel.stop_atr * p["atr"], 1)
    assert "not an edge" in out["ranking_note"]


def test_sector_that_fails_keeps_its_stocks_out():
    cfg = load_swing_config({"funnel": {"sector_near_atr": 0.01, "sector_need_trend": True}})
    u, ib, sb, reps = setup_world(cfg)
    rows = [(lo, hi, c) for lo, hi, c in saw(400)]
    ib = {"NIFTY TEST": named(make(rows + [(108.5, 109.5, 109.0)]), "NIFTY TEST")}     # at the top: resistance
    reps["NIFTY TEST"] = find_zones(ib["NIFTY TEST"], cfg.zones)
    out = funnel.build(u, ib, {k: reps[k] for k in ib}, sb, {k: reps[k] for k in sb}, {}, None, cfg)
    assert out["funnel"]["sectors_passed"] == 0 and out["funnel"]["considered"] == 0 and out["picks"] == []
    cfg2 = load_swing_config({"funnel": {"sector_near_atr": 0.01, "require_sector": False}})
    out2 = funnel.build(u, ib, {k: reps[k] for k in ib}, sb, {k: reps[k] for k in sb}, {}, None, cfg2)
    assert out2["funnel"]["plans"] == 2 and not out2["picks"][0]["sector_ok"]


def test_fundamentals_and_news_change_the_score(db):
    cfg = load_swing_config({"funnel": {"sector_need_trend": False}})
    u, ib, sb, reps = setup_world(cfg)
    fundamentals.save(db, [{"symbol": "aaa", "metrics": {"ROCE %": "22", "Debt to equity": 2.5}}], None, NOW)
    assert fundamentals.of(db) == {"AAA": {"roce": 22.0, "debt_to_equity": 2.5}}
    out = funnel.build(u, ib, {k: reps[k] for k in ib}, sb, {k: reps[k] for k in sb}, {}, None, cfg, db, NOW)
    a = next(p for p in out["picks"] if p["symbol"] == "AAA")
    b = next(p for p in out["picks"] if p["symbol"] == "BBB")
    assert a["fund"] == 50.0 and len(a["fund_checks"]) == 2 and b["fund"] is None
    assert a["news"] == 100.0
    f = cfg.funnel
    want = (f.w_tech * a["tech"] + f.w_news * 100 + f.w_fund * 50) / (f.w_tech + f.w_news + f.w_fund)
    assert abs(a["score"] - want) < 0.11


def test_fund_score_rules():
    rules = [FundRule(metric="roce", op=">=", value=15), FundRule(metric="pe", op="<=", value=30)]
    assert fundamentals.score({"roce": 20, "pe": 40}, rules)[0] == 50.0
    assert fundamentals.score({"other": 1}, rules) == (None, [])
    assert fundamentals.metric_key("Price to book value") == "price_to_book_value"
    assert fundamentals.metric_key("ROCE %") == "roce"


def test_watchlists_auto_list_is_replaced_and_read_only(db):
    ls = watchlists.lists(db, NOW)
    assert [w["kind"] for w in ls] == ["auto"] + ["manual"] * 4 and ls[0]["name"] == "Algo picks"
    pick = dict(symbol="AAA", close=101.5, entry=101.0, stop=97.0, target=109.0, rank=1, sector="NIFTY TEST",
                industry="Power", score=70.0)
    assert watchlists.replace_auto(db, [pick], NOW) == 1
    assert watchlists.replace_auto(db, [dict(pick, symbol="BBB")], NOW) == 1
    head, items = watchlists.items(db, ls[0]["id"])
    assert [i["symbol"] for i in items] == ["BBB"] and items[0]["entry"] == 101.0
    try:
        watchlists.add(db, ls[0]["id"], "CCC", NOW)
        raise AssertionError("auto list accepted a stock")
    except watchlists.WatchlistError:
        pass
    assert watchlists.add(db, ls[1]["id"], "CCC", NOW) is True
    assert watchlists.add(db, ls[1]["id"], "CCC", NOW) is False
    assert watchlists.rename(db, ls[1]["id"], "  Banks   to watch ")["name"] == "Banks to watch"
    assert watchlists.remove(db, ls[1]["id"], "CCC") is True


def test_journal_records_twins_and_follows_them(db):
    cfg = load_swing_config({"funnel": {"sector_need_trend": False}})
    u, ib, sb, reps = setup_world(cfg)
    out = funnel.build(u, ib, {k: reps[k] for k in ib}, sb, {k: reps[k] for k in sb}, {}, None, cfg)
    top = funnel.top(out)
    assert tracking.record(db, out, top, cfg, NOW) == len(top)
    assert tracking.record(db, out, top, cfg, NOW) == 0                 # pending: no second pick
    j = tracking.journal(db)
    assert len(j["rows"]) == len(top) and all(r["twin"] for r in j["rows"])
    r = j["rows"][0]
    assert r["status"] == "pending"
    tw = r["twin"]
    assert round(tw["entry"] - tw["stop"], 2) == round(r["entry"] - r["stop"], 2)

    # the next days: the low reaches the entry, then the target
    b = sb[r["symbol"]]
    later = make(saw(400) + [(101.0, 102.0, 101.5), (r["entry"] - 0.2, r["entry"] + 1, r["entry"] + 0.5),
                             (r["entry"], r["target"] + 0.5, r["target"])])
    later = named(later, b.symbol)
    n = tracking.update(db, lambda syms: {s: later for s in syms}, cfg, NOW)
    assert n == 2 * len(top)
    r2 = next(x for x in tracking.journal(db)["rows"] if x["symbol"] == r["symbol"])
    assert r2["status"] == "target" and r2["return_pct"] > 0 and r2["fill_date"]
    st = tracking.journal(db)["stats"]["picks"]
    assert st["closed"] >= 1 and st["win_rate"] == 100.0
