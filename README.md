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

If Windows blocks `.venv\Scripts\algo.exe` ("An Application Control policy has blocked this
file"), use the wrapper in the repo root instead: `.\algo.cmd <command>` (or
`.venv\Scripts\python -m trading_core <command>`). The examples below work with either.

## Every trading day

```bash
.venv/Scripts/algo login                         # each morning (~09:35); Kite tokens expire daily
.venv/Scripts/algo quote                         # optional smoke test: NIFTY 50 + India VIX
.venv/Scripts/algo run --algo iron_condor        # start by ~09:40: entry 09:45-10:00 -> 15:35 report
.venv/Scripts/algo logout                        # when the run has finished (or after closing it)
```

Leave `algo run` running. On non-trade days (outside the expiry window, VIX ≥ 20, CE/PE premiums
still unbalanced at 10:00) it journals why and exits. Only one `algo run` per algo can run at a
time; a second one stops with a message. If the window is closed by mistake, start `algo run`
again: it resumes the open trade (with a 10 s grace for the feed to reconnect), or, if the trade
had already closed, writes the day's report at once and exits. Entry rules: `docs/strategy-criteria/`. While it runs it records ticks every 5 s, so the
day can later be replayed:

```bash
.venv/Scripts/algo run --algo iron_condor --replay 2026-10-23
.venv/Scripts/algo kill --algo iron_condor       # square off and stop a running algo
.venv/Scripts/algo report --algo iron_condor     # recent daily summaries
```

If the process crashes mid-trade, run the same command again: it resumes today's run and
picks up the open position from the database.

## Dashboard

```bash
.venv/Scripts/algo dashboard                     # http://127.0.0.1:8050, opens your browser
```

Read-only view of the same database, so it can run alongside the algos or be closed at any
time. The Overview page shows the day's P&L grouped by strategy (paper / live, real sessions or
replays), intraday P&L, the last 30 days and a per-strategy scorecard; today's view refreshes
every 5 seconds. Frontend is plain HTML/CSS/JS in `src/dashboard/static/` (no build step;
Chart.js and Font Awesome are vendored so it works offline).

## Historical market data

Kept in Parquet files under `data/market/` (queried with DuckDB), separate from the trading DB.
Design and source comparison: `docs/design/historical-data-and-backtests.md`.

```bash
algo data bhavcopy --from 2021-01-01             # NSE end-of-day option chains, free; IV/greeks computed
algo data index --from 2021-01-01                # NIFTY 50 + India VIX 1-min from Kite (needs algo login)
algo data constituents --from 2021-01-01         # NIFTY 50 stocks, daily
algo data status                                 # what is stored, first/last day, problems
algo data query "select * from options_eod where trade_date = '2026-10-22' limit 5"
```

Downloads resume where they stopped (each day is recorded in `market_data_files`; holidays
are recorded as EMPTY). After each live session the day's recorded ticks are exported to
`options_ticks` / `index_ticks` automatically, and SQLite keeps only the last 7 days of ticks;
replays of older days read the Parquet copy. 1-minute history of *expired* options needs a
vendor (Groww or Upstox) — not built yet.

## Layout

```
config/platform.yaml          shared settings: DB, charges, paper fill model, runtime intervals
config/holidays/              NSE holidays (verify against the NSE circular)
config/universes/nifty50.yaml constituents (data downloads; no algo uses it now)
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
