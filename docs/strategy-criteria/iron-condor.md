# NIFTY short iron condor

- **Status:** v2 — implemented in `src/algos/iron_condor/` (config version 0.3.0, 28 Oct 2026).
  Section 9 lists what changed from v1.
- **Mode:** paper, 2 lots (from 27 Oct 2026; was 1).
- **Last changed:** 28 Oct 2026 — trades every trading day (was the last 3 days of the cycle).

## 1. Idea

Sell one OTM call and one OTM put at equal distance from ATM, and buy further-OTM call and put
as hedges. Profit comes from premium decay on a range-bound day. Keep v2 simple: a few basic
checks only, enter after 09:45, flat by 15:15.

## 2. Instrument and trade days

| Item | Rule |
|---|---|
| Underlying | NIFTY 50 weekly options, nearest expiry (Tuesday; moves earlier on a holiday) |
| Lot size | read from Kite's instrument list each day (65 at the time of writing) |
| Trade days | **every trading day** (from 28 Oct 2026): expiry day and the 4 trading days before it (Wed–Tue). Config `trade_window_days: 5` |
| No-trade days | NSE holidays, weekends |
| Trades per day | one; no re-entry after an exit |

Expiry and holidays are read each day from Kite's instrument list and `config/holidays/`, never
hard-coded.

## 3. Schedule (IST)

| Time | Step |
|---|---|
| ~09:35 | Laptop on, `algo login`, `algo run --algo iron_condor` |
| on start | Load instruments, decide trade day, start the live feed |
| **09:45** | Entry window opens — run the entry checks |
| **10:00** | Last entry time. No entry by then = no trade today |
| 09:45–15:15 | Monitor exits every second |
| 15:15 | Square off anything still open |
| 15:35 | Daily report |

## 4. Entry criteria (all must pass, checked at entry time)

| # | Check | Rule | Default | Why |
|---|---|---|---|---|
| E1 | Trade day | today is expiry day or 1–4 trading days before it | 5-day window (every day) | strategy design |
| E2 | India VIX | VIX **below** the limit | `< 20` | high VIX = big moves, stops get hit |
| E3 | PCR (log only) | PCR (OI) recorded, **not enforced yet** | band chosen after 2–3 weeks of data | extreme PCR = one-sided positioning |
| E4 | CE/PE premium balance | short CE and short PE premiums are close to each other | ratio `≤ 1.5` (proposed) | big imbalance = market leaning one way |
| E5 | Liquidity | bid-ask spread of every leg | `≤ 2 pts` | wide spreads eat the edge and trigger stops |
| E6 | Minimum credit | net credit at executable prices (shorts at bid, hedges at ask) | `≥ 10 pts` | below this it isn't worth the risk + charges |

If E4, E5 or E6 fail, the algo **waits and re-checks** (every 30 s) until the last entry time
(10:00); still failing then = no trade today. A failed E1 or E2 skips the day. E3 never blocks a
trade in v2. Every check is journaled with its value, pass or fail.

### E3 — PCR definition (log only in v2)

- PCR = total **put open interest** ÷ total **call open interest**.
- Nearest weekly expiry only, strikes within **ATM ± 1,000 pts** (about 40 strikes each side) —
  far strikes have stale OI that distorts the ratio.
- Read from one Kite quote call at entry time (well under the 500-instrument limit).
- Logged at every entry check. After 2–3 weeks of paper data, compare PCR on winning and losing
  days and pick a band (0.8–1.2 is a common starting point). On expiry day (0 DTE) PCR swings a
  lot intraday, so the band may need to differ or not apply on 0 DTE.

### E4 — premium balance definition (to confirm)

- Compare the two short legs the condor would sell: `ratio = max(CE, PE) ÷ min(CE, PE)` using mid
  prices.
- Measure the distance from **spot**, not from the rounded ATM. With spot 22,430 and ATM rounded
  to 22,450, a 600-wide condor's shorts are 280 pts below spot and 320 pts above it, so the call
  looks cheaper only because of rounding.
- Puts are normally a little richer than equally distant calls (volatility skew), so a small
  PE-over-CE premium is normal. Start with 1.5 and tune from the logged ratios.
- Log which side was richer; this same number is the signal for the option-buying idea in
  [premium-skew-option-buying.md](premium-skew-option-buying.md).

## 5. Position construction

```
atm      = spot rounded to nearest 50
short_pe = atm - width/2        short_ce = atm + width/2
long_pe  = short_pe - hedge     long_ce  = short_ce + hedge
```

| Trading days to expiry | Width (short PE ↔ short CE) | Hedge (short → long) |
|---|---|---|
| 4 (normally Wed) | 600 | 300 |
| 3 (normally Thu) | 600 | 300 |
| 2 (normally Fri) | 600 | 300 |
| 1 (normally Mon) | 550 | 250 |
| 0 (expiry, normally Tue) | 500 | 200 |

Order: **buy both hedges first**, then sell both shorts. If a hedge doesn't fill, the shorts are
never sent and anything filled is unwound.

## 6. Exit criteria (checked every second, first match wins)

| Priority | Rule | Trigger | 2 lots (130) |
|---|---|---|---|
| 1 | Kill switch | `algo kill`, or 5 errors in a row | — |
| 2 | Stale feed | no tick for 10 s | — |
| 3 | Stop loss | gross P&L ≤ −5 pts × lot size × lots | −₹650 |
| 4 | Premium blow-out | cost to close ≥ 1.35 × entry credit, **or** any short leg ≥ 2× its entry price | — |
| 5 | Target | gross P&L ≥ +8 pts × lot size × lots | +₹1,040 |
| 6 | Time exit | 15:15 | — |

P&L is marked at the prices you would actually get when closing (shorts at ask, hedges at bid),
not at LTP. Stop and target use **gross** P&L. Net P&L after charges is logged alongside it.
Charges are mostly the flat ₹20 brokerage per order (8 per round trip), so they barely grow with
lots: the 27 Oct trade cost ₹192.15 at 1 lot and would have cost ₹195.46 at 2 lots.

## 7. Capital allocation

| Item | Rule (proposed — confirm) |
|---|---|
| Lots | **2 lots** in paper from 27 Oct 2026 (was 1). Go live at the lot size paper ran at |
| Capital set aside | Kite basket margin for the 4 legs **plus** a 50% buffer. Check the margin in Kite on a real day and write the number here: `₹ ____` |
| Worst-case loss per trade | (hedge − credit) × 65 × lots. At 2 lots roughly ₹24k at 0 DTE (200 hedge) to ₹37k at 2 DTE (300 hedge) — only if NIFTY gaps through both the stop and the hedge |
| Planned loss per trade | stop ₹650 + slippage + ~₹200 charges; treat ₹1,000 as a normal bad day |
| Max loss per day | 1 trade per day, so = one stop-out. Any day losing more than **₹1,500** is reviewed by hand |
| Max loss per week | **₹3,000** (≈ 3 full stop-outs incl. charges at 2 lots) → strategy pauses until reviewed |
| Max loss per month | **₹8,000** → strategy pauses, re-check parameters on paper |
| Scaling up | +1 lot only after 20+ trade days with positive **net** P&L and a worst day you are comfortable with; never more than +1 lot per month |
| Scaling down | back to 1 lot after a monthly loss limit hit |

Weekly and monthly limits are not automated yet — the daily report shows the totals.

## 8. Logged every day

Trade-day decision, VIX, PCR, CE/PE premium ratio (and which side was richer), spreads, credit,
the 4 fills, 5-second snapshots of net premium and NIFTY, exit reason, gross / charges / net P&L.

## 9. Changes from v1 (all done)

| v1 rule | v2 | Code change |
|---|---|---|
| 08:15 day check | runs when the algo starts (~09:35) | none — a late start already runs past-due steps at once |
| 09:08 pre-open snapshot | **removed** | drop the `premarket` step |
| NIFTY 50 gainers/losers + advance/decline (breadth) filter | **removed** | drop `breadth_filter`, the 50-stock quote pull and `config/universes/nifty50.yaml` from this algo |
| Opening gap filter (±0.8% vs previous close) | **removed** — at 09:45 the gap is old news; VIX and premium balance cover it | drop `gap_filter` |
| VIX checked twice (pre-open and entry) | checked once, at entry | simplify |
| Entry 09:20–09:35 | **09:45–10:00** | `schedule.entry`, `entry_deadline` |
| — | **new:** PCR, logged only | new check (journal only) |
| — | **new:** CE/PE premium balance check, wait and re-check until 10:00 | new filter |
| Stop/target set both in points and rupees | points only | remove the rupee fallback |

Kept unchanged: trade-day window, widths/hedges, spread and minimum-credit checks, hedges-first
order, all six exits, one trade a day.

## 10. Decisions and open questions

Decided 26 Oct 2026: PCR is log-only; an unbalanced CE/PE premium waits and re-checks until
10:00; last entry 10:00; premium blow-out exit kept.

Decided 28 Oct 2026: trade **every trading day** (config 0.3.0, `trade_window_days: 5`). On flat
days such as 28 Oct the options still lose time value, which is what the condor sells. Wednesday
and Thursday (4 and 3 DTE) reuse the 2-DTE shape (600 wide, 300 hedges) for now.

Decided 27 Oct 2026: paper trades **2 lots** (config 0.2.1). Stop/target stay in points, so in
rupees they double (₹650 / ₹1,040); the day/week/month limits in section 7 were doubled to match.

Still open:

0. Wider shorts/hedges on 3–4 DTE? More time left means bigger moves before expiry; check the
   Wed/Thu paper days (Performance tab, by DTE) before changing them.

1. Premium balance limit: start at 1.5×, or another number?
2. Capital: how much will you set aside for this strategy, and are the weekly/monthly loss
   limits in section 7 acceptable?
