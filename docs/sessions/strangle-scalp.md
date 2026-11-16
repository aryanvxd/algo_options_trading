# Session prompt: NIFTY long strangle scalp (intraday)

Paste everything below the line into a new Claude Code session opened in
`E:\Algo_Trading\Algo\algo_options_trading`.

---

You are taking over the **NIFTY long strangle scalp**: option buying, long volatility. It
**paper trades on Mondays and Tuesdays**.

## Read first

1. `docs/sessions/project-context.md`: the project, Kite rules, how we work, and the shared code.
2. `docs/strategy-criteria/long-strangle-scalp.md`: the spec (pair selection P1–P5, signal S1–S5,
   day limits, exits, charges, decisions).
3. The code:
   - `src/algos/strangle_scalp/` (`algo.py`, `pair_selector.py`, `entry_signal.py`, `settings.py`,
     `config.yaml`);
   - the dashboard panel `src/dashboard/static/pages/strategies/strangle_scalp.js`;
   - `tests/algos/strangle_scalp/`.

## What it does now (config 0.2.0)

- **When:** Mondays (1 DTE) and Tuesdays (expiry day), 09:45–10:30, up to 3 trades, 1 lot.
- **The pair:** an OTM CE and PE with matched premiums.
  - Expiry day: ₹15–30 a leg.
  - 1+ DTE: delta about 0.30 (0.25–0.35), the best-matched combination, by Black-76.
- **Signal:** buy both legs when the pair value V = mid(CE) + mid(PE) rises in 3 five-second
  windows in a row by ≥ 2%. The NIFTY futures move (S4) is logged, not required.
- **Exits:** stale feed 5 s, stop 4.6 pts, day loss ₹800, target 7.7 pts, trailing (from 4.6,
  keep half), stall 90 s, max hold 8 min, 10:45.
- **Day limits:** stop after 2 losses in a row or −₹800. Records the book every second.

## How it got here

| Date | Commit | Change |
|---|---|---|
| 27 Oct 2026 | `03a1b25` | Spec written. Pair by delta on 1+ DTE (₹20 pairs far OTM move mostly with IV); stop and target in points |
| 27 Oct | `839049b` | Built: pair selector, signal, multi-trade platform support (`max_trades`, cool-down, `reentry_block`), trailing/stall/max-hold rules, 1-second recording, capital per trade, the dashboard page |
| 28 Oct | — | First paper day (Wed). A second run the same day was deleted. Fixes: keep a pair while it still qualifies; no re-pick while the signal builds |
| 28 Oct | `6e5de06` | **v0.2:** Mondays and Tuesdays only (2–4 DTE pairs are dear and IV-heavy); S4 off (it lagged the entry) |
| 29 Oct | `55c7d14` | Logs Medium+ calendar events inside 09:45–10:45 (RBI at 10:00 falls in the window) |

## Open items

1. Tune N windows, the rise % and the stop/target from the paper logs: report **per DTE**.
2. D7, the opening-range breakout (logged): does it help?
3. **Backtesting this signal is not meaningful.** The engine (`algo backtest`) can run the algo,
   but the signal is a 5-second premium rise, and rebuilt days have 4 prices a minute and modelled
   options. Use the recorded 1-second replays (`algo run --replay <date>`) instead, and say so if
   asked.
4. Paper slippage for a 4-order scalp: compare paper fills with live quotes if the user goes live.
