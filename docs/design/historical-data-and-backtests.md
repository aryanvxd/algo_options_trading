# Historical options data and backtests — sources and design

Status: section 3's store, Black-76 greeks, Kite index/VIX/constituent downloader, NSE bhavcopy
downloader and recorder export are built (23 Oct 2026); the options vendor source and backtests are not. Researched 23 Oct 2026; vendor terms change often, so re-check
the "verify" items with a test pull before paying for anything.

## 1. What a backtest of this algo actually needs

For every trading day in the test window:

| Data | Granularity | Why |
|---|---|---|
| NIFTY options, **all strikes ±~1,000 pts** of the nearest weekly expiry (and next one) | 1-min OHLC, volume, OI | Strike selection, entry credit, mark-to-market, exits. Hedges sit up to 600 pts from ATM at 2 DTE (≈ ATM±12 strikes). |
| NIFTY 50 spot | 1-min | ATM, gap filter, chart |
| India VIX | 1-min | VIX < 20 filter at pre-open and at entry |
| NIFTY 50 constituents | daily (prev close, open) | advance/decline filter |
| Expiry calendar + lot size per date | daily | expiry-day moved Thu → Tue in 2025; lot size changed several times |
| Bid/ask spread | ideally tick, at least a model | 1-min OHLC has no book; spread + slippage decide whether this strategy is profitable |

Spot, VIX and constituents come free from Kite's historical API (they are live instruments).
**Expired option contracts are the hard part**: Kite does not serve them at all.

## 2. Source comparison (expired NIFTY options, 1-minute)

| Source | Expired options | 1-min | How far back | Strikes | Extra fields | Cost | Catches |
|---|---|---|---|---|---|---|---|
| **Zerodha Kite** | ❌ No — active contracts only; expired *futures* only as daily candles | active only | — | — | — | included in Kite Connect | Use only to record our own data going forward |
| **Groww Trade API** (backtesting endpoints) | ✅ `expiries` → `contracts` → `candles` | ✅ (30 days per request) | FNO "from 2020" | any listed contract | volume, OI | historical data is a paid add-on; ~₹499 + tax/month reported by a third party (verify) | Needs a Groww account; a conflicting "3 months" claim on the marketing page — test a 2021 expiry first |
| **Upstox Plus** (expired-instruments API) | ✅ expired option contracts + candles | ✅ | not stated for expired contracts (active intraday goes back to Jan 2022) | any listed contract | volume, OI | Plus is free to activate right now, but its brokerage is ₹30/order (irrelevant if you only pull data) | Needs an Upstox account; lookback must be tested |
| **DhanHQ** rolling expired options | ✅ | ✅ (1/5/15/25/60) | **5 years** | **relative to ATM only: ATM±10** for near-expiry index options, ±3 otherwise | **IV, OI and spot included** | Data API ₹499 + tax/month | ATM±10 misses our 2-DTE hedges (ATM±12); "continuous" relative strikes change contract as spot moves; a GitHub issue reports missing 1-min rows for Jan 2026 |
| **ICICI Breeze** | ⚠️ conflicting: community reports ~3 years usable, an old reply says no expired data via API | ✅ (1-sec claimed by SDK) | "10 years" claimed by SDK, unconfirmed | absolute strike + expiry | volume, OI | free with an ICICI Direct account | 100 calls/min, **5,000 calls/day** — slow for full chains |
| **TrueData / Global Datafeeds** | ❌ for retail in practice | — | GDF publishes 1–3 month retention | — | GDF has a greeks API (live) | ₹1.4k–2.8k/month (TrueData live plans) | Real-time vendors; a user reports TrueData won't sell expired options to individuals |
| **CSV sellers** (MoneyTicks, CloudTraderPro, ShoonyaTrader) | ✅ | ✅ OHLCV+OI | 2021–2023 onwards | often "liquid strikes" only | — | free samples / ~₹999 one-time | Unknown provenance and completeness; useful only as a cross-check |
| **NSE UDiFF bhavcopy** | ✅ every contract | ❌ daily only | many years | all strikes | settle price, OI | free | Format changed 8 Jul 2024 (old + new parsers); ideal for **validating** vendor data and EOD studies |
| **Our own recorder** (Kite WebSocket, already built) | going forward | 5-sec snapshots **with bid/ask** | from the day we start | subscribed strikes | bid, ask, depth qty | free | The only source of real spreads — use it to calibrate the fill model |

### Recommendation

1. **Primary 1-min source: Groww or Upstox** (absolute strikes, full chains, OI). Open the
   account, pull one 2021 and one 2024 weekly expiry, and check: rows per strike-minute, gaps on
   far-OTM strikes, close vs NSE bhavcopy close/OI for the same day. Pick whichever goes back
   further with fewer gaps; keep the other as a fallback. Both ≈ ₹0–500/month while downloading.
2. **Dhan as a cross-check for IV**, not primary, because ATM±10 can't cover our hedges.
3. **NSE bhavcopy** (free) to validate every downloaded day and for daily-resolution studies.
4. **Keep recording live** with our Kite feed from now on — after 2–3 months it gives real
   bid/ask spreads by moneyness and DTE to calibrate backtest fills.
5. Skip TrueData/Global Datafeeds for history, and don't buy CSV dumps except to cross-check.

Budget: roughly one or two months of a ₹499 data plan to back-fill 4–5 years, then nothing —
the recorder covers the future.

## 3. Market data store: Parquet + DuckDB (not the trading SQLite)

**Why separate:** 1-min chains are ~100–180k rows per trading day (≈ 25–45 M rows/year). In
SQLite that is several GB a year with indexes, it bloats backups, and big backtest scans would
compete with the live algo's writes. Parquet (columnar, zstd-compressed) is ~10× smaller, and
DuckDB scans it in place at millions of rows per second on this laptop. New dependencies:
`duckdb`, `pyarrow`, `numpy` (all ship Windows wheels).

**Size estimate:** ~3–8 MB per trading day (the IV/greeks columns compress poorly, so they are
stored as FLOAT32) → **~1–2 GB per year**, 5–10 GB for five years. Fine on the laptop's SSD.

### Layout (hive-partitioned, one file per dataset per day)

```
data/market/
  options_1m/underlying=NIFTY/year=2024/date=2024-01-05/part-groww.parquet
  index_1m/symbol=NIFTY 50/year=2024/date=2024-01-05/part-kite.parquet
  index_1m/symbol=INDIA VIX/year=2024/...
  options_eod/underlying=NIFTY/year=2024/date=2024-01-05/part-nse.parquet
  options_ticks/underlying=NIFTY/year=2026/date=2026-10-23/part-recorder.parquet
  equity_daily/universe=nifty50/year=2024/part-kite.parquet
  reference/expiries.parquet          # underlying, expiry, kind (weekly/monthly), lot_size, weekday
  reference/rates.parquet             # date, risk-free rate (91-day T-bill)
```

One file per day keeps re-downloads atomic (write `*.tmp`, then rename over the old file) and
lets several sources coexist (`part-<source>`) until one is chosen.

### Schemas

`options_1m` (one row per contract per minute):

| Column | Type | Notes |
|---|---|---|
| `ts` | TIMESTAMP | IST minute **start**, naive (same convention as the trading DB) |
| `underlying` | VARCHAR | partition key too |
| `expiry` | DATE | |
| `strike` | DOUBLE | |
| `opt_type` | VARCHAR(2) | `CE` / `PE` |
| `open`, `high`, `low`, `close` | DOUBLE | points |
| `volume` | BIGINT | contracts traded in the minute |
| `oi` | BIGINT | |
| `filled` | BOOLEAN | true if no trade that minute and the row was forward-filled |
| `spot` | DOUBLE | NIFTY 50 close for the same minute (joined at ingest) |
| `forward` | DOUBLE | synthetic forward from ATM put-call parity: F = K + (C − P)·e^{rT} |
| `tte_years` | DOUBLE | time to 15:30 on expiry day / 365 |
| `iv` | FLOAT | Black-76 implied vol on `close` (NULL if price below intrinsic or no solution) |
| `delta`, `gamma`, `theta`, `vega` | FLOAT | Black-76, per 1 point / per day / per 1 vol-pt |
| `iv_source` | VARCHAR | `computed` or the vendor's name if supplied |
| `source` | VARCHAR | e.g. `groww` |

`index_1m`: `ts, symbol, open, high, low, close, source`.
`options_ticks`: recorder export — `ts, underlying, expiry, strike, opt_type, ltp, bid, ask,
bid_qty, ask_qty, oi, volume`.
`options_eod`: bhavcopy — `date, underlying, expiry, strike, opt_type, open, high, low, close,
settle, oi, oi_change, contracts, turnover`.

**Greeks/IV are computed by us at ingest**, vectorised with numpy (Black-76 on the synthetic
forward, so no futures history is needed and dividends drop out). A day of chains takes well
under a second. Vendor IV, when available (Dhan), is kept only to validate ours.

### Manifest in the trading DB (small)

New SQLite table `market_data_files` so downloads are resumable and auditable:
`dataset, partition_key (underlying/symbol), trade_date, source, path, rows, contracts,
first_ts, last_ts, sha256, status (OK/PARTIAL/FAILED), validated_against (e.g. nse_eod),
max_close_diff, downloaded_at`. PK `(dataset, partition_key, trade_date, source)`.

### Code layout

```
src/trading_core/marketdata/
  store.py          # paths, atomic write_day(dataset, key, date, table), list_days()
  duck.py           # DuckDB connection with views over the globs; chain(date, expiry), minute(ts) helpers
  greeks.py         # Black-76 price/IV/greeks, vectorised
  validate.py       # compare a day against NSE EOD (close, OI), gap statistics
  sources/
    base.py         # HistoricalSource: expiries(), contracts(expiry), candles(contract, start, end)
    groww.py  upstox.py  dhan.py  breeze.py
    kite_index.py   # NIFTY 50, INDIA VIX, constituents (free with Kite Connect)
    nse_bhavcopy.py # old + UDiFF formats
    recorder.py     # nightly: tick_snapshots (SQLite) -> options_ticks (Parquet), then prune SQLite
cli: algo data download --source groww --underlying NIFTY --from 2021-01-01 --to 2026-10-23
     algo data validate --from ... ; algo data status
```

The nightly recorder export also keeps the trading DB small: ticks older than ~7 days are
deleted from SQLite once they are safely in Parquet.

## 4. Backtests: same algo code, new feed

A backtest is a series of replays driven by historical data instead of recorded ticks:

- **`HistoricalFeed(MarketFeed)`** loads one day from Parquet into memory and serves `Tick`s at
  the simulated clock's minute. Bid/ask are synthesised from the 1-min close by a **spread
  model** (by price, moneyness and DTE; default `max(0.05, 0.4% of price)`, later calibrated from
  `options_ticks`). The paper broker then adds slippage and charges exactly as in live.
- **`HistoricalPremarketSource`** builds the pre-open snapshot from `index_1m` + `equity_daily`.
- The runner steps **once per minute** (not per second), ≈ 360 loops a day, so a day runs in
  well under a second and **five years takes a few minutes**. Days run in parallel across the
  laptop's cores (one process per day-chunk, each writing its own runs).
- **Intra-minute exits:** a 1-min bar hides the path inside the minute. A `bar_mode` setting
  chooses `close` (mark at close) or `worst` (shorts marked at the bar high, longs at the low) —
  run both; if a result only holds under `close`, it's not robust.
- Lot size and expiry come from `reference/expiries.parquet` for each date, so pre-2025
  Thursday expiries and old lot sizes are handled without special cases.

### Backtest tables (SQLite, migration 0002)

Reuse the existing trading tables so the dashboard, reports and scorecards work unchanged:

- `algo_runs.source` gains the value `backtest`, plus a nullable `backtest_id` FK (indexed).
  Each backtest day writes its run, positions, legs, orders, fills, charges, events and
  `daily_summary` exactly like a paper day.

New tables:

**`backtests`** — one per backtest job

| Column | Notes |
|---|---|
| `backtest_id` PK | |
| `algo_id` FK | |
| `name` | e.g. "IC width 600 stop 5pts 2021–2026" |
| `sweep_id` FK nullable | set when part of a parameter sweep |
| `status` | QUEUED, RUNNING, DONE, FAILED, CANCELLED |
| `start_date`, `end_date` | test window |
| `data_source` | e.g. `groww_1m` |
| `fill_model` JSON | spread model, slippage, `bar_mode` |
| `config_snapshot` JSON, `config_hash` | the exact algo config tested |
| `params` JSON | just the overridden parameters (for sweeps) |
| `code_version` | git commit hash, so results are reproducible |
| `created_at`, `started_at`, `finished_at` | |
| `days_total`, `days_done` | progress for the dashboard |
| `metrics` JSON | net/gross P&L, charges, trades, win rate, avg win/loss, profit factor, max drawdown (₹ and days), worst day, longest losing streak, Sharpe/Sortino on daily P&L, exit-reason counts |
| `error` | traceback summary if FAILED |
| `notes` | |

**`backtest_sweeps`** — a grid of backtests: `sweep_id, algo_id, name, grid JSON (e.g.
{"exits.stop_loss_points": [4,5,6], "filters.vix_max": [18,20,22]}), start_date, end_date,
created_at, status`.

**Volume control:** 1-min position snapshots for a 5-year backtest are ~450k rows; a 50-run
sweep would be 20M+. So backtests store **daily rows only by default** (runs, positions, legs,
orders, fills, daily_summary); intraday snapshots are written only when a single backtest is run
with `--snapshots`, or re-generated on demand by re-running one day (it takes under a second).

Sweeps are the overfitting trap: report every grid point, not just the best, and hold out the
most recent 6–12 months as an untouched test period.

## 5. Also needed before live trading (separate from backtests)

- `order_updates`: every status change Kite reports for an order (postback/WebSocket), with
  filled qty and average price, so partial fills and rejections are traceable.
- `reconciliations`: per-minute snapshots of broker positions vs the algo's book, with a
  mismatch flag that fires the kill switch.

## 6. Build order

1. `marketdata/` store + DuckDB views + Black-76 greeks (with tests against known values).
2. Kite index/VIX/constituent downloader (free, can run now) and NSE bhavcopy downloader.
3. Recorder export (SQLite ticks → Parquet) — protects the data we're already collecting.
4. Primary options source after the test pull decides Groww vs Upstox.
5. `HistoricalFeed`, spread model, minute-step runner, `backtests` tables, `algo backtest` CLI.
6. Dashboard: Backtests page (list, metrics, equity curve, per-day drill-down) and Market data
   page (coverage calendar from `market_data_files`).

## Sources

- Kite, expired options not available: [Zerodha support](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/charts/articles/historical-data-for-expired-f-o-contract), [Kite forum](https://kite.trade/forum/discussion/15361/historical-data-for-expired-options-contract), [Kite forum (2026)](https://kite.trade/forum/discussion/15660/how-can-i-retrieve-instrument-details-and-historical-data-for-expired-futures-and-options), [historical data now free](https://kite.trade/forum/discussion/14806/historical-data-is-now-free-with-base-kite-connect-subscription)
- Groww: [backtesting API docs](https://groww.in/trade-api/docs/curl/backtesting), [changelog](https://groww.in/trade-api/docs/curl/changelog), [Trade API](https://groww.in/trade-api), [AlgoTest Groww page](https://docs.algotest.in/broker/groww/), [OIData blog](https://www.oidata.in/blog/groww-api-market-data)
- Upstox: [expired historical candles](https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/), [expired instruments](https://upstox.com/developer/api-documentation/expired-instruments/), [launch note](https://upstox.com/developer/api-documentation/announcements/expired-instruments-api/), [historical candle (v3)](https://upstox.com/developer/api-documentation/get-historical-candle-data/), [community: Plus required](https://community.upstox.com/t/historical-instruments-of-options/9028), [Plus pack terms](https://upstox.com/files/terms-and-condition/plus-pack.pdf)
- Dhan: [expired options data](https://dhanhq.co/docs/v2/expired-options-data/), [data API pricing](https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/), [missing rows issue #153](https://github.com/dhan-oss/DhanHQ-py/issues/153), [dhanhq-skills market data](https://github.com/dhan-oss/dhanhq-skills/blob/main/skills/dhanhq/references/market-data.md)
- ICICI Breeze: [API docs](https://api.icicidirect.com/breezeapi/documents/index.html), [Python SDK](https://github.com/Idirect-Tech/Breeze-Python-SDK), [ICICI article](https://www.icicidirect.com/futures-and-options/articles/how-to-download-historical-data-using-breeze-api-python-sdk), [breeze-historical-options](https://pypi.org/project/breeze-historical-options/), [tradingqna thread](https://tradingqna.com/t/which-is-the-best-source-for-options-historical-data/176182), [Kite forum](https://kite.trade/forum/discussion/11627/historical-options-data)
- TrueData / Global Datafeeds: [TrueData pricing](https://www.truedata.in/price), [GDF data retention](https://globaldatafeeds.in/global-datafeeds-apis/global-datafeeds-apis/introduction/type-of-data-available/), [tradingqna thread](https://tradingqna.com/t/where-can-we-get-authentic-data-for-historical-nifty-options-1-minute-candle-data-for-all-the-expired-contracts-atleast-past-10-years-data/182480), [Kite forum 2017](https://kite.trade/forum/discussion/1724/history-api-how-can-i-download-historical-data-of-expired-futures-options), [Quora](https://www.quora.com/Where-can-I-get-the-Bank-Nifty-options-data-of-every-one-minute-for-back-testing)
- CSV sellers: [MoneyTicks](https://moneyticks.com/), [CloudTraderPro](https://cloudtraderpro.in/free-historical-nifty-expired-options-data/), [ShoonyaTrader](https://shoonyatrader.in/free-historical-expired-options-contract-data/), [Stolo](https://stolo.in/solutions/nse-spots-futures-options-historical-data/)
- NSE bhavcopy: [NSE derivatives reports](https://www.nseindia.com/all-reports-derivatives), [nser package](https://archive.linux.duke.edu/cran/web/packages/nser/index.html)
