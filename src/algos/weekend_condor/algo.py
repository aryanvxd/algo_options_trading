"""NIFTY weekend short iron condor (paper, spec: docs/strategy-criteria/weekend-iron-condor.md).

Positional: sell on the last trading day before a weekend (normally Friday 14:45-15:10), hold over
the weekend (``carry_overnight``), and buy back on the next trading day (normally Monday) from
09:35 at the first moment every spread is tight, or at any price from 09:50.

Entry day: VIX < 18, no high-impact event and no heavyweight's results in the weekend gap (market calendar),
CE/PE premium balance, spreads, minimum credit. Shorts sit 1.2 expected weekend moves from spot
(VIX-scaled), hedges 200 pts further out. Exits on both days: kill, stale feed (market hours only),
stop, premium blow-out, 60%-of-credit target; on the next day also the carried-position exit.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any

from algos.iron_condor import filters
from algos.iron_condor.exits import PremiumBlowout
from algos.iron_condor.strike_selector import Rejection, select_condor
from algos.weekend_condor.exits import CarriedExit, CreditTarget
from algos.weekend_condor.settings import WeekendCondorConfig
from trading_core.kite.instruments import InstrumentInfo
from trading_core.risk.engine import ExitRule
from trading_core.risk.rules import KillSwitch, PnlStop, StaleFeed
from trading_core.strategy.base import BaseAlgo, SkipDay
from trading_core.strategy.position import Position

MARKET_OPEN, MARKET_CLOSE = time(9, 15), time(15, 30)


class WeekendCondorAlgo(BaseAlgo):
    config_model = WeekendCondorConfig
    structure = "IRON_CONDOR"
    cfg: WeekendCondorConfig

    def __init__(self, ctx, cfg: WeekendCondorConfig):
        super().__init__(ctx, cfg)
        self.expiry = None
        self.dte: int | None = None
        self.next_day = None
        self.chain: list[InstrumentInfo] = []
        self.lot_size: int | None = None
        self.index_token: int | None = None
        self.vix_token: int | None = None
        self.feed_ready = False
        self.strikes_subscribed = False
        self.events_logged = False

    # ------------------------------------------------------------ sizing / levels

    @property
    def qty(self) -> int:
        return self.cfg.lots * (self.lot_size or 0)

    @property
    def stop_rupees(self) -> float:
        return self.cfg.exits.stop_loss_points * self.qty

    def build_exit_rules(self) -> list[ExitRule]:
        e = self.cfg.exits
        return [
            KillSwitch(),
            StaleFeed(e.stale_feed_seconds, market_hours=(MARKET_OPEN, MARKET_CLOSE)),
            PnlStop(self.stop_rupees, e.pnl_basis),
            PremiumBlowout(e.premium_blowout_mult, e.short_leg_mult),
            CreditTarget(e.target_credit_frac, e.pnl_basis),
            CarriedExit(e.exit_from, e.exit_max_spread_pts, e.hard_exit),
        ]

    def carry_today(self) -> bool:
        # Hold over the weekend only from the entry day; a carried position still open at the next
        # day's square-off (it should have gone by 09:50) is closed then.
        return not self.adopted

    def spot(self) -> float | None:
        t = self.ctx.feed.latest(self.index_token) if self.index_token else None
        return t.ltp if t else None

    def vix(self) -> float | None:
        t = self.ctx.feed.latest(self.vix_token) if self.vix_token else None
        return t.ltp if t else None

    def levels(self) -> dict[str, Any] | None:
        pos = self.position
        if pos is None or pos.entry_credit_pts is None:
            return None
        e = self.cfg.exits
        credit = pos.entry_credit_pts
        return dict(entry_credit=credit, stop_rupees=-self.stop_rupees, pnl_basis=e.pnl_basis,
                    target_rupees=round(credit * e.target_credit_frac * pos.unit_qty, 2),
                    blowout_premium=round(credit * e.premium_blowout_mult, 2), carried=self.adopted,
                    exit_from=f"{e.exit_from:%H:%M}", hard_exit=f"{e.hard_exit:%H:%M}",
                    expected_move=pos.params.get("expected_move"))

    # ------------------------------------------------------------ lifecycle hooks

    def _load_chain(self, expiry) -> None:
        ctx, cfg, d = self.ctx, self.cfg, self.ctx.trade_date
        self.expiry = expiry
        self.dte = ctx.calendar.trading_days_until(d, expiry) if expiry >= d else 0
        self.chain = ctx.instruments.option_chain(cfg.underlying, expiry, d)
        if not self.chain:
            raise SkipDay(f"empty option chain for {expiry}")
        self.lot_size = self.chain[0].lot_size
        index = ctx.instruments.by_symbol("NSE", cfg.index_symbol, d)
        vix = ctx.instruments.by_symbol("NSE", cfg.vix_symbol, d)
        if index is None or vix is None:
            raise SkipDay(f"index tokens not found ({cfg.index_symbol}, {cfg.vix_symbol})")
        self.index_token, self.vix_token = index.instrument_token, vix.instrument_token

    def day_check(self) -> None:
        ctx, cfg = self.ctx, self.cfg
        d = ctx.trade_date
        if self.adopted:
            # Exit day: only the carried position matters; no new entry today.
            self._load_chain(self.position.expiry)
            self.check("carried_in", True, value=self.position.entry_credit_pts,
                       entered=f"{self.position.entry_time:%Y-%m-%d %H:%M}", position_id=self.position.position_id)
            return
        if not ctx.calendar.is_trading_day(d):
            self.check("trade_day", False, detail="weekend or exchange holiday")
            raise SkipDay("market closed today")
        self.next_day = ctx.calendar.next_trading_day(d)
        gap = (self.next_day - d).days
        is_entry_day = gap >= cfg.filters.min_gap_days
        self.check("trade_day", is_entry_day, value=gap, threshold=cfg.filters.min_gap_days,
                   next_trading_day=str(self.next_day))
        if not is_entry_day:
            raise SkipDay(f"next trading day is {self.next_day:%a %d %b}: not the last day before a weekend")
        expiry = ctx.instruments.nearest_expiry(cfg.underlying, d)
        if expiry is None:
            raise SkipDay(f"no {cfg.underlying} option expiry found in instrument snapshot")
        if expiry <= self.next_day and expiry == d:
            raise SkipDay(f"today is expiry day ({expiry}): nothing left to hold over the weekend")
        self._load_chain(expiry)
        self.log.info("entry day: next trading day %s (gap %d days), expiry %s (DTE %d), lot size %d, stop -₹%.0f",
                      self.next_day, gap, expiry, self.dte, self.lot_size, self.stop_rupees)

    def start_feed(self) -> None:
        self.ctx.feed.subscribe([self.index_token, self.vix_token])
        self.feed_ready = True
        self._subscribe_strikes()

    def on_tick(self, now: datetime) -> None:
        if self.feed_ready and not self.strikes_subscribed and not self.adopted:
            self._subscribe_strikes()
        super().on_tick(now)

    def _subscribe_strikes(self) -> None:
        if self.adopted or self.finished:
            return
        spot = self.spot()
        if spot is None:
            return
        span = 0.06 * spot + self.cfg.entry.hedge_pts + self.cfg.entry.subscribe_buffer_pts
        tokens = [i.instrument_token for i in self.chain if i.strike is not None and abs(i.strike - spot) <= span]
        self.ctx.feed.subscribe(tokens)
        self.strikes_subscribed = True
        self.log.info("subscribed %d option tokens around spot %.0f (±%.0f)", len(tokens), spot, span)

    def expected_move(self, spot: float, vix: float, now: datetime) -> tuple[float, float]:
        """(points, calendar days) from now to the next trading day's open, scaled by VIX."""
        nxt = datetime.combine(self.next_day, MARKET_OPEN, tzinfo=now.tzinfo)
        days = max((nxt - now).total_seconds() / 86400, 0.5)
        return spot * vix / 100 * math.sqrt(days / 365), days

    def build_entry(self) -> Position | None:
        cfg, now = self.cfg, self.ctx.clock.now()
        vix = self.vix()
        if vix is None:
            return self._wait("no India VIX tick yet")
        if vix >= cfg.filters.vix_max:
            self.check("entry_vix", False, vix, cfg.filters.vix_max)
            raise SkipDay(f"India VIX {vix} >= {cfg.filters.vix_max}: weekend gap risk too high")
        if not self.events_logged:
            self.events_logged = True
            self._weekend_events(now)          # raises SkipDay when an event falls in the weekend gap
        spot = self.spot()
        if spot is None:
            return self._wait("no NIFTY tick yet")
        if not self.strikes_subscribed:
            self._subscribe_strikes()

        em, days = self.expected_move(spot, vix, now)
        distance = cfg.entry.expected_move_mult * em
        step = cfg.strike_step
        width = int(2 * math.ceil(distance / step) * step)
        quote = self.ctx.feed.latest
        balance = filters.premium_balance(spot, width / 2, self.chain, quote)
        if balance is None:
            return self._wait("no option prices for the CE/PE premium balance yet")
        if balance.ratio > cfg.filters.max_premium_ratio:
            reason = f"CE/PE premium imbalance ({balance.richer} richer)"
            if reason != self._entry_wait_reason:
                self.check("premium_balance", False, round(balance.ratio, 3), cfg.filters.max_premium_ratio,
                           ce=balance.ce, pe=balance.pe, richer=balance.richer, distance=balance.distance)
            return self._wait(reason)
        res = select_condor(spot, width, cfg.entry.hedge_pts, step, self.chain, quote, self.qty,
                            cfg.entry.max_spread_pts, cfg.entry.min_credit_pts)
        if isinstance(res, Rejection):
            return self._wait(res.reason)

        self.check("entry_vix", True, vix, cfg.filters.vix_max)
        self.check("premium_balance", True, round(balance.ratio, 3), cfg.filters.max_premium_ratio,
                   ce=balance.ce, pe=balance.pe, richer=balance.richer, distance=balance.distance)
        self.check("expected_move", True, round(em, 1), None, days=round(days, 2), mult=cfg.entry.expected_move_mult,
                   width=width, next_trading_day=str(self.next_day))
        p = res.plan
        self.log.info("entry plan: spot %.2f, expected move %.0f over %.1f days -> %s/%s PE, %s/%s CE | credit %.2f",
                      spot, em, days, p.long_pe, p.short_pe, p.short_ce, p.long_ce, res.net_credit_pts)
        return Position(structure=self.structure, underlying=cfg.underlying, legs=res.legs, expiry=self.expiry,
                        params=dict(dte=self.dte, width=width, hedge=cfg.entry.hedge_pts, atm=p.atm, spot=spot, vix=vix,
                                    lots=cfg.lots, lot_size=self.lot_size, quoted_credit=res.net_credit_pts,
                                    expected_move=round(em, 1), carry_days=round(days, 2),
                                    exit_day=str(self.next_day), premium_ratio=round(balance.ratio, 3)))

    def _weekend_events(self, now: datetime) -> None:
        """W3: no event between today's close and the next trading day's open that could gap the market."""
        from trading_core.market import events

        f = self.cfg.filters
        close = datetime.combine(self.ctx.trade_date, MARKET_CLOSE, tzinfo=now.tzinfo)
        reopen = datetime.combine(self.next_day, MARKET_OPEN, tzinfo=now.tzinfo)
        macro = self.market_events(close, reopen, category="economic", min_impact=f.event_min_impact)
        day0 = datetime.combine(self.ctx.trade_date, time(0), tzinfo=now.tzinfo)
        results = self.market_events(day0, reopen - timedelta(hours=9, minutes=15), category="results",
                                     symbols=f.results_symbols)
        found = [f"{e.ts:%a %H:%M} {e.country} {e.title} ({e.impact})" for e in macro] + \
                [f"{e.ts:%a} {e.title}" for e in results]
        fresh = events.covered(self.ctx.journal.db, now)
        have_calendar = fresh.get("forexfactory", False) and fresh.get("india", False)
        if not have_calendar and not self.ctx.is_replay:
            self.check("weekend_events", not f.require_calendar, None, None, events=found,
                       note="market calendar not fetched in the last 3 days: event check incomplete",
                       sources=fresh)
            if f.require_calendar:
                raise SkipDay("no market calendar to check the weekend against (require_calendar)")
        else:
            self.check("weekend_events", not found, len(found), 0, events=found[:10],
                       window=f"{close:%a %H:%M} - {reopen:%a %H:%M}", min_impact=f.event_min_impact)
        if found:
            raise SkipDay(f"weekend event(s): {'; '.join(found[:3])}")

    def _wait(self, reason: str) -> None:
        if reason != self._entry_wait_reason:
            self.log.info("entry waiting: %s", reason)
            self._entry_wait_reason = reason
        return None


ALGO = WeekendCondorAlgo
