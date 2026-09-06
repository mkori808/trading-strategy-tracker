# Market-Residual Momentum Attribution Ladder

Window: 2021-09-01 to 2026-08-31. Universe: dow_pit. Canonical config: {'lookback': 126, 'skipDays': 5, 'topN': 5, 'rebalanceFrequency': 'monthly'}.

## Ladder

| Rung | Cumulative return | CAGR | Sharpe | Max DD |
|---|---:|---:|---:|---:|
| A: PIT Dow equal-weight | +70.29% |  |  |  |
| B: concentration-matched random top-N (mean) | +71.26% |  |  |  |
| C: plain momentum (control) | +76.36% | +12.09% | 0.43 | 26.06% |
| D: canonical Market-Residual Momentum | +74.93% | +11.82% | 0.57 | 16.53% |

SPY (identical window): +82.20%. PIT equal-weight: +70.29%.

## Incremental effect of residualization (D minus C)

- Cumulative return delta: -1.43 pp
- CAGR delta: -0.28 pp
- Sharpe delta: +0.14
- Max drawdown delta: -9.52 pp
- Paired rebalance periods: 60

Paired bootstrap (5000 draws over 60 rebalance periods): point estimate -13.92%, 90% interval [-59.70%, +70.73%], 38.2% of draws positive.

Rung E (residual momentum + absolute-momentum filter) does not exist as a distinct configuration of the canonical strategy -- D and F are the same run. See the preregistration's notApplicable.E.

This is an attribution diagnostic, not a validation gate. It does not by itself change Market-Residual Momentum's 'Interesting, unresolved' verdict in research/frozen_research_report.md.