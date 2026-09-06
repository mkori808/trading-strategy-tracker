# Market-Residual Momentum Regime & Drawdown Attribution

Window: 2021-09-01 to 2026-08-31. Minimum periods for a bucket/episode to count toward the verdict: 6. Drawdown materiality threshold: 5.0% SPY peak-to-trough. Volatility threshold: top 30% of trailing realized-vol percentile.

## Verdict

**BROAD-BASED** -- 2/2 materially-sampled bear/correction and high-vol buckets favor D on drawdown; 1/1 materially-sampled drawdown episodes favor D; top episode accounts for 37.92% of the total drawdown improvement across 6 improving episode(s).

**Power caveat -- read this before the verdict label above.** 7 SPY-anchored episodes were identified in total; only 1 of them clear the 6-period minimum this study pre-registered as the floor for an individually reliable result. Directionally (ignoring the power floor), 6/7 episodes show D drawing down less than C -- consistent with 'broad-based' as a DIRECTIONAL pattern, but the label above is certified by only the materially-sampled episode(s) (2022-01-03 to 2023-12-13) plus the regime-bucket cut, not by independently powered results for every episode. Treat the short episodes' own numbers as illustrative, not confirmatory.

## Regime distribution (rebalance periods)

Trend: {'bull': 35, 'sideways': 16, 'bear/correction': 9}. Volatility: {'low/normal vol': 39, 'high vol': 21}. Total periods: 60.

## Trend regime buckets

| Regime | Periods | Underpowered | C return | D return | C-D return contribution | C max DD (bucket) | D max DD (bucket) | Drawdown improvement |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| bear/correction | 9 | no | +12.46% | +4.57% | +7.89% | -15.03% | -11.57% | +3.46% |
| bull | 35 | no | +0.37% | +31.01% | -30.64% | -21.97% | -9.44% | +12.53% |
| sideways | 16 | no | +56.56% | +27.42% | +29.13% | -11.01% | -6.30% | +4.71% |

## Volatility regime buckets

| Regime | Periods | Underpowered | C return | D return | C-D return contribution | C max DD (bucket) | D max DD (bucket) | Drawdown improvement |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| high vol | 21 | no | +34.15% | +30.46% | +3.68% | -9.54% | -7.16% | +2.38% |
| low/normal vol | 39 | no | +31.73% | +33.80% | -2.07% | -23.26% | -15.90% | +7.36% |

## Drawdown episodes (SPY-anchored, >=5% peak-to-trough)

| Start | Trough | Recovery | SPY DD | C max DD | D max DD | Improvement | C-D return contribution | Periods | Underpowered |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2021-09-02 | 2021-10-04 | 2021-10-20 | -5.11% | -6.73% | -4.56% | +2.17% | +2.07% | 1 | yes |
| 2022-01-03 | 2022-10-12 | 2023-12-13 | -24.50% | -21.47% | -16.53% | +4.93% | -11.88% | 24 | no |
| 2024-03-27 | 2024-04-19 | 2024-05-14 | -5.35% | -7.96% | -6.77% | +1.19% | -0.52% | 2 | yes |
| 2024-07-16 | 2024-08-05 | 2024-09-19 | -8.41% | -9.48% | -4.44% | +5.05% | -1.65% | 2 | yes |
| 2025-02-19 | 2025-04-08 | 2025-06-26 | -18.76% | -20.79% | -9.70% | +11.09% | -15.57% | 4 | yes |
| 2025-10-29 | 2025-11-20 | 2025-12-10 | -5.07% | -7.74% | -2.93% | +4.82% | +3.48% | 2 | yes |
| 2026-01-27 | 2026-03-30 | 2026-04-14 | -8.88% | -3.64% | -8.25% | -4.61% | +28.16% | 3 | yes |

Concentration check: 6 episode(s) improved drawdown at all; the single largest accounts for 37.92% of the total improvement, the largest two for 55.18%.

This is a diagnostic attribution, not a validation gate. It does not change Market-Residual Momentum's 'Interesting, unresolved' verdict in research/frozen_research_report.md, its parameters, or its leaderboard status.