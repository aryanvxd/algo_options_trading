"""Config model for the condor move hedge (values live in config.yaml next to this file)."""

from __future__ import annotations

from datetime import time as dtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_core.strategy.base import BaseAlgoConfig


class AnchorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ``condor``: NIFTY at the linked condor's entry (read from the journal); no condor entry by
    # ``wait_until`` means nothing to hedge, so the day is skipped. ``time``: NIFTY's 1-minute close
    # at ``time`` (backtests: each backtest has its own database, so the condor is not in it).
    source: Literal["condor", "time"] = "condor"
    algo: str = "iron_condor_balanced"
    time: dtime = dtime(9, 49)              # the candle whose close is the anchor (09:49 closes at 09:50)
    wait_until: dtime = dtime(10, 30)


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    move_pts: float = Field(80.0, gt=0)     # NIFTY this far from the anchor ...
    confirm_bars: int = Field(1, ge=1)      # ... on this many 1-minute closes in a row


class OptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strike_step: int = 50
    otm_strikes: int = Field(2, ge=0)       # 0 = ATM; 2 = two strikes out of the money in the move's direction
    premium_min: float = 5.0
    max_spread_pts: float = 1.0
    max_quote_age_s: float = 5.0
    subscribe_range_pts: int = 600
    resubscribe_drift_pts: int = 200


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trail_pts: float = Field(80.0, gt=0)    # NIFTY gave back this much from its best 1-minute close since entry
    premium_stop_pct: float = Field(30.0, ge=0, lt=100)   # option bid this % below its entry (0 = off)
    stale_feed_seconds: float = 10.0


class DayLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_day_loss: float = 3000.0            # net rupees; also exits an open trade at this level


Weekday = Literal["Mon", "Tue", "Wed", "Thu", "Fri"]
WEEKDAYS: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class CondorHedgeConfig(BaseAlgoConfig):
    trade_weekdays: list[Weekday] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    underlying: str = "NIFTY"
    index_symbol: str = "NIFTY 50"
    vix_symbol: str = "INDIA VIX"
    anchor: AnchorConfig = AnchorConfig()
    trigger: TriggerConfig = TriggerConfig()
    option: OptionConfig = OptionConfig()
    exits: ExitConfig = ExitConfig()
    limits: DayLimits = DayLimits()
