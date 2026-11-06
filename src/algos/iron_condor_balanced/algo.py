"""NIFTY weekly short iron condor, balanced premium (paper, spec: docs/strategy-criteria/iron-condor-balanced.md).

The original condor (``algos.iron_condor``) with these changes:
- the CE/PE premium balance is journaled only, never a reason to wait or skip;
- the shorts are the PE and CE whose premiums are closest to the average of the same-width pair
  (``strikes.mode: width`` restores the original strikes);
- minimum credit, stop and target are set per DTE; last entry 10:30;
- a trailing profit stop gives back only so much of a trade's peak (2-4 DTE).
Day check, feed, VIX, PCR and events logging, hedges-first execution and the exit rules are the
original's.
"""

from __future__ import annotations

from datetime import datetime, time

from algos.iron_condor import filters
from algos.iron_condor.algo import IronCondorAlgo
from algos.iron_condor.strike_selector import Rejection, price_plan, select_condor
from algos.iron_condor_balanced.exits import ProfitTrail
from algos.iron_condor_balanced.settings import IronCondorBalancedConfig
from algos.iron_condor_balanced.strike_selector import pick_balanced
from trading_core.market.options import atm_strike
from trading_core.risk.engine import ExitRule
from trading_core.risk.rules import PnlStop
from trading_core.strategy.base import SkipDay
from trading_core.strategy.position import Position


class IronCondorBalancedAlgo(IronCondorAlgo):
    config_model = IronCondorBalancedConfig
    cfg: IronCondorBalancedConfig

    def __init__(self, ctx, cfg: IronCondorBalancedConfig):
        super().__init__(ctx, cfg)
        self.balance_logged = False

    @property
    def _dte(self) -> int:
        return self.dte if self.dte is not None else 0

    @property
    def stop_rupees(self) -> float:
        return self.cfg.stop_for(self._dte) * self.qty

    @property
    def target_rupees(self) -> float:
        return self.cfg.target_for(self._dte) * self.qty

    def build_exit_rules(self) -> list[ExitRule]:
        rules = super().build_exit_rules()
        arm, give = self.cfg.trail_for(self._dte)
        if arm > 0:         # right after the stop, in the priority order of the spec
            at = next(i for i, r in enumerate(rules) if isinstance(r, PnlStop)) + 1
            rules.insert(at, ProfitTrail(arm * self.qty, give * self.qty, self.cfg.exits.pnl_basis))
        return rules

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
        dte = self._dte

        atm = atm_strike(spot, cfg.strike_step)
        pcr = filters.pcr(atm, cfg.filters.pcr_range_pts, self.chain, quote)
        if not self.events_logged:
            self.events_logged = True
            now = self.ctx.clock.now()
            prev = self.ctx.calendar.prev_trading_day(self.ctx.trade_date)
            self.log_events("events", datetime.combine(prev, time(15, 30), tzinfo=now.tzinfo),
                            datetime.combine(self.ctx.trade_date, time(15, 30), tzinfo=now.tzinfo))
        if pcr is not None and not self.pcr_logged:
            self.check("pcr", True, pcr, None, log_only=True, range_pts=cfg.filters.pcr_range_pts)
            self.pcr_logged = True

        width, hedge = cfg.width_for(dte), cfg.hedge_for(dte)
        balance = filters.premium_balance(spot, width / 2, self.chain, quote)
        if balance is None:
            return self._wait("no option prices for the CE/PE premium balance yet")
        if not self.balance_logged:     # log only: a big imbalance is not a reason to skip (spec B4)
            self.balance_logged = True
            self.check("premium_balance", True, round(balance.ratio, 3), None, log_only=True, ce=balance.ce,
                       pe=balance.pe, richer=balance.richer, distance=balance.distance)

        min_credit = cfg.min_credit_for(dte)
        pick = None
        if cfg.strikes.mode == "balanced":
            pick = pick_balanced(spot, width, hedge, cfg.strike_step, self.chain, quote,
                                 cfg.min_distance_for(dte), cfg.strikes.max_shift_pts)
            if pick is None:
                return self._wait("no option prices for the balanced strikes yet")
            res = price_plan(pick.plan, self.chain, quote, self.qty, cfg.entry.max_spread_pts, min_credit)
        else:
            res = select_condor(spot, width, hedge, cfg.strike_step, self.chain, quote, self.qty,
                                cfg.entry.max_spread_pts, min_credit)
        if isinstance(res, Rejection):
            return self._wait(res.reason)

        p = res.plan
        self.check("entry_vix", True, vix, cfg.filters.vix_max)
        strikes = dict(mode=cfg.strikes.mode, short_pe=p.short_pe, short_ce=p.short_ce,
                       pe_distance=round(spot - p.short_pe, 1), ce_distance=round(p.short_ce - spot, 1))
        if pick is not None:
            strikes.update(width_pe=pick.width_pe, width_ce=pick.width_ce, target=pick.target,
                           pe_mid=pick.pe_mid, ce_mid=pick.ce_mid)
        self.check("strikes", True, round(p.short_ce - p.short_pe, 1), width, log_only=True, **strikes)
        if not self.pcr_logged:
            self.check("pcr", True, pcr, None, log_only=True, note="no OI data")
            self.pcr_logged = True
        self.log.info("entry plan: spot %.2f ATM %s | %s/%s PE, %s/%s CE (%s) | quoted credit %.2f | "
                      "CE/PE balance %.2f/%.2f | PCR %s", spot, p.atm, p.long_pe, p.short_pe, p.short_ce,
                      p.long_ce, cfg.strikes.mode, res.net_credit_pts, balance.ce, balance.pe, pcr)
        return Position(structure=self.structure, underlying=cfg.underlying, legs=res.legs, expiry=self.expiry,
                        params=dict(dte=dte, width=width, hedge=hedge, atm=p.atm, spot=spot, vix=vix,
                                    lots=cfg.lots, lot_size=self.lot_size, quoted_credit=res.net_credit_pts,
                                    strike_mode=cfg.strikes.mode, premium_ratio=round(balance.ratio, 3),
                                    premium_richer=balance.richer, pcr=pcr,
                                    target_premium=pick.target if pick else None,
                                    stop_points=cfg.stop_for(dte), target_points=cfg.target_for(dte),
                                    trail_points=cfg.trail_for(dte)))


ALGO = IronCondorBalancedAlgo
