# NIFTY long strangle scalp

- **Status:** v0.1 implemented in `src/algos/strangle_scalp/` (config version 0.1.0, 27 Oct 2026).
  The thresholds marked *(proposed)* are starting points, to be tuned from the paper days' logs.
- **Mode:** paper, 1 lot, every trading day from 28 Oct 2026.
- **Last changed:** 27 Oct 2026.

## 1. Idea

Watch a pair of OTM options — one call above spot, one put below it — with about the same
premium (for example both near ₹20). When NIFTY starts moving fast, one leg gains more than the
other loses, so the **pair's combined value rises**. If that rise keeps going for several
seconds, buy both legs and ride the burst for a few minutes, with a small rupee stop and target.

It is a long-volatility trade: it needs a **fast move** (or rising IV) soon after entry. A
quiet market loses slowly to time decay and falling IV. That makes it the opposite of the iron
condor, which wins on quiet days.

Why a strangle and not a single option: we don't need to guess the direction. The losing leg
caps how much we lose if NIFTY reverses.

## 2. Instrument and trade days

| Item | Rule |
|---|---|
| Underlying | NIFTY 50 weekly options, nearest expiry |
| Lot size | read from Kite's instrument list each day (65 at the time of writing) |
| Trade days | **every trading day** (decided 27 Oct 2026). The pair is picked by premium on expiry day and by delta on other days (section 4.1) |
| Trades per day | **at most 3** |
| Entry window | **09:45–10:30** |

**Why the days to expiry (DTE) matter a lot.** These are rough Black-76 numbers: NIFTY 25,800,
IV 13%, no skew, 10:00 IST.

| DTE | Pair bought (≈₹20 a leg) | NIFTY move for +7.7 pts (₹500 at 1 lot) | Cost of 10 flat minutes | Cost of IV −0.5 |
|---|---|---|---|---|
| 0 (expiry day) | ~50–100 pts OTM | **~50 pts** | −0.9 pts | −2.2 pts |
| 1 | ~150–350 pts OTM | ~100 pts | −0.3 pts | −3.9 pts |
| 2 | ~250–300 pts OTM | ~130 pts | −0.2 pts | −4.6 pts |

- **On expiry day,** ₹20 options sit close to ATM, so a normal fast move reaches the target.
  This is where the idea works best.
- **On 1–2 DTE,** a ₹20 pair is far OTM. It moves mostly with **IV**, not with NIFTY. IV
  usually *falls* between 09:45 and 10:30, so the pair can lose value even while NIFTY moves.
- **Decision:** on 1+ DTE, pick the pair by **delta**, not by a fixed ₹20. The same Black-76
  numbers for a delta-0.30 pair:

| DTE | Delta-0.30 pair | Pair cost | NIFTY move for +7.7 pts | Cost of IV −0.5 |
|---|---|---|---|---|
| 1 | ~100 pts OTM each side | ~₹75 | **~65 pts** (vs ~100 for a ₹20 pair) | −5.2 pts |
| 2 | ~150 pts OTM | ~₹90 | ~80 pts | −6.8 pts |
| 3–4 | ~150–200 pts OTM | ~₹130 | ~85–90 pts | −8.6 to −9.4 pts |

  Picking by delta brings the needed move closer to expiry day's, but two costs remain:
  - The pair costs 3–6× more.
  - It is more exposed to IV: on 3–4 DTE, a 0.5-point IV drop alone costs more than the
    target.

  So on 1+ DTE the signal has to show a real NIFTY move (S4 is stricter there), and the logs must
  show whether 3–4 DTE days earn their place. If they don't after the replay test, drop those
  days first.

## 3. Schedule (IST)

| Time | Step |
|---|---|
| ~09:35 | Laptop on, `algo login`, `algo run --algo strangle_scalp` |
| on start | Load instruments; subscribe NIFTY, NIFTY futures, India VIX and strikes around ATM; get the 09:15–09:45 range from Kite 1-minute candles |
| **09:45** | Start watching pairs (signal checks every second) |
| **10:30** | Last entry. No new trade after this |
| **10:45** | Square off anything still open *(proposed: gives a 10:29 entry its full time stop)* |
| ~10:50 | Daily report |

This needs nothing before 09:35, so it fits the laptop constraint in [README.md](README.md). Note
that the most volatile half hour (09:15–09:45) is missed by design.

## 4. Entry criteria

### 4.1 Pair selection (re-done every 30 s while flat)

| # | Check | Rule | Default *(proposed)* |
|---|---|---|---|
| P1 | Sides | CE strike above spot, PE strike below spot, nearest expiry | — |
| P2 | Strike choice | 0 DTE: the leg's mid premium is in a band. 1+ DTE: the leg's delta is nearest to the target | 0 DTE: **₹15–30**. 1+ DTE: delta **0.30**, accepted range **0.25–0.35** |
| P3 | Matched | `max(CE, PE) ÷ min(CE, PE)` on mid prices | **≤ 1.3** |
| P4 | Liquidity | bid-ask spread of each leg | **≤ 0.30 pts** (0 DTE), **≤ 0.50 pts** (1+ DTE, dearer options) |
| P5 | Fresh quotes | both legs ticked recently | last tick **≤ 2 s** old |

If several pairs pass, take the one whose two premiums are closest to each other.

How delta is worked out on 1+ DTE:
- Use Black-76 from `trading_core/marketdata/greeks.py` (`implied_vol`, then `greeks`). It is
  already used for the bhavcopy chains.
- The forward comes from the NIFTY futures price.
- Time is measured to 15:30 on expiry day.
- `r` is `marketdata.risk_free_rate`.
- Each leg's IV comes from its own mid price.
- Delta is recomputed when the pair is re-selected (every 30 s), not every tick.

### 4.2 The signal: pair value rising

The pair value is `V(t) = mid_CE(t) + mid_PE(t)`. Use mids, not LTP: the LTP of a ₹20 option
jumps between bid and ask and would fake a signal.

| # | Check | Rule | Default *(proposed)* |
|---|---|---|---|
| S1 | Rise in each window | `V(t) − V(t − 5 s) > 0` | window **5 s** |
| S2 | Persistence | S1 true for N windows in a row, checked every second | **N = 3** (≈15 s) |
| S3 | Size of the rise | `V(now) − V(15 s ago)`, as a share of V | **≥ 2%** (0.8 pts on a ₹40 pair, 1.5 pts on a ₹75 one) |
| S4 | Real move, not noise | NIFTY futures moved in the same 15 s, either way | **≥ 12 pts** on 0 DTE, **≥ 18 pts** on 1+ DTE (a dearer pair needs a bigger move) |
| S5 | One leg drives it | the gaining leg's rise is larger than the losing leg's fall | always true when V rises; logged as the direction |

All of S1–S4 must be true at the same moment. Then check the entry cost: buy at **ask**, so
`ask_CE + ask_PE − V` must be at most half the P4 spread limit, summed over both legs. If that
fails, skip the signal.

### 4.3 Day and trade filters

| # | Check | Rule | Default *(proposed)* |
|---|---|---|---|
| D1 | Trade count | trades taken today | **< 3** |
| D2 | Cool-down | time since the last exit | **≥ 3 min** |
| D3 | Losing streak | losses in a row today | stop for the day after **2** |
| D4 | Day loss | net P&L today | stop for the day at **−₹800** |
| D5 | Day profit lock *(optional)* | net P&L today | stop after **+₹1,000**, so a good morning isn't given back |
| D6 | India VIX | log only at first | maybe skip days with VIX **< 10** (too quiet) after data |
| D7 | Opening range *(optional, recommended to test)* | NIFTY is breaking the 09:15–09:45 high or low | logged first; enforced only if the data says it helps |

Every check is journaled with its value and whether it passed, including signals that fired but
were skipped. The skipped ones are what show whether a filter is worth keeping.

## 5. Position construction

- Buy **both legs together** with limit orders at the current ask, plus a small buffer
  (e.g. +0.10).
- **If one leg fills and the other doesn't within 3 s:** cancel it and sell the filled leg at
  once. A strangle with one leg is a directional bet the strategy never intended.
- The pair is fixed once bought. Re-selecting pairs applies only while flat.
- Quantity: the same lots on both legs.

## 6. Exit criteria (checked every second, first match wins)

P&L is marked at **bid** (what we'd get selling now), not at LTP or mid. Stop and target use
gross P&L; net is logged alongside.

| Priority | Rule | Trigger *(proposed)* | 1 lot (65) |
|---|---|---|---|
| 1 | Kill switch | `algo kill`, or 5 errors in a row | — |
| 2 | Stale feed | no tick for **5 s** (tighter than the condor's 10 s: a scalp can't sit blind) | — |
| 3 | Stop loss | gross P&L ≤ **−4.6 pts × lot size × lots** | **−₹300** |
| 4 | Target | gross P&L ≥ **+7.7 pts × lot size × lots** | **+₹500** |
| 5 | Trailing stop | once gross is ≥ +₹300, exit if it falls back to half of its peak | e.g. peak ₹420 → exit at ₹210 |
| 6 | Stall | the pair value has made no new high for **90 s** | — |
| 7 | Max hold | **8 min** after entry | — |
| 8 | Time exit | **10:45** | — |

- **Stop and target in points** (decided), as the condor does. At 1 lot they equal your ₹300 /
  ₹500. At more lots the rupees grow with the lots, and the trade keeps the same room to
  breathe. Fixing ₹300 at 2 lots would mean a 2.3-point stop on a ₹40 pair. That is about the
  size of the spread plus normal noise, so it would be hit within seconds.
- **The same points on every DTE in v1.** A delta-picked pair on 1+ DTE costs more, so 4.6 pts
  is a smaller share of it: about 6% of a ₹75 pair versus 12% of a ₹40 one. Its spread is
  wider too. If replays show 1+ DTE trades stopping out on noise, the first change to try is a
  wider stop on 1+ DTE (e.g. 6 pts), not a different signal.
- **Exits 5–7 are what make it a scalp.** The edge, if there is one, is the burst. Once the
  burst stops, time decay and IV start winning, so holding "until the stop" gives back the gain.

### Charges (from `config/platform.yaml` rates)

A trade is 4 orders (2 buys, 2 sells), so the flat brokerage is ₹80 plus GST, versus 8 orders
for the condor.

| Lots | Charges per round trip | Condor, for comparison |
|---|---|---|
| 1 | **≈ ₹100** | ₹192 |
| 2 | ≈ ₹108 | ₹195 |
| 3 | ≈ ₹116 | — |

**This matters more than it looks.** At 1 lot, with paper slippage of 0.1 pt per fill
(≈ ₹26 a trade):

| | Gross | Net (after ~₹100 charges + ~₹26 slippage) |
|---|---|---|
| Win at target | +₹500 | **≈ +₹374** |
| Loss at stop | −₹300 | **≈ −₹426** |

So with a fixed 500/300 the strategy needs to win **more than about 53%** of trades just to
break even. Charges take about a third of the stop. Three ways to improve this:
1. 2 lots with the stop and target in points, so charges grow ₹8 while the rupees double.
2. A wider target with the trailing stop.
3. Fewer, better-filtered trades.

## 7. Capital allocation

| Item | Rule *(proposed — confirm)* |
|---|---|
| Lots | **1 lot** on paper to start; 2 lots once the replay test and 10 paper days look sound |
| Capital set aside | the dearest pair (~₹130 on 3–4 DTE ≈ ₹8,500 at 1 lot) plus a buffer → **₹12,000** bucket at 1 lot. No margin is needed: buying pays the premium in full. On expiry day a pair is only ~₹2,600 |
| Worst-case loss per trade | the full premium paid: ~₹2,600 on expiry day, up to ~₹8,500 on 3–4 DTE at 1 lot. This only happens if the feed dies and the exits can't run; a gap can't lose more than that |
| Planned loss per trade | ₹300 + ~₹125 charges and slippage ≈ **₹430** |
| Max loss per day | D3/D4 stop it at 2 losses or −₹800 net, whichever comes first |
| Max loss per week | **₹2,000** → pause and review |
| Max loss per month | **₹5,000** → pause, re-test on replays |
| Scaling up | +1 lot only after 20+ paper trade days with positive net P&L **and** a win rate above the break-even line in section 6 |

## 8. Logged every day

- Every pair picked: DTE, strikes, premiums, IVs, deltas, ratio and spreads.
- Every signal: V over the last 15 s, the NIFTY futures move, which leg drove it, and whether it
  was taken or why it was skipped.
- Fills, including any one-legged fill and its unwind.
- 1-second snapshots of V, both legs and NIFTY while in a trade.
- Peak and trough P&L, exit reason, hold time, gross / charges / net.
- Day totals: trades, wins, losses, and why it stopped.

## 9. Platform pieces (built 27 Oct 2026)

| Needed | Status |
|---|---|
| 1-second tick recording | done: per-algo `tick_snapshot_interval_s` (1 s here; the condor keeps 5 s) |
| Recording on every trading day | done: the scalp runs every day and records NIFTY, VIX, futures and strikes ±600 while it runs (09:35–10:50) |
| NIFTY futures in the feed | done: near-month future stored in the daily instrument snapshot; S4 falls back to spot if it is missing |
| Multiple trades per day | done: `max_trades`, `reentry_cooldown_s` and `reentry_block()` in `BaseAlgo`; a restart resumes with the day's trades counted |
| Trailing stop, stall and max-hold exits | done: `TrailingStop`, `Stall`, `MaxHold` in `trading_core/risk/rules.py` |
| One-leg-filled unwind | done: the shared executor unwinds a partial entry; it counts as a trade |
| Capital per trade | done: premium paid + entry charges (`positions.capital_required`, source `premium`) |
| Latency in paper fills | **not done**: paper fills at the tick that fired the signal, which is optimistic for a scalp. Watch the slippage in the fills and raise `paper.slippage_pts` if live fills turn out worse |
| Two algos at once | fine: two `algo run` windows; Kite allows 3 WebSocket connections per API key |

Two behaviours added while testing on recorded data:
- The pair is **not re-selected while the signal is building** (V rising), and the current pair
  is **kept while it still qualifies**. Otherwise two near-equal pairs swapped every 30 s and
  each swap restarted the signal.
- On 1+ DTE, every call/put combination with a delta in the band is considered: matched
  premiums first, then the pair closest to delta 0.30 on both sides.

### Build order *(proposed)*

1. **Record first:** 1-second recording of NIFTY, futures and strikes ±600 from ATM, every day,
   for 2 weeks, with no trading.
2. **Replay test:** run the signal on those days and count how often it fires, the win rate,
   and the average net. Try N = 2/3/4, the S3/S4 thresholds, and with/without D7. Report
   **each DTE separately**, since expiry day and 3–4 DTE behave differently. If the net is
   negative across the board, stop here. That costs nothing. If only some DTEs lose, drop those
   days.
3. **Paper trade** at 1 lot for 18–24 trade days (common rule 4).
4. Only then decide about live trading and 2 lots.

## 10. Other ideas worth testing (not in v1)

- **Opening-range breakout as the trigger** (D7): the signal counts only when NIFTY breaks out
  of the 09:15–09:45 range. A breakout is when a burst is most likely to continue.
- **IV check:** compute the pair's IV each second. A rise driven by IV (both legs up) is a
  different trade from one driven by a NIFTY move; the log should tell them apart.
- **Leg out of the loser:** after a strong one-sided move, sell the losing leg and hold the
  winner with a trailing stop. More profit on trend days, but it turns into a directional trade
  and needs its own rules. Not for v1.
- **Event days:** RBI policy (10:00 IST) falls inside the window. Log it as a flag first, then
  decide whether to skip those days or trade only on them.
- **Straddle instead of strangle on expiry day:** ATM options respond more to a move. They cost
  more, so the stop and target in points would change.

## 11. Decisions and open questions

Decided 27 Oct 2026:

1. **Days:** trade every day. Expiry day picks a ₹15–30 pair; 1+ DTE picks a delta-0.30 pair
   (section 4.1). Results are reported per DTE so weak days can be dropped later.
2. **Stop and target:** in points (4.6 / 7.7), the same on every DTE in v1. That is ₹300 /
   ₹500 at 1 lot, growing with lots. Fixed rupees would make the stop too tight at 2+ lots.
3. **Time limits:**
   - square-off at **10:45**: the last entry is 10:30, so even a 10:29 entry gets most of its
     8 minutes;
   - **8-minute** max hold;
   - **90-second** stall exit.

   A burst that hasn't paid within 8 minutes usually won't, and decay and IV take over. Tune
   these from replays (try 5 / 8 / 12 min) rather than guessing now.
4. **Day limits:** stop after **2 losses in a row or −₹800 net**, whichever comes first, on top
   of your 3-trade cap. Two quick stop-outs in a row usually mean a choppy morning, which is the
   worst case for this trade.
5. **Record first:** 2 weeks of 1-second recording before any paper trade. It costs nothing,
   and without it a 5-second signal can't be tested at all (section 9).
   *Changed 27 Oct 2026:* paper trading starts as soon as the algo is built, on every day, and
   recording runs alongside it. Live trading will later use only the days the paper results
   show to be profitable.

Still open (answered by the replay test, not now): the exact S3/S4 thresholds, N, whether D7
(opening range) helps, and whether 3–4 DTE days stay in.
