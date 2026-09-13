# V3.1 TRANSITION FILTER — Q6

> **SUPERSEDED — INVALID METHODOLOGY / CLOSED.** Preserved as a historical artifact. Subsequent controlled testing showed the apparent benefit did not survive: the frozen trigger did not activate before COVID 2020 or the 2022 bear, and no tested aggregate TYPE 2 trigger family achieved both timely and discriminating protection. Do not use this report as production evidence.

Hypothesis: `V2/research/preregistrations/v31_transition_filter_v1.json` (committed before the transition calendar was computed or Portfolio B simulated).

**Prior-claims note (read first):** The task brief's "DIM 5" transition-state IBS-spread numbers (STABLE +18.7%/yr, TRANSITIONING +2.1%/yr, TRANSITIONING+BULL→BEAR -12.3%/yr) and its "Q2/Q3 TYPE 1/TYPE 2" drawdown-anatomy labels were searched for across `V2/research/` and `V2/reports/` before this study began. A `v31_deepening_q2q3_v1` prereg and its `20260911T043543Z/summary.json` output DO exist, and DO contain a drawdown-episode classification, but under the labels `MARKET DRIVEN` / `SIGNAL FAILURE` (not "TYPE 1/TYPE 2"), with no markdown report ever produced, and with IBS-spread-during-episode figures for the COVID and 2022 episodes (-49.7%/yr and -9.9%/yr respectively) that do **not** match the brief's cited -12.3%/yr. `conditional_signal_map`'s dimensions are DIM1 (market regime), DIM2 (volatility regime), DIM3 (drawdown depth), plus a day-of-week cut — there is no "DIM 5" anywhere. The brief's specific numbers are therefore **not traceable to any computed artifact in this repo** and are treated purely as informal motivation, not verified priors. They are not repeated below as established facts.

## A. Transition calendar

Using the locked definition (first week classified BEAR immediately following a week classified BULL, from `V2/reports/v31_regime_decomposition/20260911T002649Z/weekly_panel.csv`'s `regime` column, 1251 weeks, 2001-01-12 to 2024-12-27):

**Total BULL→BEAR transitions found: 2**
| # | Transition (realization) date |
|---|---|
| 1 | 2002-04-12 |
| 2 | 2011-12-23 |

**TRANSITION_ACTIVE weeks: 8** (2 transitions × 4 weeks, no overlap/restart triggered) — **0.64% of the 1251-week sample.**

**Critical finding:** Under the *exact, locked* "immediately following" definition, **neither the COVID 2020 crash nor the 2022 rate-hike bear registers as a BULL→BEAR transition at all**, because in both cases the regime calendar passes through one or more intervening SIDEWAYS weeks before reaching BEAR:

- 2020: `...2020-02-28 BULL → 2020-03-06 SIDEWAYS → 2020-03-13 SIDEWAYS → 2020-03-20 BEAR`
- 2022: `...2022-04-14 BULL → SIDEWAYS (04-22, 04-29, 05-06) → 2022-05-13 BEAR`

Per the locked definition, a SIDEWAYS week breaks the "BULL immediately followed by BEAR" condition, so the filter's transition trigger **never fires** before either target episode. This is reported as found, not adjusted — the transition definition was locked pre-registration and is not loosened after seeing this result (that would be exactly the kind of post-hoc parameter search the brief prohibits).

The two transitions that *did* fire (2002-04-12, ahead of the 2002 bear-market decline, and 2011-12-23, inside the noisy Aug–Dec 2011 European-debt-crisis whipsaw) are unrelated to the two named TYPE-2 target events.

## B. Portfolio construction / sanity check

Portfolio A = `0.62*ibs_ret + 0.22*rsi2_ret + 0.10*srs_ret + 0.06*tue_ret` each week (standalone signal returns already in `weekly_panel.csv`). Portfolio B = same blend, substituting `{ibs:0.31, rsi2:0.375, srs:0.162, tue:0.091}` on TRANSITION_ACTIVE weeks.

Sanity check of Portfolio A's linear-blend reconstruction against the persisted actual `composite_ret` (the real score-blended, jointly-selected-holdings composite): mean absolute weekly difference **0.181pp**, max absolute difference **2.19pp**, correlation **0.9975** across all 1251 weeks. This confirms the linear blend is a close but not exact proxy for the true composite (as expected — the real composite selects one shared holdings set by blended score, not four independently-selected top-quintile sets combined by return). Portfolio B is built the same way; its accuracy relative to a hypothetical true score-blended reweighted composite cannot be independently validated (no such ground truth exists in this repo), but this ≤2.2pp/week bound on Portfolio A's own approximation error is offered as the best available estimate of the method's fidelity.

## C. Full-cycle comparison (2001-2024)

| Metric | A (V3.1) | B (filtered) | Delta (B−A) |
|---|---|---|---|
| CAGR | 14.02% | 14.00% | -0.02pp |
| Alpha/yr (FF5+UMD, HAC, weekly) | 5.55% | 5.53% | -0.02pp |
| Alpha t-stat | 2.57 | 2.56 | -0.01 |
| Sharpe | 0.608 | 0.607 | -0.001 |
| Max drawdown | -57.11% | -57.11% | ~0.00pp |
| Worst rolling 12mo return | -49.63% | -49.63% | ~0.00pp |
| Annual turnover | not computable from persisted data | — | see limitation below |
| Annual cost | not computable from persisted data | — | see limitation below |

Full-cycle alpha cost is negligible (0.02pp/yr) simply because the filter is active for only 8 of 1251 weeks (0.64%) and neither of those 8-week bursts coincides with a period of unusually large IBS-vs-other-signal divergence. Max drawdown and worst-rolling-12mo are numerically identical to the displayed precision because both are driven by the 2007-09 GFC episode, during which the filter never activates (see section E).

**Turnover/cost limitation:** `weekly_panel.csv` contains no per-week holdings list or turnover series for the composite, and reconstructing actual weekly holdings under the alternate transition-week weights would require rebuilding the composite's ranking/selection logic — precluded by the "do not recompute signals" constraint. An exact turnover delta is therefore not computable. Qualitatively: a change in score weights during a transition week can only induce turnover to the extent it flips names across the top-quintile boundary; since the filter is active on 0.64% of weeks total, any such induced churn is a second-order effect that is very unlikely to be material, but this is a reasoned qualitative judgment, not a measured figure.

## D. TYPE-2-labeled episode analysis (COVID 2020, 2022 bear)

| Episode | Weeks | A loss (peak→trough) | B loss (peak→trough) | Reduction | Filter activated? |
|---|---|---|---|---|---|
| COVID 2020 (2019-12-20 → 2020-03-20) | 14 | -47.11% | -47.11% | **0.00pp (0% of A's loss)** | **No — never triggers (see §A)** |
| 2022 rate-hike bear (2021-11-12 → 2022-07-01) | 34 | -23.95% | -23.95% | **0.00pp (0% of A's loss)** | **No — never triggers (see §A)** |

Because the BULL→BEAR transition trigger never fires ahead of either episode, Portfolio B is numerically identical to Portfolio A throughout both windows. The filter provides **zero risk reduction** for the two target episodes under its exact, as-specified definition — not because the half-weight mechanism is ineffective when active, but because the trigger condition (immediately-following, no SIDEWAYS week in between) does not match how these two real episodes' regime calendars actually evolved.

## E. TYPE-1-labeled episode check (filter should not help/hurt)

| Episode | A loss | B loss | Difference | Filter active? |
|---|---|---|---|---|
| GFC 2008-09 (2007-07-13 → 2009-03-06, 87wk) | -56.74% | -56.74% | 0.00pp | No (regime was still BULL through the peak week itself; no qualifying transition detected) |
| 2002 bear (2002-04-19 → 2002-10-04, 25wk) | -25.91% | -25.76% | **+0.15pp smaller loss for B** (0.59% of A's loss) | **Yes — 3 of the episode's 25 weeks, from the 2002-04-12 transition** |

As expected for TYPE-1 (market-driven) episodes, the filter's effect is negligible either way: GFC shows no effect (never active), and 2002 shows a trivial 0.15pp improvement (filter happened to be briefly active at the very start of that episode, but the effect size confirms it neither meaningfully helps nor hurts when the underlying signal wasn't broken).

## F. Sub-period performance

| Period | A alpha/yr | B alpha/yr | Delta | Transitions |
|---|---|---|---|---|
| 2001-2008 | 13.68% (t=3.10) | 13.68% (t=3.10) | -0.02pp | 1 (2002-04-12) |
| 2009-2016 | 2.36% (t=0.92) | 2.31% (t=0.90) | -0.05pp | 1 (2011-12-23) |
| 2017-2024 | 1.34% (t=0.40) | 1.34% (t=0.40) | 0.00pp | **0** |

No transitions occur in 2017-2024 under the locked definition — the very sub-period containing both target episodes has zero qualifying triggers, consistent with section A/D. Alpha cost is trivial (≤0.05pp/yr) in every sub-period. Not REGIME_SPECIFIC in the "helps in one period, hurts in another" sense — it simply never activates where it was meant to matter.

## G. Stable-bear check (filter OFF by construction)

Weeks classified BEAR and TRANSITION_INACTIVE: **189 of 1251**. A alpha = 17.38%/yr (t=1.79), B alpha = 17.38%/yr (t=1.79) — **identical**, exactly as required (the filter is off by construction on these weeks, so A and B use the same weights).

## H. Falsification verdict

- COVID reduction ≥ 20%: **NO (0.0pp / 0% of A's loss)**
- 2022 reduction ≥ 20%: **NO (0.0pp / 0% of A's loss)**
- Full-cycle alpha cost < 1%/yr: **YES (0.02pp cost)**
- Sharpe B ≥ A: **NO, but by a negligible margin (0.6073 vs 0.6079, Δ=-0.0007)** — effectively tied, not a meaningful failure
- Stable-bear match: **YES (identical)**

**VERDICT: MARGINAL** (bordering on non-applicable). Per the preregistered criteria, MARGINAL covers "reduces severity by < 20%" — here that is a degenerate case of exactly 0% reduction in *both* target episodes, because the filter's trigger condition never fires ahead of either one. This is not evidence that a half-IBS-weight-during-transitions mechanism is ineffective when it activates (the design cannot be judged on activation data that doesn't exist for these episodes); it is evidence that the specific "immediately following, no intervening non-BEAR week" transition definition, combined with how the existing 200d-MA/252d-trailing-return regime calendar actually behaves around real regime changes (both target crashes pass through 2-3 SIDEWAYS weeks first), makes the rule as literally specified a **non-event** for the two episodes it was designed to address. Alpha cost and stable-bear neutrality are both fine, but that is close to moot given the primary mechanism doesn't engage.

## I. Recommendation

Because the verdict is MARGINAL (not EFFECTIVE), **no V3.2 implementation is recommended from this study**, and none is built here. Per the task constraints, the transition-window length, half-weight magnitude, and "immediately following" trigger condition are **not** re-optimized or loosened after seeing this result (e.g., no retry with "BULL followed by BEAR within N weeks allowing SIDEWAYS in between," no different weight). If this risk-management idea is to be pursued further, that would require a **new, separately preregistered** hypothesis with an explicitly redefined transition trigger (e.g., one that tolerates intervening SIDEWAYS weeks, or triggers off the BEAR-classification event itself rather than requiring the immediately-prior week to be BULL) — a materially different rule, not a parameter tweak of this one, and out of scope for this study's stop condition.

---

## Files in this report directory

- `v31_transition_filter_v1.md` — this report
- `results.json` — full computed results (transition calendar, full-cycle stats, episode stats, sub-period stats, stable-bear check)
- `transition_calendar.csv` — the 2 BULL→BEAR transition dates
- `weekly_AB_nav.csv` — weekly realization_date, regime, transition_active flag, ret_A, ret_B, nav_A, nav_B, and persisted composite_ret, for all 1251 weeks

**STOP.** Reporting results per the preregistered stop condition. The filter is not implemented in live paper trading. No parameters were optimized after seeing results.
