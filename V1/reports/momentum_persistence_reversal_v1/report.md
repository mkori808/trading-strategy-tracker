# Momentum Persistence / Reversal Regime Audit v1

Primary outcome is daily strategy return minus SPY return. Every state is known by T-1. No trading filter was created.

## Primary persistence-versus-transition tests

| Strategy | Persistence N | Transition N | Persistence active mean | Transition active mean | Difference annualized | 95% block CI daily | BH q | LOYO | Classification |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Optimized DM 63D/Daily | 193 | 57 | +0.125% | +0.276% | -37.94% | [-0.743%, +0.261%] | 0.996 | 50% | exploratory historical |
| Canonical DM 189D/Monthly | 927 | 327 | +0.020% | +0.026% | -1.54% | [-0.109%, +0.092%] | 0.996 | 50% | unsupported or unstable |
| Canonical MRM | 926 | 327 | -0.005% | -0.011% | +1.64% | [-0.104%, +0.117%] | 0.996 | 83% | unsupported or unstable |
| Fixed 50/50 DM/MRM | 926 | 327 | +0.008% | +0.006% | +0.43% | [-0.074%, +0.072%] | 0.996 | 83% | exploratory historical |
| Vol-Scaled DM/MRM | 926 | 327 | +0.005% | +0.007% | -0.30% | [-0.084%, +0.076%] | 0.996 | 33% | exploratory historical |

## Four-state descriptive map

### Optimized DM 63D/Daily

| State | N | Share | Annualized mean return | Annualized mean active return | Positive days | Worst day | State-attributed max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| bullish persistence | 181 | 72.4% | +46.39% | +34.44% | 52.5% | -5.88% | -13.63% |
| pullback | 57 | 22.8% | +104.26% | +69.51% | 61.4% | -5.60% | -10.87% |
| bearish persistence | 12 | 4.8% | +77.71% | -11.75% | 58.3% | -2.83% | -6.95% |
| rebound | 0 | 0.0% | n/a | n/a | n/a | n/a | n/a |

Descriptive best absolute-return state: **pullback**. Best SPY-relative state: **pullback**.

### Canonical DM 189D/Monthly

| State | N | Share | Annualized mean return | Annualized mean active return | Positive days | Worst day | State-attributed max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| bullish persistence | 736 | 58.7% | +11.61% | -1.20% | 51.5% | -5.84% | -24.99% |
| pullback | 244 | 19.5% | +22.13% | +8.16% | 55.3% | -4.22% | -12.50% |
| bearish persistence | 191 | 15.2% | +45.16% | +28.94% | 56.5% | -6.59% | -12.63% |
| rebound | 83 | 6.6% | +20.31% | +1.80% | 59.0% | -2.21% | -5.44% |

Descriptive best absolute-return state: **bearish persistence**. Best SPY-relative state: **bearish persistence**.

### Canonical MRM

| State | N | Share | Annualized mean return | Annualized mean active return | Positive days | Worst day | State-attributed max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| bullish persistence | 735 | 58.7% | +14.17% | +1.28% | 55.2% | -2.80% | -8.74% |
| pullback | 244 | 19.5% | +12.30% | -1.67% | 57.0% | -2.37% | -7.76% |
| bearish persistence | 191 | 15.2% | +5.54% | -10.68% | 51.8% | -4.92% | -15.36% |
| rebound | 83 | 6.6% | +12.29% | -6.23% | 48.2% | -3.12% | -9.04% |

Descriptive best absolute-return state: **bullish persistence**. Best SPY-relative state: **bullish persistence**.

### Fixed 50/50 DM/MRM

| State | N | Share | Annualized mean return | Annualized mean active return | Positive days | Worst day | State-attributed max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| bullish persistence | 735 | 58.7% | +13.10% | +0.21% | 53.7% | -2.65% | -14.73% |
| pullback | 244 | 19.5% | +16.85% | +2.88% | 57.8% | -2.15% | -9.21% |
| bearish persistence | 191 | 15.2% | +25.15% | +8.93% | 55.5% | -5.75% | -10.97% |
| rebound | 83 | 6.6% | +16.30% | -2.22% | 54.2% | -2.57% | -7.10% |

Descriptive best absolute-return state: **bearish persistence**. Best SPY-relative state: **bearish persistence**.

### Vol-Scaled DM/MRM

| State | N | Share | Annualized mean return | Annualized mean active return | Positive days | Worst day | State-attributed max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| bullish persistence | 735 | 58.7% | +13.06% | +0.16% | 54.3% | -2.63% | -13.39% |
| pullback | 244 | 19.5% | +16.75% | +2.78% | 57.4% | -2.16% | -8.89% |
| bearish persistence | 191 | 15.2% | +22.20% | +5.97% | 55.0% | -5.53% | -10.73% |
| rebound | 83 | 6.6% | +16.90% | -1.62% | 54.2% | -2.53% | -7.03% |

Descriptive best absolute-return state: **bearish persistence**. Best SPY-relative state: **bearish persistence**.

## Guardrails

The primary family contains five tests and uses one BH correction. State rankings and secondary contrasts are descriptive and cannot rescue a failed primary result. Optimized DM and both derived overlays remain exploratory because their selection/design used this historical period. No live rule, order, or size changed.
