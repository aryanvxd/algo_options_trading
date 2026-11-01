# Weekend support/resistance swing (stocks)

- **Status:** plan, 1 Nov 2026. W1 (universe and candles) in progress. Numbers marked
  *(proposed)* are starting points. Your answers of 1 Nov 2026 are in section 11 and are written
  into the sections below.
- **Type:** stock buying (cash, CNC delivery), swing to positional: held for days to months.
  Long only.
- **Not** an intraday algo. It runs at the weekend, and Kite **alerts / ATOs** do the watching
  during the week. No market orders. GTTs only as the exit part of an ATO you approved
  (section 6), never on their own.
- **Top-down:** sector indices are scanned first for their own zones, which gives an industry
  overview; then the stocks in those indices.
- **Built as its own section** of the platform, `src/swing/`, with its own dashboard pages. The
  intraday `BaseAlgo` runner doesn't fit a scan that runs once a week over many stocks.

## 1. Idea

1. **Every weekend, scan the sector indices, then their stocks,** on daily and weekly candles.
2. **Mark support and resistance zones** where price has turned several times. Zones are bands,
   not single lines.
3. **Set up buys** for stocks that are near a strong support zone, with a stop below the zone
   and a target at the next resistance zone above.
4. **You approve each setup** on the dashboard, after editing the entry price and quantity if
   you want, and choose ATO or alert only.
5. **Hand the plan to Kite** as alerts and ATOs (alert-triggered orders), so an order goes in only
   if price actually comes to the zone during the week.

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

## 3. Schedule

| When | What |
|---|---|
| **Saturday or Sunday**, any time | `algo swing scan`: update candles → sector zones → stock zones → rank setups → write the weekend report. The dashboard shows it |
| Weekend, you | review the sector overview and the setups on the dashboard. For each setup: edit entry price and quantity, pick **ATO** or **alert only**, pick the exit (**OCO** or **target only**), then approve or drop |
| Weekend or Monday before 09:15, you | **Create alerts**, a button on the dashboard (or `algo swing alerts push`). Needs that day's `algo login` |
| During the week | nothing runs on the laptop. Kite watches the alerts and fires the ATO orders |
| Next weekend | `algo swing sync`: read alert history, orders and holdings from Kite. Open trades are updated, and stops and targets are reviewed |

**Laptop constraint:** the scan needs no market hours, so the 09:30 rule doesn't apply. The
order-book work in section 7 is the only part that needs the market open.

## 4. Zones: how they are found and scored

| Step | Rule *(proposed)* |
|---|---|
| Z1 Pivots | swing lows and highs on **daily** (5 bars either side) and **weekly** (3 bars either side) candles. **Daily and weekly pivots count equally** (your answer) |
| Z2 Grouping | pivots within **1 × ATR(14, daily)** of each other form one zone. Zone = the pivots' price range, at least 0.5 × ATR wide |
| Z3 Touches | how many times price came into the zone and turned. A daily close back outside the zone within 3 days counts |
| Z4 Role reversal | a zone that was support and later resistance, or the reverse, scores extra: POWERGRID's 278–293 band |
| Z5 Departure | how strongly price left the zone (the move in ATRs over the next 5 days). A sharp departure means buyers or sellers were waiting there |
| Z6 Freshness | fewer recent tests = stronger. Each test uses up some of the waiting orders. A zone tested 5 times this month is weaker than one tested twice in a year |
| Z7 Volume at price | daily volume spread across each day's range (a volume profile). A high-volume band inside the zone is extra weight |
| Z8 Confluence | 200-day average in the zone, a round number (₹250, ₹1,000), the 52-week low |
| Z9 Broken zones | a daily close beyond the zone by > 1 ATR, and staying there 2 days, breaks it. It may now act the other way (Z4) |
| Z10 Sector context | the stock's sector index is itself near a support zone (S1's rule applied to the index): extra score. Near resistance: less. A stock in several indices uses the best-placed one |

**Strength score** 0–100 = weighted sum of Z3–Z8 and Z10. The weights start equal and get set by
the backtest (section 9).

The same engine (Z1–Z9) runs on the **sector indices** first. Their zones feed Z10 and the
sector overview page (section 8). Indices have no volume, so Z7 is skipped for them.

## 5. Setups (buy only)

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
- **Not yet confirmed in the API:** Kite's docs show an ATO's order coming back with a
  `params.gtt` object (`target`, `stoploss`), but don't list `gtt` as a field you can send when
  creating one. Before W6 is built: you create one ATO with a GTT by hand in Kite, and we **read**
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
| **Weekend report** | the latest scan: setups ranked by score and reward:risk, with sector, entry / stop / target / quantity. Per setup: **entry price and quantity inputs**, an **ATO / alert-only toggle**, an **OCO / target-only toggle**, stop and target inputs in ₹, % or points; approve or drop. **Create alerts** for the approved ones (confirm dialog showing each exact alert, basket and GTT) |
| **Stock page** (`#/swing/POWERGRID`) | **candlestick chart**, daily ↔ weekly, with the zones as shaded bands (support green, resistance red, flipped amber), pivots marked, volume bars and a volume-at-price histogram on the side. Lines for entry, stop and targets; markers for fills. Zone table: range, score, touches, last test, role history |
| **Trades** | open and closed swing trades: entry, stop, current price, R multiple, days held, P&L, which alert fired. Notes, as on the strategy journals |
| **Alerts** | every alert the platform made, synced from Kite: condition, type, status, last triggered |
| **Order flow** (stock page tab, once recorded) | depth heatmap over time (price × time, colour = resting quantity), cumulative delta, book imbalance, with the zone overlaid |
| **Zone backtest** | the section 9 results: hold rate by score, R by setup, comparison with random entries |
| **Fundamentals** | for the shortlisted stocks only (this weekend's setups and open trades): the main numbers on one page, so you can judge the business before approving. Your **"fundamentally strong"** mark is set here; it allows the ₹25,000 cap (section 5). What it shows and where the data comes from are open (section 11): Kite has no fundamentals |
| **Holdings review** | every stock you hold (read from Kite, read-only) through the same zone engine: nearest support and resistance, whether price is above or below all its supports, and how far it is from your average price in ATRs. Information for your decisions; the platform never sells holdings it didn't buy |

**Charts:**
- Candlesticks need a library. [Lightweight Charts](https://github.com/tradingview/lightweight-charts)
  (TradingView, Apache 2.0) is small and built for this, and is vendored like Chart.js, so the page
  works offline.
- Chart.js stays for the rest.
- A first version of the zone chart is the POWERGRID picture above, with daily closes and shaded
  zones.

## 9. Testing the idea before money

| Test | How |
|---|---|
| **Do the zones hold?** | walk-forward over ~8 years of daily candles for the sector indices and their stocks. Each weekend, find zones using only data up to then. Count how often price reaching a zone turned (+1 ATR away) before breaking it (−1 ATR through), by score bucket. If high scores don't hold more often than low ones, the score is wrong |
| **Do the setups pay?** | simulate section 5 exactly: entry at the zone top if touched within 4 weeks, target, fixed ₹ sizing, max open trades. Run it twice: OCO (with the stop) and target only (no stop), so you can see what dropping the stop costs or saves. Also with and without Z10 (sector context). Report win rate, average R, holding days, drawdown, yearly results. Compare against random entries with the same stops and targets |
| **Your trades** | your POWERGRID buy, and others you've made, become test cases: the scan should find those zones |
| **Paper** | 8–12 weekends of scans with alerts in **simple** (notification) mode only: record what would have filled, before any ATO |

The intraday backtest engine (`trading_core/backtest/`) doesn't fit a daily, months-long strategy.
This one is a separate, much simpler daily-candle simulator in `src/swing/backtest.py`.

## 10. Build order *(proposed)*

| Step | What |
|---|---|
| W1 | Sector universe (`algo swing universe`: indices, members, industries from NSE's files; you check it); daily candles for the indices and stocks (`algo swing data`); weekly bars (`algo swing bars`) |
| W2 | Zone engine (Z1–Z9) + `algo swing zones POWERGRID` printout, and the same for a sector index (`algo swing zones "NIFTY ENERGY"`). Check its zones against your chart reading before going further |
| W3 | Zone backtest (section 9, first test). Tune the score |
| W4 | Setup rules, sizing, weekend report (`algo swing scan`), storage (migration: zones, setups, trades, alerts) |
| W5 | Dashboard: sectors, weekend report with the approval inputs and toggles, stock page with candlesticks and zones, trades |
| W6 | Kite alerts: read and sync first (including reading one hand-made ATO with a GTT, section 6), then **simple** alerts, then ATO + GTT exit behind `alerts_enabled` and the confirm dialog |
| W7 | Order-book recorder for near-zone stocks, order-flow measures, the order-flow tab |
| W8 | Holdings review page: the zone engine over your Kite holdings, refreshed with each weekend scan |
| W9 | Fundamentals page for shortlisted stocks, and the "fundamentally strong" mark. A manual mark can come earlier, with W5, so the ₹25,000 cap works before the data source is settled |

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
| 10 | Fundamentals | a dedicated **fundamental analysis page** for the shortlisted stocks (W9) |

### Still open

1. **Fundamentals:** which numbers you want on the page (e.g. sales and profit growth, ROE,
   debt, promoter holding, valuation), and the data source. Kite has none; options are the
   companies' NSE filings or a site like Screener. To settle before W9.
2. **Score tiers:** is score ≥ 75 → ₹15,000 and 60–74 → ₹7,500 a fair default? Settle with the
   W3 backtest.
3. **Results dates (S6):** the market calendar keeps results dates only for NIFTY 50 stocks
   today. It needs widening to the swing universe; to settle in W4.
