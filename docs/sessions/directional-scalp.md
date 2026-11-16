# Session prompt: NIFTY directional buying scalp (Renko)

Paste everything below the line into a new Claude Code session opened in
`E:\Algo_Trading\Algo\algo_options_trading`.

---

You are taking over the **NIFTY directional buying scalp**. It buys the ATM CE or PE when the
market leans one way, timed by a Renko chart. Paper, 2 lots, every trading day; **first paper day
is Monday 2 Nov 2026**.

## Read first

1. `docs/sessions/project-context.md`: the project, Kite rules, how we work, and the shared code.
2. `docs/strategy-criteria/directional-scalp.md`: the spec (bias score B1–B3, Renko trigger
   T1–T3, option checks, exits, day limits). **Section 10a has the two-year backtest results** and
   the v0.2 decision.
3. `docs/design/backtest.md`: the backtest engine, its known limits, and the **dashboard plan for
   backtests** (section 6, F-B1–F-B5, not built).
4. The code:
   - `src/algos/directional_scalp/` (`algo.py`, `bias.py`, `exits.py`, `settings.py`,
     `config.yaml`);
   - the dashboard panel `src/dashboard/static/pages/strategies/directional_scalp.js`;
   - `tests/algos/directional_scalp/`, `tests/core/test_renko_breadth.py`, `tests/backtest/`.
5. Shared pieces it introduced:
   - `trading_core/market/breadth.py` (weighted NIFTY 50 advances/declines);
   - `trading_core/marketdata/renko.py` (minute bars, Renko, VWAP);
   - `trading_core/backtest/` (the engine);
   - `config/universes/nifty50_weights.yaml` (index weights: **update monthly**).

## What it does now (config 0.2.0)

- **Bias score** every 5 s: weighted A/D of the 50 stocks (0.4), the CE/PE premium skew at ±300
  (0.3), and NIFTY futures vs VWAP (0.3).
  - CE at ≥ +0.5, PE at ≤ −0.5, held 2 minutes, and breadth must agree.
- **Trigger:** two **15-pt** Renko bricks (NIFTY spot, 1-minute closes) in the bias direction,
  formed after 09:45 or the last exit.
- **Order:** ATM option, 2 lots **as one order**.
- **Exits:** hard stop **14** option pts, Renko reversal, +30 target, **10-minute** time stop,
  bias flip (0.3 the other way), 11:30.
- **Day limits:** ≤ 5 trades 09:45–11:00, 2-minute cool-down, stop after 3 losses / −₹3,000 /
  profit lock 2,500 → 1,500 / 2 direction changes.
- **Mondays:** start at about 09:50 (the Kite connection limit; see the project context).

## How it got here

| Date | Commit | Change |
|---|---|---|
| 30 Oct 2026 | `f26962b` | Built v0.1 from the user's idea: A/D for direction, the CE/PE premium disparity that stops the iron condor, Renko on 1-minute for entries and stops, ≤ 5 trades before 11:00, 2 lots. Added live breadth (NIFTY 50 stock tokens in the daily snapshot; BSE replaced WIPRO from 30 Sep 2026), Renko/VWAP, partial exits (lot A +12 / lot B trailing), migration 0005 (previous closes, warm-up candles) |
| 31 Oct | `3577ac8` | **Backtest engine**: the real algo code on days rebuilt from Kite minute data (2 years of NIFTY, VIX, the 50 stocks, NSE bhavcopy chains). Kite serves no minute history for expired futures, so backtests compute B3 from the stocks' own VWAPs (index-weighted; 0.97 correlation with the futures VWAP gap over 45 days). B2 is off in backtests |
| 31 Oct | `3577ac8` | 7 variants. v0.1: −₹1.47 L (PF 0.71); the 8-pt stop sat inside normal pullbacks and charges outweighed the edge. Best: 15-pt bricks, 14-pt stop, 10-min time stop, one order: +₹35k (PF 1.09), but year 2 was −₹6k and only 11 of 24 months were positive. **Adopted as v0.2 for paper** |

## Open items

1. **The paper days are the real test.** Compare them with the backtest: ~41% win rate, PF 1.09
   (the Performance tab shows the 40% line).
2. **B2 (premium skew)** can't be backtested (no option history). Judge it on paper days: does the
   score with B2 pick better trades than without it?
3. B3 saturates: the NIFTY–VWAP gap averages about 35 pts against the 15-pt full scale. Try
   `vwap_full_pts` 30–40 in a backtest.
4. Parameter sweeps (`--set key=a,b,c`, B4) and the **backtest dashboard pages** (F-B1–F-B5 in
   `docs/design/backtest.md`). They are shared platform work, so ask the user whether they belong
   in this session.
5. NIFTY 500 breadth (logged only in the plan) is not built.
6. Be honest about overfitting: v0.2 came from trying 7 settings on the same two years. Fresh data
   (paper, or a later period) decides.
