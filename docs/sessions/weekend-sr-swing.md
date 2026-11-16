# Session prompt: weekend support/resistance swing (stocks)

Paste everything below the line into a new Claude Code session opened in
`E:\Algo_Trading\Algo\algo_options_trading`.

---

You are taking over the **weekend support/resistance review tool for stocks** in an existing
algo-trading platform. It started as an automated swing strategy. The backtests found no edge, so
the user decided on 6 Nov 2026 to keep it as a **review tool for their own decisions**.

## Read first

1. `docs/sessions/project-context.md`: the project, Kite rules, how we work, and the shared code
   you must be careful with.
2. `docs/strategy-criteria/weekend-sr-swing.md`: the spec. Sections 9a and 9b are the backtest
   results, section 10 the build order, section 11 the questions still open.

## What it is

- A weekend look at many stocks: sector and theme indices first, for an industry overview, then the
  stocks in them, with their support and resistance zones and charts.
- **It is not a signal.** "Near support" says where price is against past turning points, not that
  it will bounce.
- Sections 5–7 of the spec (setups, alerts/ATOs, order flow) are **parked**. They record what was
  tested and what Kite offers. Don't build them unless the user asks again.

## What the backtests found (1 Nov 2026)

- **Zones:** about 110,000 zone events over six years held no more often than random price bands of
  the same width (−0.5 points). Scores, flips, touches and the other score parts add nothing once
  width is matched.
- **Setups:** 4,365 setups against random entries with the same stop and target distances: no edge
  in either exit mode, with or without the trend and sector filters. Target-only trades made money,
  but their random twins made the same: that was the market's rise.
- So the pages show distances, not scores, and every page carries the "no edge" note. Keep it.
- A new entry idea can be tested against the same baseline: `src/swing/setup_backtest.py`.

## What is built

| Part | Where |
|---|---|
| Universe: 35 indices, 384 stocks, NSE industries | `algo swing universe`, `config/universes/swing.yaml` |
| Daily candles (`equity_daily`, keys `swing`, `swing_indices`, `swing_holdings`), weekly bars | `algo swing data`, `algo swing bars` |
| Zone engine | `src/swing/zones.py`, `algo swing zones <SYMBOL>` |
| Backtests | `algo swing backtest [zones\|setups]`, results in `data/backtests/swing_*` |
| Weekend routine: candles, holdings, overview, charts | `algo swing scan` (`--no-refresh` skips Kite) |
| Holdings review, read-only | `algo swing holdings`, `data/reports/swing/holdings.json` |
| Dashboard: Swing section with Sectors and stocks, Holdings and Orders (GTTs, ATOs, alerts, read-only); sub pages with a breadcrumb for each index and stock; a stock timeline | `src/dashboard/static/pages/swing.js`, `swing.css`, `src/dashboard/swing_queries.py` |
| Offline pages | `data/reports/swing/overview.html`, `algo swing chart <SYMBOL> [--mark PRICE]` |
| Weekend workflow (R5, 9 Nov): Swing sub-menu, Weekend run, Picks funnel, 5 watchlists, picks journal with random twins, Settings, fundamentals CSV | spec section 8a; `src/swing/funnel.py`, `tracking.py`, `watchlists.py`, `fundamentals.py`; `src/dashboard/swing_jobs.py`, `swing_workflow.py`; `pages/swing_*.js` |

Watchlists, the journal and fundamentals use tables from migration 0006 (`algo db upgrade`).

## Hard rules

- **Nothing in `src/swing/` writes to Kite, and it stays that way** unless the user decides otherwise
  in that moment. Never create, change or delete an alert, ATO or GTT, and never place, change or
  cancel orders. Never sell holdings.
- **Holdings are personal financial data.** They stay in the git-ignored `data/` folder and on the
  local dashboard: never in code, docs, tests or chat. Commands print counts, not symbols.
- **No investment advice on specific holdings.** Pages show information (zones, distances), not
  recommendations.
- Run `algo swing scan` outside market hours: it shares the API key with the algos.

## Working notes

- `git worktree`s have no `.venv`, `data/` or `.env` (all git-ignored). Use the main checkout's venv,
  and never link `data/` into a worktree: a worktree can be removed, and its links with it.
- Don't `cd` out of a worktree session: that can make the app remove the worktree. Commit early.
- A leftover `PYTHONPATH` pointing at a worktree makes `algo` look for `.env` there and fail.

## Where to start

R5 (the weekend workflow, spec 8a) is built. Fundamentals come from a CSV the user uploads; an
automatic source (NSE filings or a site like Screener, check its terms first) is still open: ask
before building it. Watch the Picks journal: if picks don't beat their twins, say so.
