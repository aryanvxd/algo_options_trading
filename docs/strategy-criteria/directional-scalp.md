# NIFTY directional buying scalp (Renko)

- **Status:** v0.1 implemented in `src/algos/directional_scalp/` (config version 0.1.0, 30 Oct 2026),
  with the recommendations in section 13. Numbers marked *(proposed)* are starting points, to be
  tuned from the backtest (section 10, **not built yet**) and the paper days' logs.
- **Mode:** paper, **2 lots**, every trading day, up to 5 trades between 09:45 and 11:00. First paper
  day: Mon 2 Nov 2026 (on Mondays, start it at ~09:50: see section 3).
- **Replaces:** the "CE/PE premium-skew directional buy" idea note
  ([premium-skew-option-buying.md](premium-skew-option-buying.md)). Its premium-skew check is part
  of this strategy (B2).

## 1. Idea

Buy **one option, CE or PE**, in the direction the market is leaning, and only when price is
actually moving that way.

1. **Bias: which side, if any.** A score from three things:
   - **market breadth:** how many NIFTY 50 stocks are up vs. down, weighted by their index weight;
   - **CE/PE premium skew:** the same check that keeps the iron condor out on lopsided days;
   - **NIFTY futures vs. VWAP.**

   If the score isn't clearly one way, there is no trade.
2. **Trigger: when.** A **Renko chart** of NIFTY prints two bricks in a row in the bias direction.
3. **Exits: how it ends.** The Renko chart also sets the stop and the trailing stop (a reversal
   brick ends the trade). A hard premium stop is the backstop, and a time stop deals with theta.

**Why Renko:**
- A brick only forms after a full move of a set size, so the small up-down noise of a 1-minute
  chart doesn't show.
- "Two bricks one way" is a clean trigger.
- "A reversal brick" is a clean stop, and it trails by itself as new bricks form.

**How it differs from the strangle scalp:**
- The strangle scalp needs no direction but pays for two options.
- This one picks a side, pays for one option, and needs the side to be right.

## 2. Instrument and trade days

| Item | Rule |
|---|---|
| Underlying | NIFTY 50 weekly options, nearest expiry |
| What is bought | **ATM** CE (bullish) or PE (bearish), delta about 0.50 *(proposed)* |
| Lots | **2** (130 at lot size 65), from the first paper day. Lot size is read from Kite's instrument list each day |
| Trade days | **every trading day** *(proposed)*, with results reported per DTE so weak days can be dropped later, as with the scalp |
| Trades per day | **at most 5** |
| Entry window | **09:45–11:00** (09:45 is the earliest any strategy can trade, because of the laptop) |

**Why ATM and not OTM:**
- **It moves more with NIFTY.** At delta 0.5, a 20-point NIFTY move is worth about 10 premium
  points. A ₹20 OTM option (delta about 0.15) gains only about 3.
- **It loses less to IV.** Most of its price is moneyness, not IV, so a fall in IV hurts less.
  That matters before 11:00, when IV usually drifts down.
- **Its spread is the tightest in the chain.**

## 3. Schedule (IST)

| Time | Step |
|---|---|
| ~09:35 | Laptop on, `algo login`, `algo run --algo directional_scalp`. **Mondays: start it at ~09:50**, once the weekend condor's buy-back window has closed. Kite allows 3 WebSocket connections per API key, and on Monday mornings the iron condor, the strangle scalp and the weekend condor already use them |
| on start | Load instruments; subscribe NIFTY, NIFTY futures, India VIX, the ATM ±600 strikes and the **50 NIFTY stocks**. Fetch Kite 1-minute candles from 09:15 to now, to warm up the Renko chart, the futures VWAP and the opening range |
| **09:45** | Start scoring the bias (every 5 s) and watching for Renko triggers |
| **11:00** | Last entry |
| **11:30** | Square off anything still open *(proposed: an 11:00 entry still gets a few bricks of room)* |
| ~11:35 | Daily report |

## 4. Bias: which side to trade

Each part gives a value from −1 (bearish) to +1 (bullish), recomputed every 5 seconds:

**score = 0.4 × B1 + 0.3 × B2 + 0.3 × B3**

| # | Part | Measured as | +1 at | −1 at | Weight |
|---|---|---|---|---|---|
| B1 | **Breadth (advances/declines)** | NIFTY 50 *weighted* A/D: index weight of the stocks up on the day, minus the weight of those down, in % (−100 to +100) | **≥ +40** | **≤ −40** | 0.4 |
| B2 | **CE/PE premium skew** | `PE ÷ CE` at the same distance from spot (section 4.2) | CE richer: ratio **≤ 0.95** | PE richer: ratio **≥ 1.45** | 0.3 |
| B3 | **Futures vs. VWAP** | NIFTY futures price minus its VWAP since 09:15 | **≥ +15 pts** | **≤ −15 pts** | 0.3 |

Between the two limits each part is linear: for example, a weighted A/D of +20 gives B1 = +0.5.
All thresholds are *(proposed)*.

**Direction rule:**

| Score | Allowed |
|---|---|
| **≥ +0.5**, held for 2 minutes | CE only |
| **≤ −0.5**, held for 2 minutes | PE only |
| in between | no new trade (an open trade keeps its own exits) |

- **Breadth must not disagree.** For a CE, B1 must be above 0; for a PE, below 0. If breadth says
  the opposite of the trade, no other signal overrides it.
- **The 2-minute hold** stops the bias from flipping on one heavyweight's tick.

### 4.1 Breadth (B1): what it is and where it comes from

- **NIFTY 50 weighted A/D, live.** The 50 stocks' ticks come over the WebSocket the algo already
  has (Kite allows 3,000 instruments per connection), and each tick carries the previous close.
  - **Why weighted:** HDFCBANK, RELIANCE and ICICIBANK together are about a quarter of the
    index. A plain "32 up, 18 down" can hide those three falling.
  - **Weights:** NSE publishes the index weights each month. They are kept in
    `config/universes/nifty50_weights.yaml` and updated monthly, like the holiday list.
- **Plain count:** 32 of 50 up. Logged and shown, but not scored.
- **NIFTY 500 A/D:** one Kite quote call every 60 s. This is the closest thing we can get to the
  whole market's breadth. Logged first; it can join B1 later if it adds anything.
- **NSE's own all-market advances/declines (~2,500 stocks):** not used. NSE's website blocks
  scripted polling, and NIFTY 500 covers most of the market's value.

The plan for this breadth data is in [market-calendar.md](../design/market-calendar.md),
section 5. This strategy is its first user.

### 4.2 Premium skew (B2)

- **Same measure as the iron condor's E4** (`iron_condor/filters.premium_balance`): compare a CE
  and a PE at the same distance from spot, using interpolated mid prices.
  - Distance: 300 pts *(proposed; the condor uses 250–300 by DTE)*.
  - So the two algos' logs can be compared directly.
- **Puts are normally a little dearer,** even on a flat day: buyers pay for crash protection. So
  the neutral band is not centred on 1.0. All three condor logs so far had the PE richer:

| Day | PE ÷ CE | NIFTY afterwards |
|---|---|---|
| 27 Oct | 1.07 | flat (22,739 → 22,769 by 11:57) |
| 29 Oct | 1.24 | up 88 pts by 10:26 |
| 30 Oct | **1.64** (condor skipped) | down 32 pts in the next 12 min |

- **Starting reading:**
  - A clearly PE-rich chain (**≥ 1.45**) leans bearish.
  - A CE-rich one (**≤ 0.95**) leans bullish.
  - Anything in between scores near 0.
- **This is the least proven of the three parts.** Three days prove nothing. 29 Oct had puts
  richer and the market went up, which is exactly the normal skew case. So B2 has a weight of
  only 0.3 and can't trade on its own: breadth must agree. Its weight and band get re-set from
  the paper logs (section 10).
- **Trend of the skew:** whether puts are getting dearer or cheaper over the last 15 minutes is
  also logged. It may be a better signal than the level.

## 5. Trigger: the Renko chart

| Item | Rule *(proposed)* |
|---|---|
| Price used | NIFTY **spot** (clean, no futures basis). Futures are used only for the VWAP |
| Brick source | **1-minute closes**: your "1-minute chart". It can be backtested on years of Kite's 1-minute index history (section 10) |
| Brick size | **10 NIFTY pts** fixed in v1. The backtest also tries 8 / 12 and an ATR-based size (14-period ATR of 1-minute bars) |
| Reversal | classic: a brick in the other direction needs a move of **2 bricks** (20 pts) against the last brick |
| Warm-up | bricks are built from 09:15 using Kite's 1-minute candles, so at 09:45 the chart already has a trend |

**Entry trigger:**
- **T1 · Two bricks in the bias direction in a row:** green-green for a CE, red-red for a PE.
  - Count from the last opposite brick, or from 09:45 if no opposite brick has formed.
  - A long run of bricks before 09:45 doesn't count. Buying at the end of an old move is the
    classic late entry.
- **T2 · Not over-extended:** NIFTY is within 2 bricks of the futures VWAP.
  - A move far from VWAP tends to snap back before it continues.
  - Log only at first; enforced if the data shows late entries losing.
- **T3 · Fresh trigger after a stop:** after a stop-out, re-entry in the same direction needs a
  new two-brick run. A brick that started before the stop doesn't count.

Then the option checks, at the moment of entry:

| # | Check | Rule *(proposed)* |
|---|---|---|
| O1 | Strike | ATM CE or PE, nearest expiry. If the ATM spread fails, one strike nearer the money (ITM) |
| O2 | Spread | bid-ask ≤ **0.50 pts** |
| O3 | Fresh quote | last tick ≤ **2 s** old |
| O4 | Premium band | ₹40–250 (keeps out an almost-worthless 0 DTE option late in the window, and a far too dear one) |

## 6. Position construction

- **One leg:** buy 2 lots of the chosen option with a limit order at the ask + 0.10. If it isn't
  filled in 3 s, cancel; the trigger is gone.
- **The 2 lots are managed separately** *(proposed — this is what 2 lots are best for)*:
  - **Lot A** books the first target.
  - **Lot B** runs with the trailing stop.

  The trade's day-to-day result is the sum of the two.
- **No pyramiding:** no adding to a trade that's going well.

## 7. Exit criteria (checked every second, first match wins)

Stops are on NIFTY's Renko bricks (the trade's thesis) and on the option's premium (the backstop).
P&L is marked at bid.

| Priority | Rule | Trigger *(proposed)* | At 2 lots, delta 0.5 |
|---|---|---|---|
| 1 | Kill switch | `algo kill`, or 5 errors in a row | — |
| 2 | Stale feed | no tick for **5 s** | — |
| 3 | **Hard premium stop** | the option falls **8 pts** below the entry price, checked on every tick (catches a spike or an IV drop before a brick closes) | **−₹1,040** |
| 4 | **Renko reversal** | a brick forms against the trade (2 bricks back from the last brick, ≈ 20 NIFTY pts) | about −₹1,300 at the start, then it trails |
| 5 | **Target, lot A** | option up **+12 pts** → sell lot A. Lot B's stop moves to entry + charges | lot A +₹780 |
| 6 | **Trailing, lot B** | the Renko reversal (4) trails one brick per new brick in favour. Lot B also exits at **+30 pts** if reached (about a 60-pt NIFTY move) | lot B up to +₹1,950 |
| 7 | **Time stop** | no new brick in favour for **5 min** (theta wins when the move stalls) | — |
| 8 | Bias flip | the score crosses **0.3 the other way** (e.g. below −0.3 while in a CE) | — |
| 9 | Time exit | **11:30** | — |

The hard stop sits a little inside the Renko stop on purpose:
- At delta 0.5, 8 premium points is about 16 NIFTY points. So on a normal move the hard stop is
  hit first.
- It caps the loss at a known amount while the reversal brick is still forming.
- If backtests show it cutting good trades early, widen it to 10.

### Charges (from `config/platform.yaml` rates)

Lot A and lot B are separate orders (2 buys, 2 sells), so each can exit on its own. The strangle
uses 4 orders too, and the condor uses 8.

| Lots | Charges per round trip (₹120 option) |
|---|---|
| 1 | ≈ ₹65 (one order each way) |
| **2** | **≈ ₹135** with lot A and lot B as separate orders (≈ ₹85 if both went as one order) |
| 3 | ≈ ₹155 (1 + 2 lots) |

**Rough break-even** *(to be replaced by the backtest)*, at 2 lots with 0.1 pt slippage per fill:
- **Typical loss:** 8 pts × 130 + ~₹135 charges + ~₹26 slippage ≈ **₹1,200**.
- **Typical win:** lot A +12 pts, lot B about +10 on average, so 22 × 65 − ~₹160 ≈ **₹1,270**.
- **Break-even win rate: about 49%.** Directional buying usually wins less often than that and
  makes it up on the runners. That is why lot B's trailing stop matters most.

## 8. Day limits and capital

| # | Limit | Rule *(proposed)* |
|---|---|---|
| D1 | Trades | **≤ 5** per day (each entry counts, including one that was stopped) |
| D2 | Cool-down | **2 min** after an exit before a new entry |
| D3 | Losing streak | stop for the day after **3** losses in a row |
| D4 | Day loss | stop at **−₹3,000** net (about 2½ full stops) |
| D5 | Profit lock | once the day is **+₹2,500**, stop if it falls back to **+₹1,500** |
| D6 | Opposite trades | at most **2 direction changes** a day. A day flipping CE → PE → CE is a chop day; stop |

| Item | Rule *(proposed — confirm)* |
|---|---|
| Capital bucket | **₹35,000**. 2 lots of the dearest ATM (about ₹180 on 3–4 DTE) is ≈ ₹23,400, plus a buffer. No margin: buying pays in full. On expiry day 2 lots of ATM is only about ₹8,000–12,000 |
| Worst case per trade | the full premium, only if the feed and both stops fail. A gap can't lose more than that |
| Max loss per week | **₹6,000** → pause and review |
| Max loss per month | **₹12,000** → pause, re-test on replays |
| Scaling | +1 lot only after 20+ paper trade days with positive net P&L, one change at a time |

## 9. What is logged

- **Every 5 s:**
  - the bias score and its three parts with their raw values (weighted A/D, plain count,
    PE ÷ CE ratio, futures − VWAP);
  - the NIFTY 500 A/D;
  - India VIX.
- **Every brick:** its time, direction and price, and the bias at that moment.
- **Every trigger, taken or not:** which check blocked it. The blocked ones show whether a filter
  earns its place.
- **Fills and exits:** lot A and lot B separately, exit reason, hold time, peak and trough, gross
  / charges / net.
- **Day:** trades, wins, direction changes, why it stopped, and that day's events from the market
  calendar (e.g. RBI at 10:00).

## 10. Testing before paper money matters

| Part | Can it be backtested on history? |
|---|---|
| Renko trigger, VWAP, time and brick exits | **Yes, years of it.** Kite has 1-minute NIFTY index candles (`algo data index` already downloads them) |
| Breadth (B1) | **Yes.** Needs 1-minute candles of the 50 stocks from Kite. Today only daily bars are downloaded, so a minute downloader is a small addition |
| Futures VWAP (B3) | **Partly.** Kite doesn't keep intraday data for expired futures. Use our own recorded futures ticks, or test with the index (no volume) as a proxy |
| Premium skew (B2) | **No.** Expired options have no history on Kite. Only our recorded snapshots (from 27 Oct) have it. So B2 is judged on paper days |
| Option P&L | approximated from NIFTY's move with Black-76 at that day's IV (from the bhavcopy chains). Good enough to rank brick sizes and stops, not to promise a rupee figure |

**Build order** *(proposed)*:
1. **Platform pieces** (section 11).
2. **Backtest** of the trigger, exits and breadth on 1–2 years of 1-minute data:
   - brick sizes 8 / 10 / 12 and ATR-based;
   - with and without each part of the score;
   - results by DTE and by month.

   If it loses across the board, stop there. That costs nothing.
3. **Paper** at 2 lots every day, with B2 in the score. The report also shows "would this trade
   have been taken without B2", so after 4 weeks we know whether the skew helps or hurts.
4. Live only after 18–24 paper days (common rule 4), using only the DTEs that paid.

## 11. Platform pieces (built 30 Oct 2026 unless marked)

| Piece | Status |
|---|---|
| NSE stock tokens | done: the daily instrument snapshot also keeps the stocks of `instruments.equity_universes` (`nifty50`). If a day's snapshot was taken before that, the algo takes it again on start |
| NIFTY 50 list | updated: **BSE replaced WIPRO** from 30 Sep 2026 (`config/universes/nifty50.yaml`) |
| Index weights | `config/universes/nifty50_weights.yaml`: NSE's constituent weights as on 19 Aug 2026, with BSE's weight estimated (~1.11%). **Update monthly** |
| Live breadth (NIFTY 50) | done: `trading_core/market/breadth.py`. The previous closes come from one Kite quote call on start and are stored for the day (`prev_closes` table, migration 0005), so replays use them too |
| NIFTY 500 breadth | **not built** (log-only in the plan). Needs the 500-stock list |
| Renko, 1-minute bars, VWAP | done: `trading_core/marketdata/renko.py`. Warm-up from Kite's 1-minute candles since 09:15, stored for the day (`intraday_bars` table) so a replay warms up the same way |
| Futures and stock volume in the Parquet store | done: `index_ticks` has a `volume` column now |
| **Partial exit** | done: `BaseAlgo.manage_open()` and `close_legs()`. Lot A and lot B are two legs on the same option, and the trade's cost per unit still reads as one option's price |
| Exits | done: hard premium stop, Renko reversal, lot B target, breakeven after lot A, time stop, bias flip (`algos/directional_scalp/exits.py`) |
| Strategy page | done: a Today panel with the score, its three parts, breadth, VWAP and the Renko bricks, plus the usual tabs. The Performance tab shows the ~49% break-even line |
| Backtest on Kite 1-minute history | **not built**: needs a minute downloader for the 50 stocks and an option-price model (section 10) |
| Replay tests | synthetic days (rising, falling with rich puts, flat, reversal, no previous closes): `tests/algos/directional_scalp/` |

## 12. More things worth adding (my suggestions)

Already in the plan above:
- **VWAP as a third bias part (B3).** It is the level intraday traders watch, and it catches the
  common "breadth positive but the index is fading" morning.
- **Splitting the 2 lots (lot A target, lot B trailing).** It makes the 2 lots do more than
  double the rupees: lot A pays for most stop-outs, and lot B is what catches a trend day.
- **Not over-extended from VWAP (T2) and the direction-change limit (D6).** Late entries and chop
  days are the two classic ways a directional scalper loses.

Worth testing later, each logged first and switched on only if the data says so:
1. **India VIX direction.** Buy only when VIX is flat or rising over the last 15 minutes. A
   falling VIX deflates every option, so the trade has to outrun it.
2. **Heavyweight check.** HDFCBANK, RELIANCE and ICICIBANK all moving with the trade. They often
   lead the index by a minute.
3. **Opening-range break.** The trade direction matches a break of the 09:15–09:45 high or low.
   The scalp already computes this range.
4. **Event window.** No entry 10 minutes either side of a High-impact Indian event, e.g. RBI
   policy at 10:00, which sits inside this window. The market calendar already has these dates.
5. **ATM OI build-up.** Call writers adding OI at the strike just above spot is resistance for a
   CE buyer, and put writers below spot is support for a PE buyer.
6. **BANKNIFTY agreeing.** It is cheap to add, since its quotes are one subscription. Banks are
   about a third of NIFTY.
7. **Expiry-day (Tuesday) caution.** ATM options are cheap but lose value very fast. If the
   per-DTE report shows 0 DTE losing, trade the next expiry on Tuesdays instead of skipping the
   day.

## 13. Open questions (my recommendation in bold)

1. Renko on NIFTY spot, or on the option's own price? **Spot**: options' Renko bricks are mostly
   spread and IV noise.
2. Bricks from 1-minute closes, or from every tick? **1-minute closes for the trigger**, so it can
   be backtested and matches your 1-minute chart, **with ticks for the hard stop**.
3. Fixed 10-point bricks, or ATR-based? **Fixed 10 in v1**; the backtest decides.
4. Every trading day, or some days only? **Every day**, reported by DTE, like the scalp before its
   Mon/Tue decision.
5. Split the 2 lots (lot A / lot B), or exit both together? **Split.**
6. Square-off at 11:30, or exactly at 11:00? **11:30**: a trade entered at 10:58 needs room. New
   entries still stop at 11:00.
7. Which way does the skew vote? **The richer side is the direction** (PE richer = bearish), with
   the band shifted for the normal put skew. Kept at 0.3 weight until the paper logs confirm it.
