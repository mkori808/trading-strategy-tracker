# Dual Momentum Portfolio Contribution / Concentration Audit

Canonical DM and MRM were replayed unchanged. No alternative weights were tested.

## Decision

**One narrow weighting experiment justified: NO.**

Equal weighting appears reasonable, No stable portfolio-construction inefficiency detected

DM does exhibit intermittent unequal risk allocation, but it is not a stable uncompensated inefficiency: the highest-risk buckets earned positive subsequent returns, rank ordering was not reliable, and major losses were generally broad. Therefore no weighting concept clears the preregistered follow-up gate.

## Canonical reconciliation

| Strategy | Rebalances | Return | CAGR | Max DD | Sharpe | Replay error |
|---|---:|---:|---:|---:|---:|---:|
| DM | 61 | 134.44% | 18.52% | 20.54% | 0.75 | $8.36e-12 |
| MRM | 61 | 87.19% | 13.34% | 16.53% | 0.68 | $6.00e-12 |

Selections and daily equity changes reconcile exactly to the registered engines; costs and cash interest are retained as separate attribution lines.

## DM rank contribution

| Rank | N | Mean return | Median | Win rate | Arithmetic contribution | Worst | Best |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 61 | 3.10% | 1.25% | 57.4% | 44.91% | -34.52% | 107.11% |
| 2 | 61 | 1.74% | 0.31% | 54.1% | 26.09% | -21.36% | 24.45% |
| 3 | 60 | 0.39% | -0.15% | 48.3% | 4.72% | -15.49% | 24.43% |
| 4 | 60 | 1.37% | 1.13% | 53.3% | 16.44% | -15.28% | 21.85% |
| 5 | 60 | 0.43% | 0.50% | 55.0% | 5.12% | -21.75% | 23.95% |

Spearman(rank, next return) = -0.032, clustered 95% CI [-0.147, 0.079], effective N=61 months. Rank 1 minus rank 5 averaged 2.34%, CI [-1.74%, 7.61%]. Rank 1 therefore does not reliably outperform rank 5.

## Security contribution

Top positive contributor: INTC (34.86% arithmetic contribution). Positive-contribution concentration: top 1 26.6%, top 3 50.3%, top 5 67.7%, top 10 90.2%.

The machine-readable table includes every name and explicitly labels leave-one-security-out as attribution, not a capital-reallocation counterfactual. The largest five negative cumulative contributors were TRV (-3.04%), IBM (-3.85%), CSCO (-4.90%), AMGN (-5.77%), DOW (-7.76%).

## Ex-ante risk and equal-weight efficiency

The covariance estimate uses 60 aligned closes strictly before each rebalance. Variance contributions are signed; HHI normalizes their absolute magnitudes.

Average maximum single-name risk share was 34.2%; average top-two share 57.3%; HHI 0.256; ex-ante volatility 18.6%. A 20% capital weight differed from risk weight by 7.3% on average (5.1% median; 44.2% maximum).
At least one name exceeded 30% / 40% / 50% of variance in 47.5% / 19.7% / 16.4% of months. This concentration was intermittent, and the >50% bucket subsequently earned 4.41% on average (10 observations), so disproportionate risk was not uncompensated in this sample. Moreover, >40% events were concentrated by name (INTC: 10, CVX: 1, MMM: 1), failing the leave-one-name stability requirement.

## Major drawdowns

| Episode | Largest-name loss | Top-two loss | Pre-DD corr | Realized corr | Interpretation |
|---|---:|---:|---:|---:|---|
| 2021-12-29 to 2022-06-17 | 20.4% | 34.9% | 0.39 | 0.36 | broad |
| 2022-11-30 to 2023-05-31 | 20.5% | 35.6% | 0.44 | 0.33 | broad |
| 2025-02-19 to 2025-04-08 | 21.2% | 40.1% | 0.37 | 0.64 | broad |

All three episodes were broad by security contribution. Correlation rose sharply only in the 2025 episode (0.37 pre-drawdown to 0.64 realized), not consistently across episodes.

## Worst days and single-name tails

Across the worst 10 days, the largest name supplied 45.1% of constituent losses on average and the top two 70.5%. The worst 20 event rows include holdings, SPY return, and trailing PIT correlation.
The worst 1% of negative security-days produced 9.8% of all negative security P&L. One name exceeded half of gross constituent loss on 48.7% of negative days; this share is mechanically easier to exceed when other holdings offset the loss, so it is descriptive rather than a trading threshold.

## Weight drift and correlation

The daily maximum position averaged 21.7%, peaked at 51.8%, exceeded 25% on 3.5% of sessions and 30% on 2.1%. Ending weights averaged 21.1% for winners versus 19.2% for losers. Drift resets at each monthly rebalance.
Average PIT pairwise correlation was 0.29. Its Spearman relationship with subsequent realized portfolio volatility was -0.39; it did not provide a stable positive warning signal.

## Compact DM versus MRM

| Diagnostic | DM | MRM |
|---|---:|---:|
| Mean max risk contribution | 34.2% | 26.8% |
| Risk HHI | 0.256 | 0.214 |
| Average pairwise correlation | 0.29 | 0.37 |
| Worst-10 top-two loss share | 70.5% | 55.8% |
| Days max weight >25% | 3.5% | 0.0% |

MRM had more balanced individual risk and worst-day loss attribution despite higher average pairwise correlation. That can help explain part of the frozen blend's diversification benefit, but does not authorize any blend change.

## Stability and blockers

DM rank Spearman was 0.030 in the first half and -0.091 in the second. Months with >40% single-name risk rose from 3.3% to 35.5%, but the later result is overwhelmingly one security rather than a cross-name effect. Every leave-one-year-out rank estimate remained close to zero (see JSON). Security concentration is disclosed through full leave-one-name-out attribution.

PIT sector analysis is blocked: the repository has no genuine point-in-time sector classification ledger. Current classifications were not substituted.

## Artifacts

`results.json` is the machine-readable result. CSV ledgers contain monthly holdings (scores, ranks, start/end weights and outcomes), ex-ante risk contributions, risk-month summaries, exact daily P&L/returns, and daily weights for both strategies.
