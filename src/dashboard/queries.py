"""Read-only queries behind the dashboard. Every function returns plain JSON-able dicts."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from trading_core.db import models as m
from trading_core.db.engine import Database

MODES = ("paper", "live")


def _mode_filter(q, model, mode: str):
    return q if mode == "all" else q.where(model.mode == mode)


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _position_pnl(pos: m.Position, charges_by_leg: dict[int, float],
                  live: m.LiveState | None) -> dict[str, Any]:
    """Gross / charges / net for one position. Closed positions use fills; an open one uses the
    run's live state (its net already includes estimated exit charges)."""
    if pos.status == "OPEN" or (pos.status == "OPENING" and live is not None):
        if live is None or live.gross_pnl is None:
            return dict(gross=None, charges=None, net=None, open=True)
        net = live.net_pnl if live.net_pnl is not None else live.gross_pnl
        return dict(gross=live.gross_pnl, charges=round(live.gross_pnl - net, 2), net=net, open=True)
    gross = sum(l.pnl or 0.0 for l in pos.legs)
    charges = sum(charges_by_leg.get(l.leg_id, 0.0) for l in pos.legs)
    return dict(gross=round(gross, 2), charges=round(charges, 2), net=round(gross - charges, 2), open=False)


def _charges_by_leg(s, run_ids: list[int]) -> dict[int, float]:
    rows = s.execute(
        select(m.Order.leg_id, func.sum(m.OrderCharge.total))
        .join(m.OrderCharge, m.OrderCharge.order_id == m.Order.order_id)
        .where(m.Order.run_id.in_(run_ids), m.Order.status == "COMPLETE")
        .group_by(m.Order.leg_id)
    ).all()
    return {leg_id: total or 0.0 for leg_id, total in rows if leg_id is not None}


def _intraday(s, position_ids: list[int]) -> dict[int, list[tuple[str, float]]]:
    """Net P&L per position, one point per minute (last snapshot in each minute)."""
    out: dict[int, dict[str, float]] = defaultdict(dict)
    if not position_ids:
        return {}
    rows = s.execute(
        select(m.PositionSnapshot.position_id, m.PositionSnapshot.ts, m.PositionSnapshot.net_pnl,
               m.PositionSnapshot.gross_pnl)
        .where(m.PositionSnapshot.position_id.in_(position_ids))
        .order_by(m.PositionSnapshot.ts)
    ).all()
    for pid, ts, net, gross in rows:
        v = net if net is not None else gross
        if v is not None:
            out[pid][ts.strftime("%H:%M")] = round(v, 2)
    return {pid: sorted(pts.items()) for pid, pts in out.items()}


def overview(db: Database, day: date, mode: str = "paper", source: str = "live") -> dict[str, Any]:
    with db.session() as s:
        q = (select(m.AlgoRun, m.Algo.name).join(m.Algo, m.Algo.algo_id == m.AlgoRun.algo_id)
             .where(m.AlgoRun.trade_date == day, m.AlgoRun.source == source)
             .order_by(m.AlgoRun.algo_id, m.AlgoRun.run_id))
        runs = s.execute(_mode_filter(q, m.AlgoRun, mode)).all()
        run_ids = [r.run_id for r, _ in runs]

        positions = s.scalars(select(m.Position).options(selectinload(m.Position.legs))
                              .where(m.Position.run_id.in_(run_ids)).order_by(m.Position.position_id)).all()
        by_run: dict[int, list[m.Position]] = defaultdict(list)
        for p in positions:
            by_run[p.run_id].append(p)
        live = {ls.run_id: ls for ls in s.scalars(select(m.LiveState).where(m.LiveState.run_id.in_(run_ids)))}
        summaries = {d.run_id: d for d in s.scalars(select(m.DailySummary).where(m.DailySummary.run_id.in_(run_ids)))}
        charges_by_leg = _charges_by_leg(s, run_ids) if run_ids else {}
        intraday = _intraday(s, [p.position_id for p in positions])

    strategies = []
    totals = dict(gross=0.0, charges=0.0, net=0.0, open_net=0.0, trades=0, open_positions=0)
    for run, name in runs:
        ls = live.get(run.run_id)
        gross = charges = net = 0.0
        known = True
        trades, open_count, exits, series = 0, 0, [], []
        for p in by_run.get(run.run_id, []):
            if not any(l.entry_price is not None for l in p.legs):
                continue            # rejected before any fill: nothing traded
            pnl = _position_pnl(p, charges_by_leg, ls)
            trades += 1
            if pnl["open"]:
                open_count += 1
                totals["open_net"] += pnl["net"] or 0.0
            if pnl["net"] is None:
                known = False
                continue
            gross += pnl["gross"]
            charges += pnl["charges"]
            net += pnl["net"]
            if p.exit_reason:
                exits.append(p.exit_reason)
            series.extend(intraday.get(p.position_id, []))

        first = next((p for p in by_run.get(run.run_id, []) if p.entry_credit_pts is not None), None)
        summary = summaries.get(run.run_id)
        strategies.append(dict(
            run_id=run.run_id, algo_id=run.algo_id, name=name, mode=run.mode, status=run.status,
            live_status=ls.status if ls else None, skip_reason=run.skip_reason,
            trades=trades, open_positions=open_count,
            gross=_r(gross) if trades else None, charges=_r(charges) if trades else None,
            net=_r(net) if trades and known else None,
            entry_credit=first.entry_credit_pts if first else None,
            exit_reason=", ".join(exits) or None,
            max_profit=_r(summary.max_profit) if summary else None,
            max_drawdown=_r(summary.max_drawdown) if summary else None,
            spot=ls.spot if ls else None, vix=ls.vix if ls else None,
            updated_at=ls.updated_at.isoformat() if ls else None,
            started_at=run.started_at.isoformat(), ended_at=run.ended_at.isoformat() if run.ended_at else None,
            intraday=series,
        ))
        if trades and known:
            totals["gross"] += gross
            totals["charges"] += charges
            totals["net"] += net
        totals["trades"] += trades
        totals["open_positions"] += open_count

    totals = {k: _r(v) if isinstance(v, float) else v for k, v in totals.items()}
    totals.update(strategies=len(strategies),
                  traded=sum(1 for x in strategies if x["trades"]),
                  skipped=sum(1 for x in strategies if x["status"] == "SKIPPED"),
                  running=sum(1 for x in strategies if x["status"] in ("STARTED", "IN_TRADE")))
    return dict(date=day.isoformat(), mode=mode, source=source, totals=totals, strategies=strategies)


def pnl_history(db: Database, end: date, days: int = 30, mode: str = "paper",
                source: str = "live") -> dict[str, Any]:
    """Net P&L per strategy per day over the last ``days`` calendar days (days with any run)."""
    start = end - timedelta(days=days - 1)
    with db.session() as s:
        q = (select(m.DailySummary.trade_date, m.DailySummary.algo_id, m.Algo.name, m.DailySummary.net_pnl)
             .join(m.Algo, m.Algo.algo_id == m.DailySummary.algo_id)
             .where(m.DailySummary.trade_date.between(start, end), m.DailySummary.source == source)
             .order_by(m.DailySummary.trade_date))
        rows = s.execute(_mode_filter(q, m.DailySummary, mode)).all()

    dates = sorted({d for d, *_ in rows})
    names: dict[str, str] = {}
    values: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for d, algo_id, name, net in rows:
        names[algo_id] = name
        values[algo_id][d] += net or 0.0
    series = [dict(algo_id=a, name=names[a], values=[_r(values[a].get(d, 0.0)) for d in dates])
              for a in sorted(values)]
    cumulative = 0.0
    totals = []
    for i in range(len(dates)):
        cumulative += sum(sr["values"][i] for sr in series)
        totals.append(_r(cumulative))
    return dict(dates=[d.isoformat() for d in dates], series=series, cumulative=totals)


def meta(db: Database, today: date) -> dict[str, Any]:
    with db.session() as s:
        algos = s.execute(select(m.Algo.algo_id, m.Algo.name).order_by(m.Algo.algo_id)).all()
        latest = s.scalar(select(func.max(m.AlgoRun.trade_date)).where(m.AlgoRun.source == "live"))
        latest_replay = s.scalar(select(func.max(m.AlgoRun.trade_date)).where(m.AlgoRun.source == "replay"))
    return dict(today=today.isoformat(), algos=[dict(algo_id=a, name=n) for a, n in algos],
                latest_live_date=latest.isoformat() if latest else None,
                latest_replay_date=latest_replay.isoformat() if latest_replay else None)
