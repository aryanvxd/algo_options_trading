# Dashboard: strategy pages — build plan

- **Status:** built 27 Oct 2026 (F1–F7, with journal notes). Code: `src/dashboard/strategy_queries.py`,
  `static/pages/strategy.js`, `static/pages/strategies/strangle_scalp.js`; tests in `tests/dashboard/`.
- **Scope:** a "Strategies" section in the sidebar with one page per algo (iron condor, long
  strangle scalp). Each page has tabs; the payoff chart and capital used / ROI per trade are new.
- **Stays the same:**
  - plain HTML/CSS/JS in `src/dashboard/static/`, with no build step;
  - Chart.js and Font Awesome vendored;
  - FastAPI JSON API;
  - the dashboard never touches a running algo. The one exception, if you want it, is the
    journal notes table in section 6.

## 1. Navigation

```
Sidebar                      Strategy page (e.g. #/strategy/iron_condor/today)
─────────                    ┌──────────────────────────────────────────────────────┐
Overview                     │ NIFTY short iron condor   [Paper ▾] [Sessions|Replays]│
                             │ ● In trade · v0.2.1 · 2 lots · capital ₹__           │
STRATEGIES                   ├──────────────────────────────────────────────────────┤
  ◆ Iron condor              │ Today | Trade log | Journal | Performance | Rules     │  ← tab bar
  ◆ Long strangle scalp      ├──────────────────────────────────────────────────────┤
                             │  (tab content)                                       │
TRADING / RESEARCH / SYSTEM  │                                                      │
  (existing "Soon" items)    └──────────────────────────────────────────────────────┘
```

- **The strategy list comes from the algo registry** (`src/algos/*`, skipping `_template`),
  merged with the `algos` table. A new strategy gets its page before its first run, which is
  why the scalp page exists from day one. `/api/meta` returns this list. Each entry carries:
  name, version, mode, lots, capital, status today, and its spec link
  (`docs/strategy-criteria/…`).
- **Routes** are `#/strategy/<algo_id>/<tab>`. Today the router only reads a page name, so it
  will also pass the rest of the path to the page as arguments. Back, forward and bookmarks keep
  working.
- **The tab bar is the same component for every strategy.** A strategy can hide a tab or add
  one through a small per-strategy file (`pages/strategies/<algo_id>.js`) that adds panels. With
  no such file, the generic page is used.
- The page header keeps the Overview's mode filter (Paper / Live / All) and source filter
  (Sessions / Replays). Each is remembered per strategy.
- The overview's strategy cards link to the matching strategy page.

## 2. Tabs

| Tab | Shows | Refresh |
|---|---|---|
| **Today** (any day via date picker) | Status and reason (running / waiting for entry / in trade / skipped and why), live P&L tiles, legs table (strike, side, qty, entry, LTP, bid/ask, P&L), **payoff chart**, intraday P&L with stop/target lines, entry checks with value / threshold / pass, event timeline | 5 s when the day is today and a run is live |
| **Trade log** | One row per trade (section 4). Filters: date range, exit reason, DTE, win/loss. Click a row to open trade detail. **CSV export** | on load |
| **Journal** | Day by day, newest first: trade-day decision, every check and its value, skipped days with the reason, trades with entry/exit, events, and your own notes (section 6) | on load |
| **Performance** | Equity curve (net, cumulative), drawdown, win rate, average win / loss, expectancy, profit factor, ROI on capital. Grouped by **DTE, weekday, exit reason** and entry time | on load |
| **Rules** | The current config values (stop, target, windows, lots, limits) and the version, read from the run's `config_snapshot`, with a link to the strategy's criteria doc. A run on an older config is flagged | on load |

The grouping by DTE and weekday is what will tell us, after enough paper days, **which days to
keep for live trading**.

### Trade detail (opens over the trade log, also linked from Today)

- Legs and fills: price, bid/ask at fill, slippage.
- Charges per order.
- Capital used and ROI.
- Payoff at entry, with the exit spot marked.
- P&L curve from the 5-second snapshots, with peak and trough.
- The checks that let it in, and the exit rule that fired.

## 3. Payoff chart

One line chart (Chart.js, linear x axis) from a new endpoint `GET /api/positions/{id}/payoff`.
It's computed on the server so the formulas live in Python with tests:

- **x axis:** NIFTY spot. The grid spans the outer strikes ± ¾ of the strike span, or ± 1% of
  spot, whichever is larger (±3% squashed a strangle's useful part). Points every 10 pts.
- **At-expiry line:** the sum over legs of `side × qty × (intrinsic − entry price)`, in rupees.
  Profit is shaded green and loss red. Breakevens are labelled.
- **"Now" line (T+0),** for open trades only. It uses Black-76 from
  `trading_core/marketdata/greeks.py`, with each leg's IV taken from its latest mid price and
  the time left to 15:30 on expiry day. It shows what the position is worth now if NIFTY jumps,
  which matters more than the expiry line for an intraday trade.
- **Markers:** spot at entry, spot now (or at exit), the short strikes, and horizontal stop and
  target lines, which come from the config in points × lot size × lots.
- **Closed trades:** the expiry line plus the entry and exit spots. T+0 isn't rebuilt for old
  trades in v1.

Iron condor: a flat top between the shorts, sloping down to a capped loss beyond the hedges.
Strangle: a V with its minimum (the premium paid) between the two strikes.

## 4. Capital used and ROI (new data)

Nothing records capital today. Add it when a trade is entered:

| Strategy | Capital used = | Source |
|---|---|---|
| Iron condor (sold options) | **Kite basket margin** for the 4 legs, including the hedge benefit | `kite.basket_order_margins(...)` — a read-only call, so it's safe in paper mode too. Called once, just before entry |
| Fallback, if that call fails | worst-case loss = (hedge − credit) × qty | marked "estimate" in the UI |
| Long strangle scalp (bought options) | **premium paid** = Σ ask × qty, plus entry charges | from the fills |

- **Trade ROI** = net P&L ÷ capital used. For example, ₹197.85 on about ₹90k margin is +0.22%.
- **Strategy return** = cumulative net ÷ the strategy's **capital bucket**, a new `capital:`
  field in each algo's `config.yaml`. Fill it in from the criteria docs (the iron condor's is
  still `₹ ____`).
- ROI is shown in the trade log, trade detail, the Today tiles and the Performance tab.

**DB changes (Alembic migration `0003`):**

| Change | Why |
|---|---|
| `positions.capital_required` (float), `positions.capital_source` (`kite_margin` / `estimate` / `premium`) | ROI per trade |
| `positions.spot_at_entry`, `positions.spot_at_exit` (float) | payoff markers, and grouping trades by move size. The iron condor already keeps spot in `params`; this makes it common to all algos |
| (none — `live_state.levels` already exists, JSON) | algo-specific live values for the Today tab: iron condor trail level (later), scalp pair / V / signal progress / trades used |
| `journal_notes` table (only if section 6 is approved) | your notes |

- **Shared code:** `BaseAlgo` records capital right after entry, so every algo gets it.
- **Back-fill:** the existing 27 Oct trade gets an estimate, and the UI marks it as one.

## 5. Long strangle scalp page (simple v1)

Same generic tabs, with these differences:

- **Today:**
  - a "trades used today: 1 / 3" tile, plus the day stop (2 losses / −₹800);
  - a live **signal panel** from `live_state.levels`: the pair being watched (strikes,
    premiums, deltas), the pair value V with a 60-second sparkline, the windows passed so far
    out of N, and the NIFTY futures move;
  - up to 3 trades listed in order, each with its own payoff and P&L curve.
- **Trade log:** adds hold time, the leg that drove the move, and peak P&L.
- **Performance:** adds win rate against the **~53% break-even line** (section 6 of the
  strategy doc), and results by entry minute and by DTE.
- **Rules:** links to [long-strangle-scalp.md](../strategy-criteria/long-strangle-scalp.md) and
  shows "spec: plan — thresholds not final".

**The page can be built now, but it shows nothing until the scalp algo exists.** The algo
itself (`src/algos/strangle_scalp/`) isn't written yet, and paper-trading it also needs these
platform changes from that doc's section 9:
- more than one trade per day;
- 1-second recording;
- the NIFTY futures feed;
- trailing, stall and max-hold exits;
- unwinding a one-legged fill.

That build is separate from this frontend plan.

## 6. Journal: notes (needs your OK)

The automatic journal (checks, decisions, trades, events) needs no new data. A trading journal
is more useful with **your own notes**: a text note and tags per day or per trade, such as
"news day", "manual kill", or "would have skipped".

Notes would be the dashboard's first write. It would write only to the new `journal_notes`
table, and the algos never read it, so a running algo can't be affected.

## 7. Build order

| Step | What | Done when |
|---|---|---|
| F1 | Sidebar "Strategies" section from the registry; router with arguments; strategy page with header + tab bar + empty states; both strategies listed | clicking each strategy opens its page; tabs switch; back/forward work |
| F2 | Migration 0003, capital recorded at entry (Kite margin / premium), `capital:` in configs, back-fill | a replay records capital; tests for margin fallback and ROI |
| F3 | **Today** tab + payoff endpoint and chart | the 27 Oct trade shows legs, payoff with breakevens and stop/target, checks, events |
| F4 | **Trade log** + trade detail + ROI + CSV | rows match `daily_summary`; CSV opens in Excel |
| F5 | **Journal** (automatic; notes if approved) | skipped and traded days both read clearly |
| F6 | **Performance** with DTE / weekday / exit-reason groups | numbers match a hand check on replay data |
| F7 | Scalp-specific panels (signal panel, trades-used tile) | renders against a replay of the scalp |

Each step gets `queries.py` unit tests, as the overview has, plus a check in the browser
pane at desktop and phone widths. The sidebar collapse at narrow widths is fixed as part of F1.

## 8. After the frontend: iron condor v3 (noted here, specified later)

From your message of 27 Oct 2026, to go into
[iron-condor.md](../strategy-criteria/iron-condor.md) as v3:

- **Trailing target and stop:** once the trade is in profit by X, move the stop up behind the
  peak, and let the target trail instead of exiting at the fixed +8 pts. The rules and numbers
  are to be written into the doc first.
- **Time exit 12:00** instead of 15:15, because the afternoon tends to bring the volatility the
  condor doesn't want.
- The Today tab would show the current trail level from `live_state.levels`, so the frontend
  supports it from the start.
