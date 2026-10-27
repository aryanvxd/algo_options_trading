"""Picking the CE/PE pair to watch.

Expiry day: an OTM call above spot and an OTM put below it, each with a mid premium in a band;
of the passing pairs, the one whose premiums are closest. Other days: of the calls and puts with a
delta in the band (Black-76 on a synthetic forward, IV from each option's own mid), the matched
pair closest to the target delta.
Either way both legs need a fresh, tight book, and the premiums must be matched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

import numpy as np

from algos.strangle_scalp.settings import PairConfig
from trading_core.feed.base import Tick
from trading_core.kite.instruments import InstrumentInfo
from trading_core.marketdata import greeks


@dataclass(frozen=True)
class Side:
    inst: InstrumentInfo
    mid: float
    spread: float
    delta: float | None = None
    iv: float | None = None


@dataclass(frozen=True)
class Pair:
    ce: Side
    pe: Side

    @property
    def key(self) -> tuple[int, int]:
        return self.ce.inst.instrument_token, self.pe.inst.instrument_token

    @property
    def ratio(self) -> float:
        return max(self.ce.mid, self.pe.mid) / min(self.ce.mid, self.pe.mid)

    @property
    def value(self) -> float:
        return round(self.ce.mid + self.pe.mid, 2)

    def describe(self) -> dict:
        return dict(ce=self.ce.inst.tradingsymbol, pe=self.pe.inst.tradingsymbol,
                    ce_strike=self.ce.inst.strike, pe_strike=self.pe.inst.strike,
                    ce_mid=self.ce.mid, pe_mid=self.pe.mid, ce_spread=round(self.ce.spread, 2),
                    pe_spread=round(self.pe.spread, 2), ce_delta=_r(self.ce.delta, 3), pe_delta=_r(self.pe.delta, 3),
                    ce_iv=_r(self.ce.iv, 4), pe_iv=_r(self.pe.iv, 4), ratio=round(self.ratio, 3), value=self.value)


def _r(v: float | None, n: int) -> float | None:
    return None if v is None else round(float(v), n)


def mid(t: Tick | None) -> float | None:
    return round((t.bid + t.ask) / 2, 2) if t is not None and t.has_depth else None


def quote_ok(t: Tick | None, now: datetime, max_spread: float, max_age_s: float) -> str | None:
    """None if the book is usable, else why not."""
    if t is None or not t.has_depth:
        return "no bid/ask"
    if t.spread > max_spread + 1e-9:
        return f"spread {t.spread:.2f} > {max_spread}"
    age = (now - t.received_at).total_seconds()
    if age > max_age_s:
        return f"quote {age:.0f}s old"
    return None


def select_pair(spot: float, chain: list[InstrumentInfo], quote: Callable[[int], Tick | None], now: datetime,
                dte: int, expiry: date, cfg: PairConfig, r: float, only: set[int] | None = None) -> Pair | str:
    """The pair to watch, or the reason no pair qualifies. ``only`` limits the candidates to these
    tokens (the whole chain is still used for the forward)."""
    max_spread = cfg.max_spread_pts_0dte if dte == 0 else cfg.max_spread_pts
    calls: list[Side] = []
    puts: list[Side] = []
    for inst in chain:
        if inst.strike is None or (only is not None and inst.instrument_token not in only):
            continue
        is_call = inst.instrument_type == "CE"
        if (is_call and inst.strike <= spot) or (not is_call and inst.strike >= spot):
            continue                                    # OTM only
        t = quote(inst.instrument_token)
        if quote_ok(t, now, max_spread, cfg.max_quote_age_s) is not None:
            continue
        (calls if is_call else puts).append(Side(inst, mid(t), t.spread))
    if not calls or not puts:
        return "no OTM call/put with a fresh, tight book"

    if dte == 0:
        band = lambda s: cfg.premium_min_0dte <= s.mid <= cfg.premium_max_0dte  # noqa: E731
        calls, puts = [s for s in calls if band(s)], [s for s in puts if band(s)]
        if not calls or not puts:
            return f"no OTM call and put priced ₹{cfg.premium_min_0dte:g}-{cfg.premium_max_0dte:g}"
        centre = (cfg.premium_min_0dte + cfg.premium_max_0dte) / 2
        pairs = [Pair(c, p) for c in calls for p in puts]
        best = min(pairs, key=lambda pr: (round(pr.ratio, 3), abs(pr.value - 2 * centre)))
    else:
        best_or_reason = _by_delta(spot, chain, quote, calls, puts, now, expiry, cfg, r)
        if isinstance(best_or_reason, str):
            return best_or_reason
        best = best_or_reason

    if best.ratio > cfg.max_premium_ratio:
        return (f"premiums not matched: {best.ce.inst.tradingsymbol} {best.ce.mid} vs "
                f"{best.pe.inst.tradingsymbol} {best.pe.mid} (ratio {best.ratio:.2f} > {cfg.max_premium_ratio})")
    return best


def _by_delta(spot, chain, quote, calls, puts, now, expiry, cfg: PairConfig, r: float) -> Pair | str:
    t = greeks.years_between(now, expiry)
    # Synthetic forward from the strikes that have both a call and a put quoted.
    by_strike: dict[float, dict[str, float]] = {}
    for inst in chain:
        m = mid(quote(inst.instrument_token))
        if inst.strike is not None and m is not None:
            by_strike.setdefault(inst.strike, {})[inst.instrument_type] = m
    both = sorted(k for k, v in by_strike.items() if "CE" in v and "PE" in v)
    if not both:
        return "no strike with both a call and a put quoted (forward unknown)"
    k = np.array(both)
    fwd = greeks.synthetic_forward(k, np.array([by_strike[x]["CE"] for x in both]),
                                   np.array([by_strike[x]["PE"] for x in both]), np.full(len(k), t), r, spot)
    if not np.isfinite(fwd):
        return "could not work out the forward"

    def with_greeks(sides: list[Side], is_call: bool) -> list[Side]:
        prem = np.array([s.mid for s in sides])
        strikes = np.array([s.inst.strike for s in sides])
        iv = greeks.implied_vol(prem, fwd, strikes, t, r, is_call)
        out = []
        for s, v in zip(sides, iv):
            if np.isfinite(v):
                d = float(greeks.greeks(fwd, s.inst.strike, t, r, v, is_call)["delta"])
                out.append(Side(s.inst, s.mid, s.spread, abs(d), float(v)))
        return out

    def in_band(sides: list[Side]) -> list[Side]:
        return [s for s in sides if cfg.delta_min <= s.delta <= cfg.delta_max]

    ces, pes = in_band(with_greeks(calls, True)), in_band(with_greeks(puts, False))
    if not ces or not pes:
        return f"no OTM {'call' if not ces else 'put'} with delta {cfg.delta_min}-{cfg.delta_max}"
    # Matched premiums first, then the pair closest to the target delta on both sides.
    return min((Pair(c, p) for c in ces for p in pes),
               key=lambda pr: (pr.ratio > cfg.max_premium_ratio,
                               abs(pr.ce.delta - cfg.target_delta) + abs(pr.pe.delta - cfg.target_delta)))
