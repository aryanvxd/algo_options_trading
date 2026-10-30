# CE/PE premium-skew directional option buying — note for later

- **Status:** folded into the [directional buying scalp](directional-scalp.md) on 30 Oct 2026,
  as one part of its bias score (B2). Kept for the history of the idea.
- **Last changed:** 26 Oct 2026.

## Idea

Compare a call and a put **at the same distance from spot** (for example CE at spot + 300 and PE
at spot − 300). If one side is clearly more expensive than the other, the market is pricing a
move in that direction: buy an option on the richer side (CE if calls are richer, PE if puts are
richer).

## Why the data is already being collected

The iron condor's E4 check logs the same CE/PE premium ratio every trade day, and the recorder
keeps 5-second bid/ask snapshots of the strikes around ATM. So this idea can be tested on our own
recorded data before writing any strategy code.

## To decide before building

- How to measure the imbalance: which distance from spot, mid or LTP, how to adjust for the
  normal put skew (puts are usually a little richer even on a flat day).
- Threshold that counts as a signal, and whether it must hold for some minutes, not one tick.
- Confirmation: NIFTY trend since open, PCR direction, VIX.
- What to buy: ATM or one strike OTM, which expiry.
- Exits: stop and target in premium points, trailing stop, time exit.
- Capital: option buying can lose the whole premium — size by premium paid, small fixed amount
  per trade.
- Timing: entry after 09:45 like every strategy here.
