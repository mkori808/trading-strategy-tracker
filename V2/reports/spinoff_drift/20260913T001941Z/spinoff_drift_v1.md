# SPIN-OFF DRIFT — CHILD COMPANY (primary) + PARENT (secondary)

Hypothesis: `V2/research/preregistrations/spinoff_drift_v1.json` (committed before any return computation). Mechanism: forced institutional selling of the child security post-spin-off, distinct from the earnings-based mechanisms already closed in this project (IBS intraday exhaustion, PEAD information diffusion).

## A. Event coverage

| Leg | Total events | No price data | No entry price in 5d | Data-quality gate excl. | In dev. window | Financial SIC excl. | Eligible | Events/yr |
|---|---|---|---|---|---|---|---|---|
| Child (spunofffrom) | 523 | 17 | 0 | 0 | 479 | 72 | **475** | 19.0 |
| Parent (spinoff) | 512 | 4 | 24 | 4 | 459 | 70 | **457** | 18.3 |

## B. Primary results — CHILD (spunofffrom)

| Horizon | N events | Excess return | t | Median | % positive |
|---|---|---|---|---|---|
| H1 (1mo) | 460 | **-1.93%** | **-2.03** | -1.65% | 44.1% |
| H2 (3mo, primary) | 455 | -0.80% | -0.56 | -2.02% | 46.2% |
| H3 (6mo) | 450 | -1.50% | -0.79 | -3.79% | 44.9% |
| H4 (12mo) | 444 | +1.24% | 0.39 | -2.67% | 46.2% |

**The hypothesized positive drift is absent at every horizon.** H1 is not just insignificant — it is significantly *negative* (t=-2.03): children underperform SPY by ~1.9% in the first month, the opposite of Miles & Rosenfeld's "excess returns begin immediately" finding. Median excess return is negative at all four horizons, and %-positive never exceeds 46%, i.e. spin-off children beat SPY *less than half the time* at every horizon tested.

## C. Factor regression (FF5+UMD, event-time, H2 primary)

| | Alpha (ann.) | t | R² | Mkt-RF | SMB | HML | RMW | CMA | Mom |
|---|---|---|---|---|---|---|---|---|---|
| H2 | +1.7%/yr | 0.26 | 0.176 | 1.21 | 1.16 | -0.45 | -0.30 | 0.64 | 0.04 |

SMB loading is positive as theory predicted (children do tilt small), but alpha is statistically zero (t=0.26) — whatever raw return exists is fully explained by factor exposure (mainly market beta >1 and SMB), not a distinct event effect. All four horizons (H1–H4) show the same pattern: alpha t-stat never exceeds 1.16 in either direction.

## D. Secondary results — PARENT (spinoff)

| Horizon | N events | Excess return | t | Median | % positive |
|---|---|---|---|---|---|
| H1 (1mo) | 444 | -1.22% | -1.84 | -1.20% | 44.6% |
| H2 (3mo) | 438 | **-2.50%** | **-2.36** | -3.14% | 40.2% |
| H3 (6mo) | 434 | -3.17% | -1.89 | -3.82% | 41.2% |
| H4 (12mo) | 429 | -3.58% | -1.77 | -6.27% | 41.7% |

The parent leg is worse than the child leg, not better: **significantly negative at H2** (t=-2.36) and directionally negative and borderline-significant at every other horizon. This directly contradicts McConnell & Ovtchinnikov (2004)'s "parents also outperform" finding in this dataset — parents post-spin-off underperform SPY by 2.5–3.6% across 3–12 months.

## E. Size tercile analysis (H2, child)

| Tercile | N | Excess return | t |
|---|---|---|---|
| Small | 139 | +0.67% | 0.19 |
| Mid | 139 | -0.60% | -0.26 |
| Large | 139 | -2.50% | -1.42 |

**Monotonic: NO** in the predicted sense — small-cap is closest to flat (not strongly positive), and the most negative result is in the *large*-cap tercile, not small. The theorized "forced-selling pressure most acute in small caps" pattern is not present; if anything effect direction runs the other way, though none of the three cells is individually significant.

## F. Time period analysis (H2, child)

| Period | N | Excess return | t | Significant? |
|---|---|---|---|---|
| 2000-2009 | 136 | +1.98% | 0.74 | No |
| 2010-2019 | 232 | +1.21% | 0.65 | No |
| 2020-2026 | 107 | **-8.17%** | **-2.68** | Yes (negative) |

**Stable: NO.** The two older decades are mildly positive but never significant; the most recent period is significantly *negative* — a decay-to-negative pattern, not decay-to-null as seen in the project's earnings-based signals. There was never a decade in which this effect cleared the VIABLE bar (t>2.0, positive).

## G. Leave-one-crisis-out (H2, child)

| Sample | N | Excess return | t | Sign+? |
|---|---|---|---|---|
| Full sample | 455 | -0.80% | -0.56 | No |
| Ex dot-com 2001-2002 | 423 | -1.48% | -1.03 | No |
| Ex GFC 2008-2009 | 416 | -0.39% | -0.26 | No |
| Ex COVID 2020 | 446 | -0.73% | -0.50 | No |
| Ex 2022 bear | 426 | -0.04% | -0.03 | No |

**Verdict: ROBUST (to the null/negative finding).** The result is negative or flat in the full sample and in every crisis-exclusion cut — it is not being driven or masked by any single episode. This robustness works against the hypothesis: there is no crisis-period distortion hiding a positive underlying effect.

## H. Distribution ratio conditioning (secondary)

| | N | Excess return | t |
|---|---|---|---|
| High ratio (≥0.333) | 239 | +0.67% | 0.39 |
| Low ratio (<0.333) | 216 | -2.49% | -1.19 |

Direction is opposite the mechanism's prediction (higher distribution ratio → more forced selling → stronger positive drift): high-ratio events are flat, low-ratio events are more negative. Neither cell is individually significant, so this is inconclusive rather than a clean contradiction, but it offers no support for the forced-selling channel.

## I. Falsification verdict

| Criterion | Result |
|---|---|
| H2 excess return > 3%? | **NO** (-0.8%) |
| H2 t > 2.0? | **NO** (t=-0.56) |
| 2+ decades consistent (t>1.0)? | **NO** (0/3 decades reach t>1.0 in the positive direction; 2020-2026 is significant and negative) |
| Leave-one-crisis-out ROBUST? | Robustly negative/null, not robustly positive |
| FF5+UMD alpha t > 1.5? | **NO** (t=0.26) |
| H2 excess return ≤ 0 OR t<1.0 at all four horizons? | **YES on both counts** (H2 = -0.8%; signed t at H1-H4 = -2.03, -0.56, -0.79, 0.39 — all below 1.0) |

**VERDICT: FALSIFIED.**

This is a cleaner falsification than the earlier PEAD sub-hypothesis B (which at least showed full-sample significance before decaying): here there is no horizon, no decade, and no factor-adjusted specification in which the child leg shows the predicted positive drift, and the first-month result is significantly *negative* — opposite to the mechanism's prediction that recovery begins immediately. The parent leg, tested as a secondary check, is independently and more strongly falsified (significantly negative at H2).

## Recommendation

Close this hypothesis. The classic academic spin-off literature (Cusatis/Miles/Woolridge 1993, McConnell/Ovtchinnikov 2004) does not replicate in this 2000-2024 Sharadar sample using the SPY buy-and-hold benchmark and closeadj (split-only-adjusted) prices as specified. Possible reasons not tested here (would each require a separate preregistration, not a retry of this one): (1) the original studies may have used size/value-matched benchmarks rather than a broad market-cap-weighted index, which could materially change the "excess return" sign given the strong positive SMB loading found here; (2) closeadj excludes dividends, and if children pay outsized special dividends shortly after separation this could understate raw returns; (3) the effect may be concentrated in a pre-2000 sample period not covered by this database. None of these are grounds to re-run this specific construction — they are candidate directions for a new, separately preregistered study if this research line is revisited.

---

**STOP.** FALSIFIED per the preregistered criteria on both legs. No paper track built, no holdout consumed, V3.2 and paper-trading Tracks 1-3 untouched, no thresholds/horizons re-optimized after seeing results. Reporting and waiting for instruction.
