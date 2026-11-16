# Session prompt: NIFTY short iron condor (intraday)

Paste everything below the line into a new Claude Code session opened in
`E:\Algo_Trading\Algo\algo_options_trading`.

---

You are taking over the **NIFTY short iron condor**, the platform's first strategy. It is
**paper trading every trading day**.

## Read first

1. `docs/sessions/project-context.md`: the project, Kite rules, how we work, and the shared code.
2. `docs/strategy-criteria/iron-condor.md`: the spec (entry checks E1–E6, construction, exits,
   capital, changes from v1, decisions and open questions).
3. `docs/design/dashboard-strategy-pages.md`, section 8: the noted **v3** idea.
4. The code: `src/algos/iron_condor/` (`algo.py`, `config.yaml`, `settings.py`, `filters.py`,
   `strike_selector.py`, `exits.py`) and `tests/algos/iron_condor/`.

## What it does now (config 0.3.0)

- **Trade days:** every trading day (`trade_window_days: 5`).
- **Entry 09:45–10:00:** sell OTM CE and PE at equal distance from ATM, buy further hedges.
  - Short width by days to expiry: `width_by_dte {0:500, 1:550, 2:600, 3:600, 4:600}`.
  - Hedge distance: `hedge_by_dte`, 300 on 3–4 DTE.
- **Sizing:** 2 lots, one trade a day.
- **Checks:** VIX, spread and minimum credit. The **CE/PE premium balance** (E4: the dearer side ÷
  the cheaper at the short distance from spot ≤ 1.5; otherwise wait and re-check until 10:00).
  PCR is logged only. The day's High-impact calendar events are logged only.
- **Exits:** kill switch, stale feed, stop and target in points, premium blow-out (1.5× credit or a
  short leg at 2× its entry), time exit 15:15.

## How it got here

| Date | Commit | Change |
|---|---|---|
| ~24 Oct 2026 | `f8193cf` | Platform built with this condor (v1): 08:15 day check, 09:08 pre-open breadth/gap filters, entry 09:20–09:35, last 3 days of the weekly cycle only |
| 26 Oct | `1f589eb` | **v2**: the laptop is on only from ~09:30, so the pre-open steps went. Entry 09:45–10:00; premium balance check added; PCR log-only; gap and breadth filters removed |
| 27 Oct | `19bd820` | 2 lots in paper (0.2.1); stop and target stay in points, loss limits doubled |
| 27 Oct | — | First paper trade. Its capital (₹11,966.50 estimate) was back-filled after capital recording was added |
| 28 Oct | `6e5de06` | Trade **every** trading day (0.3.0), widths by DTE |
| 29 Oct | `55c7d14` | Log-only check of the day's High-impact events from the market calendar |
| 30 Oct | — | Skipped: CE/PE premium imbalance 1.64 (puts richer). NIFTY then fell about 30 pts |

## Open items

1. **v3** (the user's idea): trailing target and trailing stop, and a 12:00 time exit instead of
   15:15, because afternoons tend to bring the volatility that hurts a condor. Write it into the
   spec first, then build.
2. Wider shorts or hedges on 3–4 DTE? Decide from the paper days (Performance tab, by DTE).
3. The premium-balance limit (1.5 for now), and the capital bucket and loss limits (spec section 7).
4. **Backtesting:** `algo backtest --algo iron_condor` runs this algo on rebuilt days (read
   `docs/design/backtest.md`).
   - Option prices are modelled (Black-76 on the previous day's IV smile, scaled by VIX), so E4
     sees only modelled skew.
   - For a seller, the model's smile and spreads matter more than for the directional scalp.
   - Treat results as rough, and say so.
