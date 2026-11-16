"""The weekend workflow (dashboard Swing › Picks): sectors first, then their stocks, then a ranking.

Stage 1, sectors: each sector and theme index against its own zones. An index **passes** when it is
inside a zone or within ``funnel.sector_near_atr`` ATR above a support, and (``sector_need_trend``)
its trend is up (``setups.trend_ok``). Also shown: RSI and relative strength against the benchmark
(NIFTY 50) over 4 and 13 weeks.

Stage 2, stocks: the liquid members of the indices that passed (all liquid stocks when
``require_sector`` is off) with a buy plan from ``setups.find_setup``: entry at the top of the
support zone, stop under it (``funnel.stop_atr``), target at the next resistance, risk:reward at
least ``setups.min_rr``, no sharp fall just before.

Stage 3, ranking: score = weighted average of the parts a stock has:
- **tech** (main): closeness to the entry, room to the target, trend, its sector, RSI turning up.
- **news**: results inside the holding window, or High-impact Indian macro events in the next few
  days, lower it and are flagged (the market calendar; nothing else is read).
- **fund**: the share of your fundamentals rules a stock passes (uploaded CSV; none = left out).

The best ``top_n`` (at most ``setups.max_per_sector`` per industry) fill the Algo picks watchlist.

**This is a ranking aid, not an edge.** The backtests (spec 9a, 9b) found that zones held no more
often than random levels and that these setups did no better than random entries with the same
stops and targets, with or without the trend and sector filters. Every pick is therefore followed
forward next to a random twin (``swing.tracking``), so your own results build up week by week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np

from swing import fundamentals
from swing.bars import Bars
from swing.chart import NOTE
from swing.overview import IN_ZONE, place
from swing.settings import SwingConfig
from swing.setups import find_setup, trend_ok
from swing.universe import SwingUniverse
from swing.zones import ZoneReport
from trading_core.clock import IST

LOOK_FOR_LONGS = "look for longs"
SUPPORT_DOWNTREND = "at support, downtrend"
NEAR_RES = "near resistance"
MID_RANGE = "mid-range"
NO_ZONES = "no zones"
RANKING_NOTE = ("The score ranks the plans; it is not an edge. In the backtests these setups did no better "
                "than random entries with the same stops and targets, so every pick is tracked next to a random twin.")


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder's RSI; NaN until there are ``n`` changes."""
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    d = np.diff(close)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(close)):
        if i > n:
            au = (au * (n - 1) + up[i - 1]) / n
            ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def _ret(b: Bars | None, sessions: int) -> float | None:
    if b is None or len(b) <= sessions:
        return None
    return float(b.close[-1] / b.close[-1 - sessions] - 1) * 100


def _rs(b: Bars, bench: Bars | None, sessions: int) -> float | None:
    a, m = _ret(b, sessions), _ret(bench, sessions)
    return None if a is None or m is None else round(a - m, 2)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _rsi_now(b: Bars, n: int) -> tuple[float | None, bool | None]:
    r = rsi(b.close, n)
    if len(r) < 4 or np.isnan(r[-1]) or np.isnan(r[-4]):
        return None, None
    return round(float(r[-1]), 1), bool(r[-1] > r[-4])


@dataclass
class Sector:
    name: str
    kind: str
    close: float
    position: str
    support_atr: float | None
    resistance_atr: float | None
    trend_up: bool
    rsi: float | None
    rsi_rising: bool | None
    rs_4w: float | None
    rs_13w: float | None
    state: str
    passed: bool
    week_pct: float | None
    month_pct: float | None


def sector_stage(u: SwingUniverse, bars: dict[str, Bars], reps: dict[str, ZoneReport], bench: Bars | None,
                 cfg: SwingConfig) -> list[Sector]:
    f = cfg.funnel
    out = []
    for ix in u.indices:
        b, rep = bars.get(ix.name), reps.get(ix.name)
        if b is None or rep is None or len(b) < 30:
            continue
        up = trend_ok(b)
        r, rising = _rsi_now(b, f.rsi_days)
        if rep.note or not rep.zones or not rep.atr:
            state, pos, s_atr, r_atr = NO_ZONES, NO_ZONES, None, None
        else:
            p = place(rep.close, [(z.lo, z.hi) for z in rep.zones], rep.atr, f.sector_near_atr)
            pos, s_atr, r_atr = p.position, p.support_atr, p.resistance_atr
            at_support = p.position == IN_ZONE or (s_atr is not None and s_atr <= f.sector_near_atr
                                                   and (r_atr is None or s_atr <= r_atr))
            if at_support:
                state = LOOK_FOR_LONGS if up else SUPPORT_DOWNTREND
            elif r_atr is not None and r_atr <= f.sector_near_atr:
                state = NEAR_RES
            else:
                state = MID_RANGE
        passed = state == LOOK_FOR_LONGS or (state == SUPPORT_DOWNTREND and not f.sector_need_trend)
        out.append(Sector(ix.name, ix.kind, float(b.close[-1]), pos, s_atr, r_atr, up, r, rising,
                          _rs(b, bench, 20), _rs(b, bench, 65), state, passed, _ret(b, 5), _ret(b, 21)))
    return out


def news_scores(db, symbols: list[str], day: date, cfg: SwingConfig) -> tuple[dict[str, tuple[float, list[str]]], list[str], dict]:
    """(per stock: news score 0-100 and its flags; flags for every pick; which sources were fresh)."""
    from trading_core.market import events

    f = cfg.funnel
    start = datetime.combine(day + timedelta(days=1), time(), tzinfo=IST)
    macro = [e for e in events.between(db, start, start + timedelta(days=f.macro_days), category="economic",
                                       countries=["IN"], min_impact="High")]
    common = [f"{e.title} on {e.ts:%d %b}" for e in macro]
    results = events.between(db, start, start + timedelta(days=f.news_days), category="results", symbols=symbols)
    by_sym: dict[str, list[str]] = {}
    for e in results:
        by_sym.setdefault(e.symbol, []).append(f"Results on {e.ts:%d %b}")
    base = 100.0 - (f.macro_penalty if macro else 0.0)
    out = {}
    for s in symbols:
        flags = by_sym.get(s, [])
        out[s] = (max(0.0, base - (f.results_penalty if flags else 0.0)), flags)
    try:
        fresh = events.covered(db, start)
    except Exception:  # noqa: BLE001 - only a hint on the page
        fresh = {}
    return out, common, fresh


def build(u: SwingUniverse, index_bars: dict[str, Bars], index_reps: dict[str, ZoneReport],
          stock_bars: dict[str, Bars], stock_reps: dict[str, ZoneReport], traded: dict[str, float],
          bench: Bars | None, cfg: SwingConfig, db=None, now: datetime | None = None) -> dict[str, Any]:
    """picks.json: the three stages, every candidate ranked, the top ones marked."""
    f, sc = cfg.funnel, cfg.setups
    plan_cfg = sc.model_copy(update={"stop_atr": f.stop_atr})
    sectors = sector_stage(u, index_bars, index_reps, bench, cfg)
    by_name = {s.name: s for s in sectors}
    passing = {s.name for s in sectors if s.passed}
    day = max((b.dates[-1] for b in list(index_bars.values()) + list(stock_bars.values())), default=None)

    considered, near, plans = 0, 0, []
    for sym, b in stock_bars.items():
        rep = stock_reps.get(sym)
        mine = [n for n in u.indices_of(sym) if n in by_name]
        ok = [n for n in mine if n in passing]
        if f.require_sector and not ok:
            continue
        considered += 1
        if rep is None or rep.note or not rep.zones:
            continue
        if any(z.lo < rep.close and rep.close - z.hi <= sc.near_atr * rep.atr for z in rep.zones):
            near += 1
        s = find_setup(b, rep, plan_cfg, u.industry.get(sym, ""), bool(ok))
        if s is None:
            continue
        pool = ok or mine
        best = max(pool, key=lambda n: by_name[n].rs_13w if by_name[n].rs_13w is not None else -1e9) if pool else None
        sec = by_name.get(best) if best else None
        r, rising = _rsi_now(b, f.rsi_days)
        parts = {
            "entry": round(1 - _clamp(s.dist_atr / sc.near_atr), 3) if sc.near_atr else 1.0,
            "room": round(_clamp((s.rr - 1) / 4), 3),
            "trend": 1.0 if s.trend_ok else 0.0,
            "sector": round(0.5 * (1.0 if ok else 0.0)
                            + 0.5 * (_clamp(0.5 + sec.rs_13w / 20) if sec and sec.rs_13w is not None else 0.5), 3),
            "rsi": round((0.5 if r is not None and 30 <= r <= 60 else 0.2) + (0.5 if rising else 0.0), 3),
        }
        tech = round(100 * sum(parts.values()) / len(parts), 1)
        qty = int(s.amount // s.entry) if s.entry > 0 else 0
        plans.append({"symbol": sym, "industry": s.industry, "sector": best, "sector_state": sec.state if sec else None,
                      "sector_ok": bool(ok), "close": round(s.close, 2), "atr": round(s.atr, 2),
                      "entry": s.entry, "stop": s.stop, "target": s.target, "rr": s.rr, "dist_atr": s.dist_atr,
                      "zone": [round(s.zone_lo, 2), round(s.zone_hi, 2)], "trend_ok": s.trend_ok,
                      "rsi": r, "rsi_rising": rising, "amount": s.amount, "qty": qty, "mode": f.default_mode,
                      "risk_pct": round((s.entry - s.stop) / s.entry * 100, 2),
                      "reward_pct": round((s.target - s.entry) / s.entry * 100, 2),
                      "traded_cr": round(traded.get(sym, 0.0), 1), "tech": tech, "tech_parts": parts,
                      "news": None, "fund": None, "fund_checks": [], "flags": []})

    syms = [p["symbol"] for p in plans]
    common, fresh = [], {}
    if db is not None and syms and day is not None:
        news, common, fresh = news_scores(db, syms, day, cfg)
        funds = fundamentals.of(db, syms)
        for p in plans:
            p["news"], p["flags"] = news[p["symbol"]][0], list(news[p["symbol"]][1])
            p["fund"], p["fund_checks"] = fundamentals.score(funds.get(p["symbol"]), f.fund_rules)
    for p in plans:
        weighted = [(f.w_tech, p["tech"]), (f.w_news, p["news"]), (f.w_fund, p["fund"])]
        weighted = [(w, v) for w, v in weighted if v is not None and w > 0]
        total = sum(w for w, _ in weighted)
        p["score"] = round(sum(w * v for w, v in weighted) / total, 1) if total else p["tech"]
        if not p["trend_ok"]:
            p["flags"].append("Downtrend")
        if p["qty"] == 0:
            p["flags"].append(f"1 share is over ₹{p['amount']:,.0f}")
    plans.sort(key=lambda p: (-p["score"], p["dist_atr"]))

    per_ind: dict[str, int] = {}
    rank = 0
    for p in plans:
        p["rank"] = None
        if rank >= f.top_n or per_ind.get(p["industry"], 0) >= sc.max_per_sector:
            continue
        rank += 1
        p["rank"] = rank
        per_ind[p["industry"]] = per_ind.get(p["industry"], 0) + 1

    return {"available": True, "candles_to": day.isoformat() if day else None,
            "generated_at": (now or datetime.now(IST)).isoformat(timespec="seconds"),
            "note": NOTE, "ranking_note": RANKING_NOTE, "benchmark": cfg.benchmark if bench is not None else None,
            "macro_flags": common, "calendar_fresh": fresh,
            "settings": {"sector_near_atr": f.sector_near_atr, "sector_need_trend": f.sector_need_trend,
                         "require_sector": f.require_sector, "near_atr": sc.near_atr, "min_rr": sc.min_rr,
                         "stop_atr": f.stop_atr, "top_n": f.top_n, "max_per_industry": sc.max_per_sector,
                         "weights": {"tech": f.w_tech, "news": f.w_news, "fund": f.w_fund}},
            "funnel": {"sectors": len(sectors), "sectors_passed": len(passing), "stocks": len(stock_bars),
                       "considered": considered, "near_support": near, "plans": len(plans), "top": rank},
            "sectors": [vars(s) for s in sorted(sectors, key=lambda s: (not s.passed, s.state, s.name))],
            "picks": plans}


def top(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted((p for p in payload.get("picks", []) if p.get("rank")), key=lambda p: p["rank"])
