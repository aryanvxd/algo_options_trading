"""Drives one algo through its day.

The same loop runs live (wall clock, sleeps) and in replay (simulated clock, jumps), so a replay
exercises exactly the code that trades live. Steps fire at the times in the algo's ``schedule``;
between steps the runner services control commands, ticks the algo, writes live state and records
ticks for future replays.
"""

from __future__ import annotations

import logging
from datetime import datetime

from trading_core.strategy.base import BaseAlgo, EXITING, IN_TRADE, DONE, SKIPPED, SkipDay

log = logging.getLogger(__name__)

STEPS = ("day_check", "premarket", "start_feed", "entry", "square_off", "report")
MAX_CONSECUTIVE_ERRORS = 5
SIM_LIVE_STATE_INTERVAL_S = 60.0


class Runner:
    def __init__(self, algo: BaseAlgo):
        self.algo = algo
        self.ctx = algo.ctx
        self.rt = self.ctx.platform.runtime
        self.feed_started = False
        self._errors = 0
        self._last_poll: datetime | None = None
        self._last_live_state: datetime | None = None
        self._last_record: datetime | None = None

    def run(self) -> str:
        ctx, algo = self.ctx, self.algo
        sched = algo.cfg.schedule
        steps = [(name, ctx.clock.at(getattr(sched, name), ctx.trade_date)) for name in STEPS]
        if algo.state == DONE:                          # resumed after the trade had closed: nothing left
            steps = [("report", ctx.clock.now())]       # to do but the report, so don't wait for 15:35
        elif algo.state in (IN_TRADE, EXITING):         # resumed after a restart
            steps = [s for s in steps if s[0] not in ("premarket", "entry")]
        log.info("run %s: %s %s (%s, %s)", ctx.run_id, ctx.algo_id, ctx.trade_date, ctx.mode, ctx.source)

        try:
            while steps:
                now = ctx.clock.now()
                name, due = steps[0]
                if now >= due:
                    steps.pop(0)
                    self._run_step(name)
                    if algo.state == SKIPPED and name != "report" and steps:
                        if self.feed_started:   # keep recording ticks until square-off for replays
                            steps = [s for s in steps if s[0] in ("square_off", "report")]
                        else:                   # nothing running: report now and exit
                            steps = [("report", ctx.clock.now())]
                    continue
                self._service(now)
                ctx.clock.sleep(self._delay(now, due))
        except KeyboardInterrupt:
            log.warning("interrupted — squaring off")
            algo.request_kill("keyboard interrupt")
            self._drain_exit()
            algo.report()
        finally:
            algo.flush_snapshots()
            self.ctx.feed.stop()
        return algo.state

    # ------------------------------------------------------------ internals

    def _delay(self, now: datetime, due: datetime) -> float:
        to_due = (due - now).total_seconds()
        if self.algo.active:
            return min(self.rt.loop_interval_s, to_due)
        if self.ctx.clock.simulated:
            return to_due                       # nothing to do until the next step: jump
        if self.feed_started:
            return min(self.rt.loop_interval_s, to_due)   # keep recording ticks
        return min(self.rt.control_poll_interval_s, to_due)

    def _run_step(self, name: str) -> None:
        algo, ctx = self.algo, self.ctx
        if algo.finished and name not in ("square_off", "report"):
            return
        log.info("step %s", name)
        try:
            if name == "day_check":
                self._prepare_reference_data()
                algo.day_check()
                algo.mark_ready()
            elif name == "premarket":
                algo.premarket()
            elif name == "start_feed":
                ctx.feed.start()
                algo.start_feed()
                if algo.position is not None:     # resumed: make sure the open legs are tracked
                    ctx.feed.subscribe(l.instrument_token for l in algo.position.legs)
                self.feed_started = True
            elif name == "entry":
                algo.begin_entry()
            elif name == "square_off":
                algo.square_off()
                self._drain_exit()
            elif name == "report":
                algo.report()
        except SkipDay as e:
            algo.skip(str(e))
        except Exception as e:  # noqa: BLE001 - journal and keep the process alive
            algo.on_error(e, f"step {name}")
            if name in ("day_check", "premarket", "start_feed"):
                algo.skip(f"error in {name}: {e}")

    def _prepare_reference_data(self) -> None:
        ctx = self.ctx
        if ctx.instruments.has_snapshot(ctx.trade_date):
            return
        if ctx.is_replay:
            from trading_core.marketdata.sources.recorder import restore_instruments
            from trading_core.marketdata.store import MarketStore

            if restore_instruments(MarketStore(ctx.platform.marketdata.dir), ctx.instruments, ctx.trade_date):
                return
            raise SkipDay(f"no instrument snapshot stored for {ctx.trade_date}")
        ctx.instruments.refresh_from_kite(ctx.kite, ctx.trade_date)

    def _drain_exit(self, max_attempts: int = 30) -> None:
        """Keep retrying an exit (e.g. at square-off) until flat or attempts run out."""
        for _ in range(max_attempts):
            if self.algo.state != EXITING:
                return
            self.ctx.clock.sleep(1.0)
            self.algo.on_tick(self.ctx.clock.now())
        if self.algo.state == EXITING:
            self.algo.event("EXIT_STUCK", "could not close all legs", level="ERROR")

    def _service(self, now: datetime) -> None:
        ctx, algo = self.ctx, self.algo

        if self._due(self._last_poll, now, self.rt.control_poll_interval_s):
            self._last_poll = now
            for cmd in ctx.journal.pending_commands(ctx.algo_id, ctx.run_id, now):
                log.warning("control command: %s", cmd)
                if cmd == "KILL":
                    algo.request_kill("manual (control command)")
                elif cmd == "PAUSE":
                    algo.paused = True
                elif cmd == "RESUME":
                    algo.paused = False

        try:
            algo.on_tick(now)
            self._errors = 0
        except Exception as e:  # noqa: BLE001
            self._errors += 1
            algo.on_error(e, "on_tick")
            if self._errors >= MAX_CONSECUTIVE_ERRORS:
                algo.request_kill(f"{self._errors} consecutive errors")

        live_every = SIM_LIVE_STATE_INTERVAL_S if ctx.clock.simulated else self.rt.live_state_interval_s
        if self._due(self._last_live_state, now, live_every):
            self._last_live_state = now
            algo.write_live_state(now)

        record_every = algo.cfg.tick_snapshot_interval_s or self.rt.tick_snapshot_interval_s
        if self.feed_started and not ctx.is_replay and self._due(self._last_record, now, record_every):
            since = self._last_record
            self._last_record = now
            ticks = [t for t in ctx.feed.snapshot() if since is None or t.received_at > since]
            ctx.journal.add_ticks(ctx.run_id, ticks, ts=now)

    @staticmethod
    def _due(last: datetime | None, now: datetime, every: float) -> bool:
        return last is None or (now - last).total_seconds() >= every
