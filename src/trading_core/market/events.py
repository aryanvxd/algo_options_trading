"""Market calendar: scheduled economic releases and company results, for the dashboard's Calendar
page and the algos' event checks.

Sources (stored in ``calendar_events``, so the page works offline and keeps past weeks):
  forexfactory  global economic events for the current week (impact, forecast, previous; no
                actual, no India): nfs.faireconomy.media/ff_calendar_thisweek.json
  nse_results   upcoming board meetings for results, from NSE's event calendar
  india         RBI policy, CPI, GDP, IIP, WPI, Budget from config/calendar/*.yaml (kept by hand)

``refresh`` fetches each source at most once a day unless forced, and never raises: a failed
source is recorded and the others still load.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date, datetime, time, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import delete, select

from trading_core.clock import IST
from trading_core.config import PlatformConfig, load_universe, read_yaml
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.market.calendar import MarketCalendar

log = logging.getLogger(__name__)

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NSE_HOME = "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"
NSE_EVENTS = "https://www.nseindia.com/api/event-calendar"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
TIMEOUT_S = 15
IMPACT_RANK = {"High": 3, "Medium": 2, "Low": 1, "Holiday": 0}
# Forex Factory labels events by currency; show the country (EUR -> EU).
FF_COUNTRY = {"USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP", "CNY": "CN", "AUD": "AU", "CAD": "CA",
              "NZD": "NZ", "CHF": "CH", "INR": "IN"}
SHIFT_ON_HOLIDAY = {"mospi", "dpiit"}         # "in case of holidays, reports release on next working day"

Fetcher = Callable[[], Any]


# ---------------------------------------------------------------- parsing (pure)

def _blank(v: Any) -> str | None:
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def parse_forexfactory(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for e in items:
        try:
            ts = datetime.fromisoformat(e["date"]).astimezone(IST)
        except (KeyError, ValueError):
            continue
        country = FF_COUNTRY.get(e.get("country", ""), e.get("country"))
        title = (e.get("title") or "").strip()
        rows.append(dict(
            event_key=f"{country}|{title}|{ts:%Y-%m-%dT%H:%M}", category="economic", ts=ts,
            all_day=e.get("impact") == "Holiday", country=country, title=title,
            impact=e.get("impact") or None, forecast=_blank(e.get("forecast")), previous=_blank(e.get("previous")),
            actual=_blank(e.get("actual")), symbol=None, detail={"currency": e.get("country")}))
    return rows


def parse_nse_results(items: Iterable[dict[str, Any]], index_members: set[str]) -> list[dict[str, Any]]:
    """Board meetings whose purpose includes financial results. NIFTY 50 members are Medium impact."""
    rows = []
    for e in items:
        purpose = (e.get("purpose") or "").strip()
        if "result" not in purpose.lower():
            continue
        try:
            d = datetime.strptime(e["date"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        sym = (e.get("symbol") or "").strip()
        member = sym in index_members
        rows.append(dict(
            event_key=f"{sym}|{d.isoformat()}", category="results", ts=datetime.combine(d, time(0), tzinfo=IST),
            all_day=True, country="IN", title=f"{e.get('company') or sym} results", impact="Medium" if member else "Low",
            forecast=None, previous=None, actual=None, symbol=sym,
            detail=dict(company=e.get("company"), purpose=purpose, description=e.get("bm_desc"), nifty50=member)))
    return rows


def load_india(paths: Iterable[Path], calendar: MarketCalendar) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        data = read_yaml(path)
        defaults = data.get("defaults") or {}
        for e in data.get("events") or []:
            planned = e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"]))
            d = planned
            if (e.get("source") in SHIFT_ON_HOLIDAY) and not calendar.is_trading_day(d):
                d = calendar.next_trading_day(d)
            t = datetime.strptime(str(e["time"]), "%H:%M").time() if e.get("time") else time(0)
            detail = {k: e[k] for k in ("source", "note") if e.get(k)}
            if d != planned:
                detail["planned"] = planned.isoformat()
            title = e["title"]
            rows.append(dict(
                event_key=f"{title}|{planned.isoformat()}", category="economic",
                ts=datetime.combine(d, t, tzinfo=IST), all_day=not e.get("time"),
                country=e.get("country", defaults.get("country", "IN")), title=title, impact=e.get("impact"),
                forecast=_blank(str(e["forecast"])) if e.get("forecast") is not None else None,
                previous=_blank(str(e["previous"])) if e.get("previous") is not None else None,
                actual=_blank(str(e["actual"])) if e.get("actual") is not None else None,
                symbol=None, detail=detail or None))
    return rows


# ---------------------------------------------------------------- fetching (network)

def fetch_forexfactory() -> list[dict[str, Any]]:
    req = urllib.request.Request(FF_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def fetch_nse_events() -> list[dict[str, Any]]:
    """NSE serves its API only after a page visit has set cookies; one request each, at most daily."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    headers = {"User-Agent": UA, "Accept": "application/json,text/html", "Accept-Language": "en-US,en;q=0.9",
               "Referer": "https://www.nseindia.com/"}
    opener.open(urllib.request.Request(NSE_HOME, headers=headers), timeout=TIMEOUT_S).read()
    with opener.open(urllib.request.Request(NSE_EVENTS, headers=headers), timeout=TIMEOUT_S) as r:
        data = json.loads(r.read())
    return data if isinstance(data, list) else data.get("data", [])


# ---------------------------------------------------------------- storing

def _store(db: Database, source: str, rows: list[dict[str, Any]], now: datetime, replace: bool = False) -> int:
    rows = list({r["event_key"]: r for r in rows}.values())     # a feed can list one event twice: keep the last
    with db.session() as s:
        if replace:
            s.execute(delete(m.CalendarEvent).where(m.CalendarEvent.source == source))
            existing = {}
        else:
            keys = [r["event_key"] for r in rows]
            existing = {e.event_key: e for e in s.scalars(select(m.CalendarEvent).where(
                m.CalendarEvent.source == source, m.CalendarEvent.event_key.in_(keys)))} if keys else {}
        for r in rows:
            row = existing.get(r["event_key"])
            if row is None:
                s.add(m.CalendarEvent(source=source, fetched_at=now, **r))
            else:
                for k, v in r.items():
                    setattr(row, k, v)
                row.fetched_at = now
    return len(rows)


def _status(db: Database, source: str, now: datetime, ok: bool, rows: int, note: str | None) -> None:
    with db.session() as s:
        row = s.get(m.CalendarSource, source)
        if row is None:
            s.add(m.CalendarSource(source=source, fetched_at=now, status="OK" if ok else "FAILED", rows=rows, note=note))
        else:
            row.fetched_at, row.status, row.rows, row.note = now, "OK" if ok else "FAILED", rows, note


def sources(db: Database) -> dict[str, dict[str, Any]]:
    with db.session() as s:
        return {r.source: dict(fetched_at=r.fetched_at.isoformat(), status=r.status, rows=r.rows, note=r.note)
                for r in s.scalars(select(m.CalendarSource))}


def refresh(db: Database, platform: PlatformConfig, now: datetime, calendar: MarketCalendar, force: bool = False,
            fetch_ff: Fetcher = fetch_forexfactory, fetch_nse: Fetcher = fetch_nse_events) -> dict[str, str]:
    """Load the India list and fetch the two web sources (each at most once a day unless ``force``).
    Returns {source: outcome}. Never raises."""
    out: dict[str, str] = {}
    done_today = {k for k, v in sources(db).items()
                  if v["status"] == "OK" and v["fetched_at"][:10] == now.date().isoformat()}

    try:
        files = sorted((platform.config_dir / "calendar").glob("*.yaml"))
        n = _store(db, "india", load_india(files, calendar), now, replace=True)
        _status(db, "india", now, True, n, f"{len(files)} file(s)")
        out["india"] = f"{n} events"
    except Exception as e:  # noqa: BLE001
        log.exception("india calendar failed")
        _status(db, "india", now, False, 0, repr(e)[:300])
        out["india"] = f"failed: {e}"

    for source, fetch, parse in (
            ("forexfactory", fetch_ff, parse_forexfactory),
            ("nse_results", fetch_nse, lambda items: parse_nse_results(items, _index_members(platform)))):
        if source in done_today and not force:
            out[source] = "already fetched today"
            continue
        try:
            n = _store(db, source, parse(fetch()), now)
            _status(db, source, now, True, n, None)
            out[source] = f"{n} events"
        except Exception as e:  # noqa: BLE001 - one source down must not stop the rest (or an algo)
            log.warning("calendar source %s failed: %s", source, e)
            _status(db, source, now, False, 0, repr(e)[:300])
            out[source] = f"failed: {e}"
    return out


def _index_members(platform: PlatformConfig) -> set[str]:
    try:
        return {k.split(":", 1)[-1] for k in load_universe(platform, "nifty50")}
    except Exception:  # noqa: BLE001
        return set()


# ---------------------------------------------------------------- querying

def between(db: Database, start: datetime, end: datetime, *, category: str | None = None,
            min_impact: str | None = None, countries: Iterable[str] | None = None,
            symbols: Iterable[str] | None = None) -> list[m.CalendarEvent]:
    """Stored events with ``start <= ts < end`` (IST), filtered; oldest first."""
    q = select(m.CalendarEvent).where(m.CalendarEvent.ts >= start, m.CalendarEvent.ts < end)
    if category:
        q = q.where(m.CalendarEvent.category == category)
    if countries:
        q = q.where(m.CalendarEvent.country.in_(list(countries)))
    if symbols:
        q = q.where(m.CalendarEvent.symbol.in_(list(symbols)))
    with db.session() as s:
        rows = list(s.scalars(q.order_by(m.CalendarEvent.ts, m.CalendarEvent.id)))
    if min_impact:
        need = IMPACT_RANK[min_impact]
        rows = [r for r in rows if IMPACT_RANK.get(r.impact or "", 0) >= need]
    return rows


def covered(db: Database, now: datetime, max_age_days: int = 3) -> dict[str, bool]:
    """Whether each source has been fetched successfully recently (an empty calendar is not proof of no events)."""
    cutoff = (now - timedelta(days=max_age_days)).isoformat()
    return {k: v["status"] == "OK" and v["fetched_at"] >= cutoff for k, v in sources(db).items()}
