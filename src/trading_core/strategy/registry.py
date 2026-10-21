"""Discovers algos in ``src/algos/<algo_id>/`` (each has ``algo.py`` exposing ``ALGO`` and a ``config.yaml``)."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from trading_core.config import load_model
from trading_core.strategy.base import BaseAlgo, BaseAlgoConfig

PACKAGE = "algos"


def algos_dir() -> Path:
    pkg = importlib.import_module(PACKAGE)
    return Path(pkg.__file__).parent


def available() -> list[str]:
    return sorted(p.name for p in algos_dir().iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "algo.py").exists())


def load(algo_id: str, overrides: dict[str, Any] | None = None,
         config_path: Path | None = None) -> tuple[type[BaseAlgo], BaseAlgoConfig]:
    if algo_id not in available():
        raise ValueError(f"unknown algo '{algo_id}'. Available: {', '.join(available()) or 'none'}")
    module = importlib.import_module(f"{PACKAGE}.{algo_id}.algo")
    cls: type[BaseAlgo] = module.ALGO
    cfg = load_model(config_path or algos_dir() / algo_id / "config.yaml", cls.config_model, overrides)
    if cfg.algo_id != algo_id:
        raise ValueError(f"config algo_id '{cfg.algo_id}' does not match folder '{algo_id}'")
    return cls, cfg
