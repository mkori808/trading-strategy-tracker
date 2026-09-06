# Ex-dividend harvestability follow-up

> Diagnostic only: no candidate returns, backtest, holdout, or preregistration were used.

Status: **COST_SENSITIVE**

The IEX quote book fails the mega-cap sanity check and therefore cannot resolve the estimator disagreement under a mechanism-consistent short holding period.

## Holding horizon

The screen uses **5 trading sessions** (0.019841 years). A generous upper bound for an ex-day price-pressure mechanism; the cited literature studies ex-dividend-day pricing, not a multi-month effect.

## Cost comparison

| Evidence | Spread | Annual drag at five sessions | Interpretation |
|---|---:|---:|---|
| Corwin-Schultz | 22.25 bps | 11.22% | Daily-range estimator |
| Abdi-Ranaldo | 0.00 bps | 0.00% | below estimator resolution, not literal zero cost |
| Alpaca IEX quotes | 364.39 bps | 183.65% | Direct one-venue top-of-book evidence |

MDA: **3.12%/yr**. Quote coverage: **45/45 symbols** across three midday windows.

IEX is one venue, not consolidated NBBO; the result is direct quoted evidence but not a complete execution-cost model.

Execution-cost proxy sanity status: **FAIL** (AAPL and MSFT must each have a median no greater than 5.0 bps).

| Sanity symbol | Median IEX bps | P90 IEX bps |
|---|---:|---:|
| AAPL | 2.49 | 10.57 |
| MSFT | 6.80 | 54.19 |

## Per-symbol quote evidence

| Symbol | Valid quotes | Rejected | Median bps | P90 bps |
|---|---:|---:|---:|---:|
| VST | 721 | 0 | 289.96 | 565.13 |
| CRWD | 833 | 0 | 91.81 | 193.96 |
| GDDY | 152 | 0 | 570.77 | 1128.23 |
| KKR | 399 | 0 | 199.89 | 889.98 |
| SW | 3282 | 0 | 8.32 | 64.28 |
| DELL | 713 | 0 | 726.75 | 932.45 |
| ERIE | 78 | 0 | 536.33 | 938.35 |
| PLTR | 14357 | 0 | 9.24 | 124.46 |
| AMTM | 716 | 0 | 29.88 | 427.70 |
| TPL | 50 | 0 | 531.17 | 1127.11 |
| APO | 224 | 0 | 530.18 | 955.92 |
| LII | 65 | 0 | 526.80 | 1153.61 |
| WDAY | 425 | 0 | 204.56 | 994.12 |
| DASH | 498 | 0 | 200.55 | 1030.22 |
| EXE | 195 | 0 | 75.61 | 621.56 |
| TKO | 53 | 0 | 655.80 | 1248.94 |
| WSM | 166 | 0 | 993.03 | 993.03 |
| COIN | 561 | 0 | 443.29 | 624.15 |
| DDOG | 208 | 0 | 524.48 | 888.24 |
| TTD | 3068 | 0 | 7.28 | 13.61 |
| XYZ | 2746 | 0 | 11.91 | 398.91 |
| IBKR | 1883 | 0 | 485.64 | 926.88 |
| APP | 367 | 0 | 67.26 | 536.28 |
| EME | 148 | 0 | 564.12 | 1096.51 |
| HOOD | 6959 | 0 | 70.43 | 313.61 |
| SOLS | 1468 | 0 | 7.69 | 133.12 |
| Q | 219 | 0 | 361.71 | 817.55 |
| SNDK | 3356 | 0 | 38.41 | 195.42 |
| ARES | 184 | 0 | 536.93 | 987.12 |
| CRH | 754 | 0 | 462.13 | 938.41 |
| CVNA | 2556 | 0 | 43.21 | 433.44 |
| FIX | 448 | 0 | 411.72 | 475.90 |
| CIEN | 571 | 0 | 475.95 | 966.70 |
| COHR | 536 | 0 | 51.25 | 327.25 |
| ECHO | 175 | 0 | 618.33 | 990.47 |
| LITE | 397 | 0 | 459.96 | 682.67 |
| VRT | 475 | 0 | 364.39 | 597.01 |
| CASY | 204 | 0 | 361.11 | 607.11 |
| VEEV | 531 | 0 | 921.50 | 921.50 |
| FDXF | 88 | 0 | 45.79 | 554.06 |
| FLEX | 411 | 0 | 399.98 | 1027.64 |
| MRVL | 1064 | 0 | 112.82 | 334.13 |
| HONA | 292 | 0 | 270.54 | 509.99 |
| FERG | 80 | 0 | 449.39 | 966.99 |
| RDDT | 86 | 0 | 490.98 | 1031.03 |

The candidate advances only if all three measures are positive and below MDA. Exact zero never counts as proof of free execution.
