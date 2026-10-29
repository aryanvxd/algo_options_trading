"""Config model for the weekend iron condor (values live in config.yaml next to this file)."""

from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_core.strategy.base import BaseAlgoConfig


class FilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vix_max: float = 18.0                 # W2: a weekend gap scales with VIX
    max_premium_ratio: float = 1.5        # W4: dearer ÷ cheaper option at the short distance
    min_gap_days: int = 3                 # W1: calendar days to the next trading day (Fri -> Mon = 3)
    event_min_impact: Literal["High", "Medium"] = "High"   # W3: skip if such an event falls in the weekend gap
    # W3 also: results of these heavyweights dated on the entry day or over the weekend (they report
    # after the close or on Saturdays and move Monday's open).
    results_symbols: list[str] = Field(default_factory=lambda: [
        "HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "BHARTIARTL", "TCS", "LT", "ITC", "SBIN", "AXISBANK"])
    require_calendar: bool = False        # True = skip the weekend when the calendar couldn't be fetched


class EntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_move_mult: float = 1.2       # shorts at this many expected weekend moves from spot
    hedge_pts: int = 200                  # short -> long distance
    max_spread_pts: float = 2.0           # W5
    min_credit_pts: float = 12.0          # W6
    subscribe_buffer_pts: int = 300


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pnl_basis: Literal["net", "gross"] = "gross"
    stop_loss_points: float = 10.0        # points × lot size × lots
    target_credit_frac: float = 0.6       # take profit at this share of the entry credit
    premium_blowout_mult: float = 1.5
    short_leg_mult: float = 2.0
    stale_feed_seconds: float = 10.0      # only judged while the market is open
    exit_from: time = time(9, 35)         # next trading day: buy back from here ...
    exit_max_spread_pts: float = 2.0      # ... at the first moment every leg's spread is this tight
    hard_exit: time = time(9, 50)         # ... and at any price from here


class WeekendCondorConfig(BaseAlgoConfig):
    underlying: str = "NIFTY"
    index_symbol: str = "NIFTY 50"
    vix_symbol: str = "INDIA VIX"
    strike_step: int = 50
    filters: FilterConfig = Field(default_factory=FilterConfig)
    entry: EntryConfig = Field(default_factory=EntryConfig)
    exits: ExitConfig = Field(default_factory=ExitConfig)
