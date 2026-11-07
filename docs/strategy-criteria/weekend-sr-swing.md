# Weekend support/resistance swing (stocks)

- **Status:** 7 Nov 2026. **A review tool, not an automated strategy** (your decision). The
  backtests found no edge: neither the zones (9a) nor the setups (9b) beat random levels or random
  entries. So the zones, charts and sector overview are kept for your own decisions, and **nothing
  here creates, changes or deletes alerts, ATOs, GTTs or orders**. It can read and show them. Sections 5–7 are parked: they record the plan that
  was tested. The build order is section 10.
- **What it is:** a weekend look at many stocks. Sector indices first, for an industry overview,
  then the stocks in them, with their support and resistance zones and charts.
- **What it isn't:** a signal. "Near support" says where price is against past turning points, not
  that it will bounce (9a). The tool shows information, not recommendations.
- **Built as its own section** of the platform, `src/swing/`. The intraday `BaseAlgo` runner doesn't
  fit a scan that runs once a week over many stocks. It needs no database tables.

## 1. Idea

1. **Every weekend, scan the sector indices, then their stocks,** on daily and weekly candles.
2. **Mark support and resistance zones** where price has turned several times. Zones are bands,
   not single lines.
3. **Show where each sector and stock sits** against those zones, with charts, for your own
   decisions.

The original idea went further: set up buys near support and hand them to Kite as alerts and
ATOs. The backtests found no edge in that (9a, 9b), so it is parked (sections 5–7).

### The structure, from POWERGRID (two years of daily data, Nov 2024 – Oct 2026)

| Zone (₹) | Role | Pivot touches | Last touched |
|---|---|---|---|
| 242–247 | **support**: held three times in Oct 2026 (where you bought) | 3 lows | 23 Oct 2026 |
| 250–256 | support | 4 lows | Feb 2026 |
| 259–265 | support, price is here now (₹263) | 5 lows | Sep 2026 |
| 268–276 | **resistance**: capped the Sep 2026 rallies | 7 highs | 24 Sep 2026 |
| 278–293 | **flipped**: support through 2025, resistance in 2026 | 26 (16 lows, 10 highs) | Jul 2026 |
| 294–301 | resistance | 6 highs | Aug 2026 |

How these were found: daily swing pivots (a low or high that is the lowest or highest of the 5
days either side), grouped when they are within about one daily ATR (~₹6) of each other. This was
a first pass; section 4 adds what makes a zone strong.

**Your actual trade:** your holdings (read from Kite on 1 Nov 2026) show **25 POWERGRID at ₹249.60**,
now ₹265.
- That is the top of the 242–247 zone and the bottom of 250–256.
- So the zone engine should merge those two into **one support area, ₹242–256**: two zones about one
  ATR apart that held the same decline.
- Z2's grouping rule gets checked against this.

Read as a setup:
- **Entry:** in the 242–247 zone.
- **Stop:** under 240.
- **Targets:** 268 first, then 278.
- **Reward:risk** from 247: about 21 : 7, so 3 : 1 to the first target.
- **What you actually planned:** no stop; add lower if it fell. So in this plan's terms it is a
  **target-only** trade (section 5).

## 2. Universe and data

Two levels: sector indices, then the stocks in them.

| Item | Plan |
|---|---|
| Sector indices | **all** of NSE's sectoral and thematic indices that publish a constituent file (your answer): **35**, listed in `src/swing/config.yaml`. 17 sectoral (Auto, Bank, Financial Services, FMCG, Healthcare, IT, Media, Metal, Pharma, Private Bank, PSU Bank, Realty, Consumer Durables, Oil & Gas, Chemicals, …) and 18 thematic (Energy, Infrastructure, PSE, CPSE, Commodities, Consumption, Defence, MNC, …). POWERGRID is in Energy, Infrastructure, PSE, CPSE and Services Sector |
| Left out | **no constituent file** on niftyindices.com: EV, Railways PSU, Internet, Non-cyclical Consumer, New Consumption, Infra Logistics. **Stale file**: Housing (it still lists HDFC, merged in 2023); Core Housing covers the theme |
| Stocks | the **members of those indices** (EQ series): **384 stocks**, all found on Kite. A stock in several indices is scanned once and carries every index as a tag. Written to `config/universes/swing.yaml` by `algo swing universe` |
| Sector of a stock | **NSE's industry** from the same files (20 industries, e.g. Power, Capital Goods, Financial Services). Used for the 3-per-sector limit, so a stock in five theme indices still counts once |
| Liquidity filter | **liquid stocks only** (your answer): 20-day average traded value ≥ ₹25 crore (S5). Applied before the zone scan, so illiquid members are dropped early |
| Daily candles | Kite historical, `day` interval, 2000 days per request, so about 8 years. Stored as `equity_daily`, key `swing` (stocks) and `swing_indices` (indices, volume 0), by `algo swing data` |
| Weekly candles | built from the daily ones (Mon–Fri), dated by the week's last trading day |
| Minute candles | not needed for the zones. Optional for volume-at-price (section 4) |
| Corporate actions | Kite's daily candles are adjusted for splits and bonuses. Dividends are not, which is fine for zones |
| Refresh | the scan downloads the past week's candles first: `algo swing data --from <last Monday>`. One request per instrument: about 420 requests, a few minutes |
| Index changes | NSE rebalances in March and September. Re-run `algo swing universe` then |
| Index history | Kite returns **no daily candles for NIFTYCONGLOMERATE** (a new index), and NIFTY CHEMICALS and NIFTY WAVES start in Nov 2025 (232 days, under the 250 needed for zones). Z10 can't use them yet |

## 3. Schedule

| When | What |
|---|---|
| **Saturday or Sunday**, any time | `algo swing scan`: update candles (needs that day's `algo login`) → sector zones → stock zones → write the overview and charts |
| Weekend, you | read the overview. Any decision, and any order, is yours, made in Kite as usual |
| During the week | nothing runs |

**Laptop constraint:** the scan needs no market hours, so the 09:30 rule doesn't apply. It uses
Kite's historical candles, not a WebSocket connection, so it doesn't touch the 3-connection limit.
Run it outside market hours anyway: it uses the same API key as the algos.

## 4. Zones: how they are found and scored

Built in W2 (`src/swing/zones.py`, `algo swing zones <SYMBOL>`). Every number is in
`src/swing/config.yaml` under `zones:`. ATR = the simple 14-day average of the daily true range.
Zones come from the **last two years** of daily candles; a stock with under 250 days of candles
gets none (recent listings).

| Step | Rule (as built, 1 Nov 2026) |
|---|---|
| Z1 Pivots | swing lows and highs on **daily** (5 bars either side) and **weekly** (3 bars either side) candles. **Daily and weekly pivots count equally** (your answer). A weekly pivot at the same price in the same week as a daily one is the same turn, kept once. Two days at the same low are one turn |
| Z2 Grouping | pivot prices sorted; a **gap of 0.5 × ATR** or more between neighbours starts a new zone, and a zone wider than **2.5 × ATR** is split at its widest gap. Zone = the pivots' price range, at least 0.5 × ATR wide. A zone needs **2 pivots or 2 touches**. *Why not "within 1 ATR":* POWERGRID's pivots are mostly under ₹3 apart, so chaining within one ATR (₹6) joins everything from 242 to 303 into one zone |
| Z3 Touches | a **visit**: a bar reaches into the zone from one side; it ends at the first close outside. Out the **same side** = a test that held (a touch), however long price sat inside. Out the other side = it went through. The next touch from a side counts once price has been **1 ATR away** on that side, so pokes at the edge aren't counted again and again. *Why not "back outside within 3 days":* in Oct 2026 POWERGRID sat inside 242–252 for three weeks before leaving upward, and that is the hold you bought |
| Z4 Role reversal | **flipped** = held as support, broke down (Z9), then held as resistance; or the reverse upward |
| Z5 Departure | the best move away over the 5 days after the visit ends, in ATRs. Averaged over the zone's touches |
| Z6 Freshness | touches in the **last 60 sessions**: 0–1 = full marks, 4 or more = none. Each test uses up some of the waiting orders |
| Z7 Volume at price | each day's volume spread evenly over its range, in bins of 0.1 ATR. The zone's volume per ₹ against the average over the two years: 1.0 = average. Indices have no volume, so it's skipped for them |
| Z8 Confluence | within 0.25 ATR of the zone: the 200-day average; a round number (half the price's leading power of ten: ₹50 steps at ₹265, ₹500 at ₹1,500); the 52-week low (zones below the price) or high (zones above) |
| Z9 Broken zones | **2 daily closes running** more than **1 ATR** beyond the zone, on the side opposite the last one, break it |
| Z10 Sector context | the stock's sector index is itself near a support zone (S1's rule applied to the index): extra score. Near resistance: less. A stock in several indices uses the best-placed one. *Built in W4, with the setups* |

**Strength score** 0–100 = the weighted mean of six parts, each 0–1:

| Part | Full marks at |
|---|---|
| Touches (Z3) | 4 touches |
| Role reversal (Z4) | flipped |
| Departure (Z5) | 3 ATR average |
| Freshness (Z6) | 0–1 recent touches |
| Volume (Z7) | 2 × the average |
| Confluence (Z8) | all 3 |

The weights start equal and get set by the backtest (section 9). Z10 is added in W4.

The same engine runs on the **sector indices** first. Their zones feed Z10 and the sector
overview page (section 8).

### POWERGRID, first check (candles to 30 Oct 2026; close ₹265, ATR ₹6.16)

| Zone (₹) | Engine says | Score | Your reading (section 1) |
|---|---|---|---|
| 289.20–296.65 | flipped resistance, 11 holds from above, 13 from below | 74 | 294–301 resistance |
| 278.40–287.05 | **flipped**: 18 holds as support through 2025–Jul 2026, broke 7 Aug 2026, now resistance | 81 | 278–293 flipped |
| 268.50–275.95 | resistance just above: 9 holds from below, last 18 Sep 2026; 5 tests in 60 sessions | 50 | 268–276 resistance: **the target** |
| 258.95–264.65 | support just below, but tested 4 times in 60 sessions and broke on 8 Oct | 34 | 259–265 support |
| 254.26–257.34 | one pivot (255.80, Dec 2025), 4 holds | 46 | part of 250–256 |
| **242.30–252.20** | **support**: 6 lows, 6 holds (Feb 2025, Jan–Feb 2026, 23 Oct 2026), departure 3.2 ATR; round ₹250 and the 52-week low inside | 63 | 242–247 + 250–256: **your ₹249.60 entry is inside it** |

- Z2's gap rule merged 242–247 with 250–252 into one zone, as hoped: the gap from 247.30 to
  250.00 is under half an ATR.
- 255.80 stayed separate: its gap from 252.20 is ₹3.60, just over half an ATR (₹3.08).
- **You confirmed these zones on 1 Nov 2026**, from the chart (`algo swing chart POWERGRID`).

## 5. Setups (buy only)

> **Parked, 6 Nov 2026.** The setups showed no edge over random entries (9b), so the tool doesn't
> produce them. This section is the rule set that was tested. `src/swing/setups.py` stays only so
> `algo swing backtest setups` can be re-run or a new idea tested against the same baseline.

A stock makes the weekend list when all of these are true *(proposed)*:

| # | Check | Rule |
|---|---|---|
| S1 | Near support | last close within **2 × ATR** above a support zone's top (or inside it) |
| S2 | Zone strength | score ≥ **60** |
| S3 | Room to the next resistance | reward : risk ≥ **2 : 1**. Reward = next resistance zone's bottom − entry. Risk = entry − stop |
| S4 | Not in free fall | weekly close not below the zone already. The last 3 daily candles not making long-bodied new lows by > 2 ATR (knife-catch filter) |
| S5 | Liquidity | 20-day average traded value ≥ **₹25 crore** |
| S6 | Events | no results date in the coming week (the market calendar has NSE's results dates) |
| S7 | Trend context *(log first)* | weekly 50 EMA rising, or price above the 200-day average. Logged, not required, until the backtest shows whether it helps |

For each setup, the plan. The scan fills in the defaults; you can change every one of them
before approving (section 8):

| Piece | Rule |
|---|---|
| Entry | limit buy at the **zone top**. **Editable** price |
| Amount | **fixed ₹ per entry**, at most **₹15,000** (your answer). Two tiers: **₹10,000–15,000** for stronger setups, **₹5,000–10,000** for less certain ones. The scan suggests the tier from the score *(proposed: score ≥ 75 → ₹15,000; 60–74 → ₹7,500)*; you change it |
| Quantity | amount ÷ entry price, rounded down to whole shares. **Editable**, but the dashboard won't approve an entry over ₹15,000 |
| Order mode | **ATO** (places the buy) or **alert only** (a notification; you place the buy yourself). A toggle per setup |
| Exit mode | **OCO**: stop and target together (one cancels the other). Or **target only**: no stop, for stocks where you may add more lower instead of exiting. A toggle per setup |
| Target | **all** of the position at the bottom of the next resistance zone (your answer). Editable as ₹, % or points from the entry |
| Stop | **zone bottom − 0.5 × ATR** *(proposed)*. Editable as ₹, % or points below the entry. Not used in target-only mode |
| Time | if price hasn't come to the zone in **4 weeks**, drop the setup *(proposed)*. A trade that hasn't moved in 8–12 weeks gets reviewed |
| Book | at most **8 open trades**, at most **3 per sector** (your answer). Sector = the stock's NSE industry (section 2) |
| Adding lower | **only in target-only trades** (your answer). An add is a new setup at the next support zone below, approved like any entry (same inputs, same ₹15,000 cap per entry) |
| Cap per stock | **₹25,000** in total, **only for stocks you mark fundamentally strong** (section 8, Fundamentals). Any other stock: ₹15,000 in total, so in practice no adding |

S3's reward:risk uses the default stop even when you pick target only, so setups are ranked the
same way. A target-only trade shows "no stop" on the trades page.

## 6. Alerts and ATOs on Kite (checked in Zerodha's docs, 1 Nov 2026)

> **Parked, 6 Nov 2026. Not built, and nothing in `src/swing/` writes to Kite.** Kept as the record
> of what Kite offers, in case a tested idea needs it. Building it needs a fresh decision from you.

Kite Connect has an **Alerts API** ([docs](https://kite.trade/docs/connect/v3/alerts/)):
- **Endpoints:** `POST /alerts`, `GET /alerts`, `GET/PUT /alerts/{uuid}`, `DELETE /alerts?uuid=…`,
  `GET /alerts/{uuid}/history`.
- **Two types:** `simple` (a notification) and **`ato`**, *Alert Triggers Order*: when the
  condition is met, Kite places the orders in the alert's basket.
- **Condition:** an instrument attribute such as `LastTradedPrice` with `<=`, `>=`, `<`, `>`, `==`
  against a constant or another instrument.
- **Basket items:** full order params: exchange, symbol, BUY/SELL, product (`CNC`), order type
  (`LIMIT`), quantity, price, validity.
- **Limit:** **500 active alerts** per user.

**How this strategy uses them** (your choices of 1 Nov 2026):

| Alert | When | Type | Condition | Action |
|---|---|---|---|---|
| Approaching | always | simple | LTP ≤ zone top + 1 ATR | notification: have a look |
| **Entry, ATO mode** | toggle = ATO | **ato** | LTP ≤ entry | **LIMIT BUY**, CNC, at your entry price and quantity, **with a GTT exit attached** (below) |
| Entry, alert-only mode | toggle = alert only | simple | LTP ≤ entry | notification; you place the buy yourself |
| Stop warning | OCO mode | simple | LTP ≤ stop | notification |

The **GTT exit** on an ATO entry:

| Exit mode | GTT | Legs |
|---|---|---|
| **OCO** | two-leg GTT | stop: LIMIT SELL below the stop trigger; target: LIMIT SELL at the target. Whichever fires first cancels the other |
| **Target only** | single-leg GTT | target: LIMIT SELL at the target. No stop |

- Stop and target are set in ₹, % or points on the dashboard. They are sent to Kite as prices.
- The GTT exists only once the entry fills, so it never tries to sell shares you don't have.
- **Partly confirmed, 7 Nov 2026:** a real ATO read from Kite does carry `params.gtt` with `target`
  and `stoploss` as plain numbers, with no unit (% or points). Kite's docs still don't list `gtt` as
  a field you can send when creating one. Before W6 is built: you create one ATO with a GTT by hand in Kite, and we **read**
  it (`GET /alerts/{uuid}`, read-only) to see the exact shape. If the API won't accept it on
  create, the fallback is: the weekend sync sees the fill and offers the GTT for your click, and
  the "stop warning" alert covers the days in between.

**Safety, since these become real orders:**
- **Nothing is created on Kite without your click.** The dashboard shows each alert's exact
  condition and basket, and **Create** asks for confirmation.
- Platform-wide, live trading is off in v1. This is the first piece that writes to Kite, so it
  has its own switch: `swing.alerts_enabled: false` until you turn it on.
- **Dry run first:** `algo swing alerts push --dry-run` prints the API calls without sending them.
- Every alert the platform creates is tagged with a name prefix (`SR:`), stored with its Kite
  `uuid`, and reconciled each weekend. Alerts you made by hand in Kite are never touched.
- GTTs: the platform creates them only as the exit of an ATO you approved. It never creates a GTT
  on its own, and never changes or deletes GTTs you made by hand.
- An ATO's order still goes through Kite's normal checks: funds, circuit limits, and the 2026
  order rules in the live-broker checklist.

## 7. Order book and "order flow" from Zerodha (checked 1 Nov 2026)

> **Parked, 6 Nov 2026.** This was a confirmation layer for setups the tool no longer makes. Kept
> as the record of what Kite offers.

What Kite gives:

| Wanted | Available? |
|---|---|
| Tick-by-tick trades (true order flow: every trade with its aggressor side) | **No.** The WebSocket sends snapshots, about **one per second per instrument**, when anything changes ([forum](https://kite.trade/forum/discussion/1291/frequency-of-ticks)). Trades between snapshots are merged |
| Market depth | **5 levels** of bids and asks, each with price, quantity and number of orders: WebSocket **full mode** ([docs](https://kite.trade/docs/connect/v3/websocket/)) |
| 20-level depth | **No public API.** The Kite web app's 20-depth isn't offered to Kite Connect ([forum](https://kite.trade/forum/discussion/4141/unable-to-get-full-market-depth)) |
| Whole-book totals | **Yes:** `total_buy_quantity` and `total_sell_quantity` (all pending orders, not just 5 levels) |
| Per-tick trade info | last traded quantity, day volume, average traded price (the day's VWAP) |
| History of any of this | **No.** Depth exists only live. We have to record it ourselves |

So "order flow" here is **built from the 1-second snapshots**:

| Measure | Built as |
|---|---|
| **Volume delta** (approximate) | each tick's new volume is signed buy or sell by where the last price sits against the previous bid/ask (trade at/above the ask = buyer). Cumulative delta per minute and per day |
| **Book imbalance** | (bid qty − ask qty) ÷ (bid qty + ask qty) over the 5 levels; also total buy vs total sell quantity |
| **Absorption at a zone** | price sitting in the support zone while sell volume (negative delta) keeps coming and the price doesn't fall, and bids refill: someone large is buying |
| **Big resting orders** | a level with unusually large quantity or order count that persists for minutes |

**How it fits a weekend strategy:**
- The entry is an ATO, so an order-flow condition can't gate it: Kite alerts only compare prices.
- Order flow is therefore a **research and confirmation layer**:
  - when a watched stock is within 1 ATR of its zone, record its depth and the measures above
    during market hours;
  - show them on the stock's page;
  - keep them for the backtest of "did absorption at the zone predict a hold?"
- If it proves useful, a v2 could hold back the entry ATO and alert you instead, so you decide
  live.

**Connection budget:** Kite allows 3 WebSocket connections per API key, and the intraday algos
already use them on Mondays. The recorder subscribes **only the stocks near a zone** (usually under
20). It runs either:
- after 11:35, when the scalps are done; or
- **in one shared feed process with the other algos** (a platform change that is worth making
  anyway; noted as its own task).

## 8. Dashboard: a new "Swing" section

Sidebar **Swing**, with these pages:

| Page | Shows |
|---|---|
| **Sectors** | every sector index with its zones: near support, mid-range or near resistance, and distance in ATRs. A small chart per index. Click through to the index's stocks. This is the industry overview |
| **Stock page** (`#/swing/POWERGRID`, indices too) | **candlestick chart**, daily ↔ weekly, with the zones as shaded bands (support green, resistance red, flipped amber, blue when price is inside), pivots marked, volume bars and a volume-at-price histogram on the side. Zone table: range, role, holds each way, pivots, last test, last break, confluence. **No scores**: they add nothing (9a) |
| **Holdings** (`#/swing/holdings`) | every stock you hold (read from Kite, read-only) through the same zone engine: nearest support and resistance, whether any support is left below the price, whether your average sits inside a zone, and how far the price is from your average in ATRs. A held stock's chart shows your average as a line. Information for your decisions, not recommendations |
| **Index page** (`#/swing/NIFTY%20ENERGY`) | the stock page for an index, with the **list of its liquid stocks** under the chart: price, position, the zone it is in, nearest support and resistance. Prices refresh from Kite every 30 seconds while the page is open and you are logged in (last close otherwise), and the position is worked out against that price. Filter by position; click a stock for its chart |
| **Orders** (`#/swing/orders`) | your **GTTs, ATOs and alerts** on Kite, read live when the page opens, with a filter between the three. **Read-only**: there are no buttons to change them; you manage them in Kite. Nothing is saved to disk |
| **Orders on charts** | active GTT and ATO/alert levels drawn on that stock's chart, with a switch to turn them off |
| **Timeline** (on the stock page) | what happened and what's coming for that stock: results dates from the market calendar, zone holds and breaks from the last year, and your active GTTs, ATOs and alerts. Results also show on the chart as vertical lines, with a switch. First version: filings, reports and other events come later |
| **Fundamentals** | the main numbers for a stock you are looking at, on one page. What it shows and where the data comes from are open (section 11): Kite has no fundamentals |
| **Backtests** | the section 9 results, so the "no edge" finding stays next to the charts |

**Pages are organised as sub pages, with a breadcrumb at the top of each:**

| Path | Page |
|---|---|
| Swing review | `#/swing` |
| Swing review › NIFTY ENERGY | `#/swing/NIFTY%20ENERGY`: the index and its stocks |
| Swing review › NIFTY ENERGY › POWERGRID | `#/swing/NIFTY%20ENERGY/POWERGRID`: a stock reached from its index |
| Swing review › Holdings › a stock | `#/swing/holdings/<SYMBOL>` |
| Swing review › Orders › a stock | `#/swing/orders/<SYMBOL>` |
| Swing review › a stock | `#/swing/<SYMBOL>`: from the stocks list |

Every page carries the same note: zones show past turning points; in the backtests they held no
more often than random levels.

**Charts:**
- Candlesticks use [Lightweight Charts](https://github.com/tradingview/lightweight-charts) 4.2.3
  (TradingView, Apache 2.0), vendored like Chart.js in `static/vendor/lightweight-charts/`, so the
  page works offline. Its licence asks for the attribution logo on the chart, so it stays.
- It has no price bands, so a small drawing plugin in `pages/swing.js` paints the zones and the
  volume at price behind the candles.
- Chart.js stays for the rest.

**How the pages get their data:** the Sectors page reads `data/reports/swing/overview.json`, written
by `algo swing scan`; the Stock page runs the zone engine on one symbol's stored candles when it
opens (`src/dashboard/swing_queries.py`). Neither talks to Kite. Only two things do, and only to
read: an index page's live prices (`ltp`), and the Orders page and chart overlay (`GET /gtt/triggers`,
`GET /alerts`). Both are ordinary REST calls, so they use no WebSocket connection, and both fall back
quietly when you aren't logged in. The Kite library has no alerts call, so `KiteClient.alerts()`
adds one. `algo swing scan` stores each stock's zones in `overview.json` for the live positions.

**Holdings** come from `algo swing holdings`, which `algo swing scan` also runs: it reads
`kite.holdings()`, downloads about three years of candles for those stocks and for any stock with a
GTT, ATO or alert (key `swing_holdings`, so stocks outside the universe work too) and writes
`data/reports/swing/holdings.json`. The
dashboard only reads that file. Your holdings are personal data: they stay in the git-ignored
`data/` folder and on this local dashboard, never in code, docs or tests. Quantity = settled +
T+1 + pledged shares.

## 9. Testing the idea before money

| Test | How |
|---|---|
| **Do the zones hold?** | walk-forward over ~8 years of daily candles for the sector indices and their stocks. Each weekend, find zones using only data up to then. Count how often price reaching a zone turned (+1 ATR away) before breaking it (−1 ATR through), by score bucket. If high scores don't hold more often than low ones, the score is wrong |
| **Do the setups pay?** | simulate section 5 exactly: entry at the zone top if touched within 4 weeks, target, fixed ₹ sizing, max open trades. Run it twice: OCO (with the stop) and target only (no stop), so you can see what dropping the stop costs or saves. Also with and without Z10 (sector context). Report win rate, average R, holding days, drawdown, yearly results. Compare against random entries with the same stops and targets |
| **Your trades** | your POWERGRID buy, and others you've made, become test cases: the scan should find those zones |
| **Paper** | 8–12 weekends of scans with alerts in **simple** (notification) mode only: record what would have filled, before any ATO |

The intraday backtest engine (`trading_core/backtest/`) doesn't fit a daily, months-long strategy.
This one is a separate, much simpler daily-candle simulator in `src/swing/backtest.py`.

### 9a. Zone backtest, first results (W3, 1 Nov 2026)

`algo swing backtest` (settings under `backtest:` in `src/swing/config.yaml`). Results in
`data/backtests/swing_zones_v2/` (`summary.txt`, and `events.csv` with one row per event).

**How it was run:**
- All 384 stocks, weekends from Nov 2020 to Oct 2026, liquid weeks only (≥ ₹25 crore a day).
- Each weekend, zones from the candles up to that day only.
- An **event** is a zone that price reaches during the following week. It **held** if price
  went 1 ATR beyond the zone's near edge before going 1 ATR through the far edge; otherwise it
  **broke**. Unresolved after 60 sessions = open (under 1%).
- **Baseline:** each weekend, random bands with the widths of that stock's zones, at 0–3 ATR from
  the close, judged by the same rules. Tagged as **on a zone** or **clear of every zone**.
- About 110,000 zone events and 119,000 random ones.

**What it found:**

| Comparison | Zones held | Baseline held | Edge |
|---|---|---|---|
| All zones vs all random bands | 59.4% | 59.9% | −0.5 |
| Matched by width, vs bands clear of every zone | 58.5% | 59.0% | −0.5 |
| … support zones | 60.9% | 62.1% | −1.2 |
| … resistance zones | 56.3% | 56.3% | 0.0 |
| … score 80–100 | 62.9% | 63.1% | −0.2 |
| … flipped zones | 59.0% | 59.6% | −0.6 |
| … 10 or more touches | 60.7% | 61.1% | −0.4 |

- **Width explains held%.** A band is reached at its near edge, so "held" needs 1 ATR one way
  and "broke" needs the zone's width plus 1 ATR the other. Wider bands "hold" more, zones or not:
  56% under 0.6 ATR wide, 77% over 2 ATR, for zones and random bands alike.
- **The score ranks zones only through width.** Held% rises from 57% (score under 40) to 66%
  (80+), but high-scoring zones are wider. Matched by width, no score band beats random by more
  than 0.6 points.
- **No part of the score helps** on its own: touches, flips, departure, freshness, volume and
  confluence all sit within ±1.2 points of random once width is matched.
- **Zones cover most of the price range:** 71% of the random bands landed on a real zone.
- **Stable over time:** 2020–2023 and 2024–2026 show the same pattern.
- With about 100,000 events, an edge of 1 point would show clearly. There isn't one.

**What it means:** on daily candles, "price bounces from a support or resistance zone" is, by
itself, no better than a random price level of the same width. The zones are still useful for
**placing** an entry, stop and target, but they are not, on their own, a reason to expect a
bounce. Any edge has to come from something else, and the setup test (W4, "Do the setups pay?",
against random entries) is where that must show. You chose to go straight to the setup test.

### 9b. Setup backtest, first results (W4, 1 Nov 2026)

`algo swing backtest setups` (`src/swing/setups.py`, `src/swing/setup_backtest.py`; settings under
`setups:`). Results in `data/backtests/swing_setups_v1/` (`summary.txt`, `trades.csv`).

**How it was run:**
- All 384 stocks, weekends from Nov 2020 to Oct 2026, liquid weeks only.
- Section 5's rules: S1 near support, S3 reward:risk ≥ 2, S4 knife filter. S2 (score ≥ 60), S7
  (trend) and Z10 (sector index near support) are recorded on every setup and reported both ways.
  S6 (results dates) can't be applied: there's no history of results dates.
- **Entry:** limit buy at the zone top, filled if reached within 4 weeks. **Stop:** zone bottom −
  0.5 ATR. **Target:** the next resistance's bottom.
- Each setup traded in **both exit modes**: OCO (stop + target) and target only (no stop; sold
  after about 6 months if the target never comes). Costs 0.25% per round trip.
- **Baseline:** every setup has a **random twin**: same stock and weekend, entry at a random level
  0–2 ATR below the close, the same stop and target distances.
- 4,365 setups (1,820 with score ≥ 60), and as many twins.

**What it found (S2 setups, score ≥ 60; average return per filled trade, net of costs):**

| | Setups | Random twins |
|---|---|---|
| **OCO:** win rate | 24.4% | 28.2% |
| **OCO:** stopped out | 75.6%, median 2 days | 71.7% |
| **OCO:** average return | −0.17% | 0.00% |
| **Target only:** win rate | 79.3% | 80.5% |
| **Target only:** average return | +1.87% | +2.18% |
| **Book** (8 open, 3 per industry, ₹7,500/₹15,000): OCO, six years | −₹5,781 | −₹1,919 |
| **Book:** target only, six years | +₹73,592 | +₹72,781 |

- **No filter helps:** the trend filter (S7), the sector filter (Z10) and both together stay within
  half a point of their twins. Higher scores do slightly *worse* than lower ones.
- **OCO:** the stop (half an ATR under a zone that is often only half an ATR wide) is about one
  ATR below the entry, and normal daily movement reaches it in two days three times in four.
- **Target only** makes money, but its random twins make just as much: the profit is the market's
  rise over 2020–2026, which any entry gets, not the zones. And without a stop the risk sits in
  trades held for months: 2026's setups average −0.58%, many still open.
- **Fill rate:** setups fill 93% of the time against 70% for twins. Price reaching the zone top
  is close by, so the order fills, and then behaves like any other level.

**What it means:** as specified, the strategy has **no edge over random entries** with the same
geometry, in either exit mode, with or without the trend and sector filters. Together with 9a,
the support/resistance idea doesn't, by itself, choose better entries than chance on these
stocks. **You decided on 6 Nov 2026 to keep it as a review tool** (top of this file, section 10).

## 10. Build order

**Done:**

| Step | What |
|---|---|
| W1 | Sector universe (`algo swing universe`), daily candles (`algo swing data`), weekly bars (`algo swing bars`) |
| W2 | Zone engine, `algo swing zones <SYMBOL>`, `algo swing chart <SYMBOL>`. POWERGRID's zones confirmed by you |
| W3 | Zone backtest, `algo swing backtest`: no edge (9a) |
| W4 | Setup rules and setup backtest, `algo swing backtest setups`: no edge (9b) |
| R1 | `algo swing scan`: refresh candles, then write the **overview** to `data/reports/swing/overview.html`: sectors first, then liquid stocks with their distance to the nearest zones, each linked to its chart in the same folder. "Near" = within 1 ATR. No scores, no database tables. `--no-refresh` skips Kite. A scan rewrites every chart, so one made with `algo swing chart --mark` loses its line |
| R2 | Dashboard: a **Swing** sidebar entry with the Sectors and Stock pages (section 8), candlesticks from vendored Lightweight Charts |
| R3 | Holdings: `algo swing holdings` (also run by the scan) and the Holdings tab; your average as a line on a held stock's chart. Read-only. Quantities checked against Kite's own P&L on 6 Nov 2026 (T+1 and pledged shares not yet seen in real data) |

| R3b | Index pages list their stocks with live positions; **Orders** tab (GTTs, ATOs, alerts, read-only) with their levels on charts. Under the Swing tabs for now; they move to the main sidebar later |
| R3c | Sub pages with a breadcrumb; first version of the stock **timeline**, with results on the chart |

**Next, for the review tool:**

| Step | What |
|---|---|
| R4 | Fundamentals page, once its data source is settled |

**Dropped:** the weekend approval report, trades and alerts pages, Kite alerts/ATOs/GTTs (old W6),
and the order-book recorder (old W7).

## 11. Questions for you

### Answered, 1 Nov 2026

| # | Question | Your answer |
|---|---|---|
| 1 | Universe | **Sector indices first** for their own zones (industry overview), then the **members of those indices**. The sector's picture is shown on its own page **and** adds to a stock's score (Z10) |
| 2 | Capital and risk | **Fixed ₹ per trade**, not risk-based sizing. Quantity = amount ÷ entry, editable |
| 3 | Stop | **You approve each entry** on the dashboard, with inputs to edit the entry price and quantity and a toggle for **ATO** or **alert only**. Every ATO carries a target, as a **GTT** set in %, points or ₹ |
| 3a | OCO or target only | **A toggle per setup:** OCO (stop + target) or target only, because in some stocks you may add more lower instead of exiting at a stop |
| 4 | Exits | **All at the next resistance** |
| 5 | Your POWERGRID trade | **No stop**: the plan was to add lower if it fell. So it is a **target-only** case, and the calibration check is that the scan finds the 242–256 support and the 268–276 resistance as its target |
| 6 | Timeframe | **Daily and weekly equally** |
| 7 | Amount per trade | at most **₹15,000** per entry: ₹10,000–15,000 for stronger setups, ₹5,000–10,000 for less certain ones |
| 8 | Book limits | **8 open, 3 per sector.** Adding lower only in target-only trades. **₹25,000** per stock, only for fundamentally strong stocks |
| 9 | Sector list | **All sectoral and thematic indices**, liquid stocks only |
| 10 | Fundamentals | a dedicated **fundamental analysis page** (R4) |

Rows 2–5, 7 and 8 belong to the parked setups (section 5).

### Answered, 6 Nov 2026

| # | Question | Your answer |
|---|---|---|
| 11 | After the backtests (9a, 9b) | **Keep it as a review tool**: zones, charts, sector overview and holdings review for your own decisions; no automated entries |

### Still open

1. **Fundamentals:** which numbers you want on the page (e.g. sales and profit growth, ROE,
   debt, promoter holding, valuation), and the data source. Kite has none; options are the
   companies' NSE filings or a site like Screener. To settle before R4.
