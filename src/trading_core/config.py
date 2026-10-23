"""Loading and validating configuration.

- ``config/platform.yaml``  -> ``PlatformConfig`` (shared by all algos)
- ``src/algos/<id>/config.yaml`` -> the algo's own pydantic model (see ``strategy.base.BaseAlgoConfig``)
- ``.env`` -> Kite API key/secret
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeVar

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


def project_root() -> Path:
    env = os.environ.get("ALGO_HOME")
    if env:
        return Path(env).resolve()
    # src/trading_core/config.py -> project root is two levels above the package
    return Path(__file__).resolve().parents[2]


class KiteConfig(BaseModel):
    redirect_host: str = "127.0.0.1"
    redirect_port: int = 5005
    redirect_path: str = "/kite/callback"
    min_request_interval_s: float = 0.35
    max_retries: int = 3


class ChargeRates(BaseModel):
    brokerage_per_order: float = 20.0
    stt_sell_rate: float = 0.0015
    exchange_txn_rate: float = 0.0003503
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00003
    gst_rate: float = 0.18


class PaperConfig(BaseModel):
    slippage_pts: float = 0.5
    max_spread_pts: float = 5.0
    tick_size: float = 0.05


class InstrumentsConfig(BaseModel):
    nfo_underlyings: list[str] = Field(default_factory=lambda: ["NIFTY"])
    keep_days: int = 30


class RuntimeConfig(BaseModel):
    loop_interval_s: float = 1.0
    live_state_interval_s: float = 1.0
    position_snapshot_interval_s: float = 5.0
    tick_snapshot_interval_s: float = 5.0
    control_poll_interval_s: float = 1.0


class MarketDataConfig(BaseModel):
    dir: Path = Path("data/market")
    index_symbols: list[str] = Field(default_factory=lambda: ["NIFTY 50", "INDIA VIX"])
    underlyings: list[str] = Field(default_factory=lambda: ["NIFTY"])
    risk_free_rate: float = 0.065          # for Black-76 IV/greeks; ~91-day T-bill yield
    sqlite_tick_keep_days: int = 7         # recorded ticks stay in SQLite this long after export
    nse_request_interval_s: float = 1.0    # politeness delay between NSE archive downloads


class PlatformConfig(BaseModel):
    root: Path = Field(default_factory=project_root)
    db_url: str = "sqlite:///data/db/platform.db"
    data_dir: Path = Path("data")
    logs_dir: Path = Path("logs")
    log_level: str = "INFO"
    dashboard_port: int = 8050
    kite: KiteConfig = Field(default_factory=KiteConfig)
    charges: ChargeRates = Field(default_factory=ChargeRates)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    instruments: InstrumentsConfig = Field(default_factory=InstrumentsConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    marketdata: MarketDataConfig = Field(default_factory=MarketDataConfig)

    def model_post_init(self, __context: Any) -> None:
        self.data_dir = self._abs(self.data_dir)
        self.logs_dir = self._abs(self.logs_dir)
        self.marketdata.dir = self._abs(self.marketdata.dir)
        prefix = "sqlite:///"
        if self.db_url.startswith(prefix) and not self.db_url.startswith(prefix + ":memory:"):
            db_path = Path(self.db_url[len(prefix):])
            self.db_url = prefix + self._abs(db_path).as_posix()

    def _abs(self, p: Path) -> Path:
        return p if p.is_absolute() else (self.root / p).resolve()

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


class KiteSecrets(BaseModel):
    api_key: str
    api_secret: str


def read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_platform_config(root: Path | None = None) -> PlatformConfig:
    root = root or project_root()
    path = root / "config" / "platform.yaml"
    data = read_yaml(path) if path.exists() else {}
    return PlatformConfig(root=root, **data)


def load_secrets(root: Path | None = None) -> KiteSecrets:
    root = root or project_root()
    load_dotenv(root / ".env")
    key, secret = os.environ.get("KITE_API_KEY", ""), os.environ.get("KITE_API_SECRET", "")
    if not key or not secret:
        raise RuntimeError(
            "KITE_API_KEY / KITE_API_SECRET missing. Copy .env.example to .env and fill them in."
        )
    return KiteSecrets(api_key=key, api_secret=secret)


def load_model(path: Path, model: type[T], overrides: dict[str, Any] | None = None) -> T:
    data = read_yaml(path)
    if overrides:
        data = deep_merge(data, overrides)
    return model.model_validate(data)


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_universe(platform: PlatformConfig, name: str) -> list[str]:
    """Return Kite quote keys (``EXCHANGE:SYMBOL``) for a universe file in config/universes."""
    data = read_yaml(platform.config_dir / "universes" / f"{name}.yaml")
    exchange = data.get("exchange", "NSE")
    return [f"{exchange}:{s}" for s in data.get("symbols", [])]
