"""Config model for the weekend S/R swing (values live in config.yaml next to this file)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from swing.backtest import BacktestConfig
from swing.setups import SetupConfig
from swing.zones import ZoneConfig
from trading_core.config import load_model

CONFIG_PATH = Path(__file__).with_name("config.yaml")


class IndexDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str                               # Kite tradingsymbol, NSE INDICES segment
    kind: Literal["sector", "theme"]
    csv: str                                # constituent file on niftyindices.com


class SwingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: str = "swing"
    history_days: int = 2920
    min_traded_value_cr: float = 25.0
    overview_near_atr: float = 1.0
    overview_stale_days: int = 7
    zones: ZoneConfig = ZoneConfig()
    setups: SetupConfig = SetupConfig()
    backtest: BacktestConfig = BacktestConfig()
    indices: list[IndexDef] = []

    @property
    def index_universe(self) -> str:
        """Store key of the indices' own daily candles (``equity_daily``)."""
        return f"{self.universe}_indices"


def load_swing_config(overrides: dict[str, Any] | None = None) -> SwingConfig:
    return load_model(CONFIG_PATH, SwingConfig, overrides)
