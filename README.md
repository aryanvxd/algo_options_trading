# Algo platform (Zerodha Kite) — NIFTY short iron condor and long strangle scalp

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
.venv/Scripts/algo run --algo strangle_scalp     # second window, same time: scalps 09:45-10:30 -> 10:50 report
.venv/Scripts/algo run --algo weekend_condor     # Fridays (enters 14:45-15:10, holds) and Mondays (buys back ~09:35)
.venv/Scripts/algo logout                        # when every run has finished (or after closing them)
```

Each algo runs in its own window (Kite allows 3 WebSocket connections per API key). The iron
condor trades every trading day; the scalp trades Mondays and Tuesdays, up to 3 trades, and
records the book every second while it runs. Instead of typing `algo run`, you can press
**Start** for a strategy on the dashboard (Overview, or the strategy's page): it opens the same
command in its own window, which keeps running if the dashboard is closed. **Stop** squares off
and stops it, like `algo kill`. The dashboard's **Log in** button opens `algo login`. Start is
greyed out once a strategy has finished for the day, after its square-off time, or while Kite
isn't logged in. After pulling new code, run
`.venv/Scripts/algo db upgrade` once (it adds new columns/tables; existing data is kept).

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

It reads the same database, so it can run alongside the algos or be closed at any time; its only
writes are your journal notes (a table no algo reads). The Overview page shows the day's P&L
grouped by strategy (paper / live, real sessions or replays), intraday P&L, the last 30 days and a
per-strategy scorecard. Each strategy has its own page under **Strategies** with tabs: Today
(status, live P&L, payoff chart, P&L curve, checks, timeline; the scalp adds its signal panel),
Trade log (capital used and ROI per trade, filters, CSV), Journal (each day's decisions and your
notes), Performance (equity curve, drawdown, results by days-to-expiry / weekday / exit / entry
time) and Rules (the config, what changed since the last run, the spec). Live views refresh
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
src/algos/strangle_scalp/     the scalp: config.yaml, pair selector (premium / delta), entry signal
src/algos/weekend_condor/     positional condor: Friday entry, carried over the weekend, Monday exit
src/algos/_template/          copy this to start a new algo
tests/                        unit tests + full-day replays of synthetic data
data/, logs/                  runtime output (git-ignored; the DB holds the daily access token)
```

## Adding an algo

Copy `src/algos/_template` to `src/algos/<algo_id>`, set `algo_id` in its `config.yaml`, and
implement `day_check`, `premarket`, `start_feed`, `build_entry` and `build_exit_rules`.
Orders, exits, snapshots, the kill switch, capital used per trade and reports come from
`BaseAlgo`. For more than one trade a day set `max_trades` (and `reentry_cooldown_s`) in the
config and override `reentry_block` for the algo's own day limits.

## Tests

```bash
.venv/Scripts/python -m pytest
```

## Status

- Phases 0–3 of the plan are built (dashboard: overview + strategy pages); the backtester (Phase 5) is next.
- Live order placement is intentionally not implemented (`broker/live.py`).
- Charge rates and the holiday list were entered from memory — check them before trusting net P&L.
