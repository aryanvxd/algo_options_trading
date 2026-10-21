"""Starting point for a new algo.

1. Copy this folder to ``src/algos/<your_algo_id>/`` (no leading underscore).
2. Set ``algo_id`` in config.yaml to the folder name.
3. Add your settings to ``MyAlgoConfig`` and config.yaml.
4. Fill in the hooks below. Order execution, exits, snapshots, the kill switch and the daily
   report are already handled by ``BaseAlgo``.
5. Run it:  algo run --algo <your_algo_id> --mode paper
"""

from __future__ import annotations

from trading_core.risk.engine import ExitRule
from trading_core.risk.rules import KillSwitch, PnlStop, PnlTarget, StaleFeed, TimeExit
from trading_core.strategy.base import BaseAlgo, BaseAlgoConfig, SkipDay
from trading_core.strategy.position import Position


class MyAlgoConfig(BaseAlgoConfig):
    stop_loss_rupees: float = 500.0
    target_rupees: float = 500.0


class MyAlgo(BaseAlgo):
    config_model = MyAlgoConfig
    structure = "CUSTOM"
    cfg: MyAlgoConfig

    def build_exit_rules(self) -> list[ExitRule]:
        return [
            KillSwitch(),
            StaleFeed(10),
            PnlStop(self.cfg.stop_loss_rupees),
            PnlTarget(self.cfg.target_rupees),
            TimeExit(self.cfg.schedule.square_off),
        ]

    def day_check(self) -> None:
        # e.g. look up expiry / tokens with self.ctx.instruments, raise SkipDay("...") to stand down
        pass

    def premarket(self) -> None:
        # e.g. self.ctx.premarket_source.capture(...) and self.check("vix", passed, value, threshold)
        pass

    def start_feed(self) -> None:
        # self.ctx.feed.subscribe([...tokens...])
        pass

    def build_entry(self) -> Position | None:
        # Return Position(structure=..., underlying=..., legs=[Leg(...), ...]) to trade now,
        # None to try again next loop, or raise SkipDay to give up for the day.
        raise SkipDay("template algo does not trade")


ALGO = MyAlgo
