# Index Deletion / Forced Selling -- Phase 8 breadth/power screen

**Verdict: STOP -- record negative design result, do not build a backtest, do not relax the threshold, move to the next hypothesis in the backlog**

Hypothesis: Index Deletion / Forced Selling reversal (post-EFFECTIVE-date, MANDATE barrier)

## Universe scope

Included: S&P 500. Excluded: S&P 400 (MidCap), S&P 600 (SmallCap).

No sp400/sp600/midcap400/smallcap600/indices table exists under the current Sharadar entitlement (empirically probed: all return HTTP 403). Decided as a pre-existing data-availability constraint before this screen ran, not narrowed after seeing a disappointing result.

## Removal reason breakdown (full history, 1998-2026)

| Reason | Count |
|---|---:|
| acquired_or_merged | 370 |
| market_cap_change | 184 |
| spinoff | 73 |
| other | 59 |
| bankruptcy | 10 |

Only action=removed rows classified market_cap_change are usable events. acquired_or_merged removals (the largest bucket) are excluded because the security typically stops trading at deal close (empirically confirmed on SWY/LO/COV: lastpricedate == deletion date), leaving no post-deletion price series to test a reversal against. spinoff/bankruptcy/other are excluded as separate, smaller, mechanistically distinct populations, per this project's own rule against pooling hypotheses that only share a label.

## Full-history design (1998-2026)

- Usable events: **184** over 28.68 years (6.416/yr)

| Design variant | Independent bets/yr | MDA %/yr | Viable (<=4%/yr)? |
|---|---:|---:|---|
| index_deletion_full_history_independent_events_rho0 | 6.42 | 15.07 | False |
| index_deletion_full_history_independent_events_standard_corr | 6.42 | 11.45 | False |
| index_deletion_full_history_clustered_standard_corr | 5.35 | 12.54 | False |

## Modern-era design (2016-2026, decay-sensitivity check)

- Usable events: **81** over 10.68 years (7.586/yr)

| Design variant | Independent bets/yr | MDA %/yr | Viable (<=4%/yr)? |
|---|---:|---:|---|
| index_deletion_modern_era_independent_events_rho0 | 7.59 | 22.71 | False |
| index_deletion_modern_era_independent_events_standard_corr | 7.59 | 17.25 | False |
| index_deletion_modern_era_clustered_standard_corr | 6.32 | 18.90 | False |

## Hard-stop gates

- Event count gate (150 minimum): **PASSED** (184 usable events)
- MDA gate (<= 4.0%/yr): **FAILED** (best-case MDA across every design variant tried: 11.447%/yr)

bestCaseMdaPct is the empirical minimum across every reported design variant (full-history and modern-era), not a specific labeled 'optimistic' case -- see _design_variants' docstring for why the variant with zero assumed correlation is not reliably the best case.
