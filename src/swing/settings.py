"""Config model for the weekend S/R swing (values live in config.yaml next to this file).

The dashboard's Swing › Settings page saves your changes to ``data/swing/overrides.json``; they are
merged over config.yaml by ``load_user_config`` (the scan and the dashboard both use it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from swing.backtest import BacktestConfig
from swing.setups import SetupConfig
from swing.zones import ZoneConfig
from trading_core.config import PlatformConfig, load_model

CONFIG_PATH = Path(__file__).with_name("config.yaml")


class IndexDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str                               # Kite tradingsymbol, NSE INDICES segment
    kind: Literal["sector", "theme"]
    csv: str                                # constituent file on niftyindices.com


class FundRule(BaseModel):
    """One fundamentals check, e.g. roce >= 15. ``metric`` is a column key from the uploaded CSV."""
    model_config = ConfigDict(extra="forbid")
    metric: str
    op: Literal[">=", "<="]
    value: float


class FunnelConfig(BaseModel):
    """The weekend workflow (Swing › Picks): sectors first, then their stocks, then a ranking."""
    model_config = ConfigDict(extra="forbid")
    # stage 1: sectors
    sector_near_atr: float = Field(1.5, gt=0)       # a sector "at support": inside a zone or this close above one
    sector_need_trend: bool = True                  # ... and its trend up (200-day average or rising weekly EMA)
    require_sector: bool = True                     # stage 2 only looks at stocks of sectors that pass
    rsi_days: int = Field(14, ge=2)
    # stage 2: stocks (entry, stop and target come from setups.find_setup with setups.*, except the stop)
    stop_atr: float = Field(1.0, ge=0)              # stop = zone bottom - 1 ATR (the backtest's 0.5 was hit in
                                                    # 2 days three times in four, spec 9b)
    default_mode: Literal["oco", "target"] = "oco"  # oco = stop + target; target = target only
    # stage 3: ranking (a ranking aid: the backtests found no edge in the parts of the zone score)
    top_n: int = Field(10, ge=1, le=50)             # the Algo picks watchlist
    w_tech: float = Field(0.75, ge=0)
    w_news: float = Field(0.10, ge=0)
    w_fund: float = Field(0.15, ge=0)
    news_days: int = Field(21, ge=1)                # results within this many days of the scan: flagged
    results_penalty: float = Field(60, ge=0, le=100)
    macro_days: int = Field(7, ge=1)                # High-impact Indian macro events this close: flagged
    macro_penalty: float = Field(20, ge=0, le=100)
    fund_rules: list[FundRule] = [FundRule(metric="roce", op=">=", value=15),
                                  FundRule(metric="debt_to_equity", op="<=", value=1),
                                  FundRule(metric="price_to_book_value", op="<=", value=5)]


class SwingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: str = "swing"
    benchmark: str = "NIFTY 50"             # relative strength of each sector is measured against it
    history_days: int = 2920
    min_traded_value_cr: float = 25.0
    overview_near_atr: float = 1.0
    overview_stale_days: int = 7
    zones: ZoneConfig = ZoneConfig()
    setups: SetupConfig = SetupConfig()
    funnel: FunnelConfig = FunnelConfig()
    backtest: BacktestConfig = BacktestConfig()
    indices: list[IndexDef] = []

    @property
    def index_universe(self) -> str:
        """Store key of the indices' own daily candles (``equity_daily``)."""
        return f"{self.universe}_indices"

    @property
    def holdings_universe(self) -> str:
        """Store key of the held stocks' daily candles (some are outside the universe)."""
        return f"{self.universe}_holdings"

    @property
    def benchmark_universe(self) -> str:
        """Store key of the benchmark's daily candles (kept apart: it is not a sector)."""
        return f"{self.universe}_benchmark"


def load_swing_config(overrides: dict[str, Any] | None = None) -> SwingConfig:
    return load_model(CONFIG_PATH, SwingConfig, overrides)


def overrides_path(platform: PlatformConfig) -> Path:
    return platform.data_dir / "swing" / "overrides.json"


def read_overrides(platform: PlatformConfig) -> dict[str, Any]:
    path = overrides_path(platform)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_user_config(platform: PlatformConfig) -> SwingConfig:
    """config.yaml with the dashboard's saved changes on top. A file that no longer validates (a
    setting renamed since) is ignored rather than stopping the scan."""
    extra = read_overrides(platform)
    if extra:
        try:
            return load_swing_config(extra)
        except ValueError:
            pass
    return load_swing_config()


def save_overrides(platform: PlatformConfig, overrides: dict[str, Any]) -> SwingConfig:
    """Check ``overrides`` (raises pydantic's ValidationError, a ValueError) and save them."""
    cfg = load_swing_config(overrides)
    path = overrides_path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    if overrides:
        path.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    elif path.exists():
        path.unlink()
    return cfg
