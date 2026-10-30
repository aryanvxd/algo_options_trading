"""The bias score: which side (CE / PE / none) the market leans to right now.

score = w_breadth × B1 + w_skew × B2 + w_vwap × B3, each part −1 (bearish) .. +1 (bullish):
  B1  NIFTY 50 weighted advances/declines        ±breadth_full  -> ±1
  B2  PE ÷ CE premium at the same distance from spot: the richer side is the direction, with the
      neutral point shifted for the normal put skew (bull_ratio -> +1, bear_ratio -> −1)
  B3  NIFTY futures − VWAP                       ±vwap_full_pts -> ±1
A missing part counts as 0. Breadth is required: without it there is no side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from algos.directional_scalp.settings import BiasConfig

CE, PE = "CE", "PE"


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


@dataclass(frozen=True)
class BiasReading:
    ts: datetime
    score: float | None
    b1: float | None
    b2: float | None
    b3: float | None
    weighted_ad: float | None
    skew_ratio: float | None
    fut_minus_vwap: float | None

    def as_dict(self) -> dict[str, Any]:
        return dict(score=self.score, b1=self.b1, b2=self.b2, b3=self.b3, weighted_ad=self.weighted_ad,
                    skew_ratio=self.skew_ratio, fut_minus_vwap=self.fut_minus_vwap)


def score(cfg: BiasConfig, now: datetime, weighted_ad: float | None, skew_ratio: float | None,
          fut_minus_vwap: float | None) -> BiasReading:
    b1 = round(_clip(weighted_ad / cfg.breadth_full), 3) if weighted_ad is not None else None
    b2 = None
    if skew_ratio is not None:
        mid = (cfg.skew_bull_ratio + cfg.skew_bear_ratio) / 2
        half = (cfg.skew_bear_ratio - cfg.skew_bull_ratio) / 2
        b2 = round(_clip((mid - skew_ratio) / half), 3)
    b3 = round(_clip(fut_minus_vwap / cfg.vwap_full_pts), 3) if fut_minus_vwap is not None else None
    total = None
    if b1 is not None:
        total = round(cfg.w_breadth * b1 + cfg.w_skew * (b2 or 0.0) + cfg.w_vwap * (b3 or 0.0), 3)
    return BiasReading(now, total, b1, b2, b3, weighted_ad, skew_ratio, fut_minus_vwap)


class BiasTracker:
    """Keeps the latest reading and how long the score has been past the threshold on one side."""

    def __init__(self, cfg: BiasConfig):
        self.cfg = cfg
        self.last: BiasReading | None = None
        self._side: str | None = None
        self._since: datetime | None = None

    def update(self, reading: BiasReading) -> None:
        self.last = reading
        side = self.raw_side(reading)
        if side != self._side:
            self._side, self._since = side, reading.ts if side else None

    def raw_side(self, r: BiasReading) -> str | None:
        if r.score is None or r.b1 is None:
            return None
        if r.score >= self.cfg.threshold and r.b1 > 0:       # breadth must agree
            return CE
        if r.score <= -self.cfg.threshold and r.b1 < 0:
            return PE
        return None

    def side(self, now: datetime) -> str | None:
        """The side the score has favoured for at least ``hold_s``, else None."""
        if self._side and self._since and (now - self._since).total_seconds() >= self.cfg.hold_s:
            return self._side
        return None

    def held_s(self, now: datetime) -> float:
        return (now - self._since).total_seconds() if self._since else 0.0

    def against(self, side: str) -> bool:
        """The score has turned ``flip_exit`` the other way from ``side``."""
        s = self.last.score if self.last else None
        if s is None:
            return False
        return s <= -self.cfg.flip_exit if side == CE else s >= self.cfg.flip_exit
