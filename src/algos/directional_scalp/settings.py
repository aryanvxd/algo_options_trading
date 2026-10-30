"""Config model for the directional buying scalp (values live in config.yaml next to this file)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_core.strategy.base import BaseAlgoConfig


class BiasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every_s: float = 5.0                    # recompute the score this often
    log_every_s: float = 60.0               # journal it this often (and at every trigger)
    # B1 breadth: NIFTY 50 weighted A/D (−100..+100) -> ±1 at ±breadth_full
    breadth_universe: str = "nifty50"
    breadth_full: float = 40.0
    breadth_min_coverage: float = 60.0      # % of index weight that must have a price
    # B2 premium skew: PE ÷ CE at spot ± distance; +1 at/below bull_ratio, −1 at/above bear_ratio
    skew_distance_pts: float = 300.0
    skew_bull_ratio: float = 0.95
    skew_bear_ratio: float = 1.45
    # B3 NIFTY futures minus its VWAP since 09:15 -> ±1 at ±vwap_full_pts
    vwap_full_pts: float = 15.0
    w_breadth: float = 0.4
    w_skew: float = 0.3
    w_vwap: float = 0.3
    threshold: float = 0.5                  # |score| needed for a side ...
    hold_s: float = 120.0                   # ... held this long
    flip_exit: float = 0.3                  # exit when the score goes this far the other way


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brick_pts: float = 10.0                 # Renko brick on NIFTY spot, from 1-minute closes
    bricks: int = 2                         # T1: this many bricks in a row in the bias direction
    max_vwap_extension_bricks: float = 2.0  # T2: spot within this many bricks of the futures VWAP ...
    enforce_extension: bool = False         # ... logged only until the data says it helps


class OptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strike_step: int = 50
    itm_fallback: bool = True               # O1: one strike nearer the money if ATM fails a check
    max_spread_pts: float = 0.50            # O2
    max_quote_age_s: float = 3.0            # O3 (recording is every 2 s, so replays see ≤ 2 s gaps)
    premium_min: float = 40.0               # O4
    premium_max: float = 250.0
    subscribe_range_pts: int = 600
    resubscribe_drift_pts: int = 200


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hard_stop_pts: float = 8.0              # option below entry by this many points (every tick)
    lot_a_target_pts: float = 12.0          # book lot A here ...
    breakeven_buffer_pts: float = 0.5       # ... then lot B's stop moves to entry + this
    lot_b_target_pts: float = 30.0          # lot B's cap
    time_stop_s: float = 300.0              # no new brick in the trade's favour for this long
    stale_feed_seconds: float = 5.0


class DayLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_consecutive_losses: int = 3
    max_day_loss: float = 3000.0            # net rupees; also exits an open trade at this level
    profit_lock_trigger: float | None = 2500.0   # once the day's net has reached this ...
    profit_lock_floor: float = 1500.0            # ... stop if it falls back to this
    max_direction_changes: int = 2          # CE -> PE -> CE = 2 changes


Weekday = Literal["Mon", "Tue", "Wed", "Thu", "Fri"]
WEEKDAYS: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class DirectionalScalpConfig(BaseAlgoConfig):
    trade_weekdays: list[Weekday] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    underlying: str = "NIFTY"
    index_symbol: str = "NIFTY 50"
    vix_symbol: str = "INDIA VIX"
    split_lots: bool = True                 # lot A books the first target, lot B trails
    bias: BiasConfig = BiasConfig()
    trigger: TriggerConfig = TriggerConfig()
    option: OptionConfig = OptionConfig()
    limits: DayLimits = DayLimits()
    exits: ExitConfig = ExitConfig()
