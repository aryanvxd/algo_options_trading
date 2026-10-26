"""NIFTY weekly short iron condor (paper, spec v2: docs/strategy-criteria/iron-condor.md).

Day: trade-day check (expiry day + 2 trading days before it) -> subscribe NIFTY, VIX and strikes
around ATM -> 09:45-10:00 entry: VIX < 20, CE/PE premium balance, spreads, min credit (PCR logged
only) -> hedges first -> monitor exits every loop (kill, stale feed, stop, premium blow-out,
target, 15:15 time exit) -> report. No pre-open checks: the laptop is switched on at ~09:35.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from algos.iron_condor import filters
from algos.iron_condor.exits import PremiumBlowout
from algos.iron_condor.settings import IronCondorConfig
from algos.iron_condor.strike_selector import Rejection, select_condor
from trading_core.kite.instruments import InstrumentInfo
from trading_core.market.options import atm_strike
from trading_core.risk.engine import ExitRule
from trading_core.risk.rules import KillSwitch, PnlStop, PnlTarget, StaleFeed, TimeExit
from trading_core.strategy.base import BaseAlgo, SkipDay
from trading_core.strategy.position import Position


class IronCondorAlgo(BaseAlgo):
    config_model = IronCondorConfig
    structure = "IRON_CONDOR"
    cfg: IronCondorConfig

    def __init__(self, ctx, cfg: IronCondorConfig):
        super().__init__(ctx, cfg)
        self.expiry = None
        self.dte: int | None = None
        self.chain: list[InstrumentInfo] = []
        self.lot_size: int | None = None
        self.index_token: int | None = None
        self.vix_token: int | None = None
        self.feed_ready = False
        self.strikes_subscribed = False
        self.pcr_logged = False

    # ------------------------------------------------------------ sizing / levels

    @property
    def qty(self) -> int:
        return self.cfg.lots * (self.lot_size or 0)

    @property
    def stop_rupees(self) -> float:
        return self.cfg.exits.stop_loss_points * self.qty

    @property
    def target_rupees(self) -> float:
        return self.cfg.exits.target_points * self.qty

    def build_exit_rules(self) -> list[ExitRule]:
        e = self.cfg.exits
        return [
            KillSwitch(),
            StaleFeed(e.stale_feed_seconds),
            PnlStop(self.stop_rupees, e.pnl_basis),
            PremiumBlowout(e.premium_blowout_mult, e.short_leg_mult),
            PnlTarget(self.target_rupees, e.pnl_basis),
            TimeExit(self.cfg.schedule.square_off),
        ]

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
        credit, unit = pos.entry_credit_pts, pos.unit_qty
        return dict(
            entry_credit=credit,
            blowout_premium=round(credit * self.cfg.exits.premium_blowout_mult, 2),
            stop_rupees=-self.stop_rupees, target_rupees=self.target_rupees, pnl_basis=self.cfg.exits.pnl_basis,
            # premium levels equivalent to the rupee stop/target (ignoring charges)
            stop_premium=round(credit + self.stop_rupees / unit, 2),
            target_premium=round(credit - self.target_rupees / unit, 2),
        )

    # ------------------------------------------------------------ lifecycle hooks

    def day_check(self) -> None:
        ctx, cfg = self.ctx, self.cfg
        d = ctx.trade_date
        if not ctx.calendar.is_trading_day(d):
            self.check("trade_day", False, detail="weekend or exchange holiday")
            raise SkipDay("market closed today")

        expiry = ctx.instruments.nearest_expiry(cfg.underlying, d)
        if expiry is None:
            raise SkipDay(f"no {cfg.underlying} option expiry found in instrument snapshot")
        dte = ctx.calendar.trading_days_until(d, expiry)
        in_window = dte < cfg.trade_window_days
        self.check("trade_day", in_window, value=dte, threshold=cfg.trade_window_days - 1, expiry=str(expiry))
        if not in_window:
            raise SkipDay(f"{dte} trading days to expiry {expiry}; trades only in the last "
                          f"{cfg.trade_window_days} days")

        self.expiry, self.dte = expiry, dte
        self.chain = ctx.instruments.option_chain(cfg.underlying, expiry, d)
        if not self.chain:
            raise SkipDay(f"empty option chain for {expiry}")
        self.lot_size = self.chain[0].lot_size

        index = ctx.instruments.by_symbol("NSE", cfg.index_symbol, d)
        vix = ctx.instruments.by_symbol("NSE", cfg.vix_symbol, d)
        if index is None or vix is None:
            raise SkipDay(f"index tokens not found ({cfg.index_symbol}, {cfg.vix_symbol})")
        self.index_token, self.vix_token = index.instrument_token, vix.instrument_token
        self.log.info("expiry %s, DTE %d, width %d, hedge %d, lot size %d | stop -₹%.0f, target +₹%.0f (%s P&L)",
                      expiry, dte, cfg.width_for(dte), cfg.hedge_for(dte), self.lot_size, self.stop_rupees,
                      self.target_rupees, cfg.exits.pnl_basis)

    def start_feed(self) -> None:
        self.ctx.feed.subscribe([self.index_token, self.vix_token])
        self.feed_ready = True
        self._subscribe_strikes()

    def on_tick(self, now: datetime) -> None:
        # The first NIFTY tick can arrive a moment after the feed starts; centre the strikes on it then.
        if self.feed_ready and not self.strikes_subscribed:
            self._subscribe_strikes()
        super().on_tick(now)

    def _subscribe_strikes(self) -> None:
        cfg = self.cfg
        if self.dte is None or self.finished:
            return
        spot = self.spot()
        if spot is None:
            return
        atm = atm_strike(spot, cfg.strike_step)
        span = max(cfg.width_for(self.dte) / 2 + cfg.hedge_for(self.dte) + cfg.subscribe_buffer_pts,
                   cfg.filters.pcr_range_pts)
        tokens = [i.instrument_token for i in self.chain if i.strike is not None and abs(i.strike - atm) <= span]
        self.ctx.feed.subscribe(tokens)
        self.strikes_subscribed = True
        self.log.info("subscribed %d option tokens around ATM %s (±%s)", len(tokens), atm, span)

    def build_entry(self) -> Position | None:
        cfg = self.cfg
        vix = self.vix()
        if vix is None:
            return self._wait("no India VIX tick yet")
        if vix >= cfg.filters.vix_max:
            self.check("entry_vix", False, vix, cfg.filters.vix_max)
            raise SkipDay(f"India VIX {vix} >= {cfg.filters.vix_max} at entry")
        spot = self.spot()
        if spot is None:
            return self._wait("no NIFTY tick yet")
        if not self.strikes_subscribed:
            self._subscribe_strikes()
        quote = self.ctx.feed.latest

        atm = atm_strike(spot, cfg.strike_step)
        pcr = filters.pcr(atm, cfg.filters.pcr_range_pts, self.chain, quote)
        if pcr is not None and not self.pcr_logged:
            self.check("pcr", True, pcr, None, log_only=True, range_pts=cfg.filters.pcr_range_pts)
            self.pcr_logged = True

        width, hedge = cfg.width_for(self.dte), cfg.hedge_for(self.dte)
        balance = filters.premium_balance(spot, width / 2, self.chain, quote)
        if balance is None:
            return self._wait("no option prices for the CE/PE premium balance yet")
        if balance.ratio > cfg.filters.max_premium_ratio:
            reason = f"CE/PE premium imbalance ({balance.richer} richer)"
            if reason != self._entry_wait_reason:
                self.check("premium_balance", False, round(balance.ratio, 3), cfg.filters.max_premium_ratio,
                           ce=balance.ce, pe=balance.pe, richer=balance.richer, distance=balance.distance)
            return self._wait(reason)

        res = select_condor(spot, width, hedge, cfg.strike_step, self.chain, quote, self.qty,
                            cfg.entry.max_spread_pts, cfg.entry.min_credit_pts)
        if isinstance(res, Rejection):
            return self._wait(res.reason)

        self.check("entry_vix", True, vix, cfg.filters.vix_max)
        self.check("premium_balance", True, round(balance.ratio, 3), cfg.filters.max_premium_ratio,
                   ce=balance.ce, pe=balance.pe, richer=balance.richer, distance=balance.distance)
        if not self.pcr_logged:
            self.check("pcr", True, pcr, None, log_only=True, note="no OI data")
            self.pcr_logged = True
        p = res.plan
        self.log.info("entry plan: spot %.2f ATM %s | %s/%s PE, %s/%s CE | quoted credit %.2f | "
                      "CE/PE %.2f/%.2f | PCR %s", spot, p.atm, p.long_pe, p.short_pe, p.short_ce, p.long_ce,
                      res.net_credit_pts, balance.ce, balance.pe, pcr)
        return Position(structure=self.structure, underlying=cfg.underlying, legs=res.legs, expiry=self.expiry,
                        params=dict(dte=self.dte, width=width, hedge=hedge, atm=p.atm, spot=spot, vix=vix,
                                    lots=cfg.lots, lot_size=self.lot_size, quoted_credit=res.net_credit_pts,
                                    premium_ratio=round(balance.ratio, 3), premium_richer=balance.richer,
                                    pcr=pcr))

    def _wait(self, reason: str) -> None:
        if reason != self._entry_wait_reason:
            self.log.info("entry waiting: %s", reason)
            self._entry_wait_reason = reason
        return None


ALGO = IronCondorAlgo
