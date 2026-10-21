"""Test doubles and a synthetic market day for offline replays."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable

from trading_core.clock import IST, ist
from trading_core.db.repo import Journal
from trading_core.feed.base import MarketFeed, Tick
from trading_core.kite.instruments import InstrumentStore
from trading_core.market.premarket import PremarketSnapshot

NIFTY_TOKEN = 256265
VIX_TOKEN = 264969
LOT_SIZE = 65


def tick(token: int, ltp: float | None = None, bid: float | None = None, ask: float | None = None,
         when: datetime | None = None) -> Tick:
    when = when or datetime(2026, 10, 23, 9, 20, tzinfo=IST)
    return Tick(instrument_token=token, ltp=ltp, bid=bid, ask=ask, ts=when, received_at=when)


class FakeFeed(MarketFeed):
    def __init__(self, ticks: Iterable[Tick] = (), age: float | None = 0.5):
        self.ticks = {t.instrument_token: t for t in ticks}
        self.age = age
        self._sub: set[int] = set()

    def set(self, *ticks: Tick) -> None:
        for t in ticks:
            self.ticks[t.instrument_token] = t

    def subscribe(self, tokens):
        self._sub |= set(tokens)

    @property
    def subscribed(self):
        return set(self._sub)

    def latest(self, token):
        return self.ticks.get(token)

    def last_tick_age(self, now):
        return self.age


# ---------------------------------------------------------------- synthetic market day

def option_token(strike: float, opt: str) -> int:
    return 10_000_000 + int(strike) * 10 + (1 if opt == "CE" else 2)


def symbol(expiry: date, strike: float, opt: str) -> str:
    return f"NIFTY{expiry:%y%m%d}{int(strike)}{opt}"


def instrument_rows(expiry: date, lo: int = 23500, hi: int = 26500, step: int = 50) -> list[dict]:
    rows = [
        dict(instrument_token=NIFTY_TOKEN, exchange_token=1001, tradingsymbol="NIFTY 50", name="NIFTY 50",
             exchange="NSE", segment="INDICES", instrument_type="EQ", strike=0, expiry="", lot_size=0,
             tick_size=0.0),
        dict(instrument_token=VIX_TOKEN, exchange_token=1002, tradingsymbol="INDIA VIX", name="INDIA VIX",
             exchange="NSE", segment="INDICES", instrument_type="EQ", strike=0, expiry="", lot_size=0,
             tick_size=0.0),
    ]
    for k in range(lo, hi + step, step):
        for opt in ("CE", "PE"):
            rows.append(dict(instrument_token=option_token(k, opt), exchange_token=None,
                             tradingsymbol=symbol(expiry, k, opt), name="NIFTY", exchange="NFO",
                             segment="NFO-OPT", instrument_type=opt, strike=float(k), expiry=expiry,
                             lot_size=LOT_SIZE, tick_size=0.05))
    return rows


def option_price(opt: str, strike: float, spot: float, decay: float) -> float:
    intrinsic = max(0.0, spot - strike) if opt == "CE" else max(0.0, strike - spot)
    extrinsic = 60.0 * math.exp(-abs(strike - spot) / 150.0) * decay
    return round(max(0.05, intrinsic + extrinsic) * 20) / 20


def seed_day(db, platform, trade_date: date, expiry: date, *, spot: Callable[[float], float],
             decay: Callable[[float], float], vix: float = 14.0, gap_pct: float = 0.1,
             interval_s: int = 15, strikes: tuple[int, int] = (24300, 25700)) -> None:
    """Store what a live recording run would have stored: instruments, pre-open breadth, ticks.

    ``spot`` and ``decay`` take the fraction of the session elapsed (0 at 09:15, 1 at 15:30)."""
    journal = Journal(db)
    InstrumentStore(db, platform.instruments).save_snapshot(trade_date, instrument_rows(expiry))
    journal.register_algo("recorder", "recorder", "0", "", ist(trade_date, time(8, 0)))
    run_id = journal.create_run("recorder", trade_date, "paper", "live", {}, ist(trade_date, time(8, 0)))
    journal.add_breadth(run_id, PremarketSnapshot(
        captured_at=ist(trade_date, time(9, 8)), index_symbol="NIFTY 50", index_spot=spot(0.0),
        prev_close=spot(0.0) / (1 + gap_pct / 100), gap_pct=gap_pct, vix=vix, advances=30, declines=20))

    start, end = ist(trade_date, time(9, 15)), ist(trade_date, time(15, 30))
    total = (end - start).total_seconds()
    t = start
    batch: list[Tick] = []
    while t <= end:
        frac = (t - start).total_seconds() / total
        s, dcy = spot(frac), decay(frac)
        batch.append(tick(NIFTY_TOKEN, ltp=round(s, 2), when=t))
        batch.append(tick(VIX_TOKEN, ltp=vix, when=t))
        for k in range(strikes[0], strikes[1] + 50, 50):
            for opt in ("CE", "PE"):
                p = option_price(opt, k, s, dcy)
                batch.append(tick(option_token(k, opt), ltp=p, bid=max(0.05, round(p - 0.1, 2)),
                                  ask=round(p + 0.1, 2), when=t))
        if len(batch) > 20000:
            journal.add_ticks(run_id, batch)
            batch = []
        t += timedelta(seconds=interval_s)
    journal.add_ticks(run_id, batch)
