# Archived strategies

Strategies retired from the app's default dashboard/leaderboard view on
**2026-07-20** after a large-enough sample showed decisively negative
results. This is a visibility change only — see
`strategies/registry.py:ARCHIVED_STRATEGY_NAMES`'s docstring:

- Every archived strategy is still in `strategy_tracker.xlsx` and the code
  registry (`strategies/registry.py`'s `DAY_TRADING_STRATEGIES` /
  `SWING_TRADING_STRATEGIES_NO_BENCHMARK` / `PAIRS_STRATEGY_NAMES`), fully
  runnable, and still logs to `engine/logging_db.py` exactly as before.
- The webapp hides them by default (Compare tab's `StrategyTable`, Lab
  tab's `StrategyPicker`) behind a "Show archived (N)" toggle rather than
  removing them from the UI entirely.
- Nothing was deleted from `strategy_tracker.xlsx` — CLAUDE.md's rule that
  the tracker is imported, not duplicated or written back to, still holds;
  archiving is a dashboard-visibility decision, not a tracker edit.

Full backtest methodology, look-ahead safeguards, and filter-comparison
detail for each of these live in `LESSONS.md`; this file is the compact
reference list, not a replacement for that history.

## Removed: large sample, consistently negative expectancy

| Strategy | Trades | Expectancy (R) |
|---|---:|---:|
| Opening Range Breakout (ORB) | 6,121 | -0.012 |
| VWAP Bounce / Reversion | 104,672 | -0.107 |
| Scalping (3-5 min) | 21,774 | -0.200 |
| Mean Reversion Scalp | 419 | -0.102 |
| News Fade | 1,047 | -0.260 |
| Range Trading | 3,293 | -0.066 |
| Fibonacci Retracement Entry | 421 | -0.045 |
| Gap Fade (daily) | 328 | -0.150 |
| Turnaround Tuesday | 346 | -0.034 |

## Removed: negative return on the portfolio engine

| Strategy | Return | Sharpe |
|---|---:|---:|
| Pairs / Stat Arb | -13.2% | -0.83 |

(No R-multiple trades — this engine holds a two-leg spread position, not
discrete bracket trades; see `engine/pairs.py`.)

## What's still active (not archived)

Every other tracker strategy stays in the default dashboard view,
including several with a small or borderline sample that isn't yet enough
to call either way (Momentum / Gap and Go, Earnings Momentum / Gap-Hold,
Anchored VWAP Breakout, Pivot-Level ETF Reversal) — CLAUDE.md's own rule
against presenting an under-30-trade result as reliable cuts both ways:
it's also not grounds to call one dead.

**Dual Momentum clears the project's shortlist bar as of 2026-07-20**
(Sharpe 0.575, +133.1% return, beats SPY's own +82.6% over the same
window) — the first strategy in this project to do so on its own
canonical run. This followed a robustness-testing round (26-year history,
4 universes, rebalance-frequency and lookback-window sweeps, all
validated against data independent of whatever window found them) that
led to shortening the strategy's momentum lookback from 252 to 189
trading days, now the registered default in
`strategies/swing/dual_momentum.py`. Two follow-up ideas (a volatility-
targeting overlay, more concentrated positions) were tested in good faith
and rejected after failing the same out-of-sample validation. See
`LESSONS.md`'s 2026-07-20 entries, especially (cont'd 6) through
(cont'd 9), for the full detail.

## Un-archiving

If a change to a strategy's rules or a new market regime makes one of
these worth re-evaluating: re-run it canonically (it never stopped being
runnable), remove its entry from `ARCHIVED_STRATEGY_NAMES` in
`strategies/registry.py`, and update this file's tables to reflect the new
result instead of deleting the old row — keep the "what we tried" record
intact even when a verdict changes.
