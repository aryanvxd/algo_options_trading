"""NIFTY condor move hedge (paper, spec: docs/strategy-criteria/condor-hedge.md).

The balanced condor loses when NIFTY runs away from where it was sold. This buyer earns on that
run: anchor = NIFTY at the condor's entry (``anchor.source: condor``) or at a fixed minute
(``time``, used by backtests) -> from 10:00, once NIFTY has closed ``move_pts`` from the anchor on
``confirm_bars`` 1-minute closes in a row, buy the option in that direction (PE when NIFTY falls),
``otm_strikes`` out of the money -> exits every second (kill, stale feed, premium stop, day loss,
NIFTY giving back ``trail_pts`` from its best 1-minute close since entry, 15:15) -> optionally look
for the next one (``max_trades``), entries until 14:45.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from algos.condor_hedge.settings import WEEKDAYS, CondorHedgeConfig
from algos.iron_condor.filters import mid
from trading_core.broker.base import BUY
from trading_core.db import models as m
from trading_core.kite.instruments import InstrumentInfo
from trading_core.marketdata.renko import Bar, MinuteBars
from trading_core.risk.engine import ExitRule, RiskContext
from trading_core.risk.rules import KillSwitch, MaxDailyLoss, StaleFeed, TimeExit
from trading_core.strategy.base import BaseAlgo, SkipDay
from trading_core.strategy.position import Leg, Position

# Backtests: each backtest has its own database, so the condor's entry is not there to read; its
# entry is at ~09:50 every day, so the 09:49 candle's close stands in for it.
BACKTEST_OVERRIDES = {"anchor": {"source": "time"}}
CONDOR_POLL_S = 5


class _Rule(ExitRule):
    def __init__(self, name: str, reason):
        self.name, self.reason = name, reason

    def check(self, ctx: RiskContext) -> str | None:
        return self.reason(ctx)


class CondorHedgeAlgo(BaseAlgo):
    config_model = CondorHedgeConfig
    structure = "LONG_OPTION"
    cfg: CondorHedgeConfig

    def __init__(self, ctx, cfg: CondorHedgeConfig):
        super().__init__(ctx, cfg)
        self.expiry = None
        self.dte: int | None = None
        self.chain: list[InstrumentInfo] = []
        self.lot_size: int | None = None
        self.index_token: int | None = None
        self.vix_token: int | None = None
        self.feed_ready = False
        self.subscribed_centre: float | None = None
        self.bars = MinuteBars()
        self.last_bar: Bar | None = None
        self.anchor: float | None = None
        self.anchor_from: str | None = None
        self._next_condor_poll: datetime | None = None
        self.streak = {1: 0, -1: 0}
        self.signal: int | None = None            # +1 / -1 once the move is confirmed
        self.trade_dir: int | None = None
        self.best: float | None = None            # best 1-minute close in the trade's favour
        self.trail_reason: str | None = None
        self.vix_logged = False

    # ------------------------------------------------------------ sizing / prices

    @property
    def qty(self) -> int:
        return self.cfg.lots * (self.lot_size or 0)

    def spot(self) -> float | None:
        t = self.ctx.feed.latest(self.index_token) if self.index_token else None
        return t.ltp if t else None

    def vix(self) -> float | None:
        t = self.ctx.feed.latest(self.vix_token) if self.vix_token else None
        return t.ltp if t else None

    def move(self) -> float | None:
        s = self.last_bar.close if self.last_bar else None
        return round(s - self.anchor, 2) if s is not None and self.anchor is not None else None

    def levels(self) -> dict[str, Any] | None:
        t, e = self.cfg.trigger, self.cfg.exits
        out: dict[str, Any] = dict(
            anchor=self.anchor, anchor_from=self.anchor_from, move=self.move(), move_pts=t.move_pts,
            trigger_up=self.anchor + t.move_pts if self.anchor is not None else None,
            trigger_down=self.anchor - t.move_pts if self.anchor is not None else None,
            confirm_bars=t.confirm_bars, streak_up=self.streak[1], streak_down=self.streak[-1],
            trades_used=len(self.trades), max_trades=self.cfg.max_trades, realized_net=self.realized_net,
            dte=self.dte)
        if self.trade_dir is not None and self.best is not None:
            out.update(best=self.best, trail_pts=e.trail_pts, trail_exit=self.best - self.trade_dir * e.trail_pts)
        return out

    # ------------------------------------------------------------ exits

    def build_exit_rules(self) -> list[ExitRule]:
        return [
            KillSwitch(),
            StaleFeed(self.cfg.exits.stale_feed_seconds),
            _Rule("STOP_LOSS", self._premium_stop),
            MaxDailyLoss(self.cfg.limits.max_day_loss),
            _Rule("TRAILING_STOP", lambda ctx: self.trail_reason),
            TimeExit(self.cfg.schedule.square_off),
        ]

    def _premium_stop(self, ctx: RiskContext) -> str | None:
        pct = self.cfg.exits.premium_stop_pct
        if not pct or ctx.mark is None or not ctx.mark.complete:
            return None
        for l in ctx.mark.legs:
            if l.close_price is not None and l.entry_price and l.close_price <= l.entry_price * (1 - pct / 100):
                return f"{l.tradingsymbol} {l.close_price:.2f} <= entry {l.entry_price:.2f} - {pct:g}%"
        return None

    def reentry_block(self, now: datetime) -> str | None:
        block = super().reentry_block(now)
        if block:
            return block
        if self.realized_net <= -self.cfg.limits.max_day_loss:
            return f"day net ₹{self.realized_net:.0f} <= -₹{self.cfg.limits.max_day_loss:.0f}"
        return None

    def _after_trade(self, now: datetime) -> None:
        self.trade_dir = self.best = self.trail_reason = None
        self.streak, self.signal = {1: 0, -1: 0}, None
        super()._after_trade(now)

    # ------------------------------------------------------------ lifecycle hooks

    def day_check(self) -> None:
        ctx, cfg = self.ctx, self.cfg
        d = ctx.trade_date
        if not ctx.calendar.is_trading_day(d):
            self.check("trade_day", False, detail="weekend or exchange holiday")
            raise SkipDay("market closed today")
        weekday = WEEKDAYS[d.weekday()]
        if weekday not in cfg.trade_weekdays:
            self.check("trade_day", False, weekday=weekday, trade_weekdays=cfg.trade_weekdays)
            raise SkipDay(f"{weekday}: trades only on {', '.join(cfg.trade_weekdays)}")
        expiry = ctx.instruments.nearest_expiry(cfg.underlying, d)
        if expiry is None:
            raise SkipDay(f"no {cfg.underlying} option expiry found in instrument snapshot")
        self.expiry, self.dte = expiry, ctx.calendar.trading_days_until(d, expiry)
        self.check("trade_day", True, value=self.dte, expiry=str(expiry), weekday=weekday)
        self.chain = ctx.instruments.option_chain(cfg.underlying, expiry, d)
        if not self.chain:
            raise SkipDay(f"empty option chain for {expiry}")
        self.lot_size = self.chain[0].lot_size
        index = ctx.instruments.by_symbol("NSE", cfg.index_symbol, d)
        vix = ctx.instruments.by_symbol("NSE", cfg.vix_symbol, d)
        if index is None or vix is None:
            raise SkipDay(f"index tokens not found ({cfg.index_symbol}, {cfg.vix_symbol})")
        self.index_token, self.vix_token = index.instrument_token, vix.instrument_token
        a, t, e = cfg.anchor, cfg.trigger, cfg.exits
        self.log.info("expiry %s, DTE %d | anchor %s | buy %d strike(s) OTM once NIFTY is %g pts from it on "
                      "%d close(s) | exit on a %g-pt give-back or -%g%% premium", expiry, self.dte,
                      f"{a.algo}'s entry" if a.source == "condor" else f"{a.time:%H:%M} close",
                      cfg.option.otm_strikes, t.move_pts, t.confirm_bars, e.trail_pts, e.premium_stop_pct)

    def start_feed(self) -> None:
        self.ctx.feed.subscribe([self.index_token, self.vix_token])
        self.feed_ready = True
        self._subscribe_strikes()

    def _subscribe_strikes(self) -> None:
        spot = self.spot()
        if spot is None or self.finished:
            return
        o = self.cfg.option
        if self.subscribed_centre is not None and abs(spot - self.subscribed_centre) < o.resubscribe_drift_pts:
            return
        tokens = [i.instrument_token for i in self.chain
                  if i.strike is not None and abs(i.strike - spot) <= o.subscribe_range_pts]
        self.ctx.feed.subscribe(tokens)
        self.subscribed_centre = spot
        self.log.info("subscribed %d option tokens around spot %.0f (±%d)", len(tokens), spot, o.subscribe_range_pts)

    # ------------------------------------------------------------ every loop

    def on_tick(self, now: datetime) -> None:
        if self.feed_ready and not self.finished:
            self._subscribe_strikes()
            spot = self.spot()
            closed = self.bars.add(now, spot) if spot is not None else []
            closed += self.bars.flush(now)
            if self.cfg.anchor.source == "condor" and self.anchor is None:
                self._poll_condor(now)
            for bar in closed:
                self._on_bar(bar)
        super().on_tick(now)

    def _set_anchor(self, value: float, source: str) -> None:
        self.anchor, self.anchor_from = round(value, 2), source
        t = self.cfg.trigger
        self.check("anchor", True, self.anchor, t.move_pts, log_only=True, source=source,
                   trigger_up=round(self.anchor + t.move_pts, 2), trigger_down=round(self.anchor - t.move_pts, 2))

    def _poll_condor(self, now: datetime) -> None:
        if self._next_condor_poll is not None and now < self._next_condor_poll:
            return
        self._next_condor_poll = now + timedelta(seconds=CONDOR_POLL_S)
        a, ctx = self.cfg.anchor, self.ctx
        with ctx.journal.db.session() as s:
            row = s.execute(
                select(m.Position.spot_at_entry, m.Position.entry_time)
                .join(m.AlgoRun, m.AlgoRun.run_id == m.Position.run_id)
                .where(m.AlgoRun.algo_id == a.algo, m.AlgoRun.trade_date == ctx.trade_date,
                       m.AlgoRun.mode == ctx.mode, m.AlgoRun.source == ctx.source,
                       m.Position.spot_at_entry.is_not(None))
                .order_by(m.Position.position_id)).first()
        if row is not None:
            self._set_anchor(row.spot_at_entry, f"{a.algo} entry {row.entry_time:%H:%M:%S}")

    def _time_anchor(self, bar: Bar) -> None:
        a = self.cfg.anchor
        if bar.ts.time() == a.time:
            self._set_anchor(bar.close, f"{a.time:%H:%M} close")
            return
        # Started after the anchor minute: its candle from Kite (live) or the stored bars (replay).
        ctx, d = self.ctx, self.ctx.trade_date
        at = datetime.combine(d, a.time, tzinfo=bar.ts.tzinfo)
        close = None
        if ctx.kite is not None and not ctx.is_replay:
            try:
                candles = ctx.kite.historical(self.index_token, at, at + timedelta(seconds=59), "minute")
                close = candles[0]["close"] if candles else None
            except Exception as e:  # noqa: BLE001 - fall back to the first minute we saw
                self.log.warning("anchor candle unavailable: %s", e)
        if close is None:
            close = next((b.close for b in ctx.journal.bars(d, self.index_token) if b.ts.time() == a.time), None)
        if close is not None:
            self._set_anchor(close, f"{a.time:%H:%M} close (fetched)")
        else:
            self._set_anchor(bar.close, f"late start: {bar.ts:%H:%M} close")

    def _on_bar(self, bar: Bar) -> None:
        self.last_bar = bar
        if self.anchor is None and self.cfg.anchor.source == "time" and bar.ts.time() >= self.cfg.anchor.time:
            self._time_anchor(bar)
        if self.anchor is None:
            return
        if self.trade_dir is not None:                # in a trade: follow NIFTY's best close
            d = self.trade_dir
            self.best = bar.close if self.best is None else (max if d > 0 else min)(self.best, bar.close)
            give_back = (self.best - bar.close) * d
            if give_back >= self.cfg.exits.trail_pts and self.trail_reason is None:
                self.trail_reason = (f"NIFTY {bar.close:.0f} gave back {give_back:.0f} pts from its best "
                                     f"{self.best:.0f} (limit {self.cfg.exits.trail_pts:g})")
            return
        if bar.ts.time() < self.cfg.schedule.entry:
            return
        t = self.cfg.trigger
        move = bar.close - self.anchor
        for d in (1, -1):
            self.streak[d] = self.streak[d] + 1 if move * d >= t.move_pts else 0
        self.signal = 1 if self.streak[1] >= t.confirm_bars else -1 if self.streak[-1] >= t.confirm_bars else None

    # ------------------------------------------------------------ entry

    def build_entry(self) -> Position | None:
        now = self.ctx.clock.now()
        if not self.vix_logged and self.vix() is not None:
            self.vix_logged = True
            self.check("vix", True, self.vix(), None, log_only=True)
        a = self.cfg.anchor
        if self.anchor is None:
            if a.source == "condor" and now.time() > a.wait_until:
                raise SkipDay(f"{a.algo} has not entered by {a.wait_until:%H:%M}: nothing to hedge")
            return self._wait(f"waiting for the anchor ({a.algo}'s entry)" if a.source == "condor"
                              else f"waiting for the {a.time:%H:%M} close")
        if self.signal is None:
            mv = self.move()
            return self._wait(f"NIFTY {mv:+.0f} from {self.anchor:.0f}; needs ±{self.cfg.trigger.move_pts:g} on "
                              f"{self.cfg.trigger.confirm_bars} close(s)" if mv is not None else "waiting for a close")
        d, spot = self.signal, self.spot()
        side = "CE" if d > 0 else "PE"
        pick = self._pick_option(side, spot, now)
        if isinstance(pick, str):
            return self._wait(f"trigger {side}: {pick}")
        inst, price = pick
        mv = self.move()
        self.check("trigger", True, mv, self.cfg.trigger.move_pts, anchor=self.anchor, anchor_from=self.anchor_from,
                   side=side, option=inst.tradingsymbol, premium=price, spot=spot,
                   streak=self.streak[d], confirm_bars=self.cfg.trigger.confirm_bars)
        self.trade_dir, self.best, self.trail_reason = d, spot, None
        self.signal, self.streak = None, {1: 0, -1: 0}
        leg = Leg(role=f"LONG_{side}", instrument_token=inst.instrument_token, tradingsymbol=inst.tradingsymbol,
                  side=BUY, qty=self.qty, option_type=side, strike=inst.strike)
        return Position(structure=self.structure, underlying=self.cfg.underlying, legs=[leg], expiry=self.expiry,
                        params=dict(side=side, dte=self.dte, spot=spot, vix=self.vix(), anchor=self.anchor,
                                    anchor_from=self.anchor_from, move=mv, lots=self.cfg.lots,
                                    lot_size=self.lot_size, trade_no=len(self.trades) + 1))

    def _pick_option(self, side: str, spot: float, now: datetime) -> tuple[InstrumentInfo, float] | str:
        o = self.cfg.option
        atm = round(spot / o.strike_step) * o.strike_step
        k = atm + (o.strike_step * o.otm_strikes if side == "CE" else -o.strike_step * o.otm_strikes)
        inst = next((i for i in self.chain if i.instrument_type == side and i.strike == k), None)
        if inst is None:
            return f"{side} {k:.0f} not listed"
        t = self.ctx.feed.latest(inst.instrument_token)
        price = mid(t)
        if t is None or price is None:
            return f"{inst.tradingsymbol}: no quote"
        if (now - t.received_at).total_seconds() > o.max_quote_age_s:
            return f"{inst.tradingsymbol}: quote {(now - t.received_at).total_seconds():.0f}s old"
        if t.spread is None or t.spread > o.max_spread_pts:
            return f"{inst.tradingsymbol}: spread {t.spread} > {o.max_spread_pts:g}"
        if price < o.premium_min:
            return f"{inst.tradingsymbol}: premium {price:.2f} < {o.premium_min:g}"
        return inst, round(price, 2)

    def _wait(self, reason: str) -> None:
        if reason != self._entry_wait_reason:
            self.log.info("entry waiting: %s", reason)
            self._entry_wait_reason = reason
        return None


ALGO = CondorHedgeAlgo
