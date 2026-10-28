"""Local dashboard: FastAPI serving a JSON API and a static, build-free frontend.

It reads the database the algos write (the same SQLite file), so it can be started and stopped at
any time without affecting a running algo. It writes only your journal notes (a table no algo
reads) and, when you press Start / Stop, launches `algo run` in its own window or sends the same
KILL as `algo kill`. Run it with ``algo dashboard``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dashboard import control, queries
from dashboard import strategy_queries as sq
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


WRITE_HEADER = "X-Algo-Desk"
LOCAL_ORIGINS = ("http://127.0.0.1", "http://localhost")


def create_app(db: Database, clock: Clock | None = None, platform: PlatformConfig | None = None,
               launcher: control.Launcher | None = None) -> FastAPI:
    clock = clock or LiveClock()
    platform = platform or load_platform_config()
    launcher = launcher or control.launch_console
    app = FastAPI(title="Algo dashboard", version=__version__, docs_url="/api/docs", redoc_url=None)

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

    @app.post("/api/kite/login")
    def api_login():
        return control.login(platform, launcher)

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
