# Project context (read by every strategy session)

Each strategy has its own Claude Code session; its prompt is in this folder. This file is what
they all share.

## The project

- **Repo:** `E:\Algo_Trading\Algo\algo_options_trading` (git, branch `main`, pushed to origin).
  Python 3.13 venv in `.venv`.
  - Run the CLI as `.venv/Scripts/algo ...`.
  - Run the tests as `.venv/Scripts/python -m pytest -q` (244 tests, about 4 minutes).
- **This laptop is the live paper-trading machine**, not the desktop. Algos run here during market
  hours.
  - The laptop is switched on at about 09:30. Nothing may need to run before 09:35, and the earliest
    entry is 09:45.
  - It can't always stay on until the close. So the record of a day is the evening's
    `algo data options` (real option candles, by each expiry's evening at the latest), and strategies
    are judged with `algo backtest --options real` (`docs/design/backtest.md`, section 5a).
- **Stack:**
  - Kite Connect (Zerodha): WebSocket feed, historical candles, orders.
  - SQLite at `data/db/platform.db`, through SQLAlchemy plus Alembic migrations (head: `0005`).
  - A FastAPI dashboard with plain JS/CSS and vendored Chart.js, Lightweight Charts and Font Awesome
    (`src/dashboard/`, `algo dashboard`, port 8050).
  - A Parquet + DuckDB market-data store in `data/market/`.
- **Platform** (`src/trading_core/`):
  - `BaseAlgo` lifecycle (`strategy/base.py`): day_check → start_feed → entry → square_off →
    report.
  - Hooks: `build_entry`, `build_exit_rules`, `reentry_block`, `manage_open` / `close_legs` (partial
    exits), `carry_overnight` / `adopt` (positional algos).
  - `Runner`: the same loop live and in replay.
  - Paper broker with charges and 0.1-pt slippage.
  - Journal: every check, event, order and position goes to the DB.
  - Exit rules: `risk/rules.py`.
  - Market calendar: `market/events.py`.
  - Live breadth: `market/breadth.py`.
  - Renko / VWAP: `marketdata/renko.py`.
  - Backtest engine: `backtest/`, `algo backtest`.
- **Every algo is paper only.** Live order placement is disabled platform-wide in v1.

## Kite rules

- You (Claude) never type Kite credentials. The user runs `algo login` each morning; the token
  lasts until about 06:00 the next day.
- The API key and secret live only in `.env` (git-ignored). Never print or commit them.
  `.env.example` has a line-ending-only change: never commit it.
- Kite allows **3 WebSocket connections per API key**. On Monday mornings the iron condor, the
  strangle scalp and the weekend condor's buy-back already use them. That is why the directional
  scalp starts at about 09:50 on Mondays.
- Reading the user's holdings, orders or alerts is fine when the task needs it. Placing, changing
  or cancelling orders, alerts or GTTs needs the user's explicit instruction at that moment.

## How we work

- The strategy's spec in `docs/strategy-criteria/` is the source of truth: change the spec first,
  then `config.yaml` and the code.
- Write docs in the same plain, clear style as the existing ones: short sentences, tables for
  rules, no jargon without explanation.
- **graphify:** before the first grep or source read for a codebase question, run
  `.venv/Scripts/graphify query "<question>"`. After code changes, run `.venv/Scripts/graphify update .`.
- **Commit or push only when the user asks.** End commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Before a migration on the real DB: check no algo is running (`data/locks/*.pid` processes), then
  back up with the sqlite3 backup API to `data/db/backups/`. A plain file copy misses the WAL.

## Shared code and the other sessions

- `src/trading_core/`, `src/dashboard/` (app.js, style.css, the shared queries) and
  `config/platform.yaml` are used by every strategy, and other sessions work on other strategies
  at the same time. So:
  - keep changes there small and backward compatible;
  - run the full test suite after touching them;
  - list every shared file you changed in your summary;
  - don't refactor shared code for one strategy's convenience.
- Strategies and their sessions (prompts in this folder):

| Strategy | Code | Prompt |
|---|---|---|
| NIFTY short iron condor (intraday) | `src/algos/iron_condor/` | `iron-condor.md` |
| NIFTY short iron condor, balanced premium (intraday; runs live, the original is replayed each evening) | `src/algos/iron_condor_balanced/` | `iron-condor.md` |
| NIFTY long strangle scalp (intraday) | `src/algos/strangle_scalp/` | `strangle-scalp.md` |
| NIFTY weekend iron condor (Fri → Mon) | `src/algos/weekend_condor/` | `weekend-condor.md` |
| NIFTY directional buying scalp (Renko) | `src/algos/directional_scalp/` | `directional-scalp.md` |
| Weekend support/resistance swing (stocks): a review tool, no automated entries or Kite writes | `src/swing/` | `weekend-sr-swing.md` |
