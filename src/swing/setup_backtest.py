"""Setup backtest (spec section 9, "Do the setups pay?").

Walk forward week by week over each stock. At each weekend: zones from the candles up to then,
then the setup (``setups.find_setup``). Each setup is traded twice, once per exit mode:

- **fill**: a limit buy at the entry; fills on the first day the low reaches it, at the entry or
  the open if it opened below. Not filled in ``fill_weeks`` weeks: dropped.
- **OCO**: sell at the stop (or the open, if it opened below) or at the target (or the open, if it
  opened above). Both on one day: the stop. On the fill day only the stop is checked.
- **target only**: no stop; sell at the target.
- either mode sells at the close after ``max_hold_days`` sessions; still open at the end of the
  data: valued at the last close.

Returns are net of ``cost_pct`` per round trip. R = (exit - fill) / (entry - stop).

A stock gets no new setup while its previous one is pending or open (in either mode).

**Random twin** of every setup: same weekend, same stock, an entry at a random level 0 to
``near_atr`` ATR below the close, and the same stop and target distances. If the setups don't beat
their twins, the zones aren't choosing better entries than chance.

**Portfolio**: setups in weekend order, best score first, kept while fewer than ``max_open`` are
pending or open and fewer than ``max_per_sector`` share the stock's industry; ₹ per entry from the
setup's amount.
"""

from __future__ import annotations

import csv
import random
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from swing.backtest import weekends
from swing.bars import Bars, avg_traded_value_cr
from swing.setups import Setup, SetupConfig, find_setup
from swing.zones import ZoneConfig, find_zones

MODES = ("oco", "target")


@dataclass
class Trade:
    symbol: str
    asof: date
    mode: str
    random: bool
    entry: float
    stop: float
    target: float
    score: float
    rr: float
    trend_ok: bool
    sector_ok: bool | None
    industry: str
    amount: float
    filled: bool
    fill_day: date | None
    fill: float
    exit_day: date              # exit, or the day the unfilled setup was dropped
    exit: float
    reason: str                 # target / stop / time / end / unfilled
    days: int                   # sessions held
    ret_pct: float              # net of costs
    r: float


def _upto(b: Bars, i: int) -> Bars:
    return Bars(b.symbol, b.dates[: i + 1], b.open[: i + 1], b.high[: i + 1], b.low[: i + 1],
                b.close[: i + 1], b.volume[: i + 1])


def simulate(b: Bars, a: int, entry: float, stop: float, target: float, mode: str,
             cfg: SetupConfig) -> tuple[bool, int, float, int, float, str]:
    """(filled, fill index, fill price, exit index, exit price, reason) for a setup made on bar a."""
    n = len(b)
    last = min(n - 1, a + cfg.fill_weeks * 5)
    k = next((i for i in range(a + 1, last + 1) if b.low[i] <= entry), None)
    if k is None:
        return False, -1, 0.0, last, 0.0, "unfilled"
    fill = min(float(b.open[k]), entry)
    if mode == "oco" and b.low[k] <= stop:
        return True, k, fill, k, min(fill, stop), "stop"
    for j in range(k + 1, n):
        if mode == "oco" and b.low[j] <= stop:
            return True, k, fill, j, min(float(b.open[j]), stop), "stop"
        if b.high[j] >= target:
            return True, k, fill, j, max(float(b.open[j]), target), "target"
        if j - k >= cfg.max_hold_days:
            return True, k, fill, j, float(b.close[j]), "time"
    return True, k, fill, n - 1, float(b.close[-1]), "end"


def _trade(b: Bars, a: int, s: Setup, entry: float, stop: float, target: float, mode: str, rnd: bool,
           cfg: SetupConfig) -> Trade:
    filled, k, fill, j, ex, reason = simulate(b, a, entry, stop, target, mode, cfg)
    ret = ((ex / fill - 1) * 100 - cfg.cost_pct) if filled else 0.0
    r = (ex - fill) / (entry - stop) if filled and entry > stop else 0.0
    return Trade(s.symbol, s.asof, mode, rnd, round(entry, 2), round(stop, 2), round(target, 2), s.score, s.rr,
                 s.trend_ok, s.sector_ok, s.industry, s.amount, filled, b.dates[k] if filled else None,
                 round(fill, 2), b.dates[j], round(ex, 2), reason, (j - k) if filled else 0, round(ret, 3),
                 round(r, 3))


def symbol_trades(b: Bars, zcfg: ZoneConfig, scfg: SetupConfig, start: date, end: date, industry: str,
                  sector_state: dict[date, bool] | None, min_traded_value_cr: float,
                  seed: int = 7) -> list[Trade]:
    rng = random.Random(f"{seed}:{b.symbol}")
    out: list[Trade] = []
    free_from = 0                                       # bar index from which a new setup may start
    for a in weekends(b.dates):
        day = b.dates[a]
        if day < start or day > end or a < free_from or a + 1 >= len(b):
            continue
        upto = _upto(b, a)
        if avg_traded_value_cr(upto) < min_traded_value_cr:
            continue
        rep = find_zones(b, zcfg, day)
        if rep.note:
            continue
        s = find_setup(upto, rep, scfg, industry, None if sector_state is None else sector_state.get(day))
        if s is None:
            continue
        real = [_trade(b, a, s, s.entry, s.stop, s.target, m, False, scfg) for m in MODES]
        free_from = max(b.dates.index(t.exit_day) for t in real) + 1
        d = rng.uniform(0, scfg.near_atr) * s.atr
        te = s.close - d
        twin = [_trade(b, a, s, te, te - (s.entry - s.stop), te + (s.target - s.entry), m, True, scfg) for m in MODES]
        out += real + twin
    return out


# ---------------------------------------------------------------- sector context (Z10)

def index_states(b: Bars, zcfg: ZoneConfig, near_atr: float, start: date, end: date) -> dict[date, bool]:
    """Each weekend: is the index itself within near_atr ATR above a support zone (or inside one)?"""
    out = {}
    for a in weekends(b.dates):
        day = b.dates[a]
        if day < start or day > end:
            continue
        rep = find_zones(b, zcfg, day)
        if rep.note:
            continue
        out[day] = any(z.lo < rep.close and rep.close - z.hi <= near_atr * rep.atr for z in rep.zones)
    return out


def _index_job(args) -> tuple[str, dict[date, bool]]:
    b = args[0]
    return b.symbol, index_states(*args)


def _symbol_job(args) -> list[Trade]:
    return symbol_trades(*args)


def run(bars: dict[str, Bars], index_bars: dict[str, Bars], members: dict[str, list[str]],
        industry: dict[str, str], zcfg: ZoneConfig, scfg: SetupConfig, start: date, end: date,
        min_traded_value_cr: float, workers: int = 1, progress: Callable[[str], None] = print) -> list[Trade]:
    """``members``: each stock's indices (Z10 is true if any of them is near support)."""
    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    mapper = pool.map if pool else map
    states = dict(mapper(_index_job, [(ib, zcfg, scfg.near_atr, start, end) for ib in index_bars.values()]))
    progress(f"sector context: {len(states)} indices")
    jobs = []
    for sym, b in bars.items():
        idx = [states[i] for i in members.get(sym, []) if i in states]
        sec = {d: any(s.get(d, False) for s in idx) for d in set().union(*idx)} if idx else None
        jobs.append((b, zcfg, scfg, start, end, industry.get(sym, ""), sec, min_traded_value_cr))
    out: list[Trade] = []
    for i, tr in enumerate(mapper(_symbol_job, jobs), 1):
        out += tr
        if i % 25 == 0 or i == len(jobs):
            progress(f"{i}/{len(jobs)} symbols, {sum(not t.random for t in out) // len(MODES):,} setups")
    if pool:
        pool.shutdown()
    return out


# ---------------------------------------------------------------- portfolio and summary

def portfolio(trades: list[Trade], cfg: SetupConfig) -> list[Trade]:
    """The trades the book would have taken (one mode, real or twins)."""
    kept: list[Trade] = []
    for t in sorted(trades, key=lambda t: (t.asof, -t.score, t.symbol)):
        busy = [k for k in kept if k.exit_day >= t.asof]
        if len(busy) >= cfg.max_open or sum(k.industry == t.industry for k in busy) >= cfg.max_per_sector:
            continue
        kept.append(t)
    return kept


def _stats(ts: list[Trade]) -> dict:
    f = [t for t in ts if t.filled]
    if not f:
        return dict(n=len(ts), fill=0.0, win=0.0, ret=0.0, r=0.0, days=0.0, tgt=0.0, stp=0.0, pnl=0.0)
    return dict(n=len(ts), fill=100 * len(f) / len(ts), win=100 * sum(t.ret_pct > 0 for t in f) / len(f),
                ret=statistics.fmean(t.ret_pct for t in f), r=statistics.fmean(t.r for t in f),
                days=statistics.median(t.days for t in f),
                tgt=100 * sum(t.reason == "target" for t in f) / len(f),
                stp=100 * sum(t.reason == "stop" for t in f) / len(f),
                pnl=sum(t.amount * t.ret_pct / 100 for t in f))


HEAD = (f"{'':<30}{'setups':>8}{'fill%':>7}{'win%':>7}{'avg ret%':>9}{'avg R':>7}{'med days':>9}"
        f"{'target%':>8}{'stop%':>7}")


def _row(label: str, ts: list[Trade]) -> str:
    s = _stats(ts)
    return (f"{label:<30}{s['n']:>8,}{s['fill']:>7.1f}{s['win']:>7.1f}{s['ret']:>9.2f}{s['r']:>7.2f}"
            f"{s['days']:>9.0f}{s['tgt']:>8.1f}{s['stp']:>7.1f}")


def _pair(label: str, real: list[Trade], twins: list[Trade]) -> list[str]:
    return [_row(label, real), _row("  random twins", twins)]


def summary(trades: list[Trade], cfg: SetupConfig) -> str:
    lines = ["Win%, avg ret% (net of costs), avg R, days and target%/stop% are of filled trades.", ""]
    for mode in MODES:
        real = [t for t in trades if t.mode == mode and not t.random]
        twin = [t for t in trades if t.mode == mode and t.random]
        key = lambda t: (t.symbol, t.asof)  # noqa: E731
        twin_of = {key(t): t for t in twin}
        tw = lambda ts: [twin_of[key(t)] for t in ts if key(t) in twin_of]  # noqa: E731
        s2 = [t for t in real if t.score >= cfg.min_score]
        lines += [f"=========== {'OCO (stop + target)' if mode == 'oco' else 'target only (no stop)'} ===========",
                  HEAD]
        lines += _pair("all setups", real, tw(real))
        lines += _pair(f"S2 score >= {cfg.min_score:g}", s2, tw(s2))
        lines += _pair("  S2 + trend (S7)", [t for t in s2 if t.trend_ok], tw([t for t in s2 if t.trend_ok]))
        lines += _pair("  S2, no trend", [t for t in s2 if not t.trend_ok], tw([t for t in s2 if not t.trend_ok]))
        lines += _pair("  S2 + sector (Z10)", [t for t in s2 if t.sector_ok], tw([t for t in s2 if t.sector_ok]))
        lines += _pair("  S2 + trend + sector", [t for t in s2 if t.trend_ok and t.sector_ok],
                       tw([t for t in s2 if t.trend_ok and t.sector_ok]))
        lines.append("")
        lines.append("by score (all setups)")
        for lo, hi in ((0, 50), (50, 60), (60, 70), (70, 101)):
            sel = [t for t in real if lo <= t.score < hi]
            lines += _pair(f"  score {lo}-{min(hi, 100)}", sel, tw(sel))
        lines.append("by year (S2)")
        for y in sorted({t.asof.year for t in s2}):
            sel = [t for t in s2 if t.asof.year == y]
            lines += _pair(f"  {y}", sel, tw(sel))
        book, book_tw = portfolio(s2, cfg), portfolio(tw(s2), cfg)
        lines += ["", f"book: S2 setups, at most {cfg.max_open} open, {cfg.max_per_sector} per industry, "
                      f"₹{cfg.amount_low:,.0f}/₹{cfg.amount_high:,.0f} per entry"]
        for label, b in (("setups", book), ("random twins", book_tw)):
            st = _stats(b)
            by_year: dict[int, float] = defaultdict(float)
            eq, peak, dd = 0.0, 0.0, 0.0
            for t in sorted((t for t in b if t.filled), key=lambda t: t.exit_day):
                p = t.amount * t.ret_pct / 100
                by_year[t.exit_day.year] += p
                eq += p
                peak = max(peak, eq)
                dd = min(dd, eq - peak)
            years = "  ".join(f"{y} {v:+,.0f}" for y, v in sorted(by_year.items()))
            lines.append(f"  {label:<14}{st['n']:>5} setups, {sum(t.filled for t in b):>4} filled, "
                         f"net ₹{st['pnl']:+,.0f}, worst drawdown ₹{dd:,.0f} | {years}")
        lines.append("")
    return "\n".join(lines)


def write(trades: list[Trade], folder: Path, header: str, cfg: SetupConfig) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "trades.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        cols = list(asdict(trades[0])) if trades else []
        w.writerow(cols)
        for t in trades:
            d = asdict(t)
            w.writerow([d[c] for c in cols])
    (folder / "summary.txt").write_text(header + "\n\n" + summary(trades, cfg) + "\n", encoding="utf-8")
    return folder
