# IBS Conditioning Research

> Historical development evidence only. The old holdout is consumed; these results require prospective confirmation.

Requested development sample: 2001-01-01 through 2018-12-31. 
Signal is formed at close and traded next-session open to close. Every conditioner is tested independently.

## Independent hypothesis results

| Hypothesis | Contrast | Annualized difference | HAC t | Days | Classification |
|---|---|---:|---:|---:|---|
| ibs_earnings_event | non_event - event | 28.82% | 1.90 | 2010 | UNRESOLVED / UNDERPOWERED |
| ibs_overnight_gap_share | low - high | 37.91% | 10.54 | 4526 | SUPPORTED |
| ibs_abnormal_volume | low - high | 14.80% | 5.52 | 4526 | SUPPORTED |
| ibs_turnover | low - high | 33.47% | 9.81 | 4526 | SUPPORTED |
| ibs_liquidity | low - high | -15.20% | -4.81 | 4526 | FALSIFIED |
| ibs_intraday_range | low - high | -0.53% | -0.16 | 4526 | UNRESOLVED / UNDERPOWERED |
| ibs_idiosyncratic_volatility | low - high | 12.27% | 3.64 | 4526 | SUPPORTED |
| ibs_market_cap | low - high | -24.16% | -7.78 | 4526 | FALSIFIED |
| ibs_volatility_bucket | low - high | 12.79% | 3.79 | 4526 | SUPPORTED |
| ibs_market_regime | bull - bear | -2.96% | -0.63 | 4145 | UNRESOLVED / UNDERPOWERED |

## Costed bucket portfolios

| Hypothesis | Bucket | Net CAGR | SPY CAGR | Excess CAGR | Alpha | Alpha t | Sharpe | Max DD | Turnover | IC |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ibs_earnings_event | non_event | -72.74% | 5.78% | -78.52% | -74.37% | -58.99 | -5.97 | -100.00% | 503.89 | -0.00 |
| ibs_earnings_event | event | -51.06% | 5.78% | -56.84% | -51.97% | -12.41 | -2.87 | -100.00% | 223.78 | -0.01 |
| ibs_overnight_gap_share | low | -72.42% | 5.78% | -78.20% | -74.15% | -53.02 | -5.68 | -100.00% | 503.89 | 0.00 |
| ibs_overnight_gap_share | high | -74.73% | 5.78% | -80.51% | -76.08% | -55.71 | -6.51 | -100.00% | 503.89 | -0.01 |
| ibs_abnormal_volume | low | -74.93% | 5.78% | -80.71% | -76.42% | -59.10 | -6.10 | -100.00% | 503.89 | -0.00 |
| ibs_abnormal_volume | high | -73.61% | 5.78% | -79.39% | -75.17% | -51.80 | -6.12 | -100.00% | 503.89 | -0.01 |
| ibs_turnover | low | -76.44% | 5.78% | -82.21% | -78.14% | -71.26 | -7.14 | -100.00% | 503.89 | 0.00 |
| ibs_turnover | high | -71.17% | 5.78% | -76.95% | -72.32% | -40.17 | -4.84 | -100.00% | 503.89 | -0.01 |
| ibs_liquidity | low | -84.64% | 5.78% | -90.42% | -85.69% | -75.40 | -8.02 | -100.00% | 503.89 | -0.00 |
| ibs_liquidity | high | -49.65% | 5.78% | -55.43% | -51.67% | -26.24 | -3.11 | -100.00% | 503.89 | -0.00 |
| ibs_intraday_range | low | -65.81% | 5.78% | -71.59% | -67.96% | -67.02 | -7.22 | -100.00% | 503.89 | -0.01 |
| ibs_intraday_range | high | -79.54% | 5.78% | -85.32% | -80.40% | -47.64 | -5.43 | -100.00% | 503.89 | 0.00 |
| ibs_idiosyncratic_volatility | low | -63.76% | 5.78% | -69.54% | -66.13% | -60.94 | -6.84 | -100.00% | 503.89 | -0.01 |
| ibs_idiosyncratic_volatility | high | -80.81% | 5.78% | -86.59% | -81.52% | -49.36 | -5.56 | -100.00% | 503.89 | -0.00 |
| ibs_market_cap | low | -84.79% | 5.78% | -90.57% | -85.69% | -65.93 | -7.41 | -100.00% | 503.89 | -0.01 |
| ibs_market_cap | high | -50.51% | 5.78% | -56.29% | -52.86% | -31.93 | -3.69 | -100.00% | 503.89 | -0.00 |
| ibs_volatility_bucket | low | -63.92% | 5.78% | -69.70% | -66.27% | -62.62 | -7.01 | -100.00% | 503.89 | -0.01 |
| ibs_volatility_bucket | high | -80.79% | 5.78% | -86.57% | -81.52% | -49.12 | -5.51 | -100.00% | 503.89 | -0.00 |
| ibs_market_regime | bull | -44.20% | 5.78% | -49.98% | -45.59% | -19.95 | -5.45 | -100.00% | 226.89 | -0.01 |
| ibs_market_regime | bear | -46.08% | 5.78% | -51.86% | -48.42% | -17.55 | -3.24 | -100.00% | 234.58 | 0.00 |

## Interpretation rules

The familywise threshold is |t| >= 2.81 across the ten registered conditioning hypotheses. SUPPORTED means the preregistered contrast has the expected sign and clears that threshold; FALSIFIED means it clears the threshold in the opposite direction; all other outcomes are UNRESOLVED / UNDERPOWERED.

A conditioning result is not a portfolio and is not assessed against the 20% finished-portfolio target. Full factor loadings, drawdown duration, costs, capacity, quintile behavior, 2/5-session horizon decay, subperiods, and regime behavior are in `bucket_metrics.csv` and `results.json`.

## Provenance and limitations

Earnings events use Sharadar ARQ filing/publication dates, with exact-date matching and no future-day tolerance. The bundle does not include announcement timestamps, historical bid/ask spreads, or historical borrow data. The combined spread/fees/slippage cost model is therefore an ADV-tier estimate, and before/after-market earnings timing cannot be separated. Market regime uses a PIT lagged-cap-weighted internal equity-market index; SPY is used as the total-return benchmark, not as a signal input. Daily close-to-close factors are the nearest available factor proxy for the open-to-close strategy return.

No existing paper track or state file was read as research evidence or modified by this run.
