"""NIFTY long strangle scalp (paper, spec: docs/strategy-criteria/long-strangle-scalp.md).

Day: Mondays and Tuesdays (``trade_weekdays``) -> subscribe NIFTY, India VIX, NIFTY futures and
strikes around spot -> from 09:45 watch an OTM call/put pair (₹15-30 premium on expiry day, delta
~0.30 otherwise) -> buy both when the pair's value keeps rising (the futures move is logged, and
required only with ``signal.require_fut_move``) -> exits every second (kill,
stale feed, stop, day loss, target, trailing stop, stall, max hold, 10:45) -> after a 3-minute
cool-down look for the next one: at most 3 trades, entries until 10:30, stop after 2 losses in a
row or the day-loss limit.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from algos.strangle_scalp.entry_signal import PairSignal
from algos.strangle_scalp.pair_selector import Pair, mid, quote_ok, select_pair
from algos.strangle_scalp.settings import WEEKDAYS, StrangleScalpConfig
from trading_core.broker.base import BUY
from trading_core.kite.instruments import InstrumentInfo
from trading_core.risk.engine import ExitRule
from trading_core.risk.rules import (KillSwitch, MaxDailyLoss, MaxHold, PnlStop, PnlTarget, StaleFeed, Stall,
                                     TimeExit, TrailingStop)
from trading_core.strategy.base import ENTRY_PENDING, BaseAlgo, SkipDay
from trading_core.strategy.position import Leg, Position

OPENING_RANGE = (time(9, 15), time(9, 45))


class StrangleScalpAlgo(BaseAlgo):
    config_model = StrangleScalpConfig
    structure = "LONG_STRANGLE"
    cfg: StrangleScalpConfig

    def __init__(self, ctx, cfg: StrangleScalpConfig):
        super().__init__(ctx, cfg)
        self.expiry = None
        self.dte: int | None = None
        self.chain: list[InstrumentInfo] = []
        self.lot_size: int | None = None
        self.index_token: int | None = None
        self.vix_token: int | None = None
        self.fut: InstrumentInfo | None = None
        self.feed_ready = False
        self.subscribed_centre: float | None = None
        self.pair: Pair | None = None
        self.pair_reason: str | None = None
        self._next_reselect: datetime | None = None
        self.signal = PairSignal(cfg.signal)
        self.last_state = None
        self.vix_logged = False
        self.opening_range: tuple[float, float] | None = None
        self._range_tried = False

    # ------------------------------------------------------------ sizing / levels

    @property
    def qty(self) -> int:
        return self.cfg.lots * (self.lot_size or 0)

    def rupees(self, points: float) -> float:
        return round(points * self.qty, 2)

    @property
    def min_move(self) -> float:
        """Futures move the signal needs (0 = not required)."""
        s = self.cfg.signal
        if not s.require_fut_move:
            return 0.0
        return s.min_fut_move_pts_0dte if self.dte == 0 else s.min_fut_move_pts

    def build_exit_rules(self) -> list[ExitRule]:
        e = self.cfg.exits
        return [
            KillSwitch(),
            StaleFeed(e.stale_feed_seconds),
            PnlStop(self.rupees(e.stop_loss_points), e.pnl_basis),
            MaxDailyLoss(self.cfg.limits.max_day_loss),
            PnlTarget(self.rupees(e.target_points), e.pnl_basis),
            TrailingStop(self.rupees(e.trail_activate_points), e.trail_keep_fraction, e.pnl_basis),
            Stall(e.stall_seconds, e.pnl_basis),
            MaxHold(e.max_hold_seconds),
            TimeExit(self.cfg.schedule.square_off),
        ]

    def spot(self) -> float | None:
        t = self.ctx.feed.latest(self.index_token) if self.index_token else None
        return t.ltp if t else None

    def vix(self) -> float | None:
        t = self.ctx.feed.latest(self.vix_token) if self.vix_token else None
        return t.ltp if t else None

    def fut_price(self) -> float | None:
        t = self.ctx.feed.latest(self.fut.instrument_token) if self.fut else None
        return t.ltp if t and t.ltp else self.spot()

    def levels(self) -> dict[str, Any] | None:
        e, lim = self.cfg.exits, self.cfg.limits
        out: dict[str, Any] = dict(
            trades_used=len(self.trades), max_trades=self.cfg.max_trades, realized_net=self.realized_net,
            losses_in_row=self._losses_in_row(), max_consecutive_losses=lim.max_consecutive_losses,
            max_day_loss=lim.max_day_loss, dte=self.dte,
            stop_rupees=-self.rupees(e.stop_loss_points), target_rupees=self.rupees(e.target_points),
            trail_activate_rupees=self.rupees(e.trail_activate_points), pnl_basis=e.pnl_basis,
            pair=self.pair.describe() if self.pair else None, pair_reason=self.pair_reason,
            signal=self.last_state.as_dict() if self.last_state else None,
            windows_needed=self.cfg.signal.windows, min_rise_pct=self.cfg.signal.min_rise_pct,
            min_fut_move=self.min_move, fut=self.fut_price(), values=self.signal.recent(60),
        )
        pos = self.position
        if pos is not None and pos.entry_credit_pts is not None:
            out["entry_cost"] = -pos.entry_credit_pts
            trail = next((r for r in (self._risk.rules if self._risk else []) if isinstance(r, TrailingStop)), None)
            out["trail_peak"] = trail.peak if trail else None
        return out

    # ------------------------------------------------------------ day limits

    def _losses_in_row(self) -> int:
        n = 0
        for p in reversed([p for p in self.trades if p.is_flat]):
            if round(p.realized_gross - p.total_charges, 2) < 0:
                n += 1
            else:
                break
        return n

    def reentry_block(self, now: datetime) -> str | None:
        block = super().reentry_block(now)
        if block:
            return block
        lim = self.cfg.limits
        if self._losses_in_row() >= lim.max_consecutive_losses:
            return f"{self._losses_in_row()} losing trades in a row"
        if self.realized_net <= -lim.max_day_loss:
            return f"day net ₹{self.realized_net:.0f} <= -₹{lim.max_day_loss:.0f}"
        if lim.day_profit_lock is not None and self.realized_net >= lim.day_profit_lock:
            return f"day net ₹{self.realized_net:.0f} >= profit lock ₹{lim.day_profit_lock:.0f}"
        return None

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
        self.fut = ctx.instruments.nearest_future(cfg.underlying, d)
        self.log.info("expiry %s, DTE %d, lot size %d, future %s | stop -₹%.0f, target +₹%.0f (%s P&L), "
                      "up to %d trades", expiry, self.dte, self.lot_size,
                      self.fut.tradingsymbol if self.fut else "none (S4 uses spot)",
                      self.rupees(cfg.exits.stop_loss_points), self.rupees(cfg.exits.target_points),
                      cfg.exits.pnl_basis, cfg.max_trades)

    def start_feed(self) -> None:
        tokens = [self.index_token, self.vix_token] + ([self.fut.instrument_token] if self.fut else [])
        self.ctx.feed.subscribe(tokens)
        self.feed_ready = True
        self._subscribe_strikes()

    def _subscribe_strikes(self) -> None:
        if self.dte is None or self.finished:
            return
        spot = self.spot()
        if spot is None:
            return
        if self.subscribed_centre is not None and \
                abs(spot - self.subscribed_centre) < self.cfg.pair.resubscribe_drift_pts:
            return
        span = self.cfg.pair.subscribe_range_pts
        tokens = [i.instrument_token for i in self.chain if i.strike is not None and abs(i.strike - spot) <= span]
        self.ctx.feed.subscribe(tokens)       # adds to what is already subscribed
        self.subscribed_centre = spot
        self.log.info("subscribed %d option tokens around spot %.0f (±%d)", len(tokens), spot, span)

    def on_tick(self, now: datetime) -> None:
        if self.feed_ready:
            self._subscribe_strikes()
        if self.state == ENTRY_PENDING:
            self._watch(now)
        super().on_tick(now)

    def _watch(self, now: datetime) -> None:
        """While flat: keep a pair selected and sample its value every loop (also during the
        cool-down, so the signal has history the moment entries reopen)."""
        spot = self.spot()
        if spot is None:
            return
        building = self.pair is not None and self.last_state is not None and self.last_state.windows_up > 0
        if building and self._next_reselect is not None and now >= self._next_reselect:
            # V is rising: re-selecting now could swap the pair mid-move and wipe the signal's history.
            self._next_reselect = now + timedelta(seconds=self.cfg.signal.window_s)
        if self._next_reselect is None or now >= self._next_reselect:
            self._next_reselect = now + timedelta(seconds=self.cfg.pair.reselect_every_s)
            args = (spot, self.chain, self.ctx.feed.latest, now, self.dte, self.expiry, self.cfg.pair,
                    self.ctx.platform.marketdata.risk_free_rate)
            # Keep the current pair while it still qualifies: two near-equal pairs would otherwise
            # swap every 30 s, and each swap restarts the signal.
            res = select_pair(*args, only=set(self.pair.key)) if self.pair is not None else None
            if not isinstance(res, Pair):
                res = select_pair(*args)
            if isinstance(res, str):
                if res != self.pair_reason:
                    self.log.info("no pair: %s", res)
                self.pair, self.pair_reason = None, res
                self.signal.reset()
            elif self.pair is None or res.key != self.pair.key:
                self.pair, self.pair_reason = res, None
                self.signal.reset(res.key)
                self.check("pair", True, round(res.ratio, 3), self.cfg.pair.max_premium_ratio, dte=self.dte,
                           spot=spot, pair=res.describe())
            else:
                self.pair = res                # same strikes: keep the history, refresh the greeks
        if self.pair is None:
            return
        quote = self.ctx.feed.latest
        ce, pe, fut = mid(quote(self.pair.ce.inst.instrument_token)), mid(quote(self.pair.pe.inst.instrument_token)), \
            self.fut_price()
        if ce is not None and pe is not None and fut is not None:
            self.signal.add(now, ce, pe, fut)
            self.last_state = self.signal.evaluate(now, self.min_move)

    def build_entry(self) -> Position | None:
        now = self.ctx.clock.now()
        if not self.vix_logged and self.vix() is not None:
            self.check("vix", True, self.vix(), None, log_only=True)
            self.vix_logged = True
            # Log only: events inside the entry window (e.g. RBI policy at 10:00) - a scalp may like them.
            sched, tz = self.cfg.schedule, now.tzinfo
            self.log_events("events", datetime.combine(self.ctx.trade_date, sched.entry, tzinfo=tz),
                            datetime.combine(self.ctx.trade_date, sched.square_off, tzinfo=tz), min_impact="Medium")
        if self.pair is None:
            return self._wait(self.pair_reason or "no pair selected yet")
        st = self.last_state
        if st is None or not st.fired:
            return self._wait("watching the pair for a signal")

        pair, p = self.pair, self.cfg.pair
        max_spread = p.max_spread_pts_0dte if self.dte == 0 else p.max_spread_pts
        quote = self.ctx.feed.latest
        for side in (pair.ce, pair.pe):
            why = quote_ok(quote(side.inst.instrument_token), now, max_spread, p.max_quote_age_s)
            if why:
                self.check("signal", False, round(st.rise_pct, 2), self.cfg.signal.min_rise_pct,
                           skipped=f"{side.inst.tradingsymbol}: {why}", signal=st.as_dict())
                self.signal.reset(pair.key)
                return self._wait(f"signal skipped: {why}")

        rng = self._opening_range(now)
        breakout = None
        if rng:
            spot = self.spot()
            breakout = "up" if spot > rng[1] else "down" if spot < rng[0] else "inside"
        self.check("signal", True, round(st.rise_pct, 2), self.cfg.signal.min_rise_pct, min_fut_move=self.min_move,
                   values=self.signal.recent(self.cfg.signal.window_s * self.cfg.signal.windows + 1),
                   opening_range=list(rng) if rng else None, breakout=breakout, signal=st.as_dict())
        self.signal.reset(pair.key)         # the next trade needs a fresh signal
        self._next_reselect = None          # and a fresh pair
        legs = [Leg(role=f"LONG_{s.inst.instrument_type}", instrument_token=s.inst.instrument_token,
                    tradingsymbol=s.inst.tradingsymbol, side=BUY, qty=self.qty, option_type=s.inst.instrument_type,
                    strike=s.inst.strike) for s in (pair.ce, pair.pe)]
        self.log.info("signal: V %.2f (+%.1f%% in %ds), futures %+.1f, driven by %s -> buy %s + %s",
                      st.value, st.rise_pct, self.cfg.signal.window_s * self.cfg.signal.windows, st.fut_move,
                      st.driver, pair.ce.inst.tradingsymbol, pair.pe.inst.tradingsymbol)
        return Position(structure=self.structure, underlying=self.cfg.underlying, legs=legs, expiry=self.expiry,
                        params=dict(dte=self.dte, spot=self.spot(), fut=self.fut_price(), vix=self.vix(),
                                    lots=self.cfg.lots, lot_size=self.lot_size, trade_no=len(self.trades) + 1,
                                    pair=pair.describe(), signal=st.as_dict(),
                                    opening_range=list(rng) if rng else None, breakout=breakout))

    def _opening_range(self, now: datetime) -> tuple[float, float] | None:
        """NIFTY's 09:15-09:45 low/high from Kite 1-minute candles (logged only; live sessions)."""
        if self._range_tried or self.ctx.kite is None or now.time() < OPENING_RANGE[1]:
            return self.opening_range
        self._range_tried = True
        try:
            d = self.ctx.trade_date
            candles = self.ctx.kite.historical(self.index_token, datetime.combine(d, OPENING_RANGE[0]),
                                               datetime.combine(d, OPENING_RANGE[1]), "minute")
            if candles:
                self.opening_range = (min(c["low"] for c in candles), max(c["high"] for c in candles))
        except Exception as e:  # noqa: BLE001 - logged only, never blocks a trade
            self.log.warning("opening range unavailable: %s", e)
        return self.opening_range

    def _wait(self, reason: str) -> None:
        if reason != self._entry_wait_reason:
            self.log.info("entry waiting: %s", reason)
            self._entry_wait_reason = reason
        return None


ALGO = StrangleScalpAlgo
