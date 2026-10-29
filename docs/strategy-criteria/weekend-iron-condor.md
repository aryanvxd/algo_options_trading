# NIFTY weekend short iron condor

- **Status:** v0.1 implemented in `src/algos/weekend_condor/` (config version 0.1.0, 29 Oct 2026), with
  the proposed defaults below. **W3 (weekend events) is logged only** until the market calendar
  exists. Numbers marked *(proposed)* are starting points to tune.
- **Mode:** paper, 1 lot, from Friday 30 Oct 2026.
- **Last changed:** 29 Oct 2026.

## 1. Idea

Sell an iron condor on **Friday afternoon** and buy it back on **Monday morning**. Friday 15:30 to
Monday 09:15 is about 66 hours of calendar time, but no trading. If the market opens on Monday
near Friday's close, the condor has lost three days of time value for one night of holding.

This is the first **positional** algo. The trade is still open when the Friday process ends, and
a new process picks it up on Monday.

Three things work against it:

1. **Much of the weekend decay is already in Friday's prices.** Market makers know about the
   weekend and mark premiums down through Friday afternoon. The edge is what is left at entry,
   plus Monday's usual IV drop at the open. Only paper results will show how much that is.
2. **Gap risk, with nobody watching.** Anything that happens over the weekend shows up at 09:15
   on Monday:
   - US jobs data on Friday evening;
   - elections, geopolitics or an RBI surprise.

   The laptop only comes on at ~09:35, so **no stop can act between 09:15 and 09:35**. The
   hedges are what limit the loss, so size by them (section 7).
3. **Overnight margin.** Holding past 15:30 needs NRML margin, which is higher than intraday
   MIS margin. Zerodha also squares off MIS positions on its own around 15:20, so a live
   version must use NRML.

## 2. Instrument and trade days

| Item | Rule |
|---|---|
| Underlying | NIFTY weekly options, nearest expiry (Tuesday): 2 DTE on Friday, 1 DTE on Monday |
| Entry day | the **last trading day before a gap of 2+ calendar days without trading**: normally Friday; Thursday if Friday is a holiday |
| Exit day | the **next trading day**: normally Monday. If that is expiry day (Monday holiday), exit at the open as usual and don't hold into expiry |
| Trades per week | one |
| Long weekends | allowed: more decay, but more gap risk; the VIX and event filters below decide |

## 3. Schedule (IST)

| When | Step |
|---|---|
| Friday, any time from 09:35 | `algo run --algo weekend_condor`, or **Start** on the dashboard. The laptop is on all day at the office |
| Friday 14:45–15:10 | entry window *(proposed)*: late, so less of Friday's own move is risked, and before the 15:15 close-out of the intraday algos |
| Friday 15:10–15:28 | exits are watched (stop, blow-out); **no square-off** |
| Friday ~15:30 | report "carried over the weekend"; the process ends |
| Monday ~09:35 | start it with the other algos. It finds Friday's open position and starts watching it at once |
| Monday 09:35–09:50 | **exit** *(proposed)*: buy back at the first moment every leg's spread is ≤ 2 pts. Stop and target also apply |
| Monday 09:50 | hard exit, at whatever price |

## 4. Entry criteria (Friday, all must pass)

| # | Check | Rule | Default *(proposed)* | Why |
|---|---|---|---|---|
| W1 | Entry day | today is the last trading day before 2+ non-trading days | — | strategy |
| W2 | India VIX | below the limit | `< 18` (stricter than the intraday condor's 20) | a weekend gap scales with VIX |
| W3 | Weekend events | no **High-impact** event between Friday 15:30 and Monday 09:15 | from the market calendar (see `docs/design/market-calendar.md`) | e.g. US jobs data on the first Friday of the month, election results, a Saturday Budget |
| W4 | Premium balance | `max(CE, PE) ÷ min(CE, PE)` at the short strikes | `≤ 1.5` | same as the intraday condor's E4 |
| W5 | Liquidity | every leg's bid-ask spread | `≤ 2 pts` | |
| W6 | Minimum credit | net credit at executable prices | `≥ 12 pts` | the credit must pay for the gap risk and ~₹195 of charges |

If W4–W6 fail, it re-checks every 30 s until 15:10, then stands aside for the week. W1–W3 skip the
week straight away. Every check is journaled.

## 5. Position construction

Strikes come from the **expected weekend move**, not fixed widths. A fixed width would be too
tight in a nervous week and too loose in a calm one.

```
expected_move = spot × VIX/100 × sqrt(calendar days to Monday 09:15 / 365)
                (Fri→Mon ≈ 2.7 days; VIX 15, NIFTY 25,800 → ≈ 335 pts)
short_ce = spot + 1.2 × expected_move   (rounded away from spot to a listed strike)
short_pe = spot − 1.2 × expected_move
long_ce  = short_ce + hedge             hedge = 200 pts (proposed)
long_pe  = short_pe − hedge
```

Hedges are bought first, as in the intraday condor. Product: NRML for margin (and for live).

## 6. Exit criteria (first match wins)

| Priority | Rule | Trigger *(proposed)* | 1 lot (65) |
|---|---|---|---|
| 1 | Kill switch | `algo kill` / Stop on the dashboard | — |
| 2 | Stale feed | no tick for 10 s, but **only while the market is open** (never over the weekend) | — |
| 3 | Stop loss | gross P&L ≤ −10 pts × qty | −₹650 |
| 4 | Premium blow-out | cost to close ≥ 1.5 × entry credit, or a short leg ≥ 2× | — |
| 5 | Target | gross P&L ≥ +60% of the credit (e.g. 12-pt credit → +7.2 pts) | ≈ +₹470 on a 12-pt credit |
| 6 | Monday exit | first moment after 09:35 with all spreads ≤ 2 pts | — |
| 7 | Hard exit | Monday 09:50 | — |

- The stop is wider than the intraday condor's 5 pts because the position has to sit through
  Friday's last 20 minutes and the Monday open.
- **On Monday the stop can only act from ~09:35.** A gap that opens beyond the stop is taken at
  the Monday price, up to the hedged maximum loss.

## 7. Capital allocation

| Item | Rule *(proposed — confirm)* |
|---|---|
| Lots | **1** on paper |
| Capital | Kite basket margin for the 4 legs **at NRML** (recorded with each trade), plus 50%. Expect roughly ₹40–70k per lot; write the real figure here after the first Friday |
| Worst case | (hedge − credit) × qty = (200 − 12) × 65 ≈ **₹12,200** per lot: a Monday gap through the short strike |
| Planned loss | stop ₹650 + charges ~₹195, plus whatever the Monday gap adds |
| Limits | 1 trade a week. Monthly max loss **₹5,000**, then pause and review |

## 8. Platform pieces (built 29 Oct 2026)

| Needed | Status |
|---|---|
| Positions carried across days | done: `carry_overnight` in the config. Square-off keeps the position open on the entry day; the next run of the algo **adopts** any position an earlier day left open (same algo, mode and source) and watches it at once |
| P&L on the right day | done: Friday's summary says "carried"; the realised trade counts on the exit day (Overview, Today, Journal, Performance). The trade shows on both days, marked *Carried* / *Carried in* |
| Stale feed only during market hours | done: `StaleFeed(market_hours=...)` |
| NRML margin | done: `product: NRML` (used for the Kite basket margin) |
| Report as soon as the day's work is done | done: `report_when_done` (Thursday skips at once; Monday reports right after the exit) |
| Weekend-event check (W3) | **logged only**: needs the market calendar (`docs/design/market-calendar.md`) |
| Two-day replays | done: replay Friday, then Monday; the Monday replay adopts the Friday position |

**Running it:**
- **Friday:** start it any time from 09:35 (`algo run --algo weekend_condor`, or **Start** on the
  dashboard). It waits until 14:30, enters 14:45–15:10 and reports at ~15:30 with the position
  open.
- **Monday:** start it again with the other algos. It takes the position over, buys it back from
  09:35 and reports at once.
- **Forgot on Monday?** Start it on Tuesday morning. It exits then; Tuesday is expiry day, so
  don't leave it later than the open.

## 9. Open questions

1. **Entry window:** 14:45–15:10 on Friday, or earlier (e.g. 13:30) to sell while more of the
   weekend premium is still in the price?
2. **Monday exit:** buy back at the first good moment after 09:35 (proposed), or hold to ~10:30
   for Monday's IV drop?
3. **Strikes:** 1.2 × the expected weekend move with 200-pt hedges, or something else?
4. **Stop:** 10 pts (₹650 at 1 lot) — OK?
5. **Event filter (W3):** skip the weekend only for High-impact events, or for Medium ones too?
