"""Buy setups from a weekend's zones (spec section 5).

A setup is a plan for one stock: a limit buy at the top of a support zone, a stop under it and a
target at the next resistance. The rules that decide whether a stock makes the list:

S1 near support: the close is within ``near_atr`` ATR above a support zone's top (or inside it).
S2 zone strength: score >= ``min_score``. Recorded on every setup; the backtest reports both sides.
S3 room: (next resistance's bottom - entry) / (entry - stop) >= ``min_rr``.
S4 not in free fall: the close hasn't dropped more than ``knife_atr`` ATR in ``knife_days`` days.
S5 liquidity: checked by the caller (20-day traded value).
S6 results dates: not applied in the backtest (no history of results dates).
S7 trend: close above its 200-day average, or the weekly 50-EMA rising. Recorded, not required.
Z10 sector: one of the stock's indices is itself near support. Recorded, not required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from pydantic import BaseModel, ConfigDict

from swing.bars import Bars, weekly
from swing.zones import ZoneReport


class SetupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    near_atr: float = 2.0               # S1
    min_score: float = 60.0             # S2
    min_rr: float = 2.0                 # S3
    stop_atr: float = 0.5               # stop = zone bottom - 0.5 ATR
    knife_atr: float = 2.0              # S4
    knife_days: int = 3
    fill_weeks: int = 4                 # drop the setup if not filled in 4 weeks
    max_hold_days: int = 120            # backtest only: sell at the close after ~6 months
    amount_high: float = 15000.0        # ₹ per entry, score >= high_score
    amount_low: float = 7500.0          # ₹ per entry, below it
    high_score: float = 75.0
    cost_pct: float = 0.25              # round trip, delivery: STT, stamp, exchange, DP, GST
    max_open: int = 8                   # book limits (portfolio pass)
    max_per_sector: int = 3


@dataclass
class Setup:
    symbol: str
    asof: date
    entry: float             # zone top
    stop: float
    target: float            # next resistance's bottom
    zone_lo: float
    zone_hi: float
    score: float
    atr: float
    close: float
    rr: float
    dist_atr: float          # close above the entry, in ATRs (0 = inside the zone)
    trend_ok: bool           # S7
    sector_ok: bool | None   # Z10 (None: no index data)
    industry: str
    amount: float


def trend_ok(b: Bars) -> bool:
    """S7: close above its 200-day average, or the weekly 50-week EMA higher than 4 weeks ago."""
    if len(b) >= 200 and b.close[-1] > b.close[-200:].mean():
        return True
    w = weekly(b).close
    if len(w) < 54:
        return False
    k = 2 / 51
    ema = np.empty(len(w))
    ema[0] = w[0]
    for i in range(1, len(w)):
        ema[i] = ema[i - 1] + k * (w[i] - ema[i - 1])
    return bool(ema[-1] > ema[-5])


def knife(b: Bars, atr: float, cfg: SetupConfig) -> bool:
    """S4: fell more than knife_atr ATR over the last knife_days sessions."""
    if len(b) <= cfg.knife_days:
        return False
    return bool(b.close[-1 - cfg.knife_days] - b.close[-1] > cfg.knife_atr * atr)


def find_setup(b: Bars, rep: ZoneReport, cfg: SetupConfig, industry: str = "",
               sector_ok: bool | None = None) -> Setup | None:
    """The setup at the nearest support that passes S1, S3 and S4 (``b`` = candles up to the
    weekend). S2, S7 and Z10 are recorded on it."""
    if not rep.zones or rep.atr <= 0 or knife(b, rep.atr, cfg):
        return None
    atr, close = rep.atr, rep.close
    supports = [z for z in rep.zones if z.lo < close and close - z.hi <= cfg.near_atr * atr]
    for z in sorted(supports, key=lambda z: -z.hi):            # nearest first
        entry = min(z.hi, close)
        stop = z.lo - cfg.stop_atr * atr
        above = [r for r in rep.zones if r.lo > entry and r is not z]
        if not above:
            continue
        target = min(r.lo for r in above)
        rr = (target - entry) / (entry - stop)
        if rr < cfg.min_rr:
            continue
        amount = cfg.amount_high if z.score >= cfg.high_score else cfg.amount_low
        return Setup(rep.symbol, rep.asof, round(entry, 2), round(stop, 2), round(target, 2), z.lo, z.hi,
                     z.score, atr, close, round(rr, 2), round(max(0.0, close - z.hi) / atr, 2),
                     trend_ok(b), sector_ok, industry, amount)
    return None
