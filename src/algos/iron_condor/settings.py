"""Config model for the iron condor (values live in config.yaml next to this file)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_core.strategy.base import BaseAlgoConfig


class FilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vix_max: float = 20.0
    # Skip (keep waiting) while the dearer of the two short-distance options costs more than this
    # multiple of the cheaper one: the market is leaning one way.
    max_premium_ratio: float = 1.5
    # PCR (put OI / call OI) over strikes within this distance of ATM. Logged only, never blocks.
    pcr_range_pts: int = 1000


class EntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_spread_pts: float = 2.0
    min_credit_pts: float = 10.0


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pnl_basis: Literal["net", "gross"] = "gross"
    # Points of premium; rupees = points × lot_size × lots.
    stop_loss_points: float = 5.0
    target_points: float = 8.0
    premium_blowout_mult: float = 1.35
    short_leg_mult: float = 2.0
    stale_feed_seconds: float = 10.0


class IronCondorConfig(BaseAlgoConfig):
    underlying: str = "NIFTY"
    index_symbol: str = "NIFTY 50"
    vix_symbol: str = "INDIA VIX"
    strike_step: int = 50
    trade_window_days: int = 3
    width_by_dte: dict[int, int] = Field(default_factory=lambda: {0: 500, 1: 550, 2: 600})
    hedge_by_dte: dict[int, int] = Field(default_factory=lambda: {0: 200, 1: 250, 2: 300})
    subscribe_buffer_pts: int = 200
    filters: FilterConfig = Field(default_factory=FilterConfig)
    entry: EntryConfig = Field(default_factory=EntryConfig)
    exits: ExitConfig = Field(default_factory=ExitConfig)

    @staticmethod
    def _for_dte(table: dict[int, int], dte: int) -> int:
        keys = sorted(table)
        return table[max(k for k in keys if k <= dte)] if dte >= keys[0] else table[keys[0]]

    def width_for(self, dte: int) -> int:
        return self._for_dte(self.width_by_dte, dte)

    def hedge_for(self, dte: int) -> int:
        return self._for_dte(self.hedge_by_dte, dte)
