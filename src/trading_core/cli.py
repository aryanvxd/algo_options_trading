"""Command line: ``algo <command>``.

  algo db upgrade                      create/upgrade the database and load holidays
  algo login                           daily Kite login (once each morning)
  algo quote                           smoke test: print NIFTY 50 and India VIX
  algo instruments                     refresh today's instrument snapshot
  algo list                            list available algos
  algo run --algo iron_condor          run today's session (paper)
  algo run --algo iron_condor --replay 2026-10-23    replay a recorded day
  algo kill --algo iron_condor         ask a running algo to square off and stop
  algo report --algo iron_condor       recent daily summaries
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
    from trading_core.strategy.bootstrap import AlreadyRan, run_algo

    replay = date.fromisoformat(args.replay) if args.replay else None
    trade_date = replay or LiveClock().today()
    log_path = setup_logging(platform.log_level, platform.logs_dir, args.algo, trade_date,
                             suffix="_replay" if replay else "")
    print(f"Logging to {log_path}")
    try:
        run_id = run_algo(platform, _db(platform), args.algo, mode=args.mode, replay_date=replay,
                          force_new=args.force_new)
    except AlreadyRan as e:
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
