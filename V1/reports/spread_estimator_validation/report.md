# Mid-liquidity spread-estimator validation

Status: **METHODOLOGY_BROKEN**

Fixed market-cap quantile probes in the $50M-$500M Sharadar daily snapshot, restricted to US-exchange common stocks with >=60 OHLCV sessions; five mechanically retained names with at least one valid current Alpaca quote.

| Ticker | CS bps | AR bps | Alpaca quoted bps | Valid quotes |
|---|---:|---:|---:|---:|
| PZG | 17.66 | 33.39 | 68.25938566552908 | 35 |
| ZUMZ | 50.80 | 0.00 | 716.3901468168247 | 20 |
| NERV | 211.75 | 96.98 | 1466.3951120162926 | 31 |
| XPER | 76.11 | 21.02 | 33.72681281618965 | 144 |
| GFUZ | 10.69 | 0.01 | 3001.899936668778 | 8 |

Neither estimator is acceptable as primary for this mid-liquidity tier; retain both only as diagnostics and replace with quoted spreads or a calibrated vendor microstructure field.

No Task 2 or Task 3 work is permitted when status is METHODOLOGY_BROKEN.