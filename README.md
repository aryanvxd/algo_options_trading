# Algo platform (Zerodha Kite) — first algo: NIFTY short iron condor

A shared Python platform for running several intraday algos on Kite Connect, paper first.
Each algo lives in its own folder under `src/algos/`; everything else (login, data feed, calendar,
paper broker, charges, exit engine, database, runner, reports) is shared in `src/trading_core/`.

The strategy spec and build plan are in `Plan_txt/Iron_Condor_Algo_Plan.pdf`.

## Setup (once)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env              # fill KITE_API_KEY / KITE_API_SECRET (paid Kite Connect plan)
.venv/Scripts/algo db upgrade     # creates data/db/platform.db and loads holidays
```

In the Kite developer console set the redirect URL to `http://127.0.0.1:5005/kite/callback`.

## Every trading day

```bash
.venv/Scripts/algo login                         # before 08:15; Kite tokens expire daily
.venv/Scripts/algo quote                         # optional smoke test: NIFTY 50 + India VIX
.venv/Scripts/algo run --algo iron_condor        # runs the day: 08:15 check -> 15:35 report
```

Leave `algo run` running. On non-trade days (outside the expiry window, VIX ≥ 20, big gap,
one-sided breadth) it journals why and exits. While it runs it records ticks every 5 s, so the
day can later be replayed:

```bash
.venv/Scripts/algo run --algo iron_condor --replay 2026-10-23
.venv/Scripts/algo kill --algo iron_condor       # square off and stop a running algo
.venv/Scripts/algo report --algo iron_condor     # recent daily summaries
```

If the process crashes mid-trade, run the same command again: it resumes today's run and
picks up the open position from the database.

## Layout

```
config/platform.yaml          shared settings: DB, charges, paper fill model, runtime intervals
config/holidays/              NSE holidays (verify against the NSE circular)
config/universes/nifty50.yaml constituents for pre-open breadth
src/trading_core/             shared platform (never imports from algos)
  kite/        login, REST client, WebSocket ticker, instrument snapshots
  market/      calendar, pre-open snapshot, option-chain helpers
  feed/        MarketFeed interface: live (Kite) and replay (recorded ticks)
  broker/      Broker interface, paper broker, charges, live broker (disabled in v1)
  risk/        exit-rule engine + reusable rules
  strategy/    BaseAlgo lifecycle, Position, execution, runner, registry
  db/          SQLAlchemy models, Alembic migrations, Journal (all reads/writes)
  reporting/   end-of-day report files
src/algos/iron_condor/        the iron condor: config.yaml, filters, strike selector, blow-out exit
src/algos/_template/          copy this to start a new algo
tests/                        unit tests + full-day replays of synthetic data
data/, logs/                  runtime output (git-ignored; the DB holds the daily access token)
```

## Adding an algo

Copy `src/algos/_template` to `src/algos/<algo_id>`, set `algo_id` in its `config.yaml`, and
implement `day_check`, `premarket`, `start_feed`, `build_entry` and `build_exit_rules`.
Orders, exits, snapshots, the kill switch and reports come from `BaseAlgo`.

## Tests

```bash
.venv/Scripts/python -m pytest
```

## Status

- Phases 0–2 of the plan are built; the dashboard (Phase 3) and backtester (Phase 5) are next.
- Live order placement is intentionally not implemented (`broker/live.py`).
- Charge rates and the holiday list were entered from memory — check them before trusting net P&L.
