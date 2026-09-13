# Residual / idiosyncratic intermediate-horizon momentum

Development-only characterization: 2001-01-01 through 2018-12-31. The consumed 2019+ holdout was not evaluated.

| Hypothesis | Classification | 20d spread annualized | HAC t | IC | Spread factor alpha | Long-only net CAGR | Turnover | Max DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| residual_momentum_60d | UNRESOLVED / UNDERPOWERED | -1.64% | -0.59 | -0.0018 | -0.02% | 5.24% | 558.98% | -56.82% |
| residual_momentum_126d | UNRESOLVED / UNDERPOWERED | -0.16% | -0.05 | 0.0034 | 1.39% | 6.04% | 396.34% | -57.53% |
| residual_momentum_252d_ex20 | UNRESOLVED / UNDERPOWERED | 1.40% | 0.40 | 0.0134 | 2.10% | 6.77% | 300.44% | -60.81% |

## Orthogonality

| signal | comparison | cross_sectional_spearman_mean | strategy_return_correlation_v31 |
|---|---|---|---|
| residual_momentum_60d | IBS | -0.178492 | 0.951173 |
| residual_momentum_60d | RSI2 | -0.198479 | 0.951173 |
| residual_momentum_60d | Sector_RS | 0.947236 | 0.951173 |
| residual_momentum_60d | Turnaround_Tuesday | -0.081062 | 0.951173 |
| residual_momentum_60d | Composite_V3_1_raw | -0.089373 | 0.951173 |
| residual_momentum_126d | IBS | -0.137641 | 0.941605 |
| residual_momentum_126d | RSI2 | -0.146622 | 0.941605 |
| residual_momentum_126d | Sector_RS | 0.657001 | 0.941605 |
| residual_momentum_126d | Turnaround_Tuesday | -0.060356 | 0.941605 |
| residual_momentum_126d | Composite_V3_1_raw | -0.076556 | 0.941605 |
| residual_momentum_252d_ex20 | IBS | -0.034873 | 0.947014 |
| residual_momentum_252d_ex20 | RSI2 | -0.020289 | 0.947014 |
| residual_momentum_252d_ex20 | Sector_RS | 0.330025 | 0.947014 |
| residual_momentum_252d_ex20 | Turnaround_Tuesday | -0.007711 | 0.947014 |
| residual_momentum_252d_ex20 | Composite_V3_1_raw | 0.005167 | 0.947014 |

## Integrity

Residual return is computed only from same-date PIT eligible stock returns and same-date sector grouping. Factor regression reuses the established monthly FF5+UMD alignment helper; the daily signal-library's warm-up boundary defect is not reused. Static Sharadar sector labels remain a data limitation: historical classification changes are unavailable in this bundle.