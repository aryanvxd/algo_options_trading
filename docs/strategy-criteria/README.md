# Strategy criteria

One file per strategy: what has to be true to enter, what makes it exit, and how much capital it
may use. These files are the spec; the numbers in each algo's `config.yaml` must match them.
When a rule changes, update the file here first, then the config/code.

| Strategy | File | Type | Status |
|---|---|---|---|
| NIFTY short iron condor | [iron-condor.md](iron-condor.md) | option selling, non-directional | v2 implemented, paper |
| NIFTY long strangle scalp | [long-strangle-scalp.md](long-strangle-scalp.md) | option buying, long volatility, up to 3 trades 09:45–10:30 | v0.1 implemented, paper from 28 Oct 2026 |
| CE/PE premium-skew directional buy | [premium-skew-option-buying.md](premium-skew-option-buying.md) | option buying, directional | idea only — build later |

## Daily constraint that applies to every strategy

The laptop is switched on at about **09:30–09:35 IST** (after reaching office). So:

- No step may need anything before 09:35 — no 08:15 day check, no 09:08 pre-open snapshot.
- `algo login` and `algo run` are started together at about 09:35; the algo works out the rest
  on startup.
- Earliest entry for any strategy is **09:45**.
- The laptop must stay awake and online until square-off (15:15) — see the power settings note
  in the main README.

## Template for a new strategy file

```
# <Strategy name>
Status / version / last changed
1. Idea (2–3 lines)
2. Instrument and trade days
3. Schedule (start, entry window, last entry, square-off)
4. Entry criteria (every check, with its threshold)
5. Position construction (strikes, legs, order of execution)
6. Exit criteria (priority order, first match wins)
7. Capital allocation (capital, lots, per-trade / daily / weekly / monthly loss limits, scaling)
8. What is logged
9. Open questions
```

## Capital allocation — common rules

Each strategy gets its own capital bucket; a strategy can never borrow from another bucket.

1. **Size by the worst case, not by the stop.** Stops can be jumped by a gap or a fast move, so
   lots are sized from the position's maximum loss (for spreads: width of the hedge minus credit).
2. **Loss limits at four levels** — per trade, per day, per week, per month. Hitting a weekly or
   monthly limit pauses the strategy until it is reviewed; it is not automatically resumed.
3. **Scale slowly.** Add 1 lot only after the conditions in the strategy's own file are met, and
   change only one parameter at a time.
4. **Paper first.** Every strategy runs paper for at least 18–24 trade days before real money, at
   the same lot size it will use live.
