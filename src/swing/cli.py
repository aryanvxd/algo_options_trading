"""``algo swing ...``: the weekend S/R swing's commands (registered by trading_core.cli)."""

from __future__ import annotations

import argparse
import json
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
    z = ssub.add_parser("zones", help="support/resistance zones of a stock or index")
    z.add_argument("symbol")
    z.add_argument("--asof", metavar="YYYY-MM-DD", help="use candles up to this day only")
    z.add_argument("--pivots", action="store_true", help="also list each zone's pivots and touches")
    c = ssub.add_parser("chart", help="HTML chart of candles and zones (data/reports/swing/<SYMBOL>.html)")
    c.add_argument("symbol")
    c.add_argument("--asof", metavar="YYYY-MM-DD", help="use candles up to this day only")
    c.add_argument("--days", type=int, default=730, help="days of candles shown (default 730)")
    c.add_argument("--mark", type=float, metavar="PRICE", help="draw a price line, e.g. your entry")
    c.add_argument("--mark-label", default="entry")
    sc = ssub.add_parser("scan", help="refresh candles and write the overview and charts (data/reports/swing)")
    sc.add_argument("--no-refresh", action="store_true", help="use the candles already stored (no Kite login)")
    ssub.add_parser("holdings", help="read your Kite holdings (read-only) and write the holdings review")
    bt = ssub.add_parser("backtest", help="zones: do zones hold (default); setups: do the setups pay "
                                          "(data/backtests/swing_<what>_*)")
    bt.add_argument("what", nargs="?", choices=["zones", "setups"], default="zones")
    bt.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="default: 2 years after the first candle")
    bt.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="default: the last candle")
    bt.add_argument("--symbols", help="comma-separated (default: every stock in the universe)")
    bt.add_argument("--workers", type=int, default=4)
    bt.add_argument("--name", help="folder suffix (default: dates)")
    sw.set_defaults(fn=cmd_swing)


def cmd_swing(args: argparse.Namespace, platform: PlatformConfig) -> int:
    from swing.settings import load_user_config

    cfg = load_user_config(platform)          # config.yaml + the dashboard's saved settings
    if args.action == "universe":
        return _universe(cfg, platform)
    if args.action == "data":
        return _data(args, cfg, platform)
    if args.action == "bars":
        return _bars(args, cfg, platform)
    if args.action == "zones":
        return _zones(args, cfg, platform)
    if args.action == "chart":
        return _chart(args, cfg, platform)
    if args.action == "backtest":
        return _backtest(args, cfg, platform)
    if args.action == "scan":
        return _scan(args, cfg, platform)
    if args.action == "holdings":
        return _holdings(cfg, platform)
    return 1


def _holdings(cfg, platform: PlatformConfig) -> int:
    """Read-only: kite.holdings() and candles. Held symbols aren't printed (personal data)."""
    from swing import holdings
    from swing.bars import daily_bars
    from swing.universe import load_swing_universe
    from trading_core.clock import LiveClock
    from trading_core.config import load_secrets
    from trading_core.db.engine import Database
    from trading_core.kite.client import KiteClient
    from trading_core.marketdata.manifest import Manifest
    from trading_core.marketdata.sources.kite_index import KiteHistory
    from trading_core.marketdata.store import MarketStore

    clock = LiveClock()
    db = Database(platform.db_url)
    client = KiteClient.from_db(db, platform.kite, load_secrets(platform.root), clock)
    held = holdings.parse(client.holdings())
    store = MarketStore(platform.marketdata.dir)
    symbols = [h.symbol for h in held]
    wanted = sorted(set(symbols) | holdings.order_symbols(client.gtts(), client.alerts()))
    if wanted:
        end = clock.today()
        kh = KiteHistory(client, store, Manifest(db), progress=lambda _: None)
        kh.equity_daily(cfg.holdings_universe, wanted, end - timedelta(days=cfg.zones.lookback_days + 365), end)
    bars = daily_bars(store, cfg.holdings_universe, symbols) if symbols else {}
    data = holdings.review(held, bars, cfg.zones, cfg.overview_near_atr, load_swing_universe(platform, cfg.universe),
                           clock.now())
    print(f"{len(held)} holdings, {len(bars)} with candles -> {holdings.write(platform, data)}")
    return 0


def _scan(args, cfg, platform: PlatformConfig) -> int:
    """The weekend workflow: candles, sectors, stocks, picks, then the Algo picks watchlist and the
    journal. Each stage is reported to scan_status.json for the dashboard's Weekend run page."""
    from swing import overview
    from swing.bars import avg_traded_value_cr, daily_bars
    from swing.chart import render_html
    from swing.progress import ScanStatus
    from swing.universe import load_swing_universe
    from swing.zones import find_zones
    from trading_core.marketdata.store import MarketStore, slug

    folder = platform.reports_dir / "swing"
    folder.mkdir(parents=True, exist_ok=True)
    status = ScanStatus(folder, refresh=not args.no_refresh)
    store = MarketStore(platform.marketdata.dir)
    try:
        if not args.no_refresh:
            status.start("refresh")
            stored = daily_bars(store, cfg.index_universe)
            last = max((b.dates[-1] for b in stored.values()), default=None)
            start = (last - timedelta(days=5)).isoformat() if last else None
            _data(argparse.Namespace(start=start, end=None, indices_only=False), cfg, platform)
            _holdings(cfg, platform)
            status.done("refresh", "candles and holdings read from Kite")
        else:
            status.skip("refresh", "stored candles (no Kite)")
        status.start("sectors")
        u = load_swing_universe(platform, cfg.universe)
        index_bars = daily_bars(store, cfg.index_universe)
        stock_bars = daily_bars(store, cfg.universe)
        bench = daily_bars(store, cfg.benchmark_universe, [cfg.benchmark]).get(cfg.benchmark)
        latest = max(b.dates[-1] for b in index_bars.values())
        fresh = latest - timedelta(days=cfg.overview_stale_days)
        traded = {s: avg_traded_value_cr(b) for s, b in stock_bars.items()}
        wanted = {s: b for s, b in stock_bars.items()
                  if b.dates[-1] >= fresh and traded[s] >= cfg.min_traded_value_cr}
        reps: dict = {}

        def rows(bars) -> list:
            out = []
            for b in bars.values():
                rep = reps[b.symbol] = find_zones(b, cfg.zones)
                out.append(overview.row_for(b, rep, cfg.overview_near_atr))
                if rep.zones:
                    (folder / f"{slug(b.symbol)}.html").write_text(render_html(b, rep), encoding="utf-8")
            return out

        indices = rows(index_bars)
        status.done("sectors", f"{len(indices)} indices, candles to {latest}")
        status.start("stocks")
        stocks = rows(wanted)
        args_ = (u, indices, stocks, traded, cfg.overview_near_atr, cfg.min_traded_value_cr, len(u.symbols) - len(stocks))
        path = folder / "overview.html"
        path.write_text(overview.render(*args_), encoding="utf-8")
        sparks = {s: [round(float(c), 2) for c in b.close[-60:]] for s, b in index_bars.items()}
        (folder / "overview.json").write_text(json.dumps(overview.payload(*args_, sparks)), encoding="utf-8")
        status.done("stocks", f"{len(stocks)} liquid stocks of {len(u.symbols)}")
        print(f"{len(indices)} indices, {len(stocks)} of {len(u.symbols)} stocks, candles to {latest} -> {path}")
        _picks(cfg, platform, status, u, index_bars, stock_bars, wanted, reps, traded, bench)
    except Exception as e:
        status.fail(f"{type(e).__name__}: {e}")
        raise
    status.finish()
    return 0


def _picks(cfg, platform: PlatformConfig, status, u, index_bars, stock_bars, wanted, reps, traded, bench) -> None:
    from swing import funnel, tracking, watchlists
    from trading_core.clock import LiveClock
    from trading_core.db.engine import Database
    from trading_core.marketdata.store import MarketStore

    status.start("picks")
    db = Database(platform.db_url)
    now = LiveClock().now()
    payload = funnel.build(u, index_bars, {s: reps[s] for s in index_bars if s in reps},
                           wanted, {s: reps[s] for s in wanted if s in reps}, traded, bench, cfg, db, now)
    folder = platform.reports_dir / "swing"
    (folder / "picks.json").write_text(json.dumps(payload), encoding="utf-8")
    fz = payload["funnel"]
    status.done("picks", f"{fz['sectors_passed']} of {fz['sectors']} sectors passed, {fz['considered']} stocks looked at, "
                         f"{fz['plans']} plans, top {fz['top']}")
    print(f"picks: {fz['plans']} plans, top {fz['top']} -> {folder / 'picks.json'}")
    status.start("journal")
    try:
        top = funnel.top(payload)
        n_list = watchlists.replace_auto(db, top, now)
        added = tracking.record(db, payload, top, cfg, now)
        store = MarketStore(platform.marketdata.dir)
        moved = tracking.update(db, lambda syms: tracking.load_bars(store, cfg, syms), cfg, now)
    except Exception as e:  # noqa: BLE001 - the picks are written; the tables may be missing
        msg = str(e).splitlines()[0][:200]
        hint = " (run `algo db upgrade`)" if "no such table" in msg else ""
        status.fail_stage("journal", f"{msg}{hint}")
        print(f"journal: failed: {msg}{hint}")
        return
    status.done("journal", f"Algo picks: {n_list} stocks; journal: {added} new picks, {moved} followed forward")
    print(f"journal: {added} new picks, {moved} rows updated")


def _backtest(args, cfg, platform: PlatformConfig) -> int:
    import time

    from swing import backtest
    from swing.bars import daily_bars
    from trading_core.marketdata.store import MarketStore

    syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    bars = daily_bars(MarketStore(platform.marketdata.dir), cfg.universe, syms)
    if not bars:
        print(f"no daily candles in equity_daily/{cfg.universe}: run `algo swing data`")
        return 2
    first = min(b.dates[0] for b in bars.values())
    last = max(b.dates[-1] for b in bars.values())
    start = date.fromisoformat(args.start) if args.start else first + timedelta(days=cfg.zones.lookback_days)
    end = date.fromisoformat(args.end) if args.end else last
    t0 = time.perf_counter()
    if args.what == "setups":
        return _backtest_setups(args, cfg, platform, bars, start, end, t0)
    events = backtest.run(bars, cfg.zones, cfg.backtest, start, end, cfg.min_traded_value_cr, args.workers)
    name = args.name or f"{start:%Y%m%d}_{end:%Y%m%d}"
    header = (f"Zone backtest {start} .. {end}: {len(bars)} stocks, liquid weeks only (>= Rs "
              f"{cfg.min_traded_value_cr:g} cr/day), {time.perf_counter() - t0:.0f} s\n"
              f"zones: {cfg.zones.model_dump()}\nbacktest: {cfg.backtest.model_dump()}")
    folder = backtest.write(events, platform.data_dir / "backtests" / f"swing_zones_{name}", header)
    print(backtest.summary(events))
    print(f"\n-> {folder}")
    return 0


def _backtest_setups(args, cfg, platform: PlatformConfig, bars, start: date, end: date, t0: float) -> int:
    import time

    from swing import setup_backtest
    from swing.bars import daily_bars
    from swing.universe import load_swing_universe
    from trading_core.marketdata.store import MarketStore

    u = load_swing_universe(platform, cfg.universe)
    index_bars = daily_bars(MarketStore(platform.marketdata.dir), cfg.index_universe)
    members = {s: u.indices_of(s) for s in bars}
    trades = setup_backtest.run(bars, index_bars, members, u.industry, cfg.zones, cfg.setups, start, end,
                                cfg.min_traded_value_cr, args.workers)
    name = args.name or f"{start:%Y%m%d}_{end:%Y%m%d}"
    header = (f"Setup backtest {start} .. {end}: {len(bars)} stocks, {len(index_bars)} indices for Z10, liquid "
              f"weeks only (>= Rs {cfg.min_traded_value_cr:g} cr/day), {time.perf_counter() - t0:.0f} s\n"
              f"setups: {cfg.setups.model_dump()}\nzones: {cfg.zones.model_dump()}")
    folder = setup_backtest.write(trades, platform.data_dir / "backtests" / f"swing_setups_{name}", header,
                                  cfg.setups)
    print(setup_backtest.summary(trades, cfg.setups))
    print(f"\n-> {folder}")
    return 0


def _chart(args, cfg, platform: PlatformConfig) -> int:
    from swing.chart import render_html
    from swing.zones import find_zones
    from trading_core.marketdata.store import slug

    sym = args.symbol.upper()
    key, bars = _load(sym, cfg, platform)
    if bars is None:
        print(f"no daily candles for {sym} in equity_daily/{key}: run `algo swing data`")
        return 2
    rep = find_zones(bars, cfg.zones, date.fromisoformat(args.asof) if args.asof else None)
    if rep.note:
        print(f"{sym}: {rep.note}")
        return 0
    out = platform.reports_dir / "swing" / f"{slug(sym)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(bars, rep, args.days, args.mark, args.mark_label), encoding="utf-8")
    print(out)
    return 0


def _load(sym: str, cfg, platform: PlatformConfig):
    from swing.bars import daily_bars
    from trading_core.marketdata.store import MarketStore

    key = cfg.index_universe if sym.startswith("NIFTY") else cfg.universe
    return key, daily_bars(MarketStore(platform.marketdata.dir), key, [sym]).get(sym)


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
    # The benchmark (NIFTY 50) for the sectors' relative strength: a year back the first time.
    from swing.bars import daily_bars

    have = daily_bars(MarketStore(platform.marketdata.dir), cfg.benchmark_universe, [cfg.benchmark])
    b_start = start if have else min(start, end - timedelta(days=400))
    print("benchmark", kh.equity_daily(cfg.benchmark_universe, [cfg.benchmark], b_start, end, segment="INDICES"))
    if not args.indices_only:
        for i in range(0, len(u.symbols), DOWNLOAD_BATCH):
            batch = u.symbols[i:i + DOWNLOAD_BATCH]
            print(f"stocks {i + 1}-{i + len(batch)} of {len(u.symbols)}",
                  kh.equity_daily(cfg.universe, batch, start, end))
    return 0


def _bars(args, cfg, platform: PlatformConfig) -> int:
    from swing.bars import avg_traded_value_cr, weekly

    sym = args.symbol.upper()
    key, bars = _load(sym, cfg, platform)
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


def _zones(args, cfg, platform: PlatformConfig) -> int:
    from swing.zones import find_zones

    sym = args.symbol.upper()
    key, bars = _load(sym, cfg, platform)
    if bars is None:
        print(f"no daily candles for {sym} in equity_daily/{key}: run `algo swing data`")
        return 2
    rep = find_zones(bars, cfg.zones, date.fromisoformat(args.asof) if args.asof else None)
    if rep.note:
        print(f"{sym}: {rep.note}")
        return 0
    print(f"{sym}  as of {rep.asof}  close {rep.close:,.2f}  ATR(14) {rep.atr:,.2f}  "
          f"zones from the last {cfg.zones.lookback_days} days")
    print(f"{'zone':<19}{'role':<20}{'score':>6}  {'touch S/R':>9}  {'piv L/H':>7}  {'last test':<11}"
          f"{'depart':>7}{'recent':>7}  {'vol':>4}  notes")
    shown_close = False
    for z in sorted(rep.zones, key=lambda z: -z.hi):
        if not shown_close and z.hi < rep.close:
            print(f"{'-- close ' + format(rep.close, ',.2f') + ' ':-<108}")
            shown_close = True
        deps = [t.departure_atr for t in z.touches]
        role = ("flipped " if z.flipped else "") + z.role
        lows = sum(p.kind == "L" for p in z.pivots)
        notes = ", ".join(z.confluence + ([f"broken {z.breaks[-1][0]} {z.breaks[-1][1]}"] if z.breaks else []))
        print(f"{z.lo:>8,.2f}-{z.hi:<10,.2f}{role:<20}{z.score:>6.0f}  "
              f"{z.count('support'):>4}/{z.count('resistance'):<4}  {lows:>3}/{len(z.pivots) - lows:<3}  "
              f"{str(z.last_test or '-'):<11}{(sum(deps) / len(deps) if deps else 0):>7.1f}{z.recent_tests:>7}  "
              f"{'' if z.volume_ratio is None else format(z.volume_ratio, '.1f'):>4}  {notes}")
        if args.pivots:
            print("      pivots: " + "  ".join(f"{p.kind}{p.tf} {p.price:,.2f} {p.day}" for p in z.pivots))
            print("      touches: " + "  ".join(f"{t.side[0].upper()} {t.day} +{t.departure_atr:.1f}"
                                                 for t in z.touches))
    if not shown_close:
        print(f"{'-- close ' + format(rep.close, ',.2f') + ' ':-<108}")
    return 0
