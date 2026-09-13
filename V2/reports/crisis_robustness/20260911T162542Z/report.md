# Crisis-conditioned leave-one-crisis-out robustness

Evidence status: **EXPLORATORY / HYPOTHESIS-GENERATING — FORWARD VALIDATION REQUIRED**

Leave one pre-existing V3.1 peak-to-recovery crisis episode out at a time; no dates or signal rules selected from these results.

This is a robustness characterization on already-viewed history, not a new holdout.

| Sample | Observations | Annualized S1 spread | HAC t-stat | Sign positive | Collapse? |
|---|---:|---:|---:|---|---|
| Full frozen BEAR+HIGH_VOL sample | 87 | 95.16% | 3.07 | YES | — |
| Excluding dot-com / 2001–2002 | 65 | 98.36% | 2.41 | YES | NO |
| Excluding Global Financial Crisis / 2008–2009 | 62 | 98.81% | 2.66 | YES | NO |
| Excluding COVID crash / 2020 | 80 | 66.85% | 3.33 | YES | NO |
| Excluding 2022 bear market | 72 | 111.74% | 2.90 | YES | NO |

Interpretation: **UNDERPOWERED**

The sign remains positive after every exclusion and no one episode causes collapse, but the frozen target condition has only 87 full-sample observations and 62–80 after exclusions. This robustness check does not convert a broad-search discovery into independent confirmation.

## Not run

- **crisis Breakout / new-high survivor sleeve — NOT AVAILABLE.** No frozen crisis-sleeve rule or unambiguous direction-consistent return series exists. The historical 'buy new LOWS' text was an interpretation error; active research direction is buy new 20-day HIGHS. Freeze the exact rule prospectively before computing robustness.
- **crisis Gap Fade sleeve — NOT AVAILABLE.** No frozen portfolio rule combining gap threshold, activation, holdings, and execution exists. Broad conditional-search cells are insufficient for a portfolio robustness rerun.
