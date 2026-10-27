"""End-of-day report files: data/reports/<algo>/<date>[_replay_<run>]/summary.{json,txt}."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trading_core.strategy.position import Position


def report_dir(reports_dir: Path, summary: dict[str, Any]) -> Path:
    name = summary["trade_date"].isoformat()
    if summary["source"] != "live":
        name += f"_replay_{summary['run_id']}"
    return reports_dir / summary["algo_id"] / name


def write_report(reports_dir: Path, summary: dict[str, Any], trades: list[Position]) -> Path:
    out = report_dir(reports_dir, summary)
    out.mkdir(parents=True, exist_ok=True)

    legs = [dict(trade=n, role=l.role, symbol=l.tradingsymbol, side=l.side, qty=l.qty, entry=l.entry_price,
                 exit=l.exit_price, pnl=l.realized_pnl, charges=round(l.entry_charges + l.exit_charges, 2))
            for n, pos in enumerate(trades, 1) for l in pos.legs]
    payload = {**summary, "trade_date": summary["trade_date"].isoformat(), "legs": legs}
    (out / "summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = [f"{summary['algo_id']}  {payload['trade_date']}  ({summary['mode']}, {summary['source']}, "
             f"run {summary['run_id']})", ""]
    for key in ("traded", "entry_credit_pts", "exit_reason", "gross_pnl", "charges", "net_pnl",
                "max_profit", "max_drawdown", "notes"):
        lines.append(f"{key:>17}: {summary.get(key)}")
    if legs:
        lines += ["", f"{'#':<3}{'role':<10}{'symbol':<26}{'side':<6}{'qty':>5}{'entry':>9}{'exit':>9}{'pnl':>10}"]
        for l in legs:
            lines.append(f"{l['trade']:<3}{l['role']:<10}{l['symbol']:<26}{l['side']:<6}{l['qty']:>5}"
                         f"{_f(l['entry']):>9}{_f(l['exit']):>9}{_f(l['pnl']):>10}")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _f(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"
