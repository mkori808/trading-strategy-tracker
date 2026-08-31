# Dual Momentum Replacement-Quality Decomposition

Canonical Dual Momentum was not modified. The closed Winner Grace hypothesis was not reopened.

## Event reconciliation

| Metric | Result |
|---|---:|
| Completed pairs | 81 |
| Effective rebalance N | 53 |
| Censored final pairs | 1 |
| Unpaired removals / additions from cash changes | 3 / 3 |
| Missing-price exclusions | 0 |
| Duplicate pairs | 0 |

## Replacement advantage

| Metric | Result |
|---|---:|
| Mean | 0.93% |
| Median | 1.21% |
| Win rate | 54.32% |
| Cluster 95% CI | -1.33% to 3.24% |
| P10 / P90 | -10.97% / 12.81% |

## Natural rank-gap buckets

| Bucket | N | Effective N | Mean advantage | Win rate |
|---|---:|---:|---:|---:|
| small (1-2) | 18 | 17 | 1.73% | 55.56% |
| medium (3-5) | 36 | 26 | 0.04% | 50.00% |
| large (6+) | 27 | 19 | 1.59% | 59.26% |

## Score-spread quartiles

| Quartile | Range | Mean advantage | Win rate |
|---|---:|---:|---:|
| Q1 | 0.007 to 0.059 | 1.87% | 52.38% |
| Q2 | 0.060 to 0.117 | -0.81% | 55.00% |
| Q3 | 0.119 to 0.163 | 3.26% | 60.00% |
| Q4 | 0.164 to 0.492 | -0.64% | 50.00% |

Score-spread Spearman: -0.09 (cluster 95% CI -0.33 to 0.14).
Rank-gap Spearman: -0.05 (cluster 95% CI -0.28 to 0.17).

## Outgoing rank deterioration

| Bucket | N | Mean advantage | Win rate |
|---|---:|---:|---:|
| barely (<=2) | 22 | 0.95% | 50.00% |
| moderate (3-5) | 31 | 0.61% | 54.84% |
| sharp (6+) | 28 | 1.28% | 57.14% |

## Incumbent state

| State | N | Effective N | Mean advantage | Win rate |
|---|---:|---:|---:|---:|
| currently profitable | 46 | 35 | 0.54% | 47.83% |
| currently unprofitable | 35 | 29 | 1.45% | 62.86% |
| winner near MFE | 18 | 15 | -1.69% | 38.89% |
| winner high giveback | 19 | 18 | 4.38% | 63.16% |
| loser meaningful recovery | 14 | 13 | 0.28% | 50.00% |
| loser near MAE | 18 | 15 | 2.64% | 72.22% |

## Recent-path diagnostics

| Diagnostic | Spearman | Cluster 95% CI |
|---|---:|---:|
| outgoing_return_20d | 0.03 | -0.16 to 0.24 |
| incoming_return_20d | -0.04 | -0.27 to 0.19 |
| outgoing_return_60d | -0.09 | -0.32 to 0.15 |
| incoming_return_60d | -0.17 | -0.41 to 0.08 |
| outgoing_acceleration | 0.15 | -0.06 to 0.36 |
| incoming_acceleration | 0.06 | -0.18 to 0.31 |

## Replacement archetypes

| Archetype | N | Mean advantage | Win rate |
|---|---:|---:|---:|
| strong upgrade | 22 | -0.12% | 54.55% |
| marginal churn | 18 | 1.73% | 55.56% |
| hot entrant | 21 | 2.36% | 52.38% |
| decaying incumbent | 21 | 1.14% | 57.14% |
| loser removal | 31 | 1.07% | 61.29% |

## Good versus bad replacements

Largest standardized differences among the preregistered explanatory variables (positive means larger in good replacements):

| Feature | Good mean | Bad mean | Hedges g | Cluster CI for mean difference |
|---|---:|---:|---:|---:|
| incoming_return_60d | 0.14 | 0.17 | -0.28 | -0.10 to 0.02 |
| outgoing_return_60d | 0.02 | 0.04 | -0.21 | -0.08 to 0.03 |
| outgoing_acceleration | -0.03 | -0.04 | 0.19 | -0.01 to 0.03 |
| incoming_acceleration | 0.03 | 0.02 | 0.16 | -0.02 to 0.05 |
| outgoing_rank_deterioration | 5.02 | 4.57 | 0.14 | -0.83 to 1.78 |
| score_spread | 0.12 | 0.14 | -0.14 | -0.06 to 0.03 |

## Null calibration

Strongest observed continuous relationship: `incoming_distance_from_189d_high` with |Spearman| 0.168. 
The cluster-preserving familywise empirical p-value was 0.812; the null 95th-percentile maximum was 0.323.

## Bad-replacement concentration

There were 37 bad replacements. The worst one, three, and five rebalance clusters account for 10.76%, 29.30%, and 45.04% of negative replacement shortfall. Leave-one-cluster-out mean replacement advantage remains 0.45% to 1.28%.

| Date | Outgoing to incoming | Advantage | Outgoing return | Incoming return |
|---|---|---:|---:|---:|
| 2024-02-01 | JPM to AMGN | -18.34% | 6.95% | -11.40% |
| 2023-04-03 | MRK to CSCO | -17.95% | 9.30% | -8.65% |
| 2023-06-01 | CAT to CRM | -17.30% | 18.43% | 1.13% |
| 2025-01-02 | JPM to AAPL | -16.85% | 9.24% | -7.61% |
| 2026-06-01 | JNJ to CSCO | -16.18% | 13.46% | -2.72% |

## Drawdown-period replacement quality

| Period | Events | Effective N | Mean advantage | Win rate |
|---|---:|---:|---:|---:|
| 2021-22 | 9 | 6 | 3.66% | 55.56% |
| 2022-23 | 13 | 6 | -2.14% | 46.15% |
| 2025 | 2 | 2 | 0.57% | 50.00% |
| 2024 | 4 | 2 | 5.34% | 75.00% |
| 2026 | 3 | 2 | -10.00% | 0.00% |

## DM versus MRM

| Metric | DM | MRM |
|---|---:|---:|
| Replacement pairs | 81 | 58 |
| Mean advantage | 0.93% | 0.83% |
| Win rate | 54.32% | 62.07% |
| Bad replacement share | 45.68% | 37.93% |
| P10 | -10.97% | -10.34% |
| Worst replacement | -18.34% | -16.09% |

## Economic magnitude

Canonical replacement pairs contribute an equal-slot arithmetic 15.14% over the sample, approximately 3.03% per year. This is descriptive event attribution, not a compounded portfolio counterfactual.

## Candidate mechanism and next step

No simple PIT replacement feature cleared the combined interpretability, materiality, independent-cluster, tail-concentration, and familywise null-calibration standard.

**Recommendation:** No narrow replacement modification is currently justified.
