"""The picks journal (dashboard Swing › Journal): every Algo pick followed forward, with a random twin.

Each scan records its top picks with their plans. Each pick gets a **random twin**: the same stock
and week, an entry at a random level 0 to ``setups.near_atr`` ATR below the close, and the same stop
and target distances (the baseline of the setup backtest, spec 9b). Both are then walked forward
on the stored daily candles with the backtest's own rules (``setup_backtest.simulate``):

- **pending**: waiting for the low to reach the entry; not reached in ``setups.fill_weeks``: **expired**.
- **open**: filled; valued at the last close.
- **target** / **stop**: closed there (both on one day: the stop; target-only plans have no stop).
- **time**: sold at the close after ``setups.max_hold_days`` sessions.

Returns are net of ``setups.cost_pct``. If the picks don't beat their twins over the weeks, the
ranking isn't choosing better entries than chance. A stock gets no new pick while its last one is
pending or open.
"""

from __future__ import annotations

import random
from datetime import date, datetime
from typing import Any, Callable, Iterable

from sqlalchemy import select

from swing.bars import Bars, daily_bars
from swing.settings import SwingConfig
from swing.setup_backtest import simulate
from trading_core.db import models as m
from trading_core.db.engine import Database

OPEN_STATES = ("pending", "open")
CLOSED_STATES = ("target", "stop", "time")


def record(db: Database, payload: dict[str, Any], picks: Iterable[dict[str, Any]], cfg: SwingConfig,
           now: datetime) -> int:
    """Journal the top picks of a scan (and a twin for each). Returns how many picks were added."""
    if not payload.get("candles_to"):
        return 0
    day = date.fromisoformat(payload["candles_to"])
    added = 0
    with db.session() as s:
        busy = set(s.scalars(select(m.SwingPick.symbol).where(m.SwingPick.is_twin.is_(False),
                                                              m.SwingPick.status.in_(OPEN_STATES))))
        for p in picks:
            if p["symbol"] in busy:
                continue
            rng = random.Random(f"{day}:{p['symbol']}")
            te = p["close"] - rng.uniform(0, cfg.setups.near_atr) * p["atr"]
            common = dict(scan_date=day, symbol=p["symbol"], industry=p.get("industry"), sector=p.get("sector"),
                          mode=p.get("mode", cfg.funnel.default_mode), close=p["close"], atr=p["atr"],
                          status="pending", updated_at=now)
            pick = m.SwingPick(**common, rank=p.get("rank"), is_twin=False, entry=p["entry"], stop=p["stop"],
                               target=p["target"], rr=p.get("rr"), score=p.get("score"),
                               parts={"tech": p.get("tech"), "news": p.get("news"), "fund": p.get("fund"),
                                      "flags": p.get("flags", []), "tech_parts": p.get("tech_parts", {})})
            s.add(pick)
            s.flush()
            s.add(m.SwingPick(**common, rank=None, is_twin=True, twin_of=pick.id, entry=round(te, 2),
                              stop=round(te - (p["entry"] - p["stop"]), 2),
                              target=round(te + (p["target"] - p["entry"]), 2), rr=p.get("rr"), score=None, parts=None))
            busy.add(p["symbol"])
            added += 1
    return added


def load_bars(store, cfg: SwingConfig, symbols: Iterable[str]) -> dict[str, Bars]:
    symbols = list(symbols)
    out = daily_bars(store, cfg.universe, symbols) if symbols else {}
    missing = [s for s in symbols if s not in out]
    if missing:
        out.update(daily_bars(store, cfg.holdings_universe, missing))
    return out


def _apply(row: m.SwingPick, b: Bars, cfg: SwingConfig, now: datetime) -> bool:
    a = max((i for i, d in enumerate(b.dates) if d <= row.scan_date), default=None)
    if a is None:
        return False
    sc = cfg.setups
    row.last_close = round(float(b.close[-1]), 2)
    row.updated_at = now
    if a >= len(b) - 1:                                   # no candle after the scan yet
        return True
    filled, k, fill, j, ex, reason = simulate(b, a, row.entry, row.stop, row.target, row.mode, sc)
    if not filled:
        window_done = a + sc.fill_weeks * 5 <= len(b) - 1
        row.status = "expired" if window_done else "pending"
        row.exit_date = b.dates[j] if window_done else None
        return True
    row.fill_date, row.fill_price = b.dates[k], round(fill, 2)
    if reason == "end":
        row.status, row.exit_date, row.exit_price = "open", None, None
        row.return_pct = round((float(b.close[-1]) / fill - 1) * 100 - sc.cost_pct, 3)
    else:
        row.status, row.exit_date, row.exit_price = reason, b.dates[j], round(ex, 2)
        row.return_pct = round((ex / fill - 1) * 100 - sc.cost_pct, 3)
    return True


def update(db: Database, bars_for: Callable[[list[str]], dict[str, Bars]], cfg: SwingConfig, now: datetime) -> int:
    """Walk every pending or open pick (and twin) forward on the stored candles."""
    with db.session() as s:
        rows = list(s.scalars(select(m.SwingPick).where(m.SwingPick.status.in_(OPEN_STATES))))
        if not rows:
            return 0
        bars = bars_for(sorted({r.symbol for r in rows}))
        return sum(_apply(r, bars[r.symbol], cfg, now) for r in rows if r.symbol in bars)


def _stats(rows: list[m.SwingPick]) -> dict[str, Any]:
    done = [r for r in rows if r.status in CLOSED_STATES]
    filled = [r for r in rows if r.fill_date is not None]
    decided = [r for r in rows if r.status != "pending"]
    opened = [r for r in rows if r.status == "open"]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    return {"picks": len(rows), "pending": sum(r.status == "pending" for r in rows), "open": len(opened),
            "closed": len(done), "expired": sum(r.status == "expired" for r in rows),
            "fill_rate": round(100 * len(filled) / len(decided), 1) if decided else None,
            "win_rate": round(100 * sum((r.return_pct or 0) > 0 for r in done) / len(done), 1) if done else None,
            "targets": sum(r.status == "target" for r in rows), "stops": sum(r.status == "stop" for r in rows),
            "avg_return": avg([r.return_pct for r in done if r.return_pct is not None]),
            "avg_open_return": avg([r.return_pct for r in opened if r.return_pct is not None])}


def _row(r: m.SwingPick) -> dict[str, Any]:
    return {"id": r.id, "scan_date": r.scan_date.isoformat(), "symbol": r.symbol, "industry": r.industry,
            "sector": r.sector, "rank": r.rank, "mode": r.mode, "close": r.close, "entry": r.entry, "stop": r.stop,
            "target": r.target, "rr": r.rr, "score": r.score, "status": r.status,
            "fill_date": r.fill_date.isoformat() if r.fill_date else None, "fill_price": r.fill_price,
            "exit_date": r.exit_date.isoformat() if r.exit_date else None, "exit_price": r.exit_price,
            "last_close": r.last_close, "return_pct": r.return_pct,
            "flags": (r.parts or {}).get("flags", []),
            "updated_at": r.updated_at.isoformat(timespec="seconds") if r.updated_at else None}


def journal(db: Database) -> dict[str, Any]:
    with db.session() as s:
        rows = list(s.scalars(select(m.SwingPick).order_by(m.SwingPick.scan_date.desc(), m.SwingPick.rank)))
    picks = [r for r in rows if not r.is_twin]
    twins = {r.twin_of: r for r in rows if r.is_twin}
    out = []
    for p in picks:
        d = _row(p)
        t = twins.get(p.id)
        d["twin"] = {k: v for k, v in _row(t).items() if k in ("entry", "stop", "target", "status", "fill_date",
                                                               "return_pct")} if t else None
        out.append(d)
    return {"available": True, "stats": {"picks": _stats(picks), "twins": _stats(list(twins.values()))},
            "weeks": sorted({r.scan_date.isoformat() for r in picks}, reverse=True), "rows": out}
