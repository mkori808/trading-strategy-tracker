# Dual Momentum Regime-Dependence Audit v1

This audit preserves both strategies. It measures state dependence; it does not create a trading filter.

| Strategy | Condition | State-one N | State-zero N | Annualized mean difference | 95% block CI (daily) | p | BH q | LOYO agreement | Classification |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Optimized DM 63D/Daily | spy trend | 238 | 12 | -17.46% | [-1.687%, +0.444%] | 0.734 | 0.880 | 100% | exploratory historical |
| Optimized DM 63D/Daily | market volatility | 121 | 129 | +47.05% | [-0.434%, +0.738%] | 0.518 | 0.777 | 50% | exploratory historical |
| Optimized DM 63D/Daily | cross sectional dispersion | 126 | 124 | -4.01% | [-0.411%, +0.375%] | 0.991 | 0.991 | 100% | exploratory historical |
| Canonical DM 189D/Monthly | spy trend | 980 | 274 | -23.38% | [-0.272%, +0.067%] | 0.249 | 0.498 | 100% | unsupported or unstable |
| Canonical DM 189D/Monthly | market volatility | 632 | 622 | +29.42% | [-0.018%, +0.251%] | 0.087 | 0.498 | 100% | unsupported or unstable |
| Canonical DM 189D/Monthly | cross sectional dispersion | 654 | 600 | +22.03% | [-0.051%, +0.223%] | 0.223 | 0.498 | 100% | unsupported or unstable |

## Interpretation guards

- Positive differences mean state one had the higher daily mean; negative differences mean state zero did.
- Six primary tests share one Benjamini-Hochberg correction.
- Optimized DM is selection-contaminated and cannot be promoted by this historical result.
- Daily subgroup metrics are attribution diagnostics, not a backtest of switching the strategy on and off.
- Any surviving canonical condition is only a candidate for a new preregistered independent validation.
