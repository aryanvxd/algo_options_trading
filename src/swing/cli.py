"""``algo swing ...``: the weekend S/R swing's commands (registered by trading_core.cli)."""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from trading_core.config import PlatformConfig

DOWNLOAD_BATCH = 50      # stocks per equity_daily call: a failure midway keeps what's already stored


def add_parser(sub: argparse._SubParsersAction) -> None:
    sw = sub.add_parser("swing", help="weekend support/resistance swing (stocks)")
    ssub = sw.add_subparsers(dest="action", required=True)
    ssub.add_parser("universe", help="refresh the sector/theme indices' member lists from niftyindices.com")
    d = ssub.add_parser("data", help="daily candles for the indices and their stocks from Kite")
    d.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="default: history_days back")
    d.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="default: today")
    d.add_argument("--indices-only", action="store_true")
    b = ssub.add_parser("bars", help="print a stock's or index's recent daily/weekly candles")
    b.add_argument("symbol")
    b.add_argument("--weekly", action="store_true")
    b.add_argument("--last", type=int, default=10)
    sw.set_defaults(fn=cmd_swing)


def cmd_swing(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from swing.settings import load_swing_config

    cfg = load_swing_config()
    if args.action == "universe":
        return _universe(cfg, platform)
    if args.action == "data":
        return _data(args, cfg, platform)
    if args.action == "bars":
        return _bars(args, cfg, platform)
    return 1


def _universe(cfg, platform: PlatformConfig) -> int:
    import requests

    from swing.universe import build_universe, fetch_csv, write_universe
    from trading_core.clock import LiveClock

    session = requests.Session()
    u = build_universe(cfg, lambda name: fetch_csv(name, session), LiveClock().today())
    path = write_universe(platform, u)
    print(f"{len(u.indices)}/{len(cfg.indices)} indices, {len(u.symbols)} stocks, "
          f"{len(set(u.industry.values()))} industries -> {path}")
    return 0 if len(u.indices) == len(cfg.indices) else 2


def _data(args, cfg, platform: PlatformConfig) -> int:
    from swing.universe import load_swing_universe
    from trading_core.clock import LiveClock
    from trading_core.config import load_secrets
    from trading_core.db.engine import Database
    from trading_core.kite.client import KiteClient
    from trading_core.marketdata.manifest import Manifest
    from trading_core.marketdata.sources.kite_index import KiteHistory
    from trading_core.marketdata.store import MarketStore

    u = load_swing_universe(platform, cfg.universe)
    today = LiveClock().today()
    end = date.fromisoformat(args.end) if args.end else today
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=cfg.history_days)
    db = Database(platform.db_url)
    client = KiteClient.from_db(db, platform.kite, load_secrets(platform.root), LiveClock())
    kh = KiteHistory(client, MarketStore(platform.marketdata.dir), Manifest(db), progress=print)
    print("indices", kh.equity_daily(cfg.index_universe, [ix.name for ix in u.indices], start, end,
                                     segment="INDICES"))
    if not args.indices_only:
        for i in range(0, len(u.symbols), DOWNLOAD_BATCH):
            batch = u.symbols[i:i + DOWNLOAD_BATCH]
            print(f"stocks {i + 1}-{i + len(batch)} of {len(u.symbols)}",
                  kh.equity_daily(cfg.universe, batch, start, end))
    return 0


def _bars(args, cfg, platform: PlatformConfig) -> int:
    from swing.bars import avg_traded_value_cr, daily_bars, weekly
    from trading_core.marketdata.store import MarketStore

    store = MarketStore(platform.marketdata.dir)
    sym = args.symbol.upper()
    key = cfg.index_universe if sym.startswith("NIFTY") else cfg.universe
    bars = daily_bars(store, key, [sym]).get(sym)
    if bars is None:
        print(f"no daily candles for {sym} in equity_daily/{key}: run `algo swing data`")
        return 2
    shown = weekly(bars) if args.weekly else bars
    print(f"{sym}: {len(bars)} days {bars.dates[0]} .. {bars.dates[-1]}, {len(weekly(bars))} weeks"
          + ("" if key == cfg.index_universe else f", 20-day traded value ₹{avg_traded_value_cr(bars):.1f} cr"))
    print(f"{'date':<12}{'open':>10}{'high':>10}{'low':>10}{'close':>10}{'volume':>14}")
    for k in range(max(0, len(shown) - args.last), len(shown)):
        print(f"{str(shown.dates[k]):<12}{shown.open[k]:>10.2f}{shown.high[k]:>10.2f}{shown.low[k]:>10.2f}"
              f"{shown.close[k]:>10.2f}{int(shown.volume[k]):>14,}")
    return 0
