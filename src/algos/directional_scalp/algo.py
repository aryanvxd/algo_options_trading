"""NIFTY directional buying scalp (paper, spec: docs/strategy-criteria/directional-scalp.md).

Day: every trading day (``trade_weekdays``) -> subscribe NIFTY, India VIX, NIFTY futures, the 50
NIFTY stocks and strikes around spot; warm up the Renko chart (NIFTY spot, 1-minute closes) and the
futures VWAP from Kite's 1-minute candles since 09:15 -> every 5 s score the bias from weighted
breadth, CE/PE premium skew and futures vs VWAP -> from 09:45, when the score has favoured one side
for 2 minutes and Renko prints two bricks in a row that way, buy the ATM CE or PE (2 lots: lot A
books +12 pts, lot B trails) -> exits every second (kill, stale feed, hard premium stop, day loss,
Renko reversal, lot B target, breakeven after lot A, time stop, bias flip, 11:30) -> after a
2-minute cool-down look for the next: at most 5 trades, entries until 11:00.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from algos.directional_scalp.bias import CE, PE, BiasReading, BiasTracker, score
from algos.directional_scalp.exits import AlgoRule, BreakevenStop, HardPremiumStop, RunnerTarget
from algos.directional_scalp.settings import WEEKDAYS, DirectionalScalpConfig
from algos.iron_condor.filters import mid, premium_balance
from trading_core.broker.base import BUY
from trading_core.config import load_universe
from trading_core.kite.instruments import InstrumentInfo
from trading_core.market.breadth import Breadth, BreadthReading, load_weights, members, prev_closes_from_quotes
from trading_core.marketdata.renko import DOWN, UP, MinuteBars, Renko, Vwap
from trading_core.risk.engine import ExitRule, RiskContext
from trading_core.risk.rules import KillSwitch, MaxDailyLoss, StaleFeed, TimeExit
from trading_core.strategy.base import BaseAlgo, SkipDay
from trading_core.strategy.position import Leg, Mark, Position

SESSION_OPEN = time(9, 15)
DIRECTION = {CE: UP, PE: DOWN}


class DirectionalScalpAlgo(BaseAlgo):
    config_model = DirectionalScalpConfig
    structure = "LONG_OPTION"
    cfg: DirectionalScalpConfig

    def __init__(self, ctx, cfg: DirectionalScalpConfig):
        super().__init__(ctx, cfg)
        self.expiry = None
        self.dte: int | None = None
        self.chain: list[InstrumentInfo] = []
        self.lot_size: int | None = None
        self.index_token: int | None = None
        self.vix_token: int | None = None
        self.fut: InstrumentInfo | None = None
        self.stocks: dict[str, InstrumentInfo] = {}
        self.breadth: Breadth | None = None
        self.feed_ready = False
        self.subscribed_centre: float | None = None
        self.bars = MinuteBars()
        self.renko = Renko(cfg.trigger.brick_pts)
        self.vwap = Vwap()
        self.warmed_up = False
        self.tracker = BiasTracker(cfg.bias)
        self.last_breadth: BreadthReading | None = None
        self._next_bias: datetime | None = None
        self._next_bias_log: datetime | None = None
        self._bricks_checked = 0
        self.last_exit: datetime | None = None
        self.peak_realized = 0.0
        self.events_logged = False

    # ------------------------------------------------------------ sizing / prices

    @property
    def qty(self) -> int:
        return self.cfg.lots * (self.lot_size or 0)

    def rupees(self, points: float) -> float:
        return round(points * self.qty, 2)

    def spot(self) -> float | None:
        t = self.ctx.feed.latest(self.index_token) if self.index_token else None
        return t.ltp if t else None

    def vix(self) -> float | None:
        t = self.ctx.feed.latest(self.vix_token) if self.vix_token else None
        return t.ltp if t else None

    def fut_price(self) -> float | None:
        t = self.ctx.feed.latest(self.fut.instrument_token) if self.fut else None
        return t.ltp if t and t.ltp else None

    def trade_side(self, pos: Position | None = None) -> str | None:
        pos = pos or self.position
        return pos.params.get("side") if pos is not None else None

    def lot_a(self, pos: Position | None = None) -> Leg | None:
        pos = pos or self.position
        return next((l for l in pos.legs if l.role.endswith("_A")), None) if pos else None

    def lot_a_booked(self) -> bool:
        a = self.lot_a()
        return a is not None and a.exit_price is not None

    # ------------------------------------------------------------ exits

    def build_exit_rules(self) -> list[ExitRule]:
        e = self.cfg.exits
        return [
            KillSwitch(),
            StaleFeed(e.stale_feed_seconds),
            HardPremiumStop(e.hard_stop_pts),
            MaxDailyLoss(self.cfg.limits.max_day_loss),
            AlgoRule("RENKO_REVERSAL", self._renko_reversal),
            RunnerTarget(e.lot_b_target_pts),
            BreakevenStop(e.breakeven_buffer_pts, self.lot_a_booked),
            AlgoRule("TIME_STOP", self._time_stop),
            AlgoRule("BIAS_FLIP", self._bias_flip),
            TimeExit(self.cfg.schedule.square_off),
        ]

    def _bricks_since_entry(self, pos: Position) -> list:
        return [b for b in self.renko.bricks if pos.entry_time is not None and b.ts > pos.entry_time]

    def _renko_reversal(self, ctx: RiskContext) -> str | None:
        want = DIRECTION.get(self.trade_side(ctx.position))
        against = [b for b in self._bricks_since_entry(ctx.position) if b.direction != want]
        if want and against:
            b = against[-1]
            return f"Renko brick against the trade at {b.ts:%H:%M} ({b.open:.0f} -> {b.close:.0f})"
        return None

    def _time_stop(self, ctx: RiskContext) -> str | None:
        pos, want = ctx.position, DIRECTION.get(self.trade_side(ctx.position))
        if pos.entry_time is None or want is None:
            return None
        favour = [b.ts for b in self._bricks_since_entry(pos) if b.direction == want]
        last = max([pos.entry_time, *favour])
        idle = (ctx.now - last).total_seconds()
        if idle >= self.cfg.exits.time_stop_s:
            return f"no new brick in the trade's favour for {idle:.0f}s"
        return None

    def _bias_flip(self, ctx: RiskContext) -> str | None:
        side = self.trade_side(ctx.position)
        if side and self.tracker.against(side):
            return f"bias score {self.tracker.last.score:+.2f} turned against the {side}"
        return None

    def manage_open(self, now: datetime, mark: Mark) -> None:
        """Book lot A at its target; lot B carries on under the exit rules."""
        a = self.lot_a()
        if a is None or not a.is_open:
            return
        lm = next((l for l in mark.legs if l.role == a.role), None)
        target = a.entry_price + self.cfg.exits.lot_a_target_pts
        if lm is not None and lm.close_price is not None and lm.close_price >= target:
            self.close_legs([a], f"lot A target {lm.close_price:.2f} >= {target:.2f}")

    # ------------------------------------------------------------ day limits

    def _net(self, p: Position) -> float:
        return round(p.realized_gross - p.total_charges, 2)

    def _losses_in_row(self) -> int:
        n = 0
        for p in reversed([p for p in self.trades if p.is_flat]):
            if self._net(p) >= 0:
                break
            n += 1
        return n

    def direction_changes(self, next_side: str | None = None) -> int:
        sides = [self.trade_side(p) for p in self.trades] + ([next_side] if next_side else [])
        return sum(1 for a, b in zip(sides, sides[1:]) if a and b and a != b)

    def reentry_block(self, now: datetime) -> str | None:
        block = super().reentry_block(now)
        if block:
            return block
        lim = self.cfg.limits
        self.peak_realized = max(self.peak_realized, self.realized_net)
        if self._losses_in_row() >= lim.max_consecutive_losses:
            return f"{self._losses_in_row()} losing trades in a row"
        if self.realized_net <= -lim.max_day_loss:
            return f"day net ₹{self.realized_net:.0f} <= -₹{lim.max_day_loss:.0f}"
        if lim.profit_lock_trigger is not None and self.peak_realized >= lim.profit_lock_trigger \
                and self.realized_net <= lim.profit_lock_floor:
            return (f"profit lock: day net fell from ₹{self.peak_realized:.0f} to ₹{self.realized_net:.0f} "
                    f"(<= ₹{lim.profit_lock_floor:.0f})")
        if self.direction_changes() >= lim.max_direction_changes:
            return f"{self.direction_changes()} direction changes today (chop)"
        return None

    def _after_trade(self, now: datetime) -> None:
        self.last_exit = now
        super()._after_trade(now)

    # ------------------------------------------------------------ dashboard levels

    def levels(self) -> dict[str, Any] | None:
        e, lim, now = self.cfg.exits, self.cfg.limits, self.ctx.clock.now()
        r = self.tracker.last
        out: dict[str, Any] = dict(
            trades_used=len(self.trades), max_trades=self.cfg.max_trades, realized_net=self.realized_net,
            losses_in_row=self._losses_in_row(), max_consecutive_losses=lim.max_consecutive_losses,
            max_day_loss=lim.max_day_loss, direction_changes=self.direction_changes(),
            max_direction_changes=lim.max_direction_changes, dte=self.dte,
            bias=r.as_dict() if r else None, bias_side=self.tracker.raw_side(r) if r else None,
            bias_held_s=round(self.tracker.held_s(now)), bias_ready=self.tracker.side(now),
            threshold=self.cfg.bias.threshold, hold_s=self.cfg.bias.hold_s,
            breadth=self.last_breadth.as_dict() if self.last_breadth else None,
            vwap=self.vwap.value, fut=self.fut_price(), brick_pts=self.renko.size,
            bricks=self.renko.recent(24), reversal_level=self.renko.reversal_level,
            hard_stop_pts=e.hard_stop_pts, lot_a_target_pts=e.lot_a_target_pts, lot_b_target_pts=e.lot_b_target_pts,
            hard_stop_rupees=-self.rupees(e.hard_stop_pts),
        )
        pos = self.position
        if pos is not None and pos.entry_credit_pts is not None:
            out.update(side=self.trade_side(), entry_price=pos.legs[0].entry_price, lot_a_booked=self.lot_a_booked())
        return out

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
        self._setup_breadth()
        self.log.info("expiry %s, DTE %d, lot size %d, future %s, breadth over %d stocks | hard stop -₹%.0f, "
                      "up to %d trades", expiry, self.dte, self.lot_size,
                      self.fut.tradingsymbol if self.fut else "none (no VWAP part)",
                      len(self.breadth.members), self.rupees(cfg.exits.hard_stop_pts), cfg.max_trades)

    def _setup_breadth(self) -> None:
        """Stock tokens and previous closes for the weighted advance/decline (B1, required)."""
        ctx, d = self.ctx, self.ctx.trade_date
        universe = self.cfg.bias.breadth_universe
        symbols = [k.split(":", 1)[-1] for k in load_universe(ctx.platform, universe)]
        self.stocks = ctx.instruments.equities(symbols, d)
        if len(self.stocks) < len(symbols) and not ctx.is_replay and ctx.kite is not None:
            # Today's snapshot was taken before stocks were part of it: take it again.
            from trading_core.config import equity_symbols

            ctx.instruments.refresh_from_kite(ctx.kite, d, equity_symbols(ctx.platform) | set(symbols))
            self.stocks = ctx.instruments.equities(symbols, d)
        if not self.stocks:
            raise SkipDay(f"no {universe} stock tokens in the instrument snapshot (breadth is required)")
        closes = ctx.journal.prev_closes(d)
        missing = [s for s, i in self.stocks.items() if i.instrument_token not in closes]
        if missing and not ctx.is_replay and ctx.kite is not None:
            quotes = ctx.kite.quote([f"NSE:{s}" for s in missing])
            ctx.journal.save_prev_closes(d, prev_closes_from_quotes(quotes, {s: self.stocks[s] for s in missing}),
                                         ctx.clock.now())
            closes = ctx.journal.prev_closes(d)
        stocks = members(self.stocks, load_weights(ctx.platform, universe), closes)
        if not stocks:
            raise SkipDay("no previous closes for the breadth stocks")
        self.breadth = Breadth(stocks, self.cfg.bias.breadth_min_coverage)
        self.check("breadth_setup", True, len(stocks), len(symbols), log_only=True,
                   missing=sorted(set(symbols) - {m.symbol for m in stocks}))

    def start_feed(self) -> None:
        tokens = [self.index_token, self.vix_token] + ([self.fut.instrument_token] if self.fut else [])
        tokens += [m.token for m in self.breadth.members]
        self.ctx.feed.subscribe(tokens)
        self.feed_ready = True
        self._subscribe_strikes()

    def _subscribe_strikes(self) -> None:
        if self.dte is None or self.finished:
            return
        spot = self.spot()
        if spot is None:
            return
        o = self.cfg.option
        if self.subscribed_centre is not None and abs(spot - self.subscribed_centre) < o.resubscribe_drift_pts:
            return
        tokens = [i.instrument_token for i in self.chain
                  if i.strike is not None and abs(i.strike - spot) <= o.subscribe_range_pts]
        self.ctx.feed.subscribe(tokens)
        self.subscribed_centre = spot
        self.log.info("subscribed %d option tokens around spot %.0f (±%d)", len(tokens), spot, o.subscribe_range_pts)

    # ------------------------------------------------------------ charts and bias (every loop)

    def on_tick(self, now: datetime) -> None:
        if self.feed_ready and not self.finished:
            self._subscribe_strikes()
            self._update_charts(now)
            self._update_bias(now)
        super().on_tick(now)

    def _warm_up(self, first_minute: datetime) -> None:
        """Renko and VWAP from 1-minute candles between 09:15 and the minute the algo's data starts.
        Live: fetched from Kite and stored for the day; replay: read back from the store."""
        ctx, d = self.ctx, self.ctx.trade_date
        start = datetime.combine(d, SESSION_OPEN, tzinfo=first_minute.tzinfo)
        loaded: dict[str, int] = {}
        for name, token in (("index", self.index_token), ("fut", self.fut.instrument_token if self.fut else None)):
            if token is None or first_minute <= start:
                continue
            if not ctx.is_replay and ctx.kite is not None:
                try:
                    candles = ctx.kite.historical(token, start, first_minute - timedelta(seconds=1), "minute")
                    ctx.journal.save_bars(d, token, [dict(ts=c["date"], open=c["open"], high=c["high"],
                                                          low=c["low"], close=c["close"], volume=c.get("volume"))
                                                     for c in candles if c["date"] < first_minute])
                except Exception as e:  # noqa: BLE001 - the chart then starts from the first live tick
                    self.log.warning("warm-up candles for %s unavailable: %s", name, e)
            rows = ctx.journal.bars(d, token, before=first_minute)
            loaded[name] = len(rows)
            for b in rows:
                if name == "index":
                    self.renko.add(b.ts + timedelta(minutes=1), b.close)
                else:
                    self.vwap.add_bar(b.high or b.close, b.low or b.close, b.close, b.volume)
        self.bars.seed(first_minute - timedelta(minutes=1))
        self._bricks_checked = len(self.renko.bricks)     # warm-up bricks are history, not triggers
        self.check("warm_up", True, len(self.renko.bricks), None, log_only=True, candles=loaded,
                   from_minute=f"{first_minute:%H:%M}", vwap=self.vwap.value, bricks=self.renko.recent(10))

    def _update_charts(self, now: datetime) -> None:
        spot = self.spot()
        if spot is None:
            return
        if not self.warmed_up:
            self.warmed_up = True
            self._warm_up(now.replace(second=0, microsecond=0))
        fut = self.ctx.feed.latest(self.fut.instrument_token) if self.fut else None
        if fut is not None:
            self.vwap.add_tick(fut.ltp, fut.volume)
        for bar in self.bars.add(now, spot):
            for b in self.renko.add(bar.ts + timedelta(minutes=1), bar.close):
                self.log.info("Renko %s brick %.0f -> %.0f (1-min close %.2f at %s)",
                              "up" if b.direction == UP else "down", b.open, b.close, bar.close, f"{bar.ts:%H:%M}")

    def _update_bias(self, now: datetime) -> None:
        b = self.cfg.bias
        if self._next_bias is not None and now < self._next_bias:
            return
        self._next_bias = now + timedelta(seconds=b.every_s)
        quote, spot = self.ctx.feed.latest, self.spot()
        self.last_breadth = self.breadth.read(quote) if self.breadth else None
        skew = None
        if spot is not None:
            bal = premium_balance(spot, b.skew_distance_pts, self.chain, quote)
            skew = round(bal.pe / bal.ce, 3) if bal else None
        fut, vwap = self.fut_price(), self.vwap.value
        reading = score(b, now, self.last_breadth.weighted if self.last_breadth else None, skew,
                        round(fut - vwap, 2) if fut is not None and vwap is not None else None)
        self.tracker.update(reading)
        if self._next_bias_log is None or now >= self._next_bias_log:
            self._next_bias_log = now + timedelta(seconds=b.log_every_s)
            self._log_bias(reading, now)

    def _log_bias(self, r: BiasReading, now: datetime, name: str = "bias") -> None:
        self.check(name, True, r.score, self.cfg.bias.threshold, log_only=True, side=self.tracker.raw_side(r),
                   held_s=round(self.tracker.held_s(now)), spot=self.spot(), vwap=self.vwap.value,
                   breadth=self.last_breadth.as_dict() if self.last_breadth else None, **r.as_dict())

    # ------------------------------------------------------------ entry

    def build_entry(self) -> Position | None:
        now = self.ctx.clock.now()
        if not self.events_logged:
            self.events_logged = True
            sched, tz = self.cfg.schedule, now.tzinfo
            self.check("vix", True, self.vix(), None, log_only=True)
            self.log_events("events", datetime.combine(self.ctx.trade_date, sched.entry, tzinfo=tz),
                            datetime.combine(self.ctx.trade_date, sched.square_off, tzinfo=tz), min_impact="Medium")

        new_bricks = len(self.renko.bricks) > self._bricks_checked
        self._bricks_checked = len(self.renko.bricks)
        side = self.tracker.side(now)
        r = self.tracker.last
        if side is None:
            if r is None or r.score is None:
                return self._wait("bias: no breadth reading yet")
            return self._wait(f"bias {r.score:+.2f}: needs {self.cfg.bias.threshold:g} one way, breadth agreeing, "
                              f"for {self.cfg.bias.hold_s:.0f}s")
        if not new_bricks:
            return self._wait(f"bias {side}: waiting for a Renko brick")
        t = self.cfg.trigger
        open_at = datetime.combine(self.ctx.trade_date, self.cfg.schedule.entry, tzinfo=now.tzinfo)
        cutoff = max(open_at, self.last_exit) if self.last_exit else open_at
        run = self.renko.run(DIRECTION[side], after=cutoff)
        if run < t.bricks:
            return self._wait(f"bias {side}: {run} of {t.bricks} bricks that way")

        spot, vwap = self.spot(), self.vwap.value
        extension = round(abs(spot - vwap) / self.renko.size, 2) if spot is not None and vwap else None
        detail = dict(side=side, bricks=run, spot=spot, vwap=vwap, extension_bricks=extension,
                      breadth=self.last_breadth.as_dict() if self.last_breadth else None, **r.as_dict())
        if extension is not None and extension > t.max_vwap_extension_bricks and t.enforce_extension:
            self.check("trigger", False, r.score, self.cfg.bias.threshold, skipped="too far from VWAP", **detail)
            return self._wait("trigger skipped: too far from VWAP")
        if self.direction_changes(side) > self.cfg.limits.max_direction_changes:
            self.check("trigger", False, r.score, self.cfg.bias.threshold, skipped="direction-change limit", **detail)
            return self._wait("trigger skipped: direction-change limit")

        pick = self._pick_option(side, spot, now)
        if isinstance(pick, str):
            self.check("trigger", False, r.score, self.cfg.bias.threshold, skipped=pick, **detail)
            return self._wait(f"trigger skipped: {pick}")
        inst, price = pick
        self.check("trigger", True, r.score, self.cfg.bias.threshold, option=inst.tradingsymbol, premium=price,
                   **detail)
        legs = self._legs(inst)
        self.log.info("trigger: bias %s %+.2f, %d bricks, spot %.2f, VWAP %s -> buy %s (%s) @ ~%.2f",
                      side, r.score, run, spot, vwap, inst.tradingsymbol, "+".join(l.role for l in legs), price)
        return Position(structure=self.structure, underlying=self.cfg.underlying, legs=legs, expiry=self.expiry,
                        params=dict(side=side, dte=self.dte, spot=spot, fut=self.fut_price(), vwap=vwap,
                                    vix=self.vix(), lots=self.cfg.lots, lot_size=self.lot_size,
                                    trade_no=len(self.trades) + 1, bias=r.as_dict(),
                                    breadth=self.last_breadth.as_dict() if self.last_breadth else None,
                                    bricks=self.renko.recent(6), extension_bricks=extension))

    def _pick_option(self, side: str, spot: float, now: datetime) -> tuple[InstrumentInfo, float] | str:
        """ATM CE/PE; one strike in the money if ATM fails a check (O1-O4)."""
        o = self.cfg.option
        atm = round(spot / o.strike_step) * o.strike_step
        strikes = [atm] + ([atm - o.strike_step if side == CE else atm + o.strike_step] if o.itm_fallback else [])
        by_strike = {i.strike: i for i in self.chain if i.instrument_type == side}
        why = "no strike"
        for k in strikes:
            inst = by_strike.get(k)
            if inst is None:
                why = f"{side} {k:.0f} not listed"
                continue
            t = self.ctx.feed.latest(inst.instrument_token)
            price = mid(t)
            if t is None or price is None:
                why = f"{inst.tradingsymbol}: no quote"
            elif (now - t.received_at).total_seconds() > o.max_quote_age_s:
                why = f"{inst.tradingsymbol}: quote {(now - t.received_at).total_seconds():.0f}s old"
            elif t.spread is None or t.spread > o.max_spread_pts:
                why = f"{inst.tradingsymbol}: spread {t.spread if t.spread is not None else 'n/a'} > {o.max_spread_pts}"
            elif not o.premium_min <= price <= o.premium_max:
                why = f"{inst.tradingsymbol}: premium {price:.2f} outside {o.premium_min:g}-{o.premium_max:g}"
            else:
                return inst, round(price, 2)
        return why

    def _legs(self, inst: InstrumentInfo) -> list[Leg]:
        def leg(role: str, lots: int) -> Leg:
            return Leg(role=role, instrument_token=inst.instrument_token, tradingsymbol=inst.tradingsymbol, side=BUY,
                       qty=lots * self.lot_size, option_type=inst.instrument_type, strike=inst.strike)

        opt, lots = inst.instrument_type, self.cfg.lots
        if self.cfg.split_lots and lots >= 2:
            a = lots // 2
            return [leg(f"LONG_{opt}_A", a), leg(f"LONG_{opt}_B", lots - a)]
        return [leg(f"LONG_{opt}", lots)]

    def _wait(self, reason: str) -> None:
        if reason != self._entry_wait_reason:
            self.log.info("entry waiting: %s", reason)
            self._entry_wait_reason = reason
        return None


ALGO = DirectionalScalpAlgo
