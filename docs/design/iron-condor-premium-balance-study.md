# Iron condor: the CE/PE premium-balance check (E4) — study and plan

- **Date:** 3 Nov 2026.
- **Question:** the condor skipped 30 Oct, 2 Nov and 3 Nov, every time because of E4 (CE/PE
  premium imbalance, puts richer). Would it have made money without E4? Should it sell
  equal-premium strikes instead of equal-width ones, and should the last entry move to 10:30?
- **Data:** `data/backtests/condor_balance_study/` (scripts in `scripts/`).

## 1. Data used

| Data | Source | Range |
|---|---|---|
| 1-minute candles with OI, NIFTY 03-Nov-2026 weekly, 130 contracts (21,600–24,800 CE and PE) | Kite historical (the expiry was still live today) | 28 Oct – 3 Nov 2026 |
| NIFTY 50 and India VIX, 1-minute | Kite historical | same |
| Bid/ask ticks the recorder saved while the algo ran | `data/market/options_ticks` | mornings only |
| End-of-day option chains | NSE bhavcopy (already stored) | Nov 2024 – Oct 2026, 491 sessions |

Every trade day from 28 Oct to 3 Nov sold this one expiry, so all five days are replayed on real
prices. 27 Oct sold the 27-Oct expiry, which Kite no longer serves.

**Checks on the data:**
- Candle closes agree with the recorded bid/ask mid to about 0.05 pt.
- OTM spreads in the recorded ticks: median 0.05 pt under ₹15, 0.10 pt at ₹15–40, 0.15–0.20 pt at
  ₹40–100.
- Replaying the real 29 Oct trade gives a credit of 75.65 (real 75.9) and a stop at 10:17 for
  −₹696 gross. The real trade stopped at 10:24 for −₹708.

## 2. The days E4 blocked

| Day | DTE | Ratio at the first check | CE / PE (mid) | Ratio at 10:30 | NIFTY from 09:45 to 15:15 |
|---|---|---|---|---|---|
| Fri 30 Oct | 2 | 1.64 | 29.17 / 47.90 | 1.71 | 22,971 → low −66 (10:16), high +147 (12:57), close +39 |
| Mon 2 Nov | 1 | 1.85 | 13.44 / 24.83 | 2.36 | 23,321 → high +39 (10:54), low −114 (13:40), close +10 |
| Tue 3 Nov | 0 | 2.40 | 3.62 / 8.67 | 3.10 | 23,613 → high +133 (13:41), close +119 |

On all three days the ratio got **worse** through the morning. A 10:30 deadline alone would not
have produced a trade on any of them.

## 3. Replay on real prices (2 lots, current exits, ₹210 charges per trade)

The strike-selection methods tested:

| Method | Shorts |
|---|---|
| **Same width** (now) | ATM ± width/2 |
| **Mean premium** | each short at the strike whose premium is closest to the average of the two same-width shorts |
| **Shift** | same width, centre slid to the most balanced pair |
| **Cheap side in** | keep the rich side; move the cheap side closer until its premium matches |
| **Rich side out** | keep the cheap side; move the rich side further out until it matches |

Hedges are always the DTE's hedge distance beyond each short. Entry is the first minute from 09:45
that passes the remaining checks.

### Net ₹ per day, E4 removed, last entry 10:30

| Day | DTE | Same width | Mean premium | Shift | Cheap side in | Rich side out |
|---|---|---|---|---|---|---|
| 28 Oct | 4 | −1,152 stop | −1,029 stop | −1,029 stop | −970 stop | −1,282 stop |
| 29 Oct | 3 | −1,087 stop | −971 stop | −971 stop | −1,217 stop | −1,081 stop |
| **30 Oct** | 2 | **+833** target | **+846** target | +833 target | +836 target | +846 target |
| **2 Nov** | 1 | **−1,087** stop | **+853** target | +853 target | +914 target | +677 time |
| **3 Nov** | 0 | no trade (credit 6.5 < 10) | no trade (6.0) | no trade (4.9) | −883 stop | no trade (1.5) |
| **3 Nov, min credit 5** | 0 | +638 time | +404 time | −642 blow-out | −883 stop | — |

On the current rules (E4 on) the five days give two trades and −₹2,239.

**What the replay shows:**

1. **30 Oct:** every version hits target. E4 cost a winning trade.
2. **2 Nov:** your observation holds. NIFTY fell 114 pts by 13:40, and the same-width condor's
   23,000 put (321 pts out at entry) took it to the stop. The mean-premium condor had sold the rich
   put at 22,950, 371 pts out, and hit target.
3. **3 Nov:** removing E4 is not enough. Every condor that is 500 wide with 200-pt hedges takes in
   only 5–8 pts, below the 10-pt minimum credit (puts this far out never fall below ~₹2 on expiry
   day, so the hedges are expensive). With the minimum at 5, the same-width and mean-premium
   condors end positive at 15:15. NIFTY rose 133 pts, on the cheap call side, so the versions that
   pulled the call in to 187 pts from spot were stopped.
4. **28 and 29 Oct** lose under every method: NIFTY moved 100–150 pts in the first half hour. The
   strike method makes no difference there.
5. **Entry time** (09:45 / 10:00 / 10:15 / 10:30) changes which of these days stop out. Five days
   are not enough to choose a time from.
6. **The exits matter more than the strikes.** On 3–4 DTE the credit is 75–110 pts, so the 5-pt
   stop is only 5–7% of it and fires on ordinary noise. Held to 15:15 with no stop or target, the
   same-width condor ends **positive on 4 of the 5 days** (+13.8, −16.9, +17.9, +9.2, +6.5 pts).
   On 0 DTE a 6-pt credit makes the blow-out rule (1.35 × credit) a ~2-pt stop.

### Holding to 12:30 instead (no stop, target or blow-out; E4 off, last entry 10:30)

| Day | DTE | Same width, held to 12:30 | Mean premium, held to 12:30 | Same width, held to 15:15 |
|---|---|---|---|---|
| 28 Oct | 4 | +11.50 pts (+₹1,285) | +9.80 (+₹1,064) | +13.80 |
| 29 Oct | 3 | −2.00 (−₹470) | −8.45 (−₹1,309) | −16.90 |
| 30 Oct | 2 | −2.70 (−₹561) | −4.75 (−₹827) | +17.93 |
| 2 Nov | 1 | +3.75 (+₹277) | +3.80 (+₹284) | +9.22 |
| 3 Nov (min credit 5) | 0 | +3.47 (+₹242) | +1.97 (+₹47) | +6.53 |
| **Total net** | | **+₹773** | −₹741 | |

With the current stop and target still active and a 12:30 time exit, same width totals −₹887
(the two stop-outs on 28 and 29 Oct happen before 12:30 either way).

- A 12:30 exit cut the worst day (29 Oct: −16.9 → −2.0 pts), because NIFTY's move ran into the
  afternoon.
- It also gave up most of the decay: on these days most of the premium drop came after 12:30.
  30 Oct went from +17.9 to −2.7 pts.
- Without a stop, it is the only version here that ends positive, but only by ₹773 over five days.

### Wed 4 Nov (3 DTE, 09-Nov expiry, the algo was not run)

Data: Kite 1-minute candles for the 09-Nov chain, downloaded the same evening
(`options_1m_2026-11-04_exp20261109.parquet`). NIFTY 23,911 at 09:45, low 23,738 at 12:15, then
back to 23,912 at 15:15. VIX 16.06.

- **E4 would have passed** (ratio 1.16), so the current algo would have traded. The premiums were
  already balanced, so every strike method sells the same condor: 23300/23600 PE, 24200/24500 CE,
  credit 94.05.
- **Current exits: stop at 11:00, −₹925.** The trade then fell to −13.1 pts at 12:15 and recovered
  to +20 pts. Held to 15:15 it makes **+₹2,104**; held to 12:30, −₹171.

### All six days (E4 off, last entry 10:30, minimum credit 5, net ₹)

| Exits | Strikes | 28 Oct | 29 Oct | 30 Oct | 2 Nov | 3 Nov | 4 Nov | Total |
|---|---|---|---|---|---|---|---|---|
| stop 5 / target 8 (the original's exits, without its E4 and minimum credit) | same width | −1,152 | −1,087 | +833 | −1,087 | +638 | −925 | **−2,780** |
| stop 5 / target 8 | mean premium | −1,029 | −971 | +846 | +853 | +404 | −925 | −822 |
| stop 15 / target 8 | same width | +850 | −2,407 | +833 | +853 | +638 | +863 | +1,630 |
| stop 15 / target 15 | same width | +1,766 | −2,407 | +1,802 | +989 | +638 | +1,773 | **+4,561** |
| stop 15 / target 15 | mean premium | +1,025 | −2,264 | +1,880 | +1,074 | +404 | +1,773 | +3,892 |
| hold to 12:30 | same width | +1,285 | −470 | −561 | +277 | +242 | −171 | +602 |
| hold to 15:15 | same width | +1,584 | −2,407 | +2,120 | +989 | +638 | +2,104 | +5,028 |

These are from the study's own simulator. The same days through the real algos on real candles:
`iron_condor_balanced` +₹3,582, `iron_condor` −₹2,383 ([backtest.md](backtest.md), section 5a).

The 5-pt stop turned three eventual winners into losses (28 Oct, 2 Nov, 4 Nov) and saved one
(29 Oct). With the 5-pt stop, mean premium beats same width by about ₹2,000. With a wider stop
the strike method matters much less. Six days in one trending week are not enough to set the
numbers. They show where to look: the stop first, the strike method second.

**Caution:** five days, one expiry week, and a strong rally (NIFTY ~22,630 → ~23,650). This tells
us E4 was wrong this week. It cannot prove which strike method is best.

## 4. Two years of end-of-day chains: does an imbalance warn of a bad day?

Each session is matched with the previous day's close, which has about as much time to expiry left
as the next morning. The ratio is measured exactly as E4 does, at the DTE's width.

**How often E4 (ratio > 1.5) would block:**

| DTE | Sessions | Blocked | Median ratio |
|---|---|---|---|
| 0 | 105 | **40%** | 1.38 |
| 1 | 105 | **30%** | 1.29 |
| 2 | 105 | 17% | 1.12 |
| 3 | 101 | 18% | 1.19 |
| 4 | 75 | 23% | 1.27 |

Near expiry the OTM premiums are tiny, so the normal put skew alone pushes the ratio past 1.5.

**What NIFTY did next, 09:45–15:15 (sessions where puts were richer):**

| Ratio | Sessions | Average move | Reached a short strike | Closed down |
|---|---|---|---|---|
| < 1.2 | 102 | 107 pts | 12% | 54% |
| 1.2–1.5 | 69 | 129 pts | 13% | 46% |
| 1.5–2.0 | 45 | **90 pts** | **7%** | 40% |
| ≥ 2.0 | 22 | **92 pts** | **5%** | 59% |

Days with a large imbalance were **calmer**: they moved less and reached a short strike less
often. The richer side did not predict direction either. So E4 has been removing some of the best
condor days, and 0–1 DTE days most of all.

## 5. Two-year engine backtest (modelled prices)

`algo backtest --algo iron_condor`, 1 Nov 2024 – 30 Oct 2026 (492 days). Results are in
`data/backtests/<name>/`.

| Run | Changes from 0.3.0 | Trades | Win rate | Net ₹ | Main exits |
|---|---|---|---|---|---|
| `ic_e4_on_2y` | none | 299 | 17% | −181,251 | 162 stops, 36 blow-outs, 89 time, 12 targets |
| `ic_e4_off_2y` | E4 off | 321 | 16.5% | −195,639 | 172 stops, 44 blow-outs, 93 time, 12 targets |
| `ic_e4_off_s20t15_2y` | E4 off, stop 20, target 15 | 321 | 20% | −235,561 | 156 blow-outs, 8 stops, 157 time |
| `ic_e4_off_hold1230_2y` | E4 off, no stop / target / blow-out, every trade closed at 12:30 | 321 | 12% | −215,004 | all time exits |

The 12:30 run loses about 3.5 pts gross per trade by 12:30. The same rule on the real prices of
28 Oct – 3 Nov averaged **+2.8 pts**. A gap that size and that one-sided points to a bias in the
option model against sellers (for example, the morning IV level or how time to expiry is counted).
Check it before using the engine for this strategy.

**What it says:**
- **E4 makes almost no difference.** The 21 extra trades taken on days E4 would block averaged
  −₹561, against −₹613 on the other days. So the blocked days were not worse days, but E4 is not
  the reason the strategy loses here either.
- **Without E4, the minimum credit blocks the day instead.** 0 DTE traded on 2 of 105 days.
- **Every exit setting loses in the model.** The losses are stops and blow-outs. Trades that
  reach 15:15 are roughly flat after charges.

**Why the model can't settle this for a condor seller:**
- On 0–1 DTE it prices far-OTM options near zero. Many blocked days show a modelled credit of 0–2
  pts, while on 3 Nov the real market paid 6–8 pts for the same structure, because far puts never
  fall below about ₹2.
- Each option's IV is yesterday's close, moved only with VIX. The real intraday drop in premium
  that a seller earns, and today's real skew, are missing.
- Every minute's price path visits both the high and the low, which makes a tight stop look worse
  than it is.

On the real prices of 28 Oct – 3 Nov, holding to 15:15 was positive on 4 of 5 days. The model
disagrees with the market in the direction that matters, so these runs rank rules at best. They
are not a P&L estimate. **Collecting real intraday option data is a prerequisite for tuning this
strategy** (section 6, C6).

## 6. Changes

Built on 4 Nov 2026 as a new algo, `iron_condor_balanced` (spec:
[iron-condor-balanced.md](../strategy-criteria/iron-condor-balanced.md)), not as config 0.4.0 of
the original, so the original stays as the baseline. C1–C5 are in it; for C5 the minimum is 5 pts
on 0 DTE. Its stop and target by DTE go beyond this table: see the spec, section 6.

| # | Change | Why |
|---|---|---|
| C1 | **E4 becomes log-only.** The ratio and the richer side are still journaled every day | It blocked 3 of the last 4 days; on two years of data high-ratio days were calmer, not riskier |
| C2 | **New strike mode `balanced`:** each short at the strike whose premium is closest to the average of the same-width pair. Hedges stay at the DTE's distance beyond each short. Config `strike_mode: width \| balanced` | Sells the rich side further out, so the condor leans with the skew instead of skipping the day. It made the difference on 2 Nov (fall on the rich put side) and kept the cheap call at the same-width distance on 3 Nov |
| C3 | **Minimum distance from spot for each short** in balanced mode (proposed 225 pts at 0–1 DTE, 250 at 2–4 DTE; else fall back to same width) | Stops the balancing from pushing one short close to spot, as the 187-pt call on 3 Nov did |
| C4 | **Last entry 10:30** (`entry_deadline: "10:30"`), first check still 09:45 | Harmless now that E4 no longer waits; gives the spread and credit checks more time |
| C5 | **Minimum credit by DTE** (`min_credit_by_dte`: 0 DTE 5, others 10) | 10 pts makes 0 DTE untradeable at 500 wide / 200 hedge. A 5-pt credit means a small profit at best, and the 1.35× blow-out becomes a ~2-pt stop |

| C6 | **Record the full option chain all day** (ATM ± 1,000, 09:15–15:30, bid/ask), even on days the condor skips or exits early, and save Kite 1-minute candles for the current weekly expiry each evening before it expires | Recording stops when the process stops: the runner records until square-off, but the window was closed early on 30 Oct, 2 Nov and 3 Nov (30 Oct has one minute of ticks). Kite serves no expired options, so every week not saved is lost. A few weeks of this data allows real-price backtests instead of the model |

**Follow-up, outside this change:** stops and targets scaled to the credit, or the v3 trailing
exits. The replay says the 5-pt stop on a 75–110-pt credit is the biggest single drag. Test it on
the engine before changing anything.
