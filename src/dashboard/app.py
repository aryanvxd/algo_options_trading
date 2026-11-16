"""Local dashboard: FastAPI serving a JSON API and a static, build-free frontend.

It reads the database the algos write (the same SQLite file), so it can be started and stopped at
any time without affecting a running algo. It writes only your journal notes (a table no algo
reads) and, when you press Start / Stop, launches `algo run` in its own window or sends the same
KILL as `algo kill`. Run it with ``algo dashboard``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sqlalchemy.exc import OperationalError

from dashboard import backtests as bt
from dashboard import calendar_queries, control, data_coverage, queries, swing_jobs, swing_queries, swing_workflow
from dashboard import strategy_queries as sq
from swing import fundamentals as swing_fundamentals
from swing import watchlists as swing_watchlists
from trading_core import __version__
from trading_core.clock import Clock, LiveClock
from trading_core.config import PlatformConfig, load_platform_config
from trading_core.db.engine import Database

STATIC = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
Mode = Query("paper", pattern="^(paper|live|all)$")
Source = Query("live", pattern="^(live|replay)$")


def _day(value: str | None, clock: Clock) -> date:
    if not value:
        return clock.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"bad date {value!r}, expected YYYY-MM-DD") from None


class NoteIn(BaseModel):
    algo_id: str | None = None
    trade_date: date | None = None
    position_id: int | None = None
    text: str = Field("", max_length=5000)
    tags: list[str] = Field(default_factory=list)


class BacktestIn(BaseModel):
    algo_id: str
    start: str
    end: str
    name: str = Field(..., max_length=80)
    option_prices: str = "real"
    overrides: dict = Field(default_factory=dict)
    not_before: str | None = None          # ISO time: wait until then (e.g. after the close)
    days: int | None = None                # trading days in the range, for "day N of M" while queued
    sweep: dict | None = None              # {name, axes, point, index, total}: set by the Parameter sweep page
    dry_run: bool = False                  # only check it (nothing is written)


WRITE_HEADER = "X-Algo-Desk"
LOCAL_ORIGINS = ("http://127.0.0.1", "http://localhost")


class DownloadIn(BaseModel):
    start: str | None = Field(None, alias="from")     # default: the last day stored (stored days are skipped)


class ScanIn(BaseModel):
    refresh: bool = True                     # False: the stored candles (no Kite login needed)
    confirm_market_hours: bool = False


class SettingsIn(BaseModel):
    overrides: dict = Field(default_factory=dict)


class RenameIn(BaseModel):
    name: str = Field(..., max_length=60)


class WatchItemIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=40)
    note: str | None = Field(None, max_length=500)


class FundamentalsIn(BaseModel):
    as_of: str | None = None
    rows: list[dict] = Field(default_factory=list, max_length=5000)   # [{symbol, metrics: {column: value}}]


def create_app(db: Database, clock: Clock | None = None, platform: PlatformConfig | None = None,
               launcher: control.Launcher | None = None, backtest_queue: bt.Queue | None = None,
               data_jobs: Any = None, swing_scan_jobs: Any = None) -> FastAPI:
    clock = clock or LiveClock()
    platform = platform or load_platform_config()
    launcher = launcher or control.launch_console
    app = FastAPI(title="Algo dashboard", version=__version__, docs_url="/api/docs", redoc_url=None)
    # Queued backtests start only once `algo dashboard` calls queue.start(); tests drive queue.tick().
    queue = app.state.backtest_queue = backtest_queue or bt.Queue(platform)
    jobs = data_jobs or data_coverage.DataJobs(platform)
    swing_scan = swing_scan_jobs or swing_jobs.SwingJobs(platform)

    @app.middleware("http")
    async def same_page_writes_only(request, call_next):
        # Anything that writes or starts a process must come from this dashboard's own page: a
        # custom header forces a CORS preflight, which another site open in the browser can't pass.
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if request.headers.get(WRITE_HEADER) != "1" or (origin and not origin.startswith(LOCAL_ORIGINS)):
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "writes are only accepted from the dashboard page"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # Local app: always revalidate (ETag) so edited JS/CSS shows up without a hard refresh.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/meta")
    def api_meta():
        return {**queries.meta(db, clock.today()), "version": __version__,
                "strategies": sq.strategy_list(db)}

    def _range(start: str | None, end: str | None, days: int) -> tuple[date, date]:
        e = _day(end, clock)
        return (_day(start, clock) if start else e - timedelta(days=days - 1)), e

    @app.get("/api/strategies/{algo_id}/day")
    def api_strategy_day(algo_id: str, day: str | None = Query(None, alias="date"), mode: str = Mode,
                         source: str = Source):
        return sq.strategy_day(db, _known(algo_id), _day(day, clock), mode, source)

    @app.get("/api/strategies/{algo_id}/trades")
    def api_trades(algo_id: str, start: str | None = None, end: str | None = None,
                   days: int = Query(90, ge=1, le=3660), mode: str = Mode, source: str = Source):
        a, b = _range(start, end, days)
        return sq.trades(db, _known(algo_id), a, b, mode, source)

    @app.get("/api/strategies/{algo_id}/journal")
    def api_journal(algo_id: str, start: str | None = None, end: str | None = None,
                    days: int = Query(30, ge=1, le=3660), mode: str = Mode, source: str = Source):
        a, b = _range(start, end, days)
        return sq.journal(db, _known(algo_id), a, b, mode, source)

    @app.get("/api/strategies/{algo_id}/performance")
    def api_performance(algo_id: str, start: str | None = None, end: str | None = None,
                        days: int = Query(365, ge=1, le=3660), mode: str = Mode, source: str = Source):
        a, b = _range(start, end, days)
        return sq.performance(db, _known(algo_id), a, b, mode, source, sq.capital_of(algo_id))

    @app.get("/api/strategies/{algo_id}/rules")
    def api_rules(algo_id: str):
        return {**sq.rules(db, _known(algo_id)), "spec_text": sq.spec_text(algo_id, PROJECT_ROOT)}

    @app.get("/api/positions/{position_id}")
    def api_position(position_id: int):
        out = sq.trade_detail(db, position_id)
        if out is None:
            raise HTTPException(404, "no such position")
        return out

    @app.get("/api/positions/{position_id}/payoff")
    def api_payoff(position_id: int):
        out = sq.position_payoff(db, position_id, clock.now())
        if out is None:
            raise HTTPException(404, "no such position")
        return out

    # Journal notes: the dashboard's only writes (table journal_notes, never read by an algo).
    @app.post("/api/notes", status_code=201)
    def api_add_note(note: NoteIn):
        if not note.algo_id or not note.trade_date or not note.text.strip():
            raise HTTPException(400, "algo_id, trade_date and text are required")
        return sq.add_note(db, _known(note.algo_id), note.trade_date, note.text, note.tags, clock.now(),
                           note.position_id)

    @app.put("/api/notes/{note_id}")
    def api_update_note(note_id: int, note: NoteIn):
        if not note.text.strip():
            raise HTTPException(400, "text is required")
        out = sq.update_note(db, note_id, note.text, note.tags, clock.now())
        if out is None:
            raise HTTPException(404, "no such note")
        return out

    @app.delete("/api/notes/{note_id}", status_code=204)
    def api_delete_note(note_id: int):
        if not sq.delete_note(db, note_id):
            raise HTTPException(404, "no such note")

    # Run control: Start opens `algo run` in its own window; Stop sends KILL (square off, then stop).
    @app.get("/api/control")
    def api_control():
        return control.status(platform, db, clock.now())

    @app.post("/api/strategies/{algo_id}/start")
    def api_start(algo_id: str):
        try:
            return control.start(platform, db, _known(algo_id), clock.now(), launcher)
        except control.ControlError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/strategies/{algo_id}/stop")
    def api_stop(algo_id: str):
        try:
            return control.stop(platform, db, _known(algo_id), clock.now())
        except control.ControlError as e:
            raise HTTPException(409, str(e)) from None

    # Market calendar: stored events; Refresh refetches the web sources now.
    @app.get("/api/calendar")
    def api_calendar(start: str | None = None, end: str | None = None, days: int = Query(21, ge=1, le=400)):
        a = _day(start, clock)
        b = _day(end, clock) if end else a + timedelta(days=days - 1)
        return calendar_queries.calendar(db, a, b)

    @app.post("/api/calendar/refresh")
    def api_calendar_refresh():
        from trading_core.db.migrate import holidays
        from trading_core.market.calendar import MarketCalendar
        from trading_core.market.events import refresh

        return refresh(db, platform, clock.now(), MarketCalendar(holidays(db)), force=True)

    @app.post("/api/kite/login")
    def api_login():
        return control.login(platform, launcher)

    # Swing review: the last `algo swing scan`, and one stock's chart and zones. Read-only.
    @app.get("/api/swing/overview")
    def api_swing_overview():
        return swing_queries.overview(platform)

    @app.get("/api/swing/holdings")
    def api_swing_holdings():
        return swing_queries.holdings(platform)

    @app.get("/api/swing/orders")
    def api_swing_orders():
        return swing_queries.orders(swing_queries.kite_client(db, platform, clock), clock.now())

    @app.get("/api/swing/index/{name}/stocks")
    def api_swing_index_stocks(name: str):
        out = swing_queries.index_stocks(platform, name, swing_queries.kite_client(db, platform, clock), clock.now())
        if out is None:
            raise HTTPException(404, f"{name!r} is not in the last scan: run `algo swing scan`")
        return out

    @app.get("/api/swing/stock/{symbol}")
    def api_swing_stock(symbol: str):
        out = swing_queries.stock(db, platform, symbol, clock.today())
        if out is None:
            raise HTTPException(404, f"no candles for {symbol!r}: run `algo swing scan`")
        return out

    # Swing workflow: the weekend scan from the dashboard, picks, watchlists, journal, settings and
    # fundamentals (dashboard/swing_workflow.py). Nothing here writes to Kite.
    @app.get("/api/swing/scan")
    def api_swing_scan_status():
        return swing_scan.status()

    @app.post("/api/swing/scan", status_code=201)
    def api_swing_scan(body: ScanIn | None = None):
        body = body or ScanIn()
        kite_ok = control.status(platform, db, clock.now())["kite"]["logged_in"] if body.refresh else True
        try:
            return swing_scan.start(body.refresh, kite_ok, clock.now(), body.confirm_market_hours)
        except swing_jobs.ScanError as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/api/swing/picks")
    def api_swing_picks():
        return swing_workflow.picks(platform)

    @app.get("/api/swing/settings")
    def api_swing_settings():
        return _swing_db(lambda: swing_workflow.settings(platform, db))

    @app.put("/api/swing/settings")
    def api_swing_settings_save(body: SettingsIn):
        try:
            return swing_workflow.save_settings(platform, body.overrides)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/swing/settings")
    def api_swing_settings_reset():
        return swing_workflow.save_settings(platform, {})

    @app.get("/api/swing/watchlists")
    def api_watchlists():
        return _swing_db(lambda: {"lists": swing_watchlists.lists(db, clock.now())})

    @app.get("/api/swing/watchlists/{list_id}")
    def api_watchlist(list_id: int):
        client = swing_queries.kite_client(db, platform, clock)
        return _swing_db(lambda: swing_workflow.watchlist(platform, db, list_id, client, clock.now()))

    @app.put("/api/swing/watchlists/{list_id}")
    def api_watchlist_rename(list_id: int, body: RenameIn):
        return _swing_db(lambda: swing_watchlists.rename(db, list_id, body.name))

    @app.post("/api/swing/watchlists/{list_id}/items", status_code=201)
    def api_watchlist_add(list_id: int, body: WatchItemIn):
        added = _swing_db(lambda: swing_workflow.add_to_watchlist(platform, db, list_id, body.symbol, body.note,
                                                                  clock.now()))
        return {"added": added}

    @app.delete("/api/swing/watchlists/{list_id}/items/{symbol}")
    def api_watchlist_remove(list_id: int, symbol: str):
        return {"removed": _swing_db(lambda: swing_watchlists.remove(db, list_id, symbol))}

    @app.get("/api/swing/journal")
    def api_swing_journal():
        return _swing_db(lambda: swing_workflow.journal(db))

    @app.post("/api/swing/journal/update")
    def api_swing_journal_update():
        return _swing_db(lambda: swing_workflow.update_journal(platform, db, clock.now()))

    @app.get("/api/swing/fundamentals")
    def api_swing_fundamentals():
        return _swing_db(lambda: swing_fundamentals.summary(db))

    @app.post("/api/swing/fundamentals", status_code=201)
    def api_swing_fundamentals_upload(body: FundamentalsIn):
        try:
            return _swing_db(lambda: swing_workflow.upload_fundamentals(db, body.rows, body.as_of, clock.now()))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/swing/fundamentals")
    def api_swing_fundamentals_clear():
        return {"removed": _swing_db(lambda: swing_fundamentals.clear(db))}

    def _swing_db(fn):
        """Swing's own tables: a clear message before `algo db upgrade`, 404/400 for bad requests."""
        try:
            return fn()
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except swing_watchlists.WatchlistError as e:
            raise HTTPException(400, str(e)) from None
        except OperationalError as e:
            if "no such table" in str(e):
                raise HTTPException(503, "the Swing tables are missing: run `algo db upgrade`") from None
            raise

    # Backtests (docs/design/backtest.md, section 6): the folders in data/backtests. A run's trades,
    # days and trade detail are the strategy queries on its own backtest.db (paper, replay).
    def _bt_run(name: str, start: str | None = None, end: str | None = None):
        rng = bt.run_range(platform, name)
        db_ = bt.run_db(platform, name)
        if rng is None:
            raise HTTPException(404, f"no backtest called {name!r}")
        if db_ is None:
            raise HTTPException(409, f"{name} has no results yet")
        algo_id, a, b = rng
        return db_, algo_id, (_day(start, clock) if start else a), (_day(end, clock) if end else b)

    @app.get("/api/backtests")
    def api_backtests():
        return {"runs": bt.list_runs(platform, clock.now())}

    @app.get("/api/backtests/{name}")
    def api_backtest(name: str):
        out = bt.detail(platform, name, clock.now())
        if out is None:
            raise HTTPException(404, f"no backtest called {name!r}")
        return out

    @app.get("/api/backtests/{name}/days")
    def api_backtest_days(name: str):
        out = bt.days(platform, name)
        if out is None:
            raise HTTPException(404, f"no backtest called {name!r}")
        return out

    @app.get("/api/backtests/{name}/trades")
    def api_backtest_trades(name: str, start: str | None = None, end: str | None = None):
        db_, algo_id, a, b = _bt_run(name, start, end)
        return sq.trades(db_, algo_id, a, b, "paper", "replay")

    @app.get("/api/backtests/{name}/journal")
    def api_backtest_journal(name: str, start: str | None = None, end: str | None = None):
        db_, algo_id, a, b = _bt_run(name, start, end)
        return sq.journal(db_, algo_id, a, b, "paper", "replay")

    @app.get("/api/backtests/{name}/performance")
    def api_backtest_performance(name: str):
        db_, algo_id, a, b = _bt_run(name)
        return sq.performance(db_, algo_id, a, b, "paper", "replay", sq.capital_of(algo_id))

    @app.get("/api/backtests/{name}/positions/{position_id}")
    def api_backtest_position(name: str, position_id: int):
        out = sq.trade_detail(_bt_run(name)[0], position_id)
        if out is None:
            raise HTTPException(404, "no such position")
        return out

    @app.get("/api/backtests/{name}/positions/{position_id}/payoff")
    def api_backtest_payoff(name: str, position_id: int):
        out = sq.position_payoff(_bt_run(name)[0], position_id, clock.now())
        if out is None:
            raise HTTPException(404, "no such position")
        return out

    @app.get("/api/backtests/{name}/log")
    def api_backtest_log(name: str, lines: int = Query(200, ge=1, le=5000)):
        out = bt.log_tail(platform, name, lines)
        if out is None:
            raise HTTPException(404, f"no backtest called {name!r}")
        return out

    @app.post("/api/backtests", status_code=201)
    def api_backtest_new(req: BacktestIn):
        try:
            body = req.model_dump(exclude={"dry_run"})
            row = bt.submit(platform, body, clock.now(), clock.today(), dry_run=req.dry_run)
        except bt.BacktestError as e:
            raise HTTPException(409 if "already exists" in str(e) else 400, str(e)) from None
        if queue.running and not req.dry_run:
            queue.tick(clock.now())                 # start at once when nothing else is running
        return row

    @app.post("/api/backtests/{name}/stop")
    def api_backtest_stop(name: str):
        if bt.folder(platform, name) is None:
            raise HTTPException(404, f"no backtest called {name!r}")
        try:
            return queue.stop(name, clock.now())
        except bt.BacktestError as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/api/algos/{algo_id}/config")
    def api_algo_config(algo_id: str):
        from trading_core.strategy import registry

        if algo_id not in registry.available():
            raise HTTPException(404, f"unknown strategy {algo_id!r}")
        return bt.algo_config(algo_id)

    @app.get("/api/data/days")
    def api_data_days():
        return {**data_coverage.days(platform), "now": clock.now().isoformat(timespec="seconds")}

    # Market data coverage and downloads (`algo data ...` in the background, one at a time).
    @app.get("/api/data/coverage")
    def api_data_coverage():
        return data_coverage.coverage(platform, db, clock.now())

    @app.get("/api/data/jobs")
    def api_data_jobs():
        return jobs.status()

    @app.post("/api/data/{dataset}", status_code=201)
    def api_data_download(dataset: str, body: DownloadIn | None = None):
        if dataset not in data_coverage.DOWNLOADS:
            raise HTTPException(404, f"unknown dataset {dataset!r}")
        needs_kite = data_coverage.DOWNLOADS[dataset]["kite"]
        kite_ok = control.status(platform, db, clock.now())["kite"]["logged_in"] if needs_kite else True
        try:
            return jobs.start(db, dataset, body.start if body else None, kite_ok, clock.now())
        except data_coverage.DataError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/backtests/{name}/trash")
    def api_backtest_trash(name: str):
        if bt.folder(platform, name) is None:
            raise HTTPException(404, f"no backtest called {name!r}")
        try:
            return bt.trash(platform, name, clock.now())
        except bt.BacktestError as e:
            raise HTTPException(409, str(e)) from None

    def _known(algo_id: str) -> str:
        if algo_id not in {a["algo_id"] for a in sq.strategy_list(db)}:
            raise HTTPException(404, f"unknown strategy {algo_id!r}")
        return algo_id

    @app.get("/api/overview")
    def api_overview(day: str | None = Query(None, alias="date"), mode: str = Mode, source: str = Source):
        return queries.overview(db, _day(day, clock), mode, source)

    @app.get("/api/pnl-history")
    def api_pnl_history(end: str | None = None, days: int = Query(30, ge=1, le=366), mode: str = Mode,
                        source: str = Source):
        return queries.pnl_history(db, _day(end, clock), days, mode, source)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
