# Trade Lifecycle / Exit-Efficiency Audit

Canonical strategies were replayed unchanged. This is a descriptive audit, not an optimization.

## Strategy summary

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Completed positions | 85.00 | 60.00 |
| Median holding period (sessions) | 42.00 | 62.50 |
| Median MFE | 5.98% | 5.50% |
| Median MAE | -5.30% | -4.80% |
| Median winner MFE capture | 62.79% | 78.27% |
| Median winner giveback | 5.91% | 2.57% |
| Post-exit 20d return | 0.92% | 1.33% |
| Replacement win rate | 54.32% | 62.07% |
| Avg replacement advantage | 0.93% | 0.83% |

## Data and lifecycle reconciliation

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Rebalances | 61 | 61 |
| All entries | 90 | 65 |
| Completed + open | 90 | 65 |
| Open positions excluded | 5 | 5 |
| Symbols with bars | 29 | 29 |
| Duplicate sessions | 0 | 0 |
| Missing OHLC rows | 0 | 0 |

## Winner table

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Positions | 43.00 | 33.00 |
| Mean realized | 10.58% | 12.96% |
| Median MFE | 11.83% | 13.37% |
| Mean giveback | 7.20% | 3.26% |
| Within 10% of MFE | 16.28% | 30.30% |
| Early spike/decay | 4.65% | 6.06% |

## Loser table

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Positions | 42.00 | 27.00 |
| Median loss | -5.77% | -4.34% |
| Median MAE | -8.98% | -8.11% |
| First meaningful loss (sessions) | 5.50 | 5.00 |
| Once profitable | 61.90% | 77.78% |
| Material recovery | 54.76% | 74.07% |

## Replacement table

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Events | 81.00 | 58.00 |
| Win rate | 54.32% | 62.07% |
| Average advantage | 0.93% | 0.83% |
| Median advantage | 1.21% | 1.82% |
| P10 | -10.97% | -10.34% |
| P90 | 12.81% | 8.92% |

## Entry-efficiency table

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Mean first-5d return | -0.01% | 0.16% |
| Median first-5d MAE | -1.27% | -0.50% |
| Median first-5d MFE | 1.25% | 1.20% |
| MAE <= -2% | 34.12% | 25.00% |

## Post-exit continuation

| Horizon/group | Dual Momentum | MRM |
|---|---:|---:|
| 5d all | 0.12% | -0.02% |
| 5d winners | -0.29% | 0.10% |
| 5d losers | 0.55% | -0.15% |
| 5d largeWinners | 0.75% | 1.84% |
| 5d largeLosers | 1.73% | -1.98% |
| 10d all | -0.05% | 0.09% |
| 10d winners | -0.20% | -0.23% |
| 10d losers | 0.11% | 0.48% |
| 10d largeWinners | 0.50% | 2.39% |
| 10d largeLosers | 3.74% | -0.93% |
| 20d all | 0.92% | 1.33% |
| 20d winners | 1.28% | 0.60% |
| 20d losers | 0.56% | 2.16% |
| 20d largeWinners | 0.26% | 3.94% |
| 20d largeLosers | 4.88% | 3.07% |
| 40d all | 1.21% | 2.13% |
| 40d winners | 2.42% | 1.25% |
| 40d losers | -0.03% | 3.14% |
| 40d largeWinners | -0.19% | 5.70% |
| 40d largeLosers | 9.32% | 4.13% |

## Winner capture distribution

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Mean | 56.17% | 69.44% |
| P25 | 18.84% | 40.22% |
| Median | 62.79% | 78.27% |
| P75 | 82.73% | 91.63% |
| P90 | 92.33% | 99.24% |
| Giveback >25% of MFE | 69.77% | 42.42% |
| Giveback >50% of MFE | 34.88% | 27.27% |
| Giveback >75% of MFE | 30.23% | 12.12% |

## Rank-transition table

| Strategy / bucket | N | Outgoing next | Incoming | Advantage |
|---|---:|---:|---:|---:|
| Dual Momentum / just outside boundary | 63 | -0.25% | 0.97% | 1.21% |
| Dual Momentum / moderate deterioration | 18 | 0.55% | 0.51% | -0.04% |
| Market-Residual Momentum / just outside boundary | 54 | 1.36% | 2.25% | 0.90% |
| Market-Residual Momentum / moderate deterioration | 3 | 5.50% | 3.73% | -1.77% |
| Market-Residual Momentum / severe/unranked | 1 | -5.42% | -0.33% | 5.09% |

## Retained vs new vs outgoing

| Strategy / group | N | Mean next-period return | Median |
|---|---:|---:|---:|
| Dual Momentum / new | 89 | 1.03% | 0.35% |
| Dual Momentum / outgoing | 84 | 0.34% | 0.52% |
| Dual Momentum / retained | 208 | 1.54% | 0.25% |
| Dual Momentum / universe_median | 60 | 0.80% | 0.80% |
| Market-Residual Momentum / new | 63 | 2.08% | 1.01% |
| Market-Residual Momentum / outgoing | 58 | 1.46% | 1.67% |
| Market-Residual Momentum / retained | 237 | 0.87% | 0.55% |
| Market-Residual Momentum / universe_median | 60 | 0.80% | 0.80% |

## Winner vs loser replacements

| Strategy / outgoing state | N | Win rate | Average advantage | Median |
|---|---:|---:|---:|---:|
| Dual Momentum / loser | 35 | 62.86% | 1.43% | 1.68% |
| Dual Momentum / near-flat | 5 | 100.00% | 10.65% | 12.56% |
| Dual Momentum / winner | 41 | 41.46% | -0.68% | -1.61% |
| Market-Residual Momentum / loser | 25 | 68.00% | 0.36% | 1.70% |
| Market-Residual Momentum / near-flat | 3 | 66.67% | -1.15% | 5.07% |
| Market-Residual Momentum / winner | 30 | 56.67% | 1.42% | 1.85% |

## Large tails

| Strategy / subset | N | Realized | MFE | MAE | Post-exit 20d | Replacement advantage |
|---|---:|---:|---:|---:|---:|---:|
| Dual Momentum / large winners | 11 | 27.45% | 39.26% | -3.08% | 0.26% | 0.76% |
| Dual Momentum / large losers | 11 | -12.96% | 0.47% | -12.25% | 4.88% | -2.46% |
| Market-Residual Momentum / large winners | 9 | 29.00% | 33.97% | -0.23% | 3.94% | -2.05% |
| Market-Residual Momentum / large losers | 7 | -10.73% | 1.78% | -12.51% | 3.07% | 0.17% |

## Dependence-aware magnitude

| Metric | Dual Momentum | MRM |
|---|---:|---:|
| Replacement raw N | 81 | 58 |
| Replacement effective N | 53 | 43 |
| Cluster-bootstrap mean CI | -1.28% to 3.19% | -1.26% to 2.98% |
| Equal-slot replacement contribution / year | 3.03% | 1.93% |
| Winner giveback hindsight ceiling | 61.88% | 21.50% |

## Major Dual Momentum drawdowns

| Peak to trough | Drawdown | Replacement advantage | Winner giveback | Worst exited-position MAE |
|---|---:|---:|---:|---:|
| 2025-02-19 to 2025-04-08 | -20.54% | 0.57% | 15.25% | -1.69% |
| 2022-11-30 to 2023-05-31 | -14.59% | -2.14% | 4.14% | -10.39% |
| 2021-12-29 to 2022-06-17 | -13.93% | 3.66% | 10.92% | -18.26% |

## Diagnostic conclusion

- Dual Momentum has a real winner-giveback signature (mean 7.20 points; median capture 62.79%), but broad turnover is not destructive: replacements beat outgoing names by 0.93 points on average and just-outside-boundary replacements add 1.21 points.
- DM replacement quality depends on outgoing state. Replacing winners loses 0.68 points on average, while replacing losers gains 1.43 points. This is the clearest candidate lifecycle inefficiency, but the aggregate replacement CI includes zero.
- MRM is more lifecycle-efficient overall: higher winner capture, lower giveback, lower MAE, longer holds, and less turnover. Its narrow weakness is the large-winner tail: those names continue +3.94% over 20 sessions and beat their replacements by 2.05 points over the next rebalance window (only seven paired events).
- Losers become negative early, but a majority recover materially before exit (DM 54.76%; MRM 74.07%). That is evidence against inferring a stop-loss rule from early drawdown alone.
- Entries do not show strong exhaustion: mean first-five-session returns are approximately flat for DM and positive for MRM, with modest median adverse excursion.
- Evidence is sufficient to justify a separate, narrowly preregistered winner-retention experiment, especially for DM winner replacements and MRM's large-winner tail. It does not justify deployment, a generic turnover reduction, or any stop/target search.


## Methodology and caveats

- Rankings use pre-session information and all entry/removal executions use the rebalance open.
- Close MFE/MAE includes executable entry and exit opens; intraday high/low fields are separate and exclude the exit session.
- Simultaneous securities are not independent regime observations. Replacement confidence intervals resample whole rebalance clusters; effective N is the number of contributing rebalance dates.
- Pairing several outgoing and incoming names is bookkeeping, not a causal identity. Set-average turnover results are pairing-independent.
- Completed positions exclude names still held at the end date; post-exit horizons require complete data.
- R-multiples are not applicable because neither portfolio strategy defines an initial risk unit.
- Hindsight best exits and the -2% loss landmark are diagnostic references only; no alternative rule was simulated.
