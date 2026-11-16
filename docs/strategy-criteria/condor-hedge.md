# NIFTY condor move hedge

Status: v0.1.0, built 11 Nov 2026, paper (not started live yet). Code: `src/algos/condor_hedge/`.
Study behind it: `data/backtests/buyside/` (scripts and results).

## 1. Idea

The balanced condor loses when NIFTY runs away from where it was sold (11 Nov 2026: −300 pts after
12:00, short PE stopped). This buyer earns on exactly that run: once NIFTY is far enough from the
condor's entry, buy an OTM option in the direction it is running and hold it while the run lasts.
It is a hedge for the condor's bad days, so it costs a little on the condor's good days.

## 2. Instrument and trade days

NIFTY weekly options, nearest expiry (the condor's). Every trading day the condor trades.

## 3. Schedule

| Step | Time |
|---|---|
| Feed | 09:15 (past-due on a 09:35 start) |
| Anchor | the condor's entry (live) / the 09:49 close (backtests) |
| Entries | 10:00 – 14:45 |
| Square-off | 15:15 |

## 4. Entry criteria

1. **Anchor.** `anchor.source: condor`: NIFTY at `iron_condor_balanced`'s entry, read from the
   journal. No condor entry by 10:30 → nothing to hedge → day skipped. `source: time`: the 09:49
   candle's close (backtests, where the condor is not in the backtest's database).
2. **Move.** NIFTY's 1-minute close is at least `move_pts` (80) from the anchor, on `confirm_bars`
   (1) closes in a row. Up → CE, down → PE.
3. **Option.** `otm_strikes` (2) strikes out of the money from ATM in the move's direction; quote
   at most 5 s old, spread ≤ 1.0 pt, premium ≥ ₹5.

## 5. Position

One leg: BUY `lots` (1) × lot size of that option, market order through the paper broker.

## 6. Exit criteria (first match wins)

1. Kill switch · 2. Stale feed (10 s) · 3. **Premium stop:** bid 30% below entry ·
4. Day loss ₹3,000 · 5. **NIFTY trail:** NIFTY gives back `trail_pts` (80) from its best 1-minute
close since entry · 6. 15:15.

## 7. Capital

`capital: 15000` (1 lot of a ₹100–200 option plus a buffer). One trade a day (`max_trades: 1`).

## 8. Logged

Check `anchor` (level, where it came from, both trigger levels); check `trigger` (move, option,
premium); the dashboard's live levels show anchor, move, streaks, best close and the trail level.

## 9. Evidence so far (10 real-candle days, 28 Oct – 11 Nov 2026)

| | Condor alone | Condor + hedge (v0.1 defaults) |
|---|---|---|
| 10-day net | −₹4,909 | **+₹7,061** |
| Worst day | −₹2,712 | **−₹1,348** |
| Condor's 4 losing days | −₹9,129 | hedge +₹17,192 on them (won all 4) |
| Condor's 6 winning days | +₹4,220 | hedge −₹5,221 on them (lost all 6) |

- Neighbourhood (36 settings): every move of 80 pts beat the condor alone. Every move of 60 pts was worse
  (early noise uses up the one trade). A 30% premium stop helped in every pairing.
- **Limits.** Ten days, all in one +12% rally. One day (6 Nov, +₹9,513) is most of the hedge's
  profit. On a range-bound fortnight it would mostly pay its ₹300–1,600 cost per day. Re-judge
  after about 20 more real days before trusting it with more than 1 lot.
