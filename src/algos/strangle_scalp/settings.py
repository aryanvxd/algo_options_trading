"""Config model for the long strangle scalp (values live in config.yaml next to this file)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from trading_core.strategy.base import BaseAlgoConfig


class PairConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Expiry day: each leg's mid premium must be in this band (rupees per unit).
    premium_min_0dte: float = 15.0
    premium_max_0dte: float = 30.0
    # Other days: pick each leg by delta (Black-76, IV from its own mid).
    target_delta: float = 0.30
    delta_min: float = 0.25
    delta_max: float = 0.35
    max_premium_ratio: float = 1.3          # dearer leg ÷ cheaper leg
    max_spread_pts_0dte: float = 0.30
    max_spread_pts: float = 0.50
    max_quote_age_s: float = 2.0            # both legs must have ticked this recently
    reselect_every_s: float = 30.0
    subscribe_range_pts: int = 600          # strikes subscribed around spot
    resubscribe_drift_pts: int = 200        # re-centre the subscription when spot moves this far


class SignalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_s: float = 5.0                   # S1: pair value V must rise in each window ...
    windows: int = 3                        # S2: ... this many windows in a row
    min_rise_pct: float = 2.0               # S3: V rose at least this % over the windows
    min_fut_move_pts_0dte: float = 12.0     # S4: NIFTY futures moved this much over the same span
    min_fut_move_pts: float = 18.0


class DayLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_consecutive_losses: int = 2
    max_day_loss: float = 800.0             # net rupees; also exits an open trade at this level
    day_profit_lock: float | None = None    # stop for the day once net reaches this (None = off)


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pnl_basis: Literal["net", "gross"] = "gross"
    # Points on the pair; rupees = points × lot_size × lots.
    stop_loss_points: float = 4.6
    target_points: float = 7.7
    trail_activate_points: float = 4.6
    trail_keep_fraction: float = 0.5
    stall_seconds: float = 90.0
    max_hold_seconds: float = 480.0
    stale_feed_seconds: float = 5.0


class StrangleScalpConfig(BaseAlgoConfig):
    underlying: str = "NIFTY"
    index_symbol: str = "NIFTY 50"
    vix_symbol: str = "INDIA VIX"
    strike_step: int = 50
    pair: PairConfig = PairConfig()
    signal: SignalConfig = SignalConfig()
    limits: DayLimits = DayLimits()
    exits: ExitConfig = ExitConfig()
