# Canonical Dual Momentum Stock-Characteristics Audit v1

This audit asks which point-in-time price/liquidity characteristics describe better next-rebalance SPY-relative outcomes among the five stocks canonical DM actually selected. It does not test a new portfolio.

## Decision

**No stock-characteristic validation study is justified.**

The ledger contains 297 selected stock-periods, 60 independent rebalance clusters, and 28 securities.

## Primary characteristic tests

Effect is the active-return change for a one-standard-deviation within-rebalance increase. P-values use the preregistered familywise maximum-statistic null.

| Characteristic | Coverage | Effect / SD | 95% cluster CI | Cluster t | FWER p | First half | Second half | LOYO sign | Qualifies |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 63-session annualized volatility | 100.0% | +1.02% | [-0.48%, +2.96%] | 1.15 | 0.734 | -0.10% | +2.11% | 100% | no |
| 126-session SPY beta | 100.0% | +0.84% | [-0.70%, +2.83%] | 0.91 | 0.877 | -0.22% | +1.88% | 83% | no |
| 20-session log average dollar volume | 100.0% | +0.69% | [-0.84%, +2.51%] | 0.81 | 0.922 | +0.18% | +1.20% | 83% | no |
| Distance from 252-session high | 100.0% | -1.51% | [-3.65%, -0.13%] | -1.68 | 0.333 | -0.83% | -2.18% | 100% | no |
| 63-vs-189-session momentum acceleration | 100.0% | -0.78% | [-2.25%, +0.56%] | -1.07 | 0.790 | -0.23% | -1.31% | 83% | no |

## High-versus-low descriptive buckets

Within each rebalance, the two highest-characteristic holdings are compared with the two lowest. These are descriptions, not extra hypotheses.

| Characteristic | Low group level | Low mean active | High group level | High mean active | High minus low |
|---|---:|---:|---:|---:|---:|
| 63-session annualized volatility | 20.4% | -0.69% | 35.1% | +0.80% | +1.49% |
| 126-session SPY beta | 0.55 | -0.18% | 1.21 | +1.37% | +1.55% |
| 20-session log average dollar volume | $786M | +0.07% | $2983M | +0.47% | +0.40% |
| Distance from 252-session high | -6.9% | +1.54% | -1.4% | -0.80% | -2.34% |
| 63-vs-189-session momentum acceleration | -42.8% | +1.24% | -16.5% | -0.43% | -1.66% |

## Incumbent status (descriptive)

Previously held names averaged +0.51% active return (208 rows), versus +0.03% for new selections (84 rows), a +0.49% difference.

## Scope and blockers

- Results apply only inside canonical DM's selected top five in the caveated Dow reconstruction; they do not describe the live optimized small/mid-cap universe.
- Sector, market cap, and fundamentals remain untested because genuine point-in-time histories are not installed.
- Simultaneous holdings are dependent. The analysis treats the rebalance date, not each stock row, as the independent unit.
- No feature, threshold, filter, weight, signal, order, or forward-test configuration changed.
