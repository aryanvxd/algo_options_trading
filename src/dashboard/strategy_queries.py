"""Read queries behind the strategy pages (Today, Trade log, Journal, Performance, Rules), plus the
payoff chart and the journal notes — the dashboard's only writes, to a table no algo reads."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.marketdata import greeks
from trading_core.strategy import payoff, registry
from trading_core.strategy.position import Leg

DOCS = {"iron_condor": "docs/strategy-criteria/iron-condor.md",
        "strangle_scalp": "docs/strategy-criteria/long-strangle-scalp.md"}
MAX_CURVE_POINTS = 600
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _r(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(float(v), n)


def _iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v is not None else None


def _mode(q, model, mode: str):
    return q if mode == "all" else q.where(model.mode == mode)


# ---------------------------------------------------------------- strategy list / config

def strategy_list(db: Database) -> list[dict[str, Any]]:
    """Every algo in src/algos (even before its first run) plus any that only exist in the DB."""
    out: dict[str, dict[str, Any]] = {}
    for algo_id in registry.available():
        try:
            _, cfg = registry.load(algo_id)
        except Exception as e:  # noqa: BLE001 - a broken config must not take the dashboard down
            out[algo_id] = dict(algo_id=algo_id, name=algo_id, error=str(e))
            continue
        out[algo_id] = dict(algo_id=algo_id, name=cfg.name, version=cfg.version, description=cfg.description,
                            mode=cfg.mode, lots=cfg.lots, capital=cfg.capital, max_trades=cfg.max_trades,
                            entry=cfg.schedule.entry.strftime("%H:%M"),
                            entry_deadline=cfg.schedule.entry_deadline.strftime("%H:%M"),
                            square_off=cfg.schedule.square_off.strftime("%H:%M"), spec=DOCS.get(algo_id))
    with db.session() as s:
        for algo_id, name in s.execute(select(m.Algo.algo_id, m.Algo.name)).all():
            if algo_id not in out and algo_id != "recorder":
                out[algo_id] = dict(algo_id=algo_id, name=name, retired=True)
    return sorted(out.values(), key=lambda a: a["algo_id"])


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def rules(db: Database, algo_id: str) -> dict[str, Any]:
    _, cfg = registry.load(algo_id)
    current = cfg.model_dump(mode="json")
    with db.session() as s:
        last = s.scalars(select(m.AlgoRun).where(m.AlgoRun.algo_id == algo_id, m.AlgoRun.source == "live")
                         .order_by(m.AlgoRun.run_id.desc())).first()
        last_cfg = dict(last.config_snapshot) if last else None
        last_info = dict(run_id=last.run_id, trade_date=_iso(last.trade_date)) if last else None
    flat, flat_last = _flatten(current), _flatten(last_cfg or {})
    rows = [dict(key=k, value=v, last_run=flat_last.get(k), new=bool(last_cfg) and k not in flat_last,
                 changed=k in flat_last and flat_last[k] != v) for k, v in flat.items()]
    return dict(algo_id=algo_id, version=cfg.version, spec=DOCS.get(algo_id), rows=rows, last_run=last_info,
                last_version=(last_cfg or {}).get("version"))


# ---------------------------------------------------------------- trades

def _domain_legs(p: m.Position) -> list[Leg]:
    return [Leg(role=l.role, instrument_token=l.instrument_token, tradingsymbol=l.tradingsymbol, side=l.side,
                qty=l.qty, option_type=l.option_type, strike=l.strike, entry_price=l.entry_price,
                exit_price=l.exit_price) for l in p.legs]


def _capital(p: m.Position) -> tuple[float | None, str | None]:
    if p.capital_required:
        return p.capital_required, p.capital_source
    legs = [l for l in _domain_legs(p) if l.entry_price is not None]
    if legs and all(l.side == "BUY" for l in legs):
        return round(sum(l.entry_price * l.qty for l in legs), 2), "premium"
    loss = payoff.max_loss(legs)
    return (loss, "estimate") if loss is not None else (None, None)


def _strikes(p: m.Position) -> str:
    parts = []
    for l in sorted(p.legs, key=lambda l: (l.strike or 0)):
        parts.append(f"{'+' if l.side == 'BUY' else '−'}{int(l.strike) if l.strike else '?'}{l.option_type or ''}")
    return " ".join(parts)


def _trade_row(p: m.Position, run: m.AlgoRun, notes: int = 0) -> dict[str, Any]:
    capital, source = _capital(p)
    params = p.params or {}
    net = p.net_pnl
    hold = (p.exit_time - p.entry_time).total_seconds() if p.exit_time and p.entry_time else None
    return dict(
        position_id=p.position_id, run_id=p.run_id, trade_date=_iso(run.trade_date), mode=p.mode,
        source=run.source, structure=p.structure, status=p.status, trade_no=params.get("trade_no"),
        dte=params.get("dte"), weekday=WEEKDAYS[run.trade_date.weekday()], expiry=_iso(p.expiry),
        entry_time=_iso(p.entry_time), exit_time=_iso(p.exit_time), hold_s=_r(hold, 0),
        strikes=_strikes(p), entry_credit=p.entry_credit_pts, exit_debit=p.exit_debit_pts,
        exit_reason=p.exit_reason, gross=_r(p.gross_pnl), charges=_r(p.charges), net=_r(net),
        capital=_r(capital), capital_source=source,
        roi_pct=_r(net / capital * 100, 3) if net is not None and capital else None,
        spot_entry=_r(p.spot_at_entry if p.spot_at_entry is not None else params.get("spot")),
        spot_exit=_r(p.spot_at_exit), lots=params.get("lots"),
        driver=(params.get("signal") or {}).get("driver"), notes=notes,
    )


def _positions(s, q) -> list[tuple[m.Position, m.AlgoRun]]:
    rows = s.execute(q.options(selectinload(m.Position.legs))).all()
    return [(p, r) for p, r in rows if any(l.entry_price is not None for l in p.legs)]


def trades(db: Database, algo_id: str, start: date, end: date, mode: str = "paper",
           source: str = "live") -> dict[str, Any]:
    with db.session() as s:
        q = (select(m.Position, m.AlgoRun).join(m.AlgoRun, m.AlgoRun.run_id == m.Position.run_id)
             .where(m.Position.algo_id == algo_id, m.AlgoRun.trade_date.between(start, end),
                    m.AlgoRun.source == source)
             .order_by(m.AlgoRun.trade_date.desc(), m.Position.position_id.desc()))
        found = _positions(s, _mode(q, m.Position, mode))
        notes = dict(s.execute(select(m.JournalNote.position_id, func.count())
                               .where(m.JournalNote.position_id.in_([p.position_id for p, _ in found]))
                               .group_by(m.JournalNote.position_id)).all()) if found else {}
        rows = [_trade_row(p, r, notes.get(p.position_id, 0)) for p, r in found]
    return dict(algo_id=algo_id, start=_iso(start), end=_iso(end), mode=mode, source=source, trades=rows)


def _downsample(points: list, limit: int = MAX_CURVE_POINTS) -> list:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    picked = [points[int(i * step)] for i in range(limit)]
    return picked + [points[-1]]


def _snapshots(s, position_ids: list[int]) -> dict[int, list[list]]:
    out: dict[int, list[list]] = defaultdict(list)
    if not position_ids:
        return {}
    for pid, ts, net, gross, spot in s.execute(
            select(m.PositionSnapshot.position_id, m.PositionSnapshot.ts, m.PositionSnapshot.net_pnl,
                   m.PositionSnapshot.gross_pnl, m.PositionSnapshot.spot)
            .where(m.PositionSnapshot.position_id.in_(position_ids)).order_by(m.PositionSnapshot.ts)).all():
        out[pid].append([ts.strftime("%H:%M:%S"), _r(net), _r(gross), _r(spot)])
    return {pid: _downsample(pts) for pid, pts in out.items()}


def _legs(p: m.Position) -> list[dict[str, Any]]:
    return [dict(leg_id=l.leg_id, role=l.role, symbol=l.tradingsymbol, option_type=l.option_type, strike=l.strike,
                 side=l.side, qty=l.qty, entry=l.entry_price, exit=l.exit_price, pnl=_r(l.pnl)) for l in p.legs]


def trade_detail(db: Database, position_id: int) -> dict[str, Any] | None:
    with db.session() as s:
        row = s.execute(select(m.Position, m.AlgoRun).join(m.AlgoRun, m.AlgoRun.run_id == m.Position.run_id)
                        .where(m.Position.position_id == position_id)
                        .options(selectinload(m.Position.legs))).first()
        if row is None:
            return None
        p, run = row
        orders = s.execute(
            select(m.Order, m.Fill, m.OrderCharge)
            .outerjoin(m.Fill, m.Fill.order_id == m.Order.order_id)
            .outerjoin(m.OrderCharge, m.OrderCharge.order_id == m.Order.order_id)
            .where(m.Order.leg_id.in_([l.leg_id for l in p.legs])).order_by(m.Order.placed_at)).all()
        fills = [dict(time=_iso(o.placed_at), purpose=o.purpose, symbol=o.tradingsymbol, side=o.side, qty=o.qty,
                      status=o.status, reject_reason=o.reject_reason, price=f.price if f else None,
                      bid=f.bid_at_fill if f else None, ask=f.ask_at_fill if f else None,
                      slippage=f.slippage_pts if f else None, charges=_r(c.total) if c else None,
                      brokerage=_r(c.brokerage) if c else None, stt=_r(c.stt) if c else None,
                      gst=_r(c.gst) if c else None)
                 for o, f, c in orders]
        start = (p.entry_time or datetime.combine(run.trade_date, datetime.min.time())) - timedelta(minutes=2)
        checks = [_check(c) for c in s.scalars(select(m.RunCheck).where(
            m.RunCheck.run_id == run.run_id, m.RunCheck.ts >= start, m.RunCheck.ts <= (p.entry_time or start))
            .order_by(m.RunCheck.id))]
        end = (p.exit_time or start + timedelta(days=1)) + timedelta(seconds=1)
        events = [_event(e) for e in s.scalars(select(m.Event).where(
            m.Event.run_id == run.run_id, m.Event.ts >= start, m.Event.ts <= end).order_by(m.Event.id))]
        notes = [_note(n) for n in s.scalars(select(m.JournalNote).where(m.JournalNote.position_id == position_id)
                                              .order_by(m.JournalNote.id))]
        curve = _snapshots(s, [p.position_id]).get(p.position_id, [])
        return dict(trade=_trade_row(p, run, len(notes)), legs=_legs(p), fills=fills, checks=checks, events=events,
                    notes=notes, curve=curve, params=p.params or {})


# ---------------------------------------------------------------- one day

def _check(c: m.RunCheck) -> dict[str, Any]:
    return dict(time=_iso(c.ts), name=c.check_name, passed=c.passed, value=c.value, threshold=c.threshold,
                detail=c.detail)


def _event(e: m.Event) -> dict[str, Any]:
    return dict(time=_iso(e.ts), level=e.level, type=e.type, message=e.message)


def _note(n: m.JournalNote) -> dict[str, Any]:
    return dict(id=n.id, algo_id=n.algo_id, trade_date=_iso(n.trade_date), position_id=n.position_id, text=n.text,
                tags=n.tags or [], created_at=_iso(n.created_at), updated_at=_iso(n.updated_at))


def _day_runs(s, algo_id: str, day: date, mode: str, source: str) -> list[m.AlgoRun]:
    """The runs that make up a day: every live run (their trades add up), or only the newest
    replay (replays of one day are alternatives)."""
    q = (select(m.AlgoRun).where(m.AlgoRun.algo_id == algo_id, m.AlgoRun.trade_date == day,
                                 m.AlgoRun.source == source).order_by(m.AlgoRun.run_id))
    runs = list(s.scalars(_mode(q, m.AlgoRun, mode)).all())
    return runs if source == "live" else runs[-1:]


def _run_info(r: m.AlgoRun, trades: int) -> dict[str, Any]:
    cfg = r.config_snapshot or {}
    return dict(run_id=r.run_id, status=r.status, mode=r.mode, source=r.source, skip_reason=r.skip_reason,
                started_at=_iso(r.started_at), ended_at=_iso(r.ended_at), version=cfg.get("version"),
                lots=cfg.get("lots"), max_trades=cfg.get("max_trades", 1), capital=cfg.get("capital"), trades=trades)


def _primary(runs: list[m.AlgoRun], traded_ids: set[int]) -> m.AlgoRun:
    """The run that speaks for the day: one still going, else the newest that traded, else the newest."""
    active = [r for r in runs if r.status in ("STARTED", "IN_TRADE")]
    traded = [r for r in runs if r.run_id in traded_ids]
    return (active or traded or runs)[-1]


def strategy_day(db: Database, algo_id: str, day: date, mode: str = "paper", source: str = "live") -> dict[str, Any]:
    with db.session() as s:
        notes = [_note(n) for n in s.scalars(select(m.JournalNote).where(
            m.JournalNote.algo_id == algo_id, m.JournalNote.trade_date == day).order_by(m.JournalNote.id))]
        runs = _day_runs(s, algo_id, day, mode, source)
        if not runs:
            return dict(algo_id=algo_id, date=_iso(day), run=None, runs=[], trades=[], checks=[], events=[],
                        notes=notes)
        run_ids = [r.run_id for r in runs]
        found = _positions(s, select(m.Position, m.AlgoRun).join(m.AlgoRun, m.AlgoRun.run_id == m.Position.run_id)
                           .where(m.Position.run_id.in_(run_ids)).order_by(m.Position.position_id))
        per_run: dict[int, int] = defaultdict(int)
        for p, _ in found:
            per_run[p.run_id] += 1
        primary = _primary(runs, set(per_run))
        curves = _snapshots(s, [p.position_id for p, _ in found])
        lives = {ls.run_id: ls for ls in s.scalars(select(m.LiveState).where(m.LiveState.run_id.in_(run_ids)))}
        live = lives.get(primary.run_id)
        summaries = list(s.scalars(select(m.DailySummary).where(m.DailySummary.run_id.in_(run_ids))))
        checks = [dict(_check(c), run_id=c.run_id) for c in s.scalars(
            select(m.RunCheck).where(m.RunCheck.run_id.in_(run_ids)).order_by(m.RunCheck.id))]
        events = [dict(_event(e), run_id=e.run_id) for e in s.scalars(
            select(m.Event).where(m.Event.run_id.in_(run_ids)).order_by(m.Event.id))]
        trades_out = []
        for n, (p, r) in enumerate(found, 1):
            row = _trade_row(p, r)
            row["trade_no"] = n                    # numbered across the day's runs
            ls = lives.get(p.run_id)
            if p.status in ("OPEN", "OPENING") and ls is not None and ls.gross_pnl is not None:
                row.update(gross=_r(ls.gross_pnl), net=_r(ls.net_pnl), open=True,
                           roi_pct=_r((ls.net_pnl or 0) / row["capital"] * 100, 3) if row["capital"] else None)
            trades_out.append(dict(**row, legs=_legs(p), curve=curves.get(p.position_id, [])))

        def total(field: str) -> float | None:
            vals = [getattr(x, field) for x in summaries if getattr(x, field) is not None]
            return _r(sum(vals)) if vals else None

        best = [x.max_profit for x in summaries if x.max_profit is not None]
        worst = [x.max_drawdown for x in summaries if x.max_drawdown is not None]
        run = _run_info(primary, per_run.get(primary.run_id, 0))
        if not trades_out and len(runs) > 1:
            run["skip_reason"] = "; ".join(f"run #{r.run_id}: {r.skip_reason}" for r in runs if r.skip_reason) or None
        elif trades_out:
            run["skip_reason"] = None          # a later run that found nothing to do doesn't make the day "skipped"
        return dict(
            algo_id=algo_id, date=_iso(day), run=run,
            runs=[_run_info(r, per_run.get(r.run_id, 0)) for r in runs],
            live=dict(status=live.status, updated_at=_iso(live.updated_at), spot=live.spot, vix=live.vix,
                      gross=_r(live.gross_pnl), net=_r(live.net_pnl), net_premium=live.net_premium,
                      legs=live.legs, levels=live.levels, feed_ok=live.feed_ok) if live else None,
            summary=dict(gross=total("gross_pnl"), charges=total("charges"), net=total("net_pnl"),
                         max_profit=_r(max(best)) if best else None, max_drawdown=_r(min(worst)) if worst else None,
                         exit_reason=",".join(x.exit_reason for x in summaries if x.exit_reason) or None,
                         notes="; ".join(x.notes for x in summaries if x.notes) or None) if summaries else None,
            trades=trades_out, checks=checks, events=events, notes=notes)


# ---------------------------------------------------------------- payoff

def _tick_mid(s, token: int, at: datetime) -> float | None:
    """Mid of the recorded tick nearest ``at`` (before it if possible), within two minutes."""
    base = select(m.TickSnapshot).where(m.TickSnapshot.instrument_token == token)
    t = s.scalars(base.where(m.TickSnapshot.ts <= at).order_by(m.TickSnapshot.ts.desc()).limit(1)).first()
    if t is None or (at - t.ts).total_seconds() > 120:
        t = s.scalars(base.where(m.TickSnapshot.ts > at).order_by(m.TickSnapshot.ts).limit(1)).first()
    if t is None or abs((at - t.ts).total_seconds()) > 120:
        return None
    if t.bid and t.ask:
        return (t.bid + t.ask) / 2
    return t.ltp


def position_payoff(db: Database, position_id: int, now: datetime, r: float = 0.065) -> dict[str, Any] | None:
    with db.session() as s:
        row = s.execute(select(m.Position, m.AlgoRun).join(m.AlgoRun, m.AlgoRun.run_id == m.Position.run_id)
                        .where(m.Position.position_id == position_id)
                        .options(selectinload(m.Position.legs))).first()
        if row is None:
            return None
        p, run = row
        legs = [l for l in _domain_legs(p) if l.entry_price is not None and l.strike]
        if not legs:
            return dict(position_id=position_id, spots=[], expiry=[], now=None)
        live = s.get(m.LiveState, run.run_id)
        is_open = p.status in ("OPEN", "OPENING")
        spot_entry = p.spot_at_entry or (p.params or {}).get("spot")
        spot_now = (live.spot if live and is_open else None) or p.spot_at_exit
        strikes = sorted({l.strike for l in legs})
        centre = spot_entry or (strikes[0] + strikes[-1]) / 2
        pad = max(0.75 * (strikes[-1] - strikes[0]), 0.01 * centre)     # room either side of the outer strikes
        lo, hi = min(strikes[0], centre) - pad, max(strikes[-1], centre) + pad
        step = 10 if hi - lo <= 2000 else 25
        spots = [float(x) for x in np.arange(np.floor(lo / step) * step, np.ceil(hi / step) * step + step, step)]
        expiry_curve = payoff.curve(legs, spots)

        # "Now" curve (T+0): each leg re-priced with Black-76 at its IV from the latest recorded mid.
        # For a closed trade it is drawn at entry, from the book at that moment.
        when = now if is_open else p.entry_time
        t0 = None
        if when is not None and p.expiry is not None:
            mids = {l.instrument_token: _tick_mid(s, l.instrument_token, when) for l in legs}
            f = (live.spot if live and is_open and live.spot else spot_entry)
            if f and all(v for v in mids.values()):
                t = greeks.years_between(when, p.expiry)
                vals = np.zeros(len(spots))
                ok = True
                for l in legs:
                    iv = float(greeks.implied_vol(mids[l.instrument_token], f, l.strike, t, r, l.option_type == "CE"))
                    if not np.isfinite(iv):
                        ok = False
                        break
                    px = greeks.price(np.array(spots), l.strike, t, r, iv, l.option_type == "CE")
                    vals += (1 if l.side == "BUY" else -1) * (px - l.entry_price) * l.qty
                if ok:
                    t0 = dict(at=_iso(when), values=[round(float(v), 2) for v in vals])

        cfg = run.config_snapshot or {}
        exits = cfg.get("exits") or {}
        qty = min(l.qty for l in legs)
        stop = -exits["stop_loss_points"] * qty if "stop_loss_points" in exits else None
        target = exits["target_points"] * qty if "target_points" in exits else None
        loss = payoff.max_loss(legs)
        return dict(position_id=position_id, structure=p.structure, open=is_open, spots=spots, expiry=expiry_curve,
                    now=t0, breakevens=payoff.breakevens(legs, spots), max_loss=loss,
                    max_profit=_r(max(expiry_curve)), spot_entry=_r(spot_entry), spot_now=_r(spot_now),
                    strikes=[dict(strike=l.strike, type=l.option_type, side=l.side) for l in legs],
                    stop=_r(stop), target=_r(target))


# ---------------------------------------------------------------- journal

def journal(db: Database, algo_id: str, start: date, end: date, mode: str = "paper",
            source: str = "live") -> dict[str, Any]:
    with db.session() as s:
        q = (select(m.AlgoRun).where(m.AlgoRun.algo_id == algo_id, m.AlgoRun.trade_date.between(start, end),
                                     m.AlgoRun.source == source).order_by(m.AlgoRun.trade_date.desc(),
                                                                          m.AlgoRun.run_id.desc()))
        runs = s.scalars(_mode(q, m.AlgoRun, mode)).all()
        run_ids = [r.run_id for r in runs]
        checks: dict[int, list] = defaultdict(list)
        events: dict[int, list] = defaultdict(list)
        positions: dict[int, list] = defaultdict(list)
        if run_ids:
            for c in s.scalars(select(m.RunCheck).where(m.RunCheck.run_id.in_(run_ids)).order_by(m.RunCheck.id)):
                checks[c.run_id].append(_check(c))
            for e in s.scalars(select(m.Event).where(m.Event.run_id.in_(run_ids)).order_by(m.Event.id)):
                events[e.run_id].append(_event(e))
            for p, r in _positions(s, select(m.Position, m.AlgoRun)
                                   .join(m.AlgoRun, m.AlgoRun.run_id == m.Position.run_id)
                                   .where(m.Position.run_id.in_(run_ids)).order_by(m.Position.position_id)):
                positions[p.run_id].append(_trade_row(p, r))
        summaries = {d.run_id: d for d in s.scalars(select(m.DailySummary).where(m.DailySummary.run_id.in_(run_ids)))} \
            if run_ids else {}
        notes: dict[str, list] = defaultdict(list)
        for n in s.scalars(select(m.JournalNote).where(m.JournalNote.algo_id == algo_id,
                                                       m.JournalNote.trade_date.between(start, end))
                           .order_by(m.JournalNote.id)):
            notes[n.trade_date.isoformat()].append(_note(n))

    # Live runs of one day add up; of several replays of a day only the newest counts.
    by_day: dict[str, list[m.AlgoRun]] = defaultdict(list)
    for r in sorted(runs, key=lambda r: r.run_id):
        by_day[r.trade_date.isoformat()].append(r)
    days = []
    for d, day_runs in by_day.items():
        if source != "live":
            day_runs = day_runs[-1:]
        traded = {r.run_id for r in day_runs if positions[r.run_id]}
        primary = _primary(day_runs, traded)
        sms = [summaries[r.run_id] for r in day_runs if r.run_id in summaries]
        nets = [x.net_pnl for x in sms if x.net_pnl is not None]
        day_trades = [t for r in day_runs for t in positions[r.run_id]]
        for n, t in enumerate(day_trades, 1):
            t["trade_no"] = n
        days.append(dict(
            date=d, weekday=WEEKDAYS[date.fromisoformat(d).weekday()], run_id=primary.run_id, status=primary.status,
            runs=[_run_info(r, len(positions[r.run_id])) for r in day_runs],
            skip_reason=None if day_trades else "; ".join(r.skip_reason for r in day_runs if r.skip_reason) or None,
            net=_r(sum(nets)) if nets else None,
            summary_notes="; ".join(x.notes for x in sms if x.notes) or None,
            checks=[c for r in day_runs for c in checks[r.run_id]],
            events=[e for r in day_runs for e in events[r.run_id]],
            trades=day_trades, notes=notes.pop(d, [])))
    for d, ns in notes.items():       # notes on days the algo did not run
        days.append(dict(date=d, weekday=WEEKDAYS[date.fromisoformat(d).weekday()], run_id=None, status=None,
                         skip_reason=None, net=None, summary_notes=None, checks=[], events=[], trades=[], notes=ns))
    days.sort(key=lambda x: x["date"], reverse=True)
    return dict(algo_id=algo_id, start=_iso(start), end=_iso(end), days=days)


def add_note(db: Database, algo_id: str, trade_date: date, text: str, tags: Iterable[str], now: datetime,
             position_id: int | None = None) -> dict[str, Any]:
    with db.session() as s:
        n = m.JournalNote(algo_id=algo_id, trade_date=trade_date, position_id=position_id, text=text.strip(),
                          tags=_clean_tags(tags), created_at=now, updated_at=now)
        s.add(n)
        s.flush()
        return _note(n)


def update_note(db: Database, note_id: int, text: str, tags: Iterable[str], now: datetime) -> dict[str, Any] | None:
    with db.session() as s:
        n = s.get(m.JournalNote, note_id)
        if n is None:
            return None
        n.text, n.tags, n.updated_at = text.strip(), _clean_tags(tags), now
        s.flush()
        return _note(n)


def delete_note(db: Database, note_id: int) -> bool:
    with db.session() as s:
        n = s.get(m.JournalNote, note_id)
        if n is None:
            return False
        s.delete(n)
        return True


def _clean_tags(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = str(t).strip().lower()[:30]
        if t and t not in out:
            out.append(t)
    return out[:10]


# ---------------------------------------------------------------- performance

def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [r["net"] for r in rows if r["net"] is not None]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]
    rois = [r["roi_pct"] for r in rows if r["roi_pct"] is not None]
    n = len(nets)
    return dict(
        trades=n, wins=len(wins), losses=len(losses),
        win_rate=_r(len(wins) / n * 100, 1) if n else None,
        net=_r(sum(nets)) if nets else 0.0,
        avg_win=_r(sum(wins) / len(wins)) if wins else None,
        avg_loss=_r(sum(losses) / len(losses)) if losses else None,
        expectancy=_r(sum(nets) / n) if n else None,
        profit_factor=_r(sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        best=_r(max(nets)) if nets else None, worst=_r(min(nets)) if nets else None,
        avg_roi_pct=_r(sum(rois) / len(rois), 3) if rois else None,
        charges=_r(sum(r["charges"] or 0 for r in rows)),
    )


def _group(rows: list[dict[str, Any]], key, order: list | None = None) -> list[dict[str, Any]]:
    buckets: dict[Any, list] = defaultdict(list)
    for r in rows:
        buckets[key(r)].append(r)
    keys = [k for k in (order or sorted(buckets, key=lambda k: (k is None, str(k)))) if k in buckets]
    return [dict(key=k if k is not None else "—", **_stats(buckets[k])) for k in keys]


def _entry_bucket(r: dict[str, Any]) -> str | None:
    if not r["entry_time"]:
        return None
    t = datetime.fromisoformat(r["entry_time"])
    minute = t.minute - t.minute % 15
    return f"{t.hour:02d}:{minute:02d}"


def performance(db: Database, algo_id: str, start: date, end: date, mode: str = "paper",
                source: str = "live", capital: float | None = None) -> dict[str, Any]:
    rows = [r for r in trades(db, algo_id, start, end, mode, source)["trades"] if r["net"] is not None]
    rows.sort(key=lambda r: (r["trade_date"], r["entry_time"] or ""))
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        by_day[r["trade_date"]] += r["net"]
    equity, drawdown, peak, cum = [], [], 0.0, 0.0
    for d in sorted(by_day):
        cum += by_day[d]
        peak = max(peak, cum)
        equity.append([d, _r(cum)])
        drawdown.append([d, _r(cum - peak)])
    stats = _stats(rows)
    stats.update(days=len(by_day), max_drawdown=_r(min((v for _, v in drawdown), default=0.0)),
                 capital=capital, return_pct=_r(stats["net"] / capital * 100, 2) if capital else None,
                 winning_days=sum(1 for v in by_day.values() if v > 0))
    return dict(
        algo_id=algo_id, start=_iso(start), end=_iso(end), stats=stats, equity=equity, drawdown=drawdown,
        daily=[[d, _r(v)] for d, v in sorted(by_day.items())],
        by_dte=_group(rows, lambda r: r["dte"]),
        by_weekday=_group(rows, lambda r: r["weekday"], WEEKDAYS),
        by_exit=_group(rows, lambda r: r["exit_reason"]),
        by_entry_time=_group(rows, _entry_bucket),
    )


def capital_of(algo_id: str) -> float | None:
    try:
        return registry.load(algo_id)[1].capital
    except Exception:  # noqa: BLE001
        return None


def spec_text(algo_id: str, root: Path) -> str | None:
    rel = DOCS.get(algo_id)
    path = root / rel if rel else None
    return path.read_text(encoding="utf-8") if path and path.exists() else None
