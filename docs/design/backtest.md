# Backtests on minute history — engine and dashboard plan

- **Status:** engine built 31 Oct 2026 (B1–B3 below). Dashboard pages (section 6) built 8 Nov 2026:
  Backtests in the sidebar runs, studies, compares and sweeps backtests, and shows the data coverage.
- **First user:** the directional scalp. The engine runs any algo, but its option prices are
  modelled (section 3), so results for option-selling algos need the same care.

## 1. Approach: run the real algo on reconstructed days

A backtest replays history through **the same code that trades live**:
- the same `Runner`, algo class and exit rules;
- the same paper broker and charges.

Only the feed differs. `BacktestFeed` rebuilds each day from 1-minute history. So a rule
changed in the algo is backtested as it really is, not as a re-implementation of it.

Each backtest gets its own folder, `data/backtests/<name>/`:
- `backtest.db` holds runs, trades, checks and events, in the same schema as the live database;
- `summary.json` holds the metrics, the settings used and the data coverage.

So nothing a backtest does can touch the live database, and the dashboard can open any backtest
with the queries it already has.

## 2. Data (downloaded once, kept in `data/market`)

| Data | Command | Source | Needs Kite login |
|---|---|---|---|
| NIFTY 50 and India VIX, 1-minute | `algo data index --from …` (existed) | Kite historical | yes |
| NIFTY futures, 1-minute with volume (the current near month only) | `algo data futures --from …` (new) | Kite historical | yes |
| The 50 NIFTY stocks, 1-minute | `algo data constituents-1m --from …` (new) | Kite historical | yes |
| Option chains, end of day: expiries, strikes, IV per strike | `algo data bhavcopy --from …` (existed) | NSE archives | no |

**Amount of data:**
- Kite serves 60 days of minute candles per request.
- One year is about 6 requests per instrument, so about 330 requests for 50 stocks, the index,
  VIX and the future. That takes a few minutes at Kite's rate limit.
- Every download resumes where it stopped and skips days already stored.

## 3. How a day is rebuilt

| Piece | Rebuilt as |
|---|---|
| NIFTY, VIX, futures, stocks | 4 ticks per minute, 15 s apart: open, the nearer extreme, the other extreme, close (the usual OHLC path). Stocks go open, open, close, close: on a shared grid all 50 would otherwise hit their lows and highs at the same moment. Volume is spread across the minute |
| Option quotes | **modelled**: Black-76 on spot × e^(rT), with each strike's IV from the **previous** day's end-of-day chain (no look-ahead). It is scaled through the morning by India VIX ÷ the previous close's VIX. Bid/ask = model ± a spread of about 0.1% of the price (min 0.05 each side). The paper broker adds its 0.1-pt slippage on top |
| Expiry, strikes | from that day's bhavcopy. Without one: weekly expiry on Thursday until Aug 2025, Tuesday from Sep 2025, moved to the previous trading day over holidays |
| Trading days, holidays | days with NIFTY minute data |
| Previous closes (breadth) | each stock's last minute close of the previous trading day |
| Lot size | today's (65) on every day, so rupee results compare across years. Points results don't depend on it |

## 4. Known limits (printed in every backtest's summary)

0. **No futures minute history before the current contract.**
   - Kite serves 1-minute candles only for live contracts. Its `continuous` option works for daily
     candles only ("invalid interval for continuous data", checked 31 Oct 2026). So futures minute
     data reaches back about 3 months.
   - Backtests therefore measure B3 (NIFTY vs VWAP) from the 50 stocks: each stock's distance from
     its own VWAP, weighted by index weight, in index points (`bias.vwap_source: stocks`).
   - Live keeps the futures VWAP. The two should agree closely, and the ~3 months of futures data
     can confirm it.

1. **Premium skew (B2) is not in the history.**
   - Modelled options carry yesterday's smile, not today's real CE/PE imbalance.
   - So the directional scalp's backtest runs with the skew weight at 0 and the other two
     re-weighted: breadth 0.57, VWAP 0.43. That is the same 4:3 ratio as live.
   - Whether B2 helps is judged on paper days, as the spec says.
2. **Option prices are a model.**
   - IV moves only with VIX, so a strike-specific IV jump (a squeeze on one side) is missed.
   - Fine for ranking brick sizes and stops; not a promise of rupees.
3. **Today's NIFTY 50 list and weights on every day** (survivorship). Stocks Kite no longer
   lists (renamed or merged) are missing on older days. The breadth needs only 60% of the index
   weight to have a price.
4. **Fills at the modelled touch plus slippage.** No queueing, partial fills or latency.

## 5. Running one

```bash
.venv/Scripts/algo backtest --algo directional_scalp --from 2025-01-01 --to 2026-10-30
.venv/Scripts/algo backtest --algo directional_scalp --from 2025-01-01 --set trigger.brick_pts=12 --name brick12
```

`--set key=value` overrides any config value (repeatable). `--overrides-file x.json` does the same
from a JSON object, applied after any `--set`; keys made only of digits become integers, so a by-DTE
table replaces the YAML one instead of sitting beside it (section 6.8). The dashboard uses it. The
summary has:
- trades, win rate, average win and loss, profit factor, expectancy;
- max drawdown and the equity curve;
- results by DTE, by month, by exit reason and by side (CE/PE);
- skip reasons, and data coverage (days with all data / missing pieces).

A year takes several minutes: each day runs the full minute-by-minute loop.

## 5a. Real option prices (from 4 Nov 2026)

Modelled prices are too rough for option sellers, and Kite serves no expired options. So real
1-minute option candles are now collected every evening and backtests can run on them.

**Collecting: `algo data options`.** Run it in the evening, after any `algo login` that day.

| Step | What it does |
|---|---|
| 1 | NIFTY 50 and India VIX, 1-minute |
| 2 | Every listed strike of the nearest 3 expiries (`marketdata.option_expiries`), 1-minute with OI, into `options_1m` (source `kite`) |
| 3 | The near-month future and the 50 stocks, 1-minute, so every strategy can be replayed |
| 4 | Prints what is stored per expiry, and the next deadline |

- **Missed days come back.** A live contract keeps its whole history, so one run fetches every day
  not stored yet (up to `marketdata.option_lookback_days`, 59 by default). Kite's limit is 60 days
  per request.
- **The deadline is each expiry's evening.** Measured: the 3 Nov contracts downloaded on the evening
  of 3 Nov, and returned `invalid token` the next day. A week not saved by then is lost.
- **Only finished sessions are stored.** Before 15:35 the command stops at the previous trading day,
  so a half-day is never saved as complete.
- **A contract that fails to download marks that day `PARTIAL`,** and the next run fetches it again.
- **Bookkeeping:** one file per day holds every expiry. The manifest has one row per expiry and day,
  under the key `NIFTY:2026-11-09`.
- `algo login` prints a reminder on expiry day, or when finished days are still not stored.

**Using them: `algo backtest --options real`.**

```bash
.venv/Scripts/algo backtest --algo iron_condor_balanced --from 2026-10-28 --options real
```

| Piece | With `--options real` |
|---|---|
| Option quotes | The close of the contract's last **finished** minute, so no look-ahead. No candle yet = no quote |
| Bid/ask | Close ± half the spread measured in the recorded ticks: 0.025 pt under ₹5, 0.05 under ₹40, 0.10 under ₹100, 0.15 under ₹150, 0.20 under ₹200, 0.30 under ₹300, 1.00 above (re-measured 11 Nov 2026; until then it was 0.50 for everything above ₹100, about three times the real spread of a ₹100-300 option). The paper broker adds its 0.1-pt slippage |
| OI | From the candles, so PCR is real |
| Expiry and strikes | The nearest expiry in that day's candles |
| Holidays | The real list, so DTE is right when a holiday falls before the expiry |
| A day whose nearest expiry was never saved | **Not traded.** It is listed under `missing_options` in the summary, never run on the wrong contract |

The default stays `--options model`, so older results still compare.

**Inside the minute (15 Nov 2026).** Quotes are minute closes, so a peak or a dip between two
closes was invisible. Exits that follow a peak now also see the minute that just finished: on the
first loop of each minute `BaseAlgo` marks the position at four points (`BacktestFeed.minute_path`,
`RiskContext.minute_path`): the open, the index's first extreme, its second extreme, the close. At
the index's high every CE stands at its candle high and every PE at its low, the other way round at
the index's low, in the index's own order (`_path`). Only minutes the trade was open for count; a
leg that did not trade that minute keeps its last quote. Live and modelled feeds return no path.

- **Why the index decides the order:** each leg's own OHLC order puts a CE and a PE at their lows at
  the same moment whenever both close below their open. On 13 Nov 2026 that made a +₹819 condor peak
  at 10:07 that live never saw (live's best that minute: +₹468).
- **Used by:** the balanced condor's trailing profit stop (`ProfitTrail`). Stop, target and premium
  blow-out still look at closes only.
- **13 Nov 2026, the check:** live left at 10:45:29 (peak +₹845, gross ₹403, net ₹168). Closes only:
  12:09 (peak +₹1,781, net ₹996). With the minute path: 10:46 (peak +₹786, gross ₹448, net ₹214).
  Over 28 Oct–13 Nov only 12 and 13 Nov changed (net −₹4,637, was −₹3,755).
- **The fill is still the minute's close:** a give-back inside a minute exits at the first price the
  backtest has once the candle is known, so it can fill a little better or worse than live did.

**Limits of real candles:**
- 1-minute closes, not ticks. A stop between two closes fires at the next close (the trailing
  profit stop sees the minute's path, above).
- The spread is a model. It agreed with the recorded ticks to about 0.05 pt on OTM strikes on one
  morning, but it can be wrong in a fast market.
- A minute with no trade has no candle, so the last close stands. Far strikes can look stale.
- The directional scalp's `BACKTEST_OVERRIDES` still switch its premium skew off, even on real prices.

## 6. Dashboard: run and study backtests in the browser (plan, 8 Nov 2026)

**Goal:** pick a strategy, change settings, run, watch progress, read the results and compare runs,
all from the dashboard. Routine backtests need neither the terminal nor a chat.

**Principles:**
- **The page does the thinking, the server stays thin.** The page builds the form from the config
  schema, works out what changed, generates sweep combinations, compares runs and draws the charts.
  The server only lists and reads backtest folders, and launches the existing `algo backtest`.
  There is no second backtest engine, in JavaScript or anywhere else.
- **The folder is the record.** Everything about a run stays in `data/backtests/<name>/`, so a run
  started in the terminal shows up in the dashboard too.
- **Nothing touches a live algo.** A backtest has its own database and only reads `data/market/`.
  The dashboard never places, changes or cancels orders.
- Same stack: plain HTML/CSS/JS in `src/dashboard/static/`, no build step, the vendored Chart.js,
  Lightweight Charts and Font Awesome, the existing CSS tokens, dark and light themes, nothing
  from the internet.

### 6.1 Sidebar: main menu and Backtests sub-menu

```
Main menu                         Backtests sub-menu
─────────                         ──────────────────
Overview                          ← Main menu
▾ STRATEGIES                      ▾ RUN
    ● Iron condor                     New backtest
    ● …                               Parameter sweep
▾ MARKETS                         ▾ RESULTS
    Calendar                          All runs
    Swing                             Compare
▾ RESEARCH                        ▾ DATA
    Backtests              ›          Coverage
    Market data                   RECENT
▾ SYSTEM                              ● icb_real_1028_1106   (running)
    Settings (soon)                   ● icb_stop12           (done)
                                      ● …                    (3–5 newest)
```

| Behaviour | Rule |
|---|---|
| Opening the sub-menu | Clicking **Backtests ›** swaps the sidebar to the sub-menu and opens All runs. **← Main menu** swaps back without changing the page |
| Links | Any `#/backtests/...` link (typed, bookmarked or from another page) opens the sub-menu directly. The browser's Back button works, because the menu follows the route |
| Collapsible sections | Each section header (both menus) opens and closes on click, with a ▾/▸ marker. The state is saved per section with `App.prefs` (`nav.open.<section>`) |
| Animation | A short slide (about 180 ms) between the two menus; none under `prefers-reduced-motion` |
| Icon-only sidebar (collapsed, and always below 900 px) | Section headers are hidden, so every section shows its icons (a collapsed section would otherwise hide its items with no way to open them). **← Main menu** becomes an arrow icon with a tooltip. Recent runs are hidden |
| Market data (main menu) | Opens the Coverage page (`#/backtests/data`), so the data has one page, not two |
| Recent | Newest 3–5 folders by start time, with a status dot: blue pulsing = running, grey = queued, green = done, red = failed or stale. Refreshed from the same list the All runs page polls, only while the sub-menu is showing |

### 6.2 Pages (routes under `#/backtests/`)

| Page | Route | What it does |
|---|---|---|
| **All runs** | `runs` (default) | One row per folder: name, algo, version, dates, option prices (real / model badge), trades, net, win rate, profit factor, max drawdown, run time, status. While running: "day N of M" with a bar. Filter by algo, search by name, sort any column, and a "show sweeps grouped" toggle. Tick 2–6 rows → **Compare**. Row menu: **Open**, **Run again with changes**, **Move to trash** (after a confirm; never a hard delete). Polls every 2 s only while something is queued or running |
| **New backtest** | `new` (`new?from=<name>` pre-fills from a run) | Section 6.3 |
| **Run** | `run/<name>/<tab>` | Tabs like a strategy page, section 6.4 |
| **Parameter sweep** | `sweep` (`sweep/<group>` for results) | Section 6.5 |
| **Compare** | `compare?runs=a,b,c` | Section 6.6 |
| **Coverage** | `data` | Section 6.7 |

### 6.3 New backtest

1. **Strategy picker:** every algo in the registry, with its version and lots.
2. **Settings form, built from the config.** The page reads `GET /api/algos/{id}/config` and builds
   one field per setting, grouped like `config.yaml` (top level, then `filters`, `strikes`, `entry`,
   `exits`, `schedule`, …), pre-filled with the current values.
   | Setting kind | Field |
   |---|---|
   | number, text, true/false, choice (`mode: balanced \| width`) | number / text input, switch, select (choices from the schema's enum) |
   | time (`"09:45"`) | time input |
   | by-DTE table (`stop_by_dte`, `trail_arm_by_dte`, …: any `*_by_dte`) | one row of inputs, DTE 0 … `trade_window_days − 1`; a DTE the config leaves out shows the value it falls back to, greyed |
   | lists and anything the page can't type | a small JSON box, checked on change |
   - Help text is the YAML comment on or just above each key, read by a small comment parser on
     the server.
   - Fixed things a backtest can't change are shown read-only, not as fields: `algo_id`, `mode`
     (always paper), `name`, `version`. The algo's `BACKTEST_OVERRIDES` (for example the scalp's
     skew weight) are listed under "Always applied in backtests".
3. **Changes from the live config:** a live list beside the form, one line per changed value
   (`exits.stop_by_dte[2]: 15 → 12`), each with an undo. **Reset all** goes back to the live config.
4. **Date range:** from / to, with presets **last 5 / 20 / 60 trading days, 1 y, 2 y** (counted on
   days with NIFTY minute data, so holidays don't count).
5. **Option prices:** **real** by default. Under the range, a coverage strip shows each trading day:
   green = real candles for its nearest expiry, red = not saved (the run will skip it as
   `missing_options`). With few green days the page says so before you run ("only 8 of 250 days
   have real option prices").
   **Model** is allowed, with a warning: modelled prices are biased against sellers, so rupee
   results are not reliable for condors (section 4).
6. **Name:** auto-generated, editable, checked against existing folders as you type. Pattern:
   `<algo short>_<real|model>_<from MMDD>_<to MMDD>[_<first change>]`, for example
   `icb_real_1028_1106_stop2-12`. Allowed characters: letters, digits, `_ - .`; at most 80.
7. **Run** queues it (section 6.8) and opens its Run page, which shows the progress.
8. **During market hours** (09:15–15:30 IST on a trading day) the button reads **Run after 15:30**,
   with a "start now" option. This laptop is the live paper-trading machine, and a 2-year run takes
   a core for several minutes.

**What is sent.** Only changed values go in `overrides`. A changed by-DTE table is sent whole, with
every DTE key, so the run's record shows the full table it used.

### 6.4 Run page (`#/backtests/run/<name>/<tab>`)

Header: name, algo and version, dates, real/model badge, status chip, "day N of M" bar while it
runs, and buttons **Run again with changes** (opens New pre-filled with this run's overrides),
**Add to compare**, and **Stop** (running only; it ends that backtest process, never an algo).

| Tab | Shows | Source |
|---|---|---|
| **Summary** | KPI tiles (net, trades, win rate, profit factor, expectancy, max drawdown, days traded / skipped, best / worst day). Equity curve with the drawdown below it. Monthly heatmap (rows = years, columns = months). Tables **by DTE, weekday, exit reason, entry time, side**. Skip reasons. Data coverage and the **limits** (section 4), with the model warning for condors on modelled prices | `summary.json`; entry time from the performance query |
| **Trades** | The strategy trade log (filters, CSV), each row opening the same trade detail: legs and fills, payoff chart, P&L path, checks, exit rule | `strategy_queries` on the folder's `backtest.db` |
| **Days** | Every day, newest first: result, or the skip reason and the checks behind it. Days not run (`missing_options`, `missing_index`) are listed too | the journal query on `backtest.db`, plus `summary.coverage` |
| **Settings** | The overrides used. The full config the run used (`config_snapshot` of its first run), with the differences from **today's** live config marked, and a note when the live config has changed since | `status.json` meta and `backtest.db` |
| **Log** | The last 200 lines of the run's log, mainly to see why a run failed | `run.log` in the folder |

Old folders written before some summary keys existed (for example `by_weekday`) show what they
have; a missing table is simply left out.

**Reuse:** the trade log, trade detail, payoff chart, journal and group tables come from
`pages/strategy.js`. Today they call `strategies/<id>/…` and `positions/<id>`. A small change lets
those panels take an API base (`backtests/<name>/` instead), so the same code serves both. Notes are
hidden on backtest pages.

### 6.5 Parameter sweep

1. Pick a strategy, a date range and option prices (the same controls as New).
2. Pick **1 or 2 settings** from the same schema-built list, and a list of values for each (typed,
   or a from / to / step helper). A by-DTE setting is swept per DTE cell, e.g.
   `exits.stop_by_dte[2]` over 10, 12, 15.
3. Optional base changes that apply to every run: "Start from" an earlier run of the algo, whose
   changes every combination keeps.
4. The page builds the grid and shows it before running: N runs, at most 20, and an estimated
   time from earlier runs' seconds per day.
5. Runs are named `<sweep>_<nn>` (`<sweep>_01` …) and queued one after another. Each run's
   `request.json` records the sweep's name and its point in the grid.
6. **Results** (`sweep/<sweep>`): one row per combination with the KPIs, the best marked (by net, or a
   chosen KPI), and for 2 settings a heatmap of the chosen KPI. **Compare selected** opens Compare.

This replaces B4 (`--set key=a,b,c` in the CLI): the page builds the combinations and the queue runs
them.

### 6.6 Compare (`compare?runs=a,b,c`, 2–6 runs)

- Equity curves overlaid, one colour per run (or their drawdowns).
- KPIs side by side, the best in each row marked, and how many days each run was the best.
- Net by DTE, weekday, exit reason and month, side by side.
- **Settings that differ:** each run's full config, flattened, showing only keys whose values
  differ.
- **Day by day:** one row per date in any run, a column of net P&L per run, the best per day
  marked, with totals.
- A warning when the runs mix real and model prices, or cover different date ranges.

All of this is worked out in the page from each run's `summary.json` and settings.

### 6.7 Coverage (`#/backtests/data`)

| Dataset | Shown |
|---|---|
| NIFTY 50 and India VIX, 1-minute | first and last day stored, days, gaps (weekdays with no file that aren't holidays) |
| NIFTY options, real 1-minute | one row per saved expiry (`NIFTY:2026-11-09`, …): days stored, and the next deadline (an expiry's evening). A calendar strip of trading days, red where the nearest expiry wasn't saved, because real-price backtests skip those days |
| NIFTY future, 1-minute | as above; the note that Kite serves only the live contract (section 4) |
| The 50 stocks, 1-minute | days, and stocks with fewer days than the index |
| Option chains, end of day (bhavcopy) | days, gaps |

Coverage comes from the market-data manifest (`market_data_files`, what `algo data status` prints).
Whether a day has real option prices uses the engine's own rule (`History.real_options`), so the
page shows exactly the days a real run will skip.

**Download** buttons run the existing commands in the background, with their log in `logs/`:
`algo data options`, `index`, `futures`, `constituents-1m` and `bhavcopy`. One download at a time.
The Kite ones need the day's login: they show the login state from `/api/control` and the existing
**Log in to Kite** button. You run the login; the dashboard never types credentials.

### 6.8 Launching and the queue

- **The folder is the queue.** `POST /api/backtests` checks the request, creates
  `data/backtests/<name>/` with `request.json` (the request as sent) and `overrides.json`, and
  writes `status.json` with `state: queued`. A small worker thread in the dashboard starts the
  oldest queued folder whenever no backtest is running. If the dashboard restarts, it picks the
  queue up again from the folders.
- **One at a time,** because a backtest uses a full core.
- **A background process, not a console window:**
  `python -m trading_core backtest --algo … --from … --to … --name … --options … --overrides-file overrides.json`,
  with its output written to `run.log` in the folder. On Windows it starts with below-normal
  priority and no window, so live algos keep the CPU first.
- **Progress** is `status.json`, which the engine already updates after every day. The page polls
  every 2 s while a run is queued or running.
- **Stale runs.** The engine will write its `pid` into `status.json`. A run marked "running" whose
  process is gone shows as **stale (stopped at day N of M)**. Old folders without a pid count as
  stale when `updated_at` is more than 15 minutes old (for example `ic_e4_off_hold_2y`, stopped at
  day 432 of 492 on 4 Nov).
- **Trash:** moves the folder to `data/backtests/.trash/<name>_<YYYYMMDD-HHMMSS>/`. Refused while
  the run is queued or running (stop it first). Nothing is ever hard-deleted.

**Why an overrides file.** `--set schedule.entry=09:53` parses the value as YAML, so an unquoted
time can become a number. `--overrides-file` takes JSON instead. It merges with `deep_merge` as
`--set` does, after any `--set` values. JSON object keys are always strings, while by-DTE tables
have integer keys in YAML: `{"2": 12}` would sit beside `{2: 15}` instead of replacing it. So keys
made only of digits are turned into integers when the file is read.

### 6.9 Server additions (all thin, in `app.py` style)

New module `src/dashboard/backtests.py` (folder scan, queue worker, trash) and
`src/dashboard/data_coverage.py`; `app.py` adds the routes. Writes need the `X-Algo-Desk` header,
like every other write.

| Endpoint | Does |
|---|---|
| `GET /api/backtests` | Scans `data/backtests/*/` (skipping `.trash` and folders without `status.json`, e.g. `condor_balance_study`, the swing studies). Returns per folder: `status.json`, `summary.totals` and `meta`, and `request.json`'s sweep fields. Cached by file modification time, so 150 folders stay fast |
| `GET /api/backtests/{name}` | Status, meta, the whole `summary.json`, the request, the config snapshot of its first run |
| `GET /api/backtests/{name}/trades`, `/journal`, `/performance`, `/positions/{id}`, `/positions/{id}/payoff` | The existing `strategy_queries` functions on that folder's `backtest.db` (mode paper, source replay) |
| `GET /api/backtests/{name}/log` | Last lines of `run.log` |
| `POST /api/backtests` | Body `{algo_id, start, end, name, option_prices, overrides, not_before?, sweep?, dry_run?}` (`dry_run`: check only, write nothing; the sweep checks every combination first). Checks the name pattern and clash, the dates, and validates the overrides by loading the config with them (the same `registry.load` the engine calls), so a bad value fails at once with the field named. Queues the run |
| `POST /api/backtests/{name}/stop` | Ends that backtest's process (only a pid the queue started); status becomes failed, "stopped" |
| `POST /api/backtests/{name}/trash` | Moves the folder to `.trash/` |
| `GET /api/algos/{algo_id}/config` | Current values (`model_dump`), `config_model.model_json_schema()`, the YAML comments by key, `BACKTEST_OVERRIDES`, `trade_window_days` |
| `GET /api/data/days` (built in F-B3) | Every trading day, and the days a real-price run can trade (the engine's own `History.real_options` test, reading only the expiry column). The New backtest page uses it for the date presets and the coverage strip. Cached until the files change |
| `GET /api/data/coverage` | Per dataset, as in 6.7: first / last / days / gaps / problems, each saved expiry and its state, stocks short of days, the `algo login` reminder |
| `POST /api/data/{dataset}` | Body `{from?}` (default: the last day stored). Starts the matching `algo data …` in the background, one at a time, log in `logs/data-<dataset>-<time>.log`. Kite datasets need the day's login (409 otherwise); `bhavcopy` doesn't |
| `GET /api/data/jobs` | The current or last download: running / exit code, and its last log lines |

### 6.10 Shared-code changes (kept small)

| File | Change |
|---|---|
| `src/trading_core/cli.py` | `algo backtest --overrides-file PATH` (JSON; digit keys → integers) |
| `src/trading_core/backtest/engine.py` | `pid` in the status meta. Nothing else |
| `src/dashboard/static/pages/strategy.js` | Panels take an API base, so the Run page reuses them; notes hidden for backtests |
| `src/dashboard/static/app.js` | Router passes the `?query` part to pages; two-menu sidebar with collapsible sections |
| `src/dashboard/static/index.html`, `style.css` | Sidebar markup and styles; new page scripts |
| `src/dashboard/app.py` | The routes in 6.9 |

`strategy_queries.py` needs no change: its functions already take the database as an argument.

### 6.11 Build order

Each stage is usable on its own, gets tests in `tests/dashboard/` for every new endpoint, and is
checked in the browser (desktop and narrow, dark and light), with a screenshot and a check that the
existing pages still work.

| Stage | What | Done when |
|---|---|---|
| **F-B1** | Sidebar: two menus, collapsible sections, routes `#/backtests/...` open the sub-menu, icon-only mode, reduced motion. Placeholder pages | Back/forward and bookmarks open the right menu; section state survives a reload; strategy pages unchanged |
| **F-B2** | `GET /api/backtests`, `/{name}` and the per-run queries; **All runs** and **Run** pages (Summary, Trades, Days, Settings, Log) on the existing 144 folders; stale detection; trash | Every existing folder lists; a run's numbers match its `summary.json` and the CLI print |
| **F-B3** | `--overrides-file`, `pid` in status; config endpoint; **New backtest** form; queue worker; Stop; real-coverage strip | A one-day real run of the balanced condor started from the page finishes and opens; an edited by-DTE table reaches the engine as integers |
| **F-B4** | **Compare** | Two runs overlay; the day-by-day table adds up to each run's net |
| **F-B5** | **Parameter sweep** and its results page | A 2 × 2 sweep queues 4 runs, runs them in order and shows the heatmap |
| **F-B6** | **Coverage** and downloads | Gaps and missing option days match `algo data status` and a real backtest's `missing_options` |

## 7. Build order

| Step | What | Status |
|---|---|---|
| B1 | Downloaders: futures and stock minute history | done |
| B2 | `BacktestFeed`, option pricer, day builder, `algo backtest`, summary | done |
| B3 | Runner fix: replays and backtests watch the market between feed start and entry, as live does | done |
| B4 | Parameter sweeps | replaced by the dashboard sweep (F-B5, section 6.5) |
| F-B1 | Sidebar sub-menu, collapsible sections, `#/backtests/...` routes (placeholder pages) | done 8 Nov 2026 |
| F-B2 | All runs and Run pages (Summary, Trades, Days, Settings, Log), stale detection, trash | done 8 Nov 2026 |
| F-B3 | `--overrides-file`, `pid` in status, config and days endpoints, New backtest form, queue worker, Stop / Cancel, live log | done 8 Nov 2026 |
| F-B4 | Compare: overlaid equity / drawdown, KPIs with the best marked, by DTE / weekday / exit / month, settings that differ, day by day | done 8 Nov 2026 |
| F-B5 | Parameter sweep: 1–2 settings (by-DTE cells included), up to 20 runs, every combination dry-run checked before any is queued; results table, heatmap or bar chart, best by a chosen KPI | done 8 Nov 2026 |
| F-B6 | Coverage: real option days strip, expiries (collecting / complete / expiry day lost), gaps per dataset, the `algo login` expiry reminder; Download buttons running `algo data ...` in the background, Kite ones gated on the login | done 8 Nov 2026 |
