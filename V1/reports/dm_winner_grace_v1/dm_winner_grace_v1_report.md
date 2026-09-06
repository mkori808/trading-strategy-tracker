# Dual Momentum - One-Rebalance Winner Grace v1

**Final classification: Modification unsupported**

All historical results are development evidence. No clean historical holdout remains.

## Canonical vs modified performance

| Metric | Canonical DM | Winner Grace | Difference |
|---|---:|---:|---:|
| CAGR | 18.52% | 16.94% | -1.57% |
| Cumulative return | 134.44% | 119.32% | -15.12% |
| Annualized volatility | 19.95% | 20.55% | 0.60% |
| Sharpe | 0.75 | 0.65 | -0.10 |
| Sortino | 1.11 | 0.97 | -0.15 |
| Max drawdown | 20.54% | 21.62% | 1.09% |
| Max DD duration | 250.00 sessions | 236.00 sessions | -14.00 sessions |
| Worst day | -6.59% | -6.62% | -0.03% |
| Worst rolling 5 | -11.58% | -12.52% | -0.94% |
| Worst rolling 20 | -13.74% | -13.50% | 0.25% |
| Worst month | -12.06% | -11.98% | 0.09% |
| Positive months | 56.67% | 55.00% | -1.67% |
| Annual turnover | 7.41x | 5.36x | -2.05x |
| Transaction costs | 118.21$ | 79.45$ | -38.76$ |

## Lifecycle changes

| Metric | Canonical DM | Winner Grace | Difference |
|---|---:|---:|---:|
| Median hold | 42.00 sessions | 70.00 sessions | 28.00 sessions |
| Winner MFE capture | 62.79% | 58.63% | -4.17% |
| Mean winner giveback | 7.20% | 10.21% | 3.01% |
| Post-exit 20d | 0.92% | 0.97% | 0.05% |
| Replacement win rate | 54.32% | 53.57% | -0.75% |
| Replacement advantage | 0.93% | 0.19% | -0.75% |
| Loser replacement advantage | 2.33% | 1.44% | -0.89% |

## Grace-event economics

- 42 grace events across 34 rebalance clusters; 49.41% of canonical exits affected.
- Incumbent beat deferred incoming in 47.62% of events; mean difference -0.86%, median -1.85%.
- Rebalance-cluster 95% CI: -3.67% to 1.99%.
- Equal-slot arithmetic contribution: -7.25% versus an actual net portfolio difference of -15.12%.

## Rank diagnostic

| Exit-rank category | Events | Win rate | Mean incumbent-minus-incoming |
|---|---:|---:|---:|
| rank 6-7 | 23 | 43.48% | -0.67% |
| rank 8-10 | 15 | 53.33% | -1.97% |
| rank >10 or unranked | 4 | 50.00% | 2.15% |

## Deferred incoming names

| Metric | Result |
|---|---:|
| Deferred events | 42 |
| Mean next-period return | 0.95% |
| Canonical incoming mean | 0.96% |
| Rank-1 share | 2.38% |
| Extreme-momentum share | 30.95% |
| Later-large-winner share | 28.57% |

## Evidence concentration and leave-one-cluster-out

| Metric | Result |
|---|---:|
| Positive / negative events | 20 / 22 |
| Top 1 share of positive contribution | 11.44% |
| Top 3 share | 31.89% |
| Top 5 share | 50.32% |
| LOO mean range | -1.36% to -0.46% |
| LOO cumulative range | -10.86% to -3.64% |

## Side effects

| Metric | Canonical DM | Winner Grace |
|---|---:|---:|
| Average holdings | 4.95 | 4.95 |
| Effective equal-weight names | 4.88 | 4.88 |
| Average within-portfolio correlation | 0.30 | 0.33 |
| Total entries | 90 | 64 |
| Total exits | 85 | 59 |

No point-in-time historical sector-classification ledger is installed; current labels were not backfilled.

## $100,000 historical illustration

| Metric | Canonical DM | Winner Grace |
|---|---:|---:|
| Ending value | $234,435 | $219,319 |
| Cumulative profit | $134,435 | $119,319 |
| Max drawdown dollars | $20,538 | $21,624 |
| Worst-day dollars | $-6,593 | $-6,619 |

## Prop-account comparison

Existing standard sweep: Conservative $100,000 account, 4% total-loss limit, 2% daily-loss limit, 5,000 block-bootstrap paths.

| Conservative sizing metric | Canonical DM | Winner Grace |
|---|---:|---:|
| Risk multiplier | 0.10x | 0.10x |
| Failure probability | 0.10% | 0.42% |
| Daily-limit breach probability | 0.00% | 0.00% |
| Total-drawdown breach probability | 0.10% | 0.42% |
| Expected net payout | $1,144 | $1,085 |

## Verdict

The modification failed at least one preregistered primary, net-performance, mechanism-preservation, or tail-risk condition.

The canonical Dual Momentum registration and the frozen DM/MRM portfolio were not changed.
