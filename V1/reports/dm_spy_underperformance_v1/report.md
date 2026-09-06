# Dual Momentum SPY-Underperformance Antecedent Audit v1

Primary event: same-day strategy return minus SPY return is below zero. Every qualification is known by T-1.

## Event decomposition

| Strategy | Days | Underperform rate | Absolute loss rate | Joint damaging rate | Severe holdout rate | Beta-adjusted residual underperform rate |
|---|---:|---:|---:|---:|---:|---:|
| Optimized DM 63D/Daily | 250 | 45.2% | 45.2% | 36.4% | 11.0% | 44.4% |
| Canonical DM 189D/Monthly | 1254 | 48.0% | 46.5% | 31.7% | 13.1% | 47.8% |

## Primary qualification tests

| Strategy | T-1 qualification | Event rate true | Event rate false | Risk difference | Risk ratio | 95% block CI | BH q | LOYO agreement | Classification |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Optimized DM 63D/Daily | spy trend | 45.0% | 50.0% | -5.0% | 0.90 | [-20.3%, +44.6%] | 1.000 | 100% | exploratory historical |
| Optimized DM 63D/Daily | market volatility | 45.5% | 45.0% | +0.5% | 1.01 | [-12.8%, +16.0%] | 1.000 | 50% | exploratory historical |
| Optimized DM 63D/Daily | cross sectional dispersion | 44.4% | 46.0% | -1.5% | 0.97 | [-11.2%, +7.4%] | 1.000 | 50% | exploratory historical |
| Optimized DM 63D/Daily | prior drawdown 5pct | 47.7% | 44.3% | +3.4% | 1.08 | [-10.7%, +15.4%] | 1.000 | 50% | exploratory historical |
| Optimized DM 63D/Daily | strategy volatility | 44.4% | 43.6% | +0.8% | 1.02 | [-9.6%, +12.0%] | 1.000 | 100% | exploratory historical |
| Canonical DM 189D/Monthly | spy trend | 49.6% | 42.3% | +7.3% | 1.17 | [+1.5%, +12.9%] | 0.164 | 83% | unsupported or unstable |
| Canonical DM 189D/Monthly | market volatility | 45.1% | 51.0% | -5.9% | 0.88 | [-11.6%, +0.1%] | 0.264 | 100% | unsupported or unstable |
| Canonical DM 189D/Monthly | cross sectional dispersion | 47.9% | 48.2% | -0.3% | 0.99 | [-6.2%, +5.5%] | 1.000 | 50% | unsupported or unstable |
| Canonical DM 189D/Monthly | prior drawdown 5pct | 48.0% | 48.0% | +0.0% | 1.00 | [-5.8%, +5.8%] | 1.000 | 17% | unsupported or unstable |
| Canonical DM 189D/Monthly | strategy volatility | 48.1% | 48.1% | +0.0% | 1.00 | [-6.4%, +6.6%] | 1.000 | 50% | unsupported or unstable |

## Guardrails

These are predictive-base-rate comparisons, not counts of qualifications among bad days alone. Secondary absolute-loss, severe-miss, and beta-adjusted outcomes cannot promote a failed primary result. Optimized DM remains exploratory because its configuration was selected on the analyzed history. No live rule changed.
