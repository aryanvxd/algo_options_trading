"""Local dashboard: FastAPI serving a JSON API and a static, build-free frontend.

It only reads the database (the same SQLite file the algos write), so it can be started and
stopped at any time without affecting a running algo. Run it with ``algo dashboard``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard import queries
from trading_core import __version__
from trading_core.clock import Clock, LiveClock
from trading_core.db.engine import Database

STATIC = Path(__file__).parent / "static"
Mode = Query("paper", pattern="^(paper|live|all)$")
Source = Query("live", pattern="^(live|replay)$")


def _day(value: str | None, clock: Clock) -> date:
    if not value:
        return clock.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"bad date {value!r}, expected YYYY-MM-DD") from None


def create_app(db: Database, clock: Clock | None = None) -> FastAPI:
    clock = clock or LiveClock()
    app = FastAPI(title="Algo dashboard", version=__version__, docs_url="/api/docs", redoc_url=None)

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # Local app: always revalidate (ETag) so edited JS/CSS shows up without a hard refresh.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/meta")
    def api_meta():
        return {**queries.meta(db, clock.today()), "version": __version__}

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
