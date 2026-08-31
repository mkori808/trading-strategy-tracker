# Optimized Dual Momentum Forward SPY-Underperformance Log

This is an account-level paper-forward diagnostic. Underperformance means the completed-session account return minus the aligned SPY return was below zero.

## Current read

- 6 of 9 aligned completed sessions underperformed SPY (66.7%).
- Mean daily active return was -0.09%.
- Compounded account return was -1.32% versus -0.43% for SPY, a -0.89-percentage-point gap.
- The worst relative session was 2026-08-27 at -2.46%; mean active return excluding that day was +0.20%.
- All 6 relative misses were also absolute-loss days; all 3 outperforming days were profitable.
- Only 2 session(s) are sealed in the preregistered ledger; no inference or trading rule is permitted at this maturity.
- Sessions through August 26 are exploratory context; later settled sessions remain distinct as locked or pending prospective observations.

## Underperformance days

| Date | Account | SPY | Active | Absolute loss? | T-1 state | Trend | High vol | High dispersion | Evidence |
|---|---:|---:|---:|---|---|---|---|---|---|
| 2026-08-18 | -1.81% | -0.68% | -1.14% | yes | bullish persistence | above 200SMA | yes | no | exploratory pre preregistration |
| 2026-08-19 | -0.27% | +0.21% | -0.48% | yes | bullish persistence | above 200SMA | yes | no | exploratory pre preregistration |
| 2026-08-25 | -0.71% | +0.32% | -1.03% | yes | bullish persistence | above 200SMA | yes | no | exploratory pre preregistration |
| 2026-08-26 | -0.16% | +0.02% | -0.19% | yes | bullish persistence | above 200SMA | yes | no | exploratory pre preregistration |
| 2026-08-27 | -1.80% | +0.66% | -2.46% | yes | bullish persistence | above 200SMA | yes | no | prospective locked |
| 2026-08-28 | -0.46% | -0.23% | -0.23% | yes | bullish persistence | above 200SMA | no | no | prospective locked |

## Base-rate comparisons

Looking only at bad days would be misleading. These rows compare each condition's underperformance rate with its absence.

| Condition | N true | Underperform rate true | N false | Underperform rate false | Difference | Timing |
|---|---:|---:|---:|---:|---:|---|
| SPY above 200-day SMA at T-1 | 9 | +66.7% | 0 | n/a | n/a | known by T minus 1 |
| High SPY volatility at T-1 | 8 | +62.5% | 1 | +100.0% | -37.5% | known by T minus 1 |
| High cross-sectional dispersion at T-1 | 1 | +0.0% | 8 | +75.0% | -75.0% | known by T minus 1 |
| SPY down during session | 4 | +50.0% | 5 | +80.0% | -30.0% | same session descriptive |

## Interpretation

With fewer than 20 aligned sessions--and only 2 currently sealed prospective observation(s)--this can identify dates and data-quality issues, but it cannot identify a repeatable market condition. Every current session was bullish persistence and above the 200-day SMA, so the forward record has no trend-state contrast. Same-session SPY direction is descriptive, never predictive. T-1 conditions may become testable only after substantially more locked observations. No order, signal, or sizing rule changed.
