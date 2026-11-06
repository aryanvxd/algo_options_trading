# Backtests on minute history — engine and dashboard plan

- **Status:** engine built 31 Oct 2026 (B1–B3 below). The dashboard pages (section 6) are a plan.
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

`--set key=value` overrides any config value (repeatable). The summary has:
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
| Bid/ask | Close ± half the spread measured in the recorded ticks: 0.025 pt under ₹5, 0.05 under ₹40, 0.10 under ₹100, 0.50 above. The paper broker adds its 0.1-pt slippage |
| OI | From the candles, so PCR is real |
| Expiry and strikes | The nearest expiry in that day's candles |
| Holidays | The real list, so DTE is right when a holiday falls before the expiry |
| A day whose nearest expiry was never saved | **Not traded.** It is listed under `missing_options` in the summary, never run on the wrong contract |

The default stays `--options model`, so older results still compare.

**Limits of real candles:**
- 1-minute closes, not ticks. A stop between two closes fires at the next close.
- The spread is a model. It agreed with the recorded ticks to about 0.05 pt on OTM strikes on one
  morning, but it can be wrong in a fast market.
- A minute with no trade has no candle, so the last close stands. Far strikes can look stale.
- The directional scalp's `BACKTEST_OVERRIDES` still switch its premium skew off, even on real prices.

## 6. Dashboard plan (not built)

Sidebar **Research → Backtests** (now "Soon").

| Step | Page / piece | What it shows / does |
|---|---|---|
| **F-B1** | **Backtests list** | one row per backtest folder: name, algo, version, date range, trades, net, win rate, profit factor, max drawdown, when it ran, status (running / done / failed). Tick 2–4 rows → **Compare** |
| **F-B2** | **Backtest page**, tabs like a strategy page | **Summary**: KPI tiles, equity curve with drawdown below it, monthly P&L heatmap (rows = years, columns = months), tables by DTE / exit reason / side, and the limits from section 4. **Trades**: the trade log from the strategy pages (same filters, CSV), each trade opening the same detail (payoff chart, P&L path, the bias and bricks at entry). **Days**: every day with its result or skip reason. **Settings**: the config used, with the differences from the live config highlighted |
| **F-B3** | **New backtest** form | algo, date range, name, and the algo's main settings as fields (pre-filled from its config; only changes are passed as `--set`). **Run** starts `algo backtest` in its own window, like the strategy Start button. The list shows progress (day N of M) from the folder's status file |
| **F-B4** | **Compare** | equity curves overlaid, KPIs side by side, settings that differ. For a parameter sweep (`--set trigger.brick_pts=8,10,12`): one row per value, with the best marked |
| **F-B5** | **Data coverage** | per dataset, the date range stored and the gaps, with a **Download** button for each (Kite ones need the day's login) |

Built on what exists:
- the strategy pages' tables, charts and trade detail, pointed at the backtest's database;
- the Start-button launcher;
- the payoff chart.

New server pieces:
- `GET /api/backtests` (scans the folders);
- `GET /api/backtests/{name}/summary|trades|days|config`;
- `POST /api/backtests` (launch);
- `GET /api/data/coverage` and `POST /api/data/{dataset}` (launch a download).

## 7. Build order

| Step | What | Status |
|---|---|---|
| B1 | Downloaders: futures and stock minute history | done |
| B2 | `BacktestFeed`, option pricer, day builder, `algo backtest`, summary | done |
| B3 | Runner fix: replays and backtests watch the market between feed start and entry, as live does | done |
| B4 | Parameter sweeps (`--set key=a,b,c`) | next |
| F-B1–F-B5 | Dashboard pages above | plan |
