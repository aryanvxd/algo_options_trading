# Market calendar page and market breadth — plan

- **Status:** calendar built 29 Oct 2026 (C1–C3).
  - Code: `trading_core/market/events.py`, `dashboard/calendar_queries.py`,
    `static/pages/calendar.js`.
  - India list: `config/calendar/india_2026_27.yaml`.
  - Data: migration 0004.
  - Advances/declines (section 5) is still a plan.
- **Scope:**
  - a **Calendar** page in the dashboard, in our own UI, with the same content as Sensibull's
    stock-market calendar (economic events, company results, holidays);
  - the same events fed to the algos as entry checks;
  - a plan for **advances/declines** (section 5).

## 1. The page

Sidebar: a new **Markets** section with **Calendar** (`#/calendar/economic`). Tabs across the top,
like the strategy pages:

| Tab | Shows | Source |
|---|---|---|
| **Economic** | event, country flag, date/time IST, impact chip (High / Medium / Low), expected (forecast), previous; actual where we have it | global: Forex Factory weekly feed. India: our own list (section 2) |
| **Results** | company, date, purpose (results / dividend / other); NIFTY 50 companies highlighted, with a "NIFTY 50 only" filter | NSE event calendar |
| **Holidays & expiries** | NSE trading holidays, weekly and monthly NIFTY expiries, and the days each algo trades | `config/holidays/`, the daily instrument snapshot, the algos' configs |

- **Filters:** date range (default today → +3 weeks), country, impact, and search.
- **Marked on the page:** a "this weekend" band, for the weekend condor's event check, and today.
- **Layout:** works at phone width, like the strategy pages.

## 2. Data sources (checked 29 Oct 2026)

| Source | What it gives | Limits |
|---|---|---|
| **Forex Factory feed** `nfs.faireconomy.media/ff_calendar_thisweek.json` | ~100 events/week: title, country (USD, EUR, JPY, GBP, CNY, AUD, CAD, NZD, CHF), time, impact, forecast, previous | **this week only**, **no actual values**, **no India**. Free and unofficial, so fetch it once or twice a day, never in a loop |
| **NSE event calendar** `nseindia.com/api/event-calendar` | 847 upcoming board meetings: symbol, company, purpose, date | needs a cookie from an NSE page first; NSE throttles scripts, so at most once a day |
| **Our India list** `config/calendar/india_2026.yaml` | RBI policy dates, CPI / WPI / IIP / GDP release dates, Budget, GST collections | hand-kept, like the holiday file. I'll fill it from the RBI and MoSPI release calendars and mark each date with its source; you check them, as with the holidays |
| Our own data | holidays, expiries (from Kite's instrument list), algo trade days | — |

**Not available for free:** Sensibull's "actual" values for India data. The page shows expected
and previous; actual values could be typed into the YAML after release if you want them.

Everything fetched is stored in a new `calendar_events` table (migration 0004). So the page loads
instantly, works offline, and keeps past weeks, which the free feed drops. It is fetched by:
- `algo data calendar` (manual), and
- automatically when any algo starts, at most once a day, never fatal.

## 3. Algos use it

A shared helper, `ctx.events(start, end, min_impact)`, returns stored events in a time window:

| Algo | Use |
|---|---|
| Weekend condor | **W3**: skip the weekend if a High-impact event falls between Friday 15:30 and Monday 09:15, or if a heavyweight (`results_symbols`: the top 10 NIFTY weights) has results dated Friday–Sunday |
| Intraday condor | log only at first: "event today" (RBI policy, US CPI the night before) on the entry check; decide from the data whether to skip |
| Strangle scalp | log only: an event inside 09:45–10:30 (RBI at 10:00) is exactly when a scalp might pay; the logs will show |

## 4. Build order (all done)

| Step | What |
|---|---|
| C1 | `calendar_events` table, fetchers (Forex Factory, NSE results), India YAML, `algo data calendar` |
| C2 | Calendar page with three tabs, filters, NIFTY 50 highlighting |
| C3 | `ctx.events()` and the weekend condor's W3; log-only checks in the other two algos |

## 5. Advances / declines

**What exists today:**
- `trading_core/market/premarket.py` counts advances, declines and unchanged among the
  **NIFTY 50 stocks**, plus the top gainers and losers. It does this from **one Kite quote call**
  (last price vs. previous close), and stores the result in the `market_breadth` table.
- It was the iron condor's v1 "breadth filter" at 09:08. v2 removed that step, so **nothing calls
  it now**, and the table is empty.
- `algo data constituents` downloads the 50 stocks' daily bars for backtests.
- The 50 symbols are in `config/universes/nifty50.yaml`.

**What doesn't exist:**
- **Live, intraday breadth.** The count was only ever taken once, before the open.
- **Market-wide breadth** (all NSE stocks). NSE's website blocks scripts, so it isn't fetched.

**Proposal (for buying strategies such as the scalp):**

| Breadth | How | Update rate |
|---|---|---|
| NIFTY 50 A/D, **live** | subscribe the 50 stocks on the WebSocket the algo already has (Kite allows 3,000 instruments per connection). Each tick carries the previous close, so "up vs. down" updates on every tick. Needs the 50 NSE stock tokens added to the daily instrument snapshot | every tick (~1 s) |
| NIFTY 500 A/D | one Kite quote call for up to 500 symbols | every 60 s (well inside Kite's limits) |
| Weighted breadth *(later)* | NIFTY 50 A/D weighted by index weight, which says more about NIFTY's direction than a plain count. Needs the weights (NSE publishes them monthly) | with the live A/D |

Stored every few seconds alongside the ticks, so replays and backtests see it. Shown in the
strategy pages' Today tab and available to entry checks, e.g. for the scalp: "buy the CE side
only when ≥ 35 of 50 are up". Built as one shared piece that any algo can switch on.
