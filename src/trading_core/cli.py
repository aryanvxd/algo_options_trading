"""Command line: ``algo <command>``.

  algo db upgrade                      create/upgrade the database and load holidays
  algo login                           daily Kite login (once each morning)
  algo logout                          end today's Kite session (after the run)
  algo quote                           smoke test: print NIFTY 50 and India VIX
  algo instruments                     refresh today's instrument snapshot
  algo list                            list available algos
  algo run --algo iron_condor          run today's session (paper)
  algo run --algo iron_condor --replay 2026-10-23    replay a recorded day
  algo kill --algo iron_condor         ask a running algo to square off and stop
  algo report --algo iron_condor       recent daily summaries
  algo dashboard                       local web dashboard on http://127.0.0.1:8050
  algo data status                     what is in the Parquet market-data store
  algo data index --from 2021-01-01    NIFTY 50 + India VIX 1-min history from Kite
  algo data constituents --from ...    NIFTY 50 constituents daily bars from Kite (breadth filter)
  algo data bhavcopy --from 2021-01-01 NSE end-of-day option chains (free) with IV/greeks
  algo data export-recorder            move recorded live ticks to Parquet (runs after each session)
  algo data query "select ..."         run SQL over the store with DuckDB
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from trading_core.clock import LiveClock
from trading_core.config import PlatformConfig, load_platform_config, load_secrets
from trading_core.db.engine import Database
from trading_core.logging_setup import setup_logging


def _db(platform: PlatformConfig) -> Database:
    return Database(platform.db_url)


def cmd_db(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.db import migrate

    if args.action == "upgrade":
        migrate.upgrade(platform.db_url)
        n = migrate.seed_holidays(_db(platform), platform)
        print(f"Database ready at {platform.db_url} ({n} holidays loaded).")
    return 0


def cmd_login(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.kite.auth import login

    login(_db(platform), platform.kite, load_secrets(platform.root), LiveClock(), open_browser=not args.no_browser)
    print("Logged in.")
    return 0


def cmd_logout(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.kite.auth import logout

    n = logout(_db(platform), load_secrets(platform.root), LiveClock())
    print("Logged out." if n else "No active Kite session.")
    return 0


def cmd_quote(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.kite.client import KiteClient

    client = KiteClient.from_db(_db(platform), platform.kite, load_secrets(platform.root), LiveClock())
    for key, q in client.quote(["NSE:NIFTY 50", "NSE:INDIA VIX"]).items():
        print(f"{key:<16} {q.get('last_price')}  (prev close {(q.get('ohlc') or {}).get('close')})")
    return 0


def cmd_instruments(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.kite.client import KiteClient
    from trading_core.kite.instruments import InstrumentStore

    clock = LiveClock()
    db = _db(platform)
    client = KiteClient.from_db(db, platform.kite, load_secrets(platform.root), clock)
    n = InstrumentStore(db, platform.instruments).refresh_from_kite(client, clock.today())
    print(f"Stored {n} instruments for {clock.today()}.")
    return 0


def cmd_list(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.strategy import registry

    for algo_id in registry.available():
        _, cfg = registry.load(algo_id)
        print(f"{algo_id:<20} {cfg.name}  (v{cfg.version}, {cfg.mode})")
    return 0


def cmd_run(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.strategy.bootstrap import AlreadyRan, AlreadyRunning, run_algo

    replay = date.fromisoformat(args.replay) if args.replay else None
    trade_date = replay or LiveClock().today()
    log_path = setup_logging(platform.log_level, platform.logs_dir, args.algo, trade_date,
                             suffix="_replay" if replay else "")
    print(f"Logging to {log_path}")
    try:
        run_id = run_algo(platform, _db(platform), args.algo, mode=args.mode, replay_date=replay,
                          force_new=args.force_new)
    except (AlreadyRan, AlreadyRunning) as e:
        print(e)
        return 1
    print(f"Run {run_id} finished.")
    return 0


def cmd_kill(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.db.repo import Journal

    Journal(_db(platform)).issue_command(args.algo, args.command.upper(), LiveClock().now())
    print(f"{args.command.upper()} sent to {args.algo}; the running process picks it up within a second.")
    return 0


def cmd_report(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.db.repo import Journal

    rows = Journal(_db(platform)).daily_summaries(args.algo, args.limit)
    if not rows:
        print("No runs yet.")
        return 0
    print(f"{'date':<11}{'run':>5} {'src':<7}{'traded':<7}{'credit':>8}{'exit':>18}{'gross':>10}"
          f"{'charges':>9}{'net':>10}")
    for r in rows:
        print(f"{r.trade_date.isoformat():<11}{r.run_id:>5} {r.source:<7}{'yes' if r.traded else 'no':<7}"
              f"{_f(r.entry_credit_pts):>8}{(r.exit_reason or '-'):>18}{_f(r.gross_pnl):>10}"
              f"{_f(r.charges):>9}{_f(r.net_pnl):>10}")
        if r.notes:
            print(f"{'':<11}      {r.notes}")
    return 0


def cmd_dashboard(args: argparse.Namespace, platform: PlatformConfig) -> int:
    import uvicorn

    from dashboard.app import create_app

    port = args.port or platform.dashboard_port
    url = f"http://127.0.0.1:{port}"
    print(f"Dashboard on {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, [url]).start()
    # Bound to localhost only: the dashboard has no login.
    uvicorn.run(create_app(_db(platform), platform=platform), host="127.0.0.1", port=port, log_level="warning")
    return 0


def cmd_data(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from trading_core.marketdata.manifest import Manifest
    from trading_core.marketdata.store import MarketStore

    db = _db(platform)
    store = MarketStore(platform.marketdata.dir)
    manifest = Manifest(db)
    md = platform.marketdata
    today = LiveClock().today()
    start = date.fromisoformat(args.start) if getattr(args, "start", None) else None
    end = date.fromisoformat(args.end) if getattr(args, "end", None) else today

    if args.action == "status":
        rows = manifest.summary()
        if not rows:
            print(f"Store is empty ({md.dir}).")
            return 0
        print(f"{'dataset':<15}{'key':<12}{'source':<10}{'first':<12}{'last':<12}{'days':>6}{'rows':>12}{'bad':>5}")
        for r in rows:
            print(f"{r['dataset']:<15}{r['key']:<12}{r['source']:<10}{str(r['first']):<12}{str(r['last']):<12}"
                  f"{r['days']:>6}{r['rows']:>12,}{r['problems']:>5}")
        return 0

    if args.action == "export-recorder":
        from trading_core.marketdata.sources.recorder import export_day, prune_sqlite_ticks

        day = date.fromisoformat(args.date) if args.date else today
        print(f"{day}: {export_day(db, store, manifest, day)}")
        if args.prune:
            print(f"pruned {prune_sqlite_ticks(db, manifest, today, md.sqlite_tick_keep_days)} old tick rows")
        return 0

    if args.action == "bhavcopy":
        from trading_core.marketdata.sources.nse_bhavcopy import NseBhavcopy

        if start is None:
            raise ValueError("--from is required")
        nse = NseBhavcopy(store, manifest, md.underlyings, md.risk_free_rate, md.nse_request_interval_s,
                          progress=print)
        print(nse.download(start, end, force=args.force))
        return 0

    if args.action in ("index", "constituents"):
        from trading_core.config import load_universe
        from trading_core.kite.client import KiteClient
        from trading_core.marketdata.sources.kite_index import KiteHistory

        if start is None:
            raise ValueError("--from is required")
        client = KiteClient.from_db(db, platform.kite, load_secrets(platform.root), LiveClock())
        kh = KiteHistory(client, store, manifest, progress=print)
        if args.action == "index":
            for sym in ([args.symbol] if args.symbol else md.index_symbols):
                print(sym, kh.index_1m(sym, start, end, force=args.force))
        else:
            symbols = [k.split(":", 1)[1] for k in load_universe(platform, args.universe)]
            print(kh.equity_daily(args.universe, symbols, start, end))
        return 0

    if args.action == "query":
        from trading_core.marketdata.duck import MarketDB

        table = MarketDB(store).sql(args.sql)
        shown = table.slice(0, args.limit).to_pylist()
        cols = table.column_names
        width = {c: max([len(c)] + [len(_cell(r[c])) for r in shown]) for c in cols}
        print("  ".join(c.ljust(width[c]) for c in cols))
        for r in shown:
            print("  ".join(_cell(r[c]).ljust(width[c]) for c in cols))
        print(f"({table.num_rows} rows{', showing ' + str(len(shown)) if len(shown) < table.num_rows else ''})")
        return 0
    return 1


def _cell(v) -> str:
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1e-2 and v != 0 else f"{v:.2f}"
    return "" if v is None else str(v)


def _f(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="algo", description="Multi-algo trading platform (Zerodha Kite)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("db", help="database management")
    d.add_argument("action", choices=["upgrade"])
    d.set_defaults(fn=cmd_db)

    lg = sub.add_parser("login", help="daily Kite login")
    lg.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    lg.set_defaults(fn=cmd_login)

    sub.add_parser("logout", help="end today's Kite session").set_defaults(fn=cmd_logout)
    sub.add_parser("quote", help="print NIFTY 50 and India VIX").set_defaults(fn=cmd_quote)
    sub.add_parser("instruments", help="refresh today's instrument snapshot").set_defaults(fn=cmd_instruments)
    sub.add_parser("list", help="list algos").set_defaults(fn=cmd_list)

    r = sub.add_parser("run", help="run an algo for today (or replay a recorded day)")
    r.add_argument("--algo", required=True)
    r.add_argument("--mode", choices=["paper", "live"], default=None, help="defaults to the algo's config")
    r.add_argument("--replay", metavar="YYYY-MM-DD", help="replay a day recorded by a live run")
    r.add_argument("--force-new", action="store_true", help="start a new run even if today's already finished")
    r.set_defaults(fn=cmd_run)

    k = sub.add_parser("kill", help="send a control command to a running algo")
    k.add_argument("--algo", required=True)
    k.add_argument("--command", default="KILL", choices=["KILL", "PAUSE", "RESUME", "kill", "pause", "resume"])
    k.set_defaults(fn=cmd_kill)

    rp = sub.add_parser("report", help="recent daily summaries")
    rp.add_argument("--algo", required=True)
    rp.add_argument("--limit", type=int, default=20)
    rp.set_defaults(fn=cmd_report)

    db_ = sub.add_parser("dashboard", help="local web dashboard")
    db_.add_argument("--port", type=int, default=None)
    db_.add_argument("--no-browser", action="store_true")
    db_.set_defaults(fn=cmd_dashboard)

    dt = sub.add_parser("data", help="historical market data store (Parquet + DuckDB)")
    dsub = dt.add_subparsers(dest="action", required=True)
    dsub.add_parser("status", help="coverage of the store")
    for name, hlp in (("index", "NIFTY 50 / India VIX 1-min from Kite"),
                      ("constituents", "index constituents daily bars from Kite"),
                      ("bhavcopy", "NSE end-of-day option chains")):
        a = dsub.add_parser(name, help=hlp)
        a.add_argument("--from", dest="start", required=True, metavar="YYYY-MM-DD")
        a.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="default: today")
        a.add_argument("--force", action="store_true", help="re-download days already stored")
        if name == "index":
            a.add_argument("--symbol", help='one index, e.g. "NIFTY 50" (default: all in config)')
        if name == "constituents":
            a.add_argument("--universe", default="nifty50")
    ex = dsub.add_parser("export-recorder", help="move recorded live ticks to Parquet")
    ex.add_argument("--date", metavar="YYYY-MM-DD", help="default: today")
    ex.add_argument("--prune", action="store_true", help="also delete exported ticks older than the keep window")
    q = dsub.add_parser("query", help="SQL over the store (views: index_1m, options_eod, options_1m, ...)")
    q.add_argument("sql")
    q.add_argument("--limit", type=int, default=30)
    dt.set_defaults(fn=cmd_data)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    platform = load_platform_config()
    if args.cmd != "run":
        setup_logging(platform.log_level, platform.logs_dir)
    try:
        return args.fn(args, platform)
    except Exception as e:  # noqa: BLE001 - show a clean message for expected setup errors
        from trading_core.kite.auth import NotLoggedIn

        if isinstance(e, (NotLoggedIn, RuntimeError, ValueError)):
            print(f"error: {e}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
