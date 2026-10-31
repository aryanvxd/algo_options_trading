"""Backtest metrics from the backtest's own database (trades = closed or failed-and-unwound positions)."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select

from trading_core.db import models as m
from trading_core.db.engine import Database

LIMITS = [
    "Option prices are modelled (Black-76, the previous day's IV smile scaled by India VIX): fine for "
    "ranking settings, not a promise of rupees.",
    "Signals the history can't show are switched off (see backtest_overrides), e.g. the CE/PE premium skew.",
    "Today's NIFTY 50 list and weights on every day (survivorship); stocks Kite no longer lists are missing.",
    "Fills at the modelled touch plus the paper slippage; no queueing, partial fills or latency.",
    "Today's lot size on every day, so rupee results compare across years.",
]


def _stats(nets: list[float]) -> dict[str, Any]:
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return dict(
        trades=len(nets), net=round(sum(nets), 2), wins=len(wins), losses=len(losses),
        win_rate=round(100 * len(wins) / len(nets), 1) if nets else None,
        avg_win=round(gross_win / len(wins), 2) if wins else None,
        avg_loss=round(-gross_loss / len(losses), 2) if losses else None,
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss else None,
        expectancy=round(sum(nets) / len(nets), 2) if nets else None,
    )


def _group(trades: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        groups[str(t[key])].append(t["net"])
    return {k: _stats(v) for k, v in sorted(groups.items())}


def summarize(db: Database, meta: dict[str, Any], coverage: dict[str, dict[str, Any]]) -> dict[str, Any]:
    with db.session() as s:
        runs = {r.run_id: r for r in s.scalars(select(m.AlgoRun))}
        rows = s.scalars(select(m.Position).where(m.Position.net_pnl.is_not(None))
                         .order_by(m.Position.entry_time)).all()
        trades = []
        for p in rows:
            params, run = p.params or {}, runs[p.run_id]
            trades.append(dict(
                date=str(run.trade_date), month=f"{run.trade_date:%Y-%m}", weekday=f"{run.trade_date:%a}",
                dte=params.get("dte"), side=params.get("side") or p.structure, exit_reason=p.exit_reason or "?",
                entry=f"{p.entry_time:%H:%M}" if p.entry_time else None, net=p.net_pnl, gross=p.gross_pnl,
                charges=p.charges))

    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t["date"]] += t["net"]
    equity, cum, peak, max_dd = [], 0.0, 0.0, 0.0
    for r in sorted(runs.values(), key=lambda r: r.trade_date):
        day = str(r.trade_date)
        cum += by_day.get(day, 0.0)
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
        equity.append(dict(date=day, net=round(by_day.get(day, 0.0), 2), cum=round(cum, 2),
                           drawdown=round(cum - peak, 2)))
    skips = Counter(_reason(r.skip_reason) for r in runs.values() if r.status == "SKIPPED")
    day_nets = [v for v in by_day.values()]
    stocks_seen = [c.get("stocks", 0) for c in coverage.values() if "stocks" in c]
    return dict(
        meta=meta,
        totals={**_stats([t["net"] for t in trades]),
                "gross": round(sum(t["gross"] or 0 for t in trades), 2),
                "charges": round(sum(t["charges"] or 0 for t in trades), 2),
                "max_drawdown": round(max_dd, 2),
                "days": len(runs), "days_traded": len(by_day),
                "days_skipped": sum(skips.values()),
                "best_day": round(max(day_nets), 2) if day_nets else None,
                "worst_day": round(min(day_nets), 2) if day_nets else None},
        by_dte=_group(trades, "dte"), by_month=_group(trades, "month"), by_exit=_group(trades, "exit_reason"),
        by_side=_group(trades, "side"), by_weekday=_group(trades, "weekday"),
        equity=equity, skips=dict(skips.most_common()),
        coverage=dict(days=len(coverage),
                      with_futures=sum(1 for c in coverage.values() if c.get("fut")),
                      with_vix=sum(1 for c in coverage.values() if c.get("vix")),
                      with_smile=sum(1 for c in coverage.values() if c.get("smile")),
                      expiry_from_bhavcopy=sum(1 for c in coverage.values() if c.get("expiry") == "bhavcopy"),
                      stocks_min=min(stocks_seen) if stocks_seen else 0,
                      stocks_max=max(stocks_seen) if stocks_seen else 0,
                      missing_index=[d for d, c in coverage.items() if c.get("missing")]),
        limits=LIMITS,
    )


def _reason(text: str | None) -> str:
    """Group skip reasons: drop the numbers so 'no entry by 11:00 (last: bias +0.31 ...)' lumps together."""
    if not text:
        return "unknown"
    head = text.split(" (last:")[0]
    last = text.split("(last: ", 1)[1].rstrip(")") if "(last: " in text else ""
    last = last.split(":")[0] if last.startswith(("bias", "trigger skipped")) else last
    last = "".join(ch for ch in last if not ch.isdigit() and ch not in "+-.").strip()
    return f"{head} ({last})" if last else head


def print_summary(summary: dict[str, Any], out=print) -> None:
    t, meta, cov = summary["totals"], summary["meta"], summary["coverage"]
    out(f"\n{meta['algo_id']} v{meta['version']}  {meta['start']} .. {meta['end']}  ({t['days']} days, "
        f"{t['days_traded']} traded)")
    out(f"  trades {t['trades']}  win rate {t['win_rate']}%  net ₹{t['net']:,.0f}  "
        f"(gross ₹{t['gross']:,.0f}, charges ₹{t['charges']:,.0f})")
    out(f"  avg win ₹{t['avg_win'] or 0:,.0f}  avg loss ₹{t['avg_loss'] or 0:,.0f}  profit factor {t['profit_factor']}  "
        f"expectancy ₹{t['expectancy'] or 0:,.0f}/trade  max drawdown ₹{t['max_drawdown']:,.0f}")
    for title, key in (("By DTE", "by_dte"), ("By side", "by_side"), ("By exit", "by_exit")):
        out(f"  {title}: " + "; ".join(f"{k}: {v['trades']} tr, ₹{v['net']:,.0f}, {v['win_rate']}%"
                                      for k, v in summary[key].items()))
    if summary["skips"]:
        out("  Days without a trade: " + "; ".join(f"{k} ×{n}" for k, n in list(summary["skips"].items())[:5]))
    out(f"  Data: {cov['days']} days; futures {cov['with_futures']}, VIX {cov['with_vix']}, IV smile "
        f"{cov['with_smile']}, expiry from bhavcopy {cov['expiry_from_bhavcopy']}, stocks {cov['stocks_min']}-"
        f"{cov['stocks_max']}")
