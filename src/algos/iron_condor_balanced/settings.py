"""Config model for the balanced-premium iron condor (values live in config.yaml next to this file).

Extends the original condor's config: the CE/PE balance is logged only, the shorts are picked by
premium, and the minimum credit, stop and target are set per DTE.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from algos.iron_condor.settings import ExitConfig, IronCondorConfig


class BalancedFilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vix_max: float = 20.0
    # PCR (put OI / call OI) over strikes within this distance of ATM. Logged only, never blocks.
    pcr_range_pts: int = 1000


class StrikeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # balanced: each short at the premium closest to the average of the same-width pair.
    # width: the original condor's shorts (ATM ± width/2).
    mode: Literal["balanced", "width"] = "balanced"
    # A balanced short closer to spot than this is not used (keyed by DTE).
    min_distance_by_dte: dict[int, int] = Field(default_factory=lambda: {0: 225, 2: 250})
    # A balanced short at most this far from its same-width strike.
    max_shift_pts: int = 150


class BalancedEntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_spread_pts: float = 2.0
    min_credit_by_dte: dict[int, float] = Field(default_factory=lambda: {0: 5.0, 1: 10.0})


class BalancedExitConfig(ExitConfig):
    # Points of premium by DTE; rupees = points × lot_size × lots. Replace the flat stop/target.
    stop_loss_points: None = None
    target_points: None = None
    stop_by_dte: dict[int, float] = Field(default_factory=lambda: {0: 5.0, 1: 8.0, 2: 15.0})
    target_by_dte: dict[int, float] = Field(default_factory=lambda: {0: 5.0, 1: 8.0, 2: 15.0})


class IronCondorBalancedConfig(IronCondorConfig):
    filters: BalancedFilterConfig = Field(default_factory=BalancedFilterConfig)
    strikes: StrikeConfig = Field(default_factory=StrikeConfig)
    entry: BalancedEntryConfig = Field(default_factory=BalancedEntryConfig)
    exits: BalancedExitConfig = Field(default_factory=BalancedExitConfig)

    def min_distance_for(self, dte: int) -> int:
        return self._for_dte(self.strikes.min_distance_by_dte, dte)

    def min_credit_for(self, dte: int) -> float:
        return self._for_dte(self.entry.min_credit_by_dte, dte)

    def stop_for(self, dte: int) -> float:
        return self._for_dte(self.exits.stop_by_dte, dte)

    def target_for(self, dte: int) -> float:
        return self._for_dte(self.exits.target_by_dte, dte)
