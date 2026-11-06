# NIFTY short iron condor — balanced premium

- **Status:** v0.2 — implemented in `src/algos/iron_condor_balanced/` (config version 0.2.0,
  5 Nov 2026: adds the trailing profit stop).
- **Mode:** paper, 2 lots.
- **Relation to [iron-condor.md](iron-condor.md):** same idea, schedule and order of execution.
  The original condor stays unchanged as the baseline. This one changes the rules that made it skip
  most days or stop out on noise. The evidence is in
  [../design/iron-condor-premium-balance-study.md](../design/iron-condor-premium-balance-study.md).

## 1. Idea

Sell one OTM call and one OTM put **of about the same premium**, and buy further-OTM hedges. This
is how the condor was traded by hand: when puts are dearer than calls, sell the put further out
instead of skipping the day. The CE/PE imbalance is logged, never a reason to skip. Profit comes
from premium decay on a range-bound day.

## 2. Instrument and trade days

Same as the original: NIFTY weekly options, nearest expiry, every trading day (expiry day and the
4 trading days before it), one trade a day, no re-entry. Lot size from Kite's instrument list.

## 3. Schedule (IST)

| Time | Step |
|---|---|
| ~09:35 | `algo login`, `algo run --algo iron_condor_balanced` |
| **09:45** | Entry window opens |
| **10:30** | Last entry time (the original: 10:00) |
| 15:15 | Square off anything still open |
| 15:35 | Daily report |

## 4. Entry criteria (checked at entry time)

| # | Check | Rule | Default | Change from the original |
|---|---|---|---|---|
| B1 | Trade day | expiry day or 1–4 trading days before it | 5-day window | same |
| B2 | India VIX | below the limit, else skip the day | `< 20` | same |
| B3 | PCR | logged only | — | same |
| B4 | CE/PE premium balance | **logged only**: ratio and richer side, at the short distance from spot | — | was a block above 1.5 |
| B5 | Liquidity | bid-ask spread of every leg | `≤ 2 pts` | same |
| B6 | Minimum credit | net credit at executable prices, **by DTE** | 0 DTE: `5`, 1–4 DTE: `10` | was 10 on every day |

B5 and B6 wait and re-check until 10:30.

**Why B4 is log-only:** it blocked 3 of the 4 days from 30 Oct to 3 Nov. Over two years of NSE
end-of-day chains, sessions with a ratio above 1.5 moved less (about 90 pts vs 107–129) and reached a
short strike less often (5–7% vs 12–13%). The richer side did not predict direction.

**Why 5 pts on 0 DTE:** 500 wide with 200-pt hedges takes in 5–8 pts on expiry day, because far
puts never trade below about ₹2. At 10 pts, 0 DTE never trades.

## 5. Position construction

```
same-width shorts   short_pe0 = atm - width/2     short_ce0 = atm + width/2   (as the original)
target premium      t = (mid(short_pe0) + mid(short_ce0)) / 2
balanced shorts     short_pe = the listed PE below spot whose mid is closest to t
                    short_ce = the listed CE above spot whose mid is closest to t
                    (ties go further OTM)
hedges              long_pe = short_pe - hedge    long_ce = short_ce + hedge
```

Guards on each balanced short:

| Guard | Rule | Default |
|---|---|---|
| Minimum distance from spot | a candidate closer to spot than this is not used | 0–1 DTE: `225`, 2–4 DTE: `250` |
| Maximum shift | at most this far from the same-width strike | `150 pts` |

If no strike passes both guards on a side, that side keeps its same-width strike.
`strikes.mode: width` switches back to the original construction.

Width and hedge by DTE are the original's (500/200 at 0 DTE, 550/250 at 1 DTE, 600/300 at 2–4
DTE). Order: buy both hedges first, then sell both shorts.

## 6. Exit criteria (first match wins)

| Priority | Rule | Trigger |
|---|---|---|
| 1 | Kill switch | `algo kill`, or 5 errors in a row |
| 2 | Stale feed | no tick for 10 s |
| 3 | Stop loss | gross P&L ≤ −stop pts × lot size × lots, **stop by DTE** |
| 4 | Trailing profit stop | once gross P&L has reached +arm pts, exit if it falls give-back pts below its peak, **by DTE** |
| 5 | Premium blow-out | cost to close ≥ 1.35 × credit, or a short leg at 2× its entry |
| 6 | Target | gross P&L ≥ +target pts × lot size × lots, **target by DTE** |
| 7 | Time exit | 15:15 |

| DTE | Typical credit | Stop | Target | 2 lots (130): stop / target |
|---|---|---|---|---|
| 0 | 5–8 | 5 pts | 5 pts | −₹650 / +₹650 |
| 1 | 20–27 | 8 pts | 8 pts | −₹1,040 / +₹1,040 |
| 2–4 | 55–110 | 15 pts | 15 pts | −₹1,950 / +₹1,950 |

| DTE | Trail arms at | Give-back from the peak | 2 lots (130): arm / give-back |
|---|---|---|---|
| 0–1 | off | off | their 5- and 8-pt targets take profit first |
| 2–4 | 6 pts | 3 pts | +₹780 / ₹390 |

**Why a trailing stop:** on 5 Nov the live trade reached +₹1,254 gross at 12:52, then NIFTY's rally
took it to the stop for −₹2,002. With this rule it would have left at about +₹845 gross (the
real 5-second snapshots; arm ₹800, give back ₹400).

**Why these numbers:**
- **In points, by DTE, not rupees:** points scale with the lots, and a 0–1 DTE credit is far smaller.
- **Narrow trails cost more than they save.** Arming at ₹250–350 and giving back the same turned
  the seven days from 28 Oct to 5 Nov from +₹1,145 to −₹1,538 or worse, because normal swings in a
  winning trade set it off (entries at 09:45, 5 Nov at its real 09:53).
- **It can't help a trade that never made money.** On 29 Oct the best before the stop was +₹202.
- **It has a cost:** it cut 30 Oct from +₹1,821 to +₹266, a dip that later reached target.
- **It depends on the entry.** A 09:45 entry on 5 Nov peaked at only +₹546, so the trail never
  armed and the stop still hit.

The peak is held in memory, so a restart during a trade starts the trail again from that moment.

**Why wider than the original's 5 / 8:** on 3–4 DTE the credit is 75–110 pts, and a 5-pt stop
fired on ordinary noise. From 28 Oct to 4 Nov it turned three eventual winners into losses (28 Oct,
2 Nov, 4 Nov) and saved one (29 Oct). On real candles (`algo backtest --options real`, 28 Oct –
4 Nov) this algo makes +₹3,582 over six trades; the original makes −₹2,383 over four trades,
skipping two days. It ran on one trending week: review after 20 trading days.

## 7. Capital allocation

| Item | Rule |
|---|---|
| Lots | 2 in paper |
| Planned loss per trade | 2–4 DTE: stop ₹1,950 + charges ~₹210 ≈ **₹2,200**. 1 DTE ≈ ₹1,250, 0 DTE ≈ ₹850 |
| Worst case per trade | (hedge − credit) × 130: about ₹17k at 0 DTE (200 hedge) to ₹32k at 2–4 DTE (300 hedge), only if NIFTY gaps through the stop and the hedge |
| Max loss per day | one trade a day: one stop-out. Review any day worse than **₹2,500** by hand |
| Max loss per week | **₹5,000** → pause and review |
| Max loss per month | **₹10,000** → pause, re-check on paper |

Weekly and monthly limits are not automated; the dashboard shows the totals.

## 8. Logged every day

Everything the original logs (trade day, VIX, PCR, events, spreads, credit, fills, snapshots, exit,
P&L). Also:
- the premium-balance ratio and richer side as a log-only check;
- a `strikes` check: the mode, the target premium, the same-width shorts and the shorts chosen,
  with each one's distance from spot and mid price.

## 9. Testing it against the original

The laptop can't stay on until 15:30 at the office, so the comparison doesn't depend on it. Each
evening:

```bash
.venv/Scripts/algo data options
.venv/Scripts/algo backtest --algo iron_condor_balanced --from 2026-10-28 --options real
.venv/Scripts/algo backtest --algo iron_condor --from 2026-10-28 --options real
```

- Both algos run on the same real candles, so the results compare directly.
- `algo data options` needs that day's `algo login`, and must run by each expiry's evening
  ([backtest.md](../design/backtest.md), section 5a).
- **These backtests are the result to judge by**, not the morning paper run.
- A morning paper run is optional. It records real bid/ask for as long as the laptop is on. If the
  laptop shuts with a trade open, nothing watches its stop or squares it off, so that run's P&L
  means nothing.
- Kite allows 3 WebSocket connections per API key, and Monday, Tuesday and Friday afternoon already
  use 3. So run only one of the two condors live.

## 10. Open questions

1. After 20 paper days: compare with the original's replays. Is the gain from B4/B6, from the
   strikes or from the exits? Each part can be switched off in the config and replayed.
2. Stop and target on 2–4 DTE: 15 pts each, or scale them to the credit?
   Review the trail (arm 6, give back 3) on the same 20 days.
3. 0 DTE: is a 5–8 pt credit worth the ₹210 of charges, or skip expiry day?
