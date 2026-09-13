# BEAR OVERLAY — S1 IN BEAR+HIGH_VOL

> **EXPLORATORY / UNDERPOWERED — FORWARD VALIDATION REQUIRED.** The activation hypothesis came from a broad conditional search on already-viewed history. The frozen follow-up is useful characterization, not independent confirmation. Leave-one-crisis-out results are in `V2/reports/crisis_robustness/20260911T162542Z/`.

Hypothesis: `V2/research/preregistrations/bear_overlay_v1.json` (committed before S1 scores were computed on the V3.2 universe; underlying signal formula reused verbatim from `strategies/signal_library.py`).

## Prior-claims note (read first)

The brief's "evidence already established" was checked against `conditional_signal_map` (`V2/reports/conditional_signal_map/20260911T044626Z/`) before writing the preregistration. Confirmed: S1's full-sample figure (15.9%/yr, t=3.46) matches exactly. **Not confirmed:** the brief's "BEAR+HIGH_VOL: 89.4%/yr t=4.21" — that study only computed BEAR (63.8%/yr, t=2.64) and HIGH_VOL (72.2%/yr, t=3.95) as **separate marginal cuts**; no joint BEAR-AND-HIGH_VOL intersection cell was ever computed there. The brief's "Corr with RSI14: 0.91" is also not traceable to any artifact — no signal-correlation matrix appears anywhere in that study. Both diagnostic checks below are therefore the **first computation** of these specific joint statistics in this repo, not replications.

## Diagnostic checks

**1. Correlation check (S1 vs IBS, cross-sectional, pooled over BEAR+HIGH_VOL weeks):**

**0.44** — well below the 0.70 "LOW" threshold. **Verdict: LOW.** S1 is not a noisier version of IBS in the target regime; it holds a substantially different set of names (corroborated below by the turnover check: ~1.18 average name-set turnover between the IBS-held and S1-held quintiles, i.e. the two signals' top-quintile holdings are almost entirely disjoint even within BEAR+HIGH_VOL weeks). Pooled correlation across all weeks is nearly identical (0.45), so this is not regime-specific — the two signals are just not very related at any time.

**2. Activation frequency:**

**87 / 1251 weeks (6.95%)** are BEAR AND HIGH_VOL simultaneously (194 BEAR weeks, 287 HIGH_VOL weeks total, overlapping in 87). This is below the brief's "expected 10-15%" band but above the 5% "too little opportunity" floor — reported as somewhat low-opportunity, not a stop condition.

**3. Standalone S1 replication in BEAR+HIGH_VOL** (computed fresh on this study's own universe/period, since no prior artifact ever computed this joint cell):

**95.16%/yr, t=3.07, n=87.** Spread magnitude clears the "confirmed" bar (>=50%/yr) comfortably — in fact it's larger than the brief's own unverified 89.4% claim — but the t-stat (3.07) falls short of the >=3.5 bar for full confirmation, landing in the preregistered PARTIAL band (30-50%/yr or t 2.5-3.5 — here it's the t-stat that's in-band while the spread clears the top bar). This does **not** breach the STOP floor (spread >=30%/yr and t>=2.5 are both satisfied). The moderate t-stat despite a huge point estimate reflects the small sample (87 weeks) and high week-to-week variance of a 20%-of-sample-conditioned spread, not a weak effect.

**Diagnostic verdict: PROCEED.** No diagnostic hard-fails (correlation is LOW, not HIGH; replication is PARTIAL, not below the STOP floor; activation frequency is within the non-stopping range).

## Full-cycle comparison (2001-2024)

| Metric | A (V3.2) | B (overlay) | Delta |
|---|---|---|---|
| CAGR | 14.93% | 15.05% | +0.11pp |
| Alpha/yr (FF5+UMD, HAC) | 6.50% | 6.64% | +0.14pp |
| Alpha t-stat | 2.84 | 2.86 | +0.02 |
| Sharpe | 0.700 | 0.699 | **-0.0013** (negligible, but technically a regression) |
| Max drawdown | -57.28% | -57.34% | -0.05pp (negligible) |
| Annual cost increase from overlay | — | ~4.3bps/yr (rough estimate, see note) | well under the 0.5%/yr (50bps) flag |

**Cost note:** turnover cannot be measured from a fully rebuilt composite (same linear-blend-proxy limitation as every prior study in this thread), but the average IBS-vs-S1 held-name turnover fraction in overlay-on weeks (1.18 — i.e. almost complete non-overlap between the two signals' top-quintile sets) times the ~7% activation frequency gives a rough estimate of ~4bps/yr in added round-trip cost — small relative to the 0.5%/yr flag threshold. This is a rough proxy, not an exact figure, flagged as such.

## BEAR+HIGH_VOL weeks comparison

| Metric | A (V3.2) | B (overlay) | Delta |
|---|---|---|---|
| Alpha/yr in regime | 45.49%/yr (t=2.27) | 49.85%/yr (t=2.39) | +4.36pp |
| Sharpe in regime | 0.666 | 0.687 | **+0.021** |
| N weeks | 87 | 87 | — |

The overlay does improve alpha and Sharpe within the target regime, but the improvement (+0.021 Sharpe) falls short of the preregistered **+0.05 bar** required for the primary EFFECTIVE criterion.

## TYPE 2 episodes (directional check)

| Episode | A loss | B loss | Direction |
|---|---|---|---|
| COVID 2020 | -48.56% | -48.76% | **Worse** |
| 2022 bear | -23.99% | -23.89% | **Better** |

**Mixed.** COVID 2020 has only 1 of its 13 weeks classified BEAR+HIGH_VOL (the overlay barely engages, and that one week happened to be slightly negative for S1), so the overlay does essentially nothing there and the tiny residual effect is unfavorable. The 2022 bear has 7 of 33 weeks overlay-active and shows a small improvement. Neither is a large effect in either direction.

## TYPE 1 episodes (overlay should not hurt)

| Episode | A loss | B loss | Delta | Overlay-ON weeks |
|---|---|---|---|---|
| GFC 2008-09 | -57.28% | -57.34% | -0.05pp (negligible) | 25 / 86 (29.1%) |
| 2002 bear | -27.44% | -27.22% | +0.22pp (slightly better) | 16 / 24 (66.7%) |

The overlay is active for a substantial share of both TYPE 1 episodes (29% of GFC weeks, 67% of the 2002 bear's weeks — much more than its 6.95% overall activation rate, confirming these were indeed high-vol bear stretches). Consistent with the economic hypothesis (amplifying an already-working IBS engine should not hurt), the effect in both is negligible-to-slightly-positive, not negative. Overlay does not measurably hurt either TYPE 1 episode.

## Sub-period S1 strength in BEAR+HIGH_VOL

| Period | S1 spread | t | BEAR+HV weeks |
|---|---|---|---|
| 2001-2008 | 79.4%/yr | 2.42 | 46 |
| 2009-2016 | 66.1%/yr | 1.78 | 13 |
| 2017-2024 | 141.5%/yr | 1.72 | 28 |

**Consistent: PARTIALLY.** Direction and magnitude are consistently large and positive across all three sub-periods (66-142%/yr) — this is not concentrated in one era. But statistical significance is only clearly present in 2001-2008 (t=2.42); the other two sub-periods have t<2 despite very large point estimates, driven by small per-period sample sizes (13 and 28 weeks respectively) and high variance, not by the effect being economically weaker there. Not classified REGIME_SPECIFIC (the effect isn't concentrated in one period), but the significance is thin outside 2001-2008 purely due to sample size.

## Signal attribution in BEAR+HIGH_VOL weeks

Mean weekly contribution to the composite return when overlay is ON (contribution = weight × mean(component return)):

| Signal | Contribution | % of total |
|---|---|---|
| IBS | 0.349% | 53.6% |
| RSI2 | 0.114% | 17.4% |
| Turnaround Tue | 0.022% | 3.4% |
| S1 | 0.167% | **25.6%** |

S1 contributes a genuine, non-trivial quarter of the composite's average weekly return in overlay-on weeks despite carrying only 20% of the weight — consistent with the low correlation and largely disjoint holdings found above (S1 is adding real, differentiated return, not diluting IBS's contribution). IBS remains the dominant contributor even in its own best regime.

## Falsification verdict

| Criterion | Result |
|---|---|
| Diagnostic gate | PROCEED (all three checks pass or land in a non-stopping band) |
| BEAR+HV Sharpe improvement >= 0.05 | **NO** (+0.021) |
| Full-cycle Sharpe >= A (no regression) | **NO, marginally** (-0.0013 — technically a regression, though negligible in magnitude) |
| Full-cycle alpha within 1%/yr of A | YES (+0.14pp) |
| TYPE 2 directional improvement | **MIXED** (2022 better, COVID worse, both small) |
| Sub-period consistent | PARTIALLY (direction consistent, significance thin in 2 of 3 sub-periods due to small N) |
| Falsified? (Sharpe < A - 0.03, OR corr > 0.85, OR S1 < 30%/t<2.5, OR activation <5%/>30%) | **NO** — none of the hard falsification conditions are breached |

**VERDICT: MARGINAL.**

The overlay is not falsified — no hard failure condition is breached, alpha cost is negligible, correlation confirms genuine informational independence, and the joint BEAR+HIGH_VOL S1 effect does replicate (in the PARTIAL band) on this exact universe. But it also does not clear the EFFECTIVE bar: the BEAR+HIGH_VOL Sharpe improvement (+0.021) is well under the required +0.05, the full-cycle Sharpe technically (if negligibly) regresses rather than holding flat or improving, and the TYPE 2 directional check is mixed rather than uniformly favorable. The effect is real (confirmed by the correlation, holdings-overlap, and signal-attribution checks) but too small and too infrequently activated (6.95% of weeks, below the 10-15% expected band) to move full-cycle or target-episode metrics by a decisive margin.

## Recommended next step (not itself undertaken)

Given the offensive approach is not falsified — unlike all four defensive TYPE 2 triggers — but only marginally clears the bar for informativeness rather than decisively passing, two reasonable paths exist, neither pursued here per the no-reoptimization constraint: (a) treat this as a "confirmed but too small to matter at 20%/6.95%-activation" result and move to crisis-sleeve research as the brief's fallback direction; or (b) a separately preregistered follow-up that tests whether a different (not re-optimized within this study) activation definition or weight allocation captures more of the target episodes' overlay-on weeks — COVID 2020 in particular saw the regime calendar assign only 1 of 13 episode weeks as BEAR+HIGH_VOL, the same slow-regime-calendar problem that killed the Q6 defensive trigger. This is noted, not solved, here.

---

## Files in this report directory

- `bear_overlay_v1.md` — this report
- `weekly_s1_panel.csv` / `weekly_s1_panel_eval.csv` — full 1251-week panel: signal_date, realization_date, regime, high_vol, vol20, ibs_ret, rsi2_ret, tue_ret, s1_ret, s1_bottom_ret, s1_spread, cs_corr_s1_ibs, turnover_ibs_vs_s1_frac, ret_A, ret_B, FF5+UMD factor columns
- `eval_results.json` — full computed diagnostic and evaluation numbers

**STOP.** MARGINAL verdict per the preregistered criteria — neither EFFECTIVE nor FALSIFIED. No V3.4 paper track is built or launched. No weights or thresholds were optimized after seeing results. Reporting and waiting for instruction.
