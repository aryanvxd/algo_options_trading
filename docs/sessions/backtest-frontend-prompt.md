# Prompt for a new chat: the Backtests section of the dashboard

Copy everything below the line into a new chat opened in `E:\Algo_Trading\Algo\algo_options_trading`.

---

I want to run and study backtests from the dashboard instead of the terminal. Today, every
backtest goes through `algo backtest` or one-off scripts, and the sidebar's **Backtests** button
still says "Soon". Plan it with me first, then build it in stages.

## Read first

- `docs/sessions/project-context.md`: project rules, and how I work with you.
- `docs/design/backtest.md`: the engine, real option prices (section 5a), limits (section 4) and
  the dashboard plan (section 6, F-B1 to F-B5). This prompt extends section 6. **Update that doc
  first, show me the changes, then build.**
- `docs/design/dashboard-strategy-pages.md`: how the strategy pages were built. Reuse their
  tables, charts, trade detail and payoff chart.
- Code: `src/dashboard/` (`app.py`, `control.py`, `strategy_queries.py`, `static/index.html`,
  `static/app.js`, `static/style.css`, `static/pages/strategy.js`),
  `src/trading_core/backtest/` (`engine.py` `run_backtest`, `summary.py`), and the backtest
  arguments in `src/trading_core/cli.py`.
- Run `.venv/Scripts/graphify query "..."` before your first code search, and
  `.venv/Scripts/graphify update .` after code changes (see `CLAUDE.md`).

## What I want

1. **Do it all from the browser.** Pick a strategy, change settings, run, watch progress, read
   results, compare runs. I should not need the terminal or a chat for routine backtests.
2. **Keep the server thin.** Put the work in the page: build forms from the config schema, work
   out what changed, generate sweep combinations, compare and chart on the client. The server
   only lists and reads backtest folders, and launches the existing engine. Don't write a second
   backtest engine, in JavaScript or anywhere else.
3. **Navigation like Vercel's dashboard.** Clicking **Backtests** in the main sidebar swaps the
   sidebar to a Backtests sub-menu, with **← Main menu** at the top to return. Sections in both
   menus collapse and expand. Use Vercel only as inspiration for this pattern; keep our own look.
4. Let me see clearly how each strategy behaves: by DTE, weekday, exit reason, entry time and
   month, with trade-by-trade detail.

## Proposed design (challenge anything that's wrong)

### Sidebar

- **Main menu:** Overview, then collapsible sections: Strategies, Markets, Research (Backtests ›,
  Market data), System.
  - Each section's open/closed state is remembered with `App.prefs`.
  - Backtests gets a chevron (›) showing it opens a sub-menu.
- **Backtests sub-menu:**
  - **← Main menu**
  - Run (collapsible): New backtest, Parameter sweep
  - Results (collapsible): All runs, Compare
  - Data (collapsible): Coverage
  - Recent: the 3–5 most recent runs, with a status dot (running / done / failed)
- **Behaviour:**
  - A short slide between the menus, with no animation under `prefers-reduced-motion`.
  - A `#/backtests/...` link opens the sub-menu directly, and the browser Back button still works.
  - In the collapsed, icon-only sidebar, Back becomes an icon with a tooltip.
  - It must still work on narrow windows.

### Pages, with routes under `#/backtests/`

| Page | What it does |
|---|---|
| **All runs** (`runs`) | One row per folder in `data/backtests/`: name, algo, version, dates, option prices (real/model), trades, net, win rate, profit factor, max drawdown, run time, status with day N of M while running. Filter by algo, search by name, sort any column. Tick 2–6 rows → Compare. Delete moves the folder to `data/backtests/.trash/` after a confirm, never a hard delete |
| **New backtest** (`new`) | Strategy picker → form built from the algo's config: current values pre-filled, grouped like `config.yaml`, with the YAML comments as help text if practical. By-DTE tables (`stop_by_dte`, `trail_arm_by_dte`, …) edit as one row of DTE 0–4. Times use a time input. Shows a "changes from live config" list as you edit. Date range with presets (last 5 / 20 / 60 trading days, 1 y, 2 y). Option prices: **real** by default, with real-data coverage for the chosen range shown before running; model with a warning. Auto-generated name, editable, checked for clashes. **Run** → opens the run page showing progress |
| **Run** (`run/<name>`) | Tabs like a strategy page. **Summary:** KPI tiles, equity curve with drawdown, monthly heatmap, tables by DTE / weekday / exit / entry time, the data limits. **Trades:** the strategy trade log and trade detail, read from that folder's `backtest.db`. **Days:** every day with result or skip reason. **Settings:** the overrides used, as a diff from the live config. Buttons: **Run again with changes** (opens New pre-filled) and **Add to compare** |
| **Parameter sweep** (`sweep`) | Choose 1–2 settings and a list of values each; the page builds the grid (max about 20 runs), names them `<sweep>_<nn>`, and the server queue runs them one after another. The result is a table, one row per combination, best marked, plus a heatmap for a 2-setting sweep |
| **Compare** (`compare?runs=a,b,c`) | Overlaid equity curves, KPIs side by side, settings that differ, day-by-day P&L table with the best per day marked |
| **Coverage** (`data`) | For each dataset (index, options real candles per expiry, futures, VIX), the dates stored and the gaps, from `algo data` stores. **Download** buttons launch `algo data options` and the like. They need the day's Kite login, so they show the existing login state and button. Highlight days with no saved option candles, because real-price backtests skip them |

### Server additions, all thin, following `app.py` style and `X-Algo-Desk` for writes

- `GET /api/backtests`: scan folders and read only `status.json` and `summary.json`; there are
  about 150 folders. Skip folders without `status.json`, such as `condor_balance_study`. Mark a
  "running" status as stale if `updated_at` is old and its process is gone.
- `GET /api/backtests/{name}`: summary, status, meta. Trades, days and trade detail come from
  the existing `strategy_queries` functions pointed at that folder's `backtest.db`. Those runs
  are mode `paper`, source `replay`.
- `GET /api/algos/{algo_id}/config`: the current config values plus
  `config_model.model_json_schema()`, so the page can build the form.
- `POST /api/backtests`: body `{algo_id, start, end, name, option_prices, overrides}`, where
  `overrides` is a JSON object. Add an `--overrides-file` (JSON) option to `algo backtest` rather
  than building `--set` strings. That avoids the YAML trap where `--set schedule.entry=09:53`
  parses as a number unless the value is quoted. Because `deep_merge` merges dicts, a by-DTE
  table must be sent whole, with all DTE keys.
  - Launch it as a background process, not a console window. Write its log to the run folder,
    and run one at a time through a small queue, because backtests are CPU-heavy.
  - Progress comes from `status.json`, polled every 2 seconds while running.
- `POST /api/backtests/{name}/trash`, `GET /api/data/coverage`, `POST /api/data/{dataset}`.

## How we work

- **Plan first.** Update `docs/design/backtest.md` section 6 with the final design and build
  order, and wait for my OK before writing code.
- **Build in stages, each one usable and tested:**
  1. sidebar sub-menu and collapsible sections;
  2. All runs and Run pages on existing folders;
  3. New backtest and the launch queue;
  4. Compare;
  5. Parameter sweep;
  6. Coverage and downloads.
- Add tests to `tests/dashboard/` for every new API.
- After each stage, check it in the dashboard (`algo dashboard`, http://127.0.0.1:8050) with the
  browser preview. Show me a screenshot, and check that existing pages still work.
- **Stack stays as it is:** plain HTML/CSS/JS in `src/dashboard/static/`, no build step,
  Chart.js / Lightweight Charts / Font Awesome as already vendored, the existing CSS tokens and
  components, dark and light themes, and nothing loaded from the internet.
- **Keep shared-code changes small** (CLI, engine, `strategy_queries`) and list them for me.

## Rules that still apply

- Every algo is paper only. The dashboard must never place, change or cancel orders, or touch a
  running live algo.
- I run `algo login` myself. Never type Kite credentials or print `.env`.
- **Commits:** commit or push only when I ask, and branch first if on `main`. Never commit
  `.env.example` or `docs/sessions/`, or another session's uncommitted files such as
  `docs/strategy-criteria/README.md` and `docs/strategy-criteria/weekend-sr-swing.md`.
- This laptop is the live paper-trading machine. Don't stop or restart running algos, and don't
  run heavy backtests during market hours (09:15–15:30 IST) without asking.

## Facts you'll need

- **Lot size and capital:** lot size 65, and the condors use 2 lots.
- **Backtest folders:** each one in `data/backtests/<name>/` holds `backtest.db` (same schema as
  `platform.db`), `status.json` (state, done, days, `updated_at`), `summary.json` (totals and
  `by_dte` / `by_month` / `by_exit` / …) and per-day `reports/`.
- **Real option prices** use the close of the last finished minute plus a modelled half-spread.
  Days whose nearest expiry wasn't saved aren't traded, and are counted as `missing_options`.
  - Modelled prices are biased against sellers, so don't present them as reliable for condors.
- **One-off study scripts** in `data/backtests/condor_balance_study/scripts/` patched exit rules
  in code, for example `trail_sweep.py`. The UI only needs settings that exist in the algo's
  config.
