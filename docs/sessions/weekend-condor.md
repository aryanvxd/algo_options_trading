# Session prompt: NIFTY weekend iron condor (positional, Friday → Monday)

Paste everything below the line into a new Claude Code session opened in
`E:\Algo_Trading\Algo\algo_options_trading`.

---

You are taking over the **NIFTY weekend iron condor**: sell on Friday afternoon, buy back on
Monday morning, to collect the weekend's time decay. Paper, 1 lot.

## Read first

1. `docs/sessions/project-context.md`: the project, Kite rules, how we work, and the shared code.
2. `docs/strategy-criteria/weekend-iron-condor.md`: the spec (filters W1–W5, construction, exits,
   capital, platform pieces, open questions).
3. `docs/design/market-calendar.md`: the event data the weekend check (W3) reads.
4. The code:
   - `src/algos/weekend_condor/` (`algo.py`, `exits.py`, `settings.py`, `config.yaml`);
   - `tests/algos/weekend_condor/`;
   - `tests/core/test_market_events.py` (W3 tests);
   - the platform's positional support in `trading_core/strategy/base.py`: `carry_overnight`,
     `adopt()`, `carry_today()`, `product: NRML`, `report_when_done`;
   - `journal.open_positions_before`.

## What it does now (config 0.1.0)

- **Friday:** feed from 14:30, entry 14:45–15:10.
  - Shorts at about 1.2× the expected weekend move from VIX; hedges 200 pts further out.
  - Minimum credit 12, maximum spread 2.
  - Filters: VIX ≤ 18, premium balance ≤ 1.5, at least 3 calendar days to Monday.
  - **W3 events:** skip if a High-impact macro event falls between Friday 15:30 and Monday 09:15,
    or if a heavyweight (`results_symbols`, the top 10 NIFTY weights) reports results Friday–Sunday.
    If the calendar is stale it fails open unless `require_calendar`.
  - The position is held over the weekend (NRML).
- **Monday:** the run adopts the position and exits from 09:35 (spread ≤ 2), hard exit 09:50.
  Stop 10 pts, credit target 60%, blow-out 1.5× or a short leg at 2×.
- **Dashboard:** a carried trade shows on both days; its P&L counts on the exit day.

## How it got here

| Date | Commit | Change |
|---|---|---|
| 29 Oct 2026 | `216e527` | Built: positional platform support (carry, adopt, NRML margin lookup, report when done) and the Monday carry exit. W3 was log-only |
| 29 Oct | `55c7d14` | Market calendar (Forex Factory, NSE results, India YAML of RBI/CPI/GDP/IIP/WPI/Budget) → W3 enforced, including heavyweight results |
| 30 Oct | — | First Friday **skipped**: Reliance results on Fri 30 Oct (W3). No weekend trade yet |

## Open items

1. **No weekend has been traded yet.** Watch the first one end to end: Friday entry, carry, Monday
   adoption and exit, the dashboard on both days. Fix whatever misbehaves.
2. The spec's section 9 questions: expected-move multiple, hedge distance, credit target, lots.
3. **The Monday connection limit:** Kite allows 3 WebSocket connections, and Monday mornings are
   crowded (see the project context). If the platform gets a shared feed process, this algo is one
   of its users.
4. Keep `config/calendar/india_2026_27.yaml` current: India release dates, maintained by hand. The
   WPI dates and the Budget date still need the user's confirmation.
