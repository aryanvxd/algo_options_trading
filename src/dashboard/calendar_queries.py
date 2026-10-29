"""The Calendar page: stored market events, holidays, expiries and the days each algo trades."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select

from trading_core.clock import IST
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.db.migrate import holidays as load_holidays
from trading_core.market import events
from trading_core.market.calendar import MarketCalendar
from trading_core.strategy import registry

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _event(e: m.CalendarEvent) -> dict[str, Any]:
    return dict(id=e.id, source=e.source, category=e.category, ts=e.ts.isoformat(), all_day=e.all_day,
                country=e.country, title=e.title, impact=e.impact, forecast=e.forecast, previous=e.previous,
                actual=e.actual, symbol=e.symbol, detail=e.detail or {})


def _expiries(s, start: date, end: date) -> list[dict[str, Any]]:
    latest = s.scalar(select(func.max(m.Instrument.as_of_date)))
    if latest is None:
        return []
    listed = sorted(set(s.scalars(select(m.Instrument.expiry).where(
        m.Instrument.as_of_date == latest, m.Instrument.segment == "NFO-OPT", m.Instrument.name == "NIFTY",
        m.Instrument.expiry.is_not(None)))))
    by_month: dict[tuple[int, int], date] = {}
    for d in listed:
        by_month[(d.year, d.month)] = max(by_month.get((d.year, d.month), d), d)
    return [dict(date=d.isoformat(), weekday=WEEKDAYS[d.weekday()], underlying="NIFTY",
                 monthly=by_month[(d.year, d.month)] == d) for d in listed if start <= d <= end]


def _algo_days(cal: MarketCalendar, start: date, end: date, expiries: list[date]) -> dict[str, list[dict[str, Any]]]:
    """The days each algo would trade (by its config), before any of its entry checks."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    for algo_id in registry.available():
        try:
            _, cfg = registry.load(algo_id)
        except Exception:  # noqa: BLE001
            continue
        for d in days:
            if not cal.is_trading_day(d):
                continue
            role = None
            if getattr(cfg, "carry_overnight", False):
                gap = (cal.next_trading_day(d) - d).days
                if gap >= cfg.filters.min_gap_days:
                    role = "entry"
                elif (d - cal.prev_trading_day(d)).days >= cfg.filters.min_gap_days:
                    role = "exit"
            elif hasattr(cfg, "trade_weekdays"):
                role = "trade" if WEEKDAYS[d.weekday()] in cfg.trade_weekdays else None
            elif hasattr(cfg, "trade_window_days"):
                nxt = next((x for x in expiries if x >= d), None)
                if nxt is not None and cal.trading_days_until(d, nxt) < cfg.trade_window_days:
                    role = "trade"
            else:
                role = "trade"
            if role:
                out[d.isoformat()].append(dict(algo_id=algo_id, name=cfg.name, role=role))
    return out


def calendar(db: Database, start: date, end: date) -> dict[str, Any]:
    lo = datetime.combine(start, time(0), tzinfo=IST)
    hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=IST)
    rows = events.between(db, lo, hi)
    hol = load_holidays(db)
    cal = MarketCalendar(hol)
    with db.session() as s:
        holiday_rows = s.execute(select(m.MarketHoliday.date, m.MarketHoliday.description)
                                 .where(m.MarketHoliday.date.between(start, end)).order_by(m.MarketHoliday.date)).all()
        expiries = _expiries(s, start, end + timedelta(days=60))
    exp_dates = [date.fromisoformat(x["date"]) for x in expiries]
    return dict(
        start=start.isoformat(), end=end.isoformat(),
        economic=[_event(e) for e in rows if e.category == "economic"],
        results=[_event(e) for e in rows if e.category == "results"],
        holidays=[dict(date=d.isoformat(), weekday=WEEKDAYS[d.weekday()], description=desc) for d, desc in holiday_rows],
        expiries=[x for x in expiries if x["date"] <= end.isoformat()],
        algo_days=_algo_days(cal, start, end, exp_dates),
        sources=events.sources(db),
    )
