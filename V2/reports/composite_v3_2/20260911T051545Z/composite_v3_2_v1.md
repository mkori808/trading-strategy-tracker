# COMPOSITE V3.2 — Development Period 2001-2024

> **SUPERSEDED — INVALID METHODOLOGY.** This historical variant included the failed transition filter and used a linear-blend proxy rather than a rebuilt score-ranked portfolio. It is preserved for audit only. Canonical frozen V3.2 is IBS 0.69 / RSI2 0.25 / Turnaround Tuesday 0.06, with Sector Rotation removed, no Gap Fade, and no transition filter. See `V2/research/canonical/v3_2_specification.json`.

Hypothesis: `V2/research/preregistrations/composite_v3_2_v1.json` (committed before any computation).

## Prior-claims note (read first)

The task brief asserted two "confirmed" improvements. Checking both against existing repo artifacts before computing anything:

- **Improvement 1 (remove Sector RS): CONFIRMED.** `v31_regime_decomposition`'s `sector_rs` block shows full-cycle alpha **-2.61%/yr** (t=-1.09), negative in BULL (-2.04%), BEAR (-7.65%), and SIDEWAYS (-4.25%) — matches the brief's -2.6%/yr and "drag in all regimes" claim closely.
- **Improvement 2 (transition filter): NOT CONFIRMED — directly contradicted.** `v31_transition_filter_v1` (Q6, run the same day) tested this exact mechanism on V3.1 and found only **2** BULL→BEAR transitions in 2001-2024 (2002-04-12, 2011-12-23), **8** TRANSITION_ACTIVE weeks total, and that **neither COVID 2020 nor the 2022 bear ever triggers the filter** (both pass through intervening SIDEWAYS weeks first). Measured drawdown reduction there was **0.00pp / 0%** for both episodes, not 23%/20%, and the study's verdict was **MARGINAL**, not EFFECTIVE. No other artifact in the repo produces the brief's cited figures.

Per the same precedent `v31_transition_filter_v1.json` set, this is reported rather than silently repeated, and the filter is tested anyway, fresh, with V3.2's own weights — but with the expectation (confirmed below) that it recurs with the same non-engagement against the two target episodes, since the regime calendar is unchanged.

## A. Specification confirmation

| Check | Result |
|---|---|
| Transition activations | **2** transitions, **8** TRANSITION_ACTIVE weeks (matches Q6 exactly — same calendar, reused verbatim) |
| Sector RS contribution removed | **YES**, confirmed (V3.2 blend uses only `ibs_ret`, `rsi2_ret`, `tue_ret`) |
| Filter engages before COVID 2020 / 2022 bear | **NO** — 0 TRANSITION_ACTIVE weeks inside either episode window (confirms Q6's finding recurs unchanged) |
| Adverse interaction (a): does removing Sector RS change *when* the filter fires? | **NO** — identical transition calendar to Q6, by construction (regime calendar doesn't depend on Sector RS) |
| Adverse interaction (b)/(c): see full-cycle and regime tables below | reported, not assumed |

**Weight-sum note:** V3.2's brief-specified transition weights (0.345/0.405/0.06) sum to **0.81**, a 19pp gross-exposure shortfall during TRANSITION_ACTIVE weeks — larger than Q6's ~6pp shortfall. Per the no-reoptimization constraint, this is used as locked and reported, not corrected.

## B. Methodology caveat (read before the tables)

V3.1 here is the **actual persisted, score-blended composite_ret** (the real live spec). V3.2 does not exist as a score-blended composite — its ranking/holdings logic was not rebuilt (precluded by "reuse existing infrastructure, don't recompute signals") — so it is approximated as a **linear blend of the standalone signal returns** already in `weekly_panel.csv`, the same method `v31_transition_filter_v1` used for its Portfolio B.

Sanity check (identical to Q6's): V3.1's own linear-blend reconstruction vs its true composite_ret has mean abs diff 0.18pp/week, max 2.19pp, corr 0.9975 — but the **signed** mean diff is -0.038pp/week, i.e. the linear-blend method *systematically understates* the true score-blended composite by roughly **1.9pp/yr of alpha** (5.55%/yr linear-blend alpha vs 7.44%/yr actual, both measured on V3.1's own unchanged spec). This gap is a property of the approximation method itself, not of any V3.2 signal change, and it is large relative to the ~1pp/yr deltas below — so two comparisons are reported:

1. **Primary (as literally requested):** actual V3.1 composite vs linear-blend V3.2 — what the brief's acceptance criteria apply to directly.
2. **Method-controlled:** V3.1's own linear-blend reconstruction vs V3.2's linear blend — isolates the effect of the two signal changes from the proxy-method gap.

## C. Full-cycle comparison (2001-2024)

**Primary (actual V3.1 vs linear-blend V3.2):**

| Metric | V3.1 | V3.2 | Delta |
|---|---|---|---|
| CAGR | 16.08% | 14.87% | -1.21pp |
| Alpha/yr (FF5+UMD, HAC, weekly) | 7.44% | 6.43% | -1.01pp |
| Alpha t-stat | 3.12 | 2.81 | -0.31 |
| Sharpe | 0.735 | 0.698 | -0.037 |
| Sortino | 0.993 | 0.948 | -0.045 |
| Max drawdown | -56.32% | -57.28% | -0.96pp (worse) |
| Worst rolling 12mo | -49.61% | -50.49% | -0.88pp (worse) |
| Annual turnover / cost | not computable from persisted data (no per-week holdings; same limitation as Q6) | — | — |

**Method-controlled (V3.1 linear-blend vs V3.2 linear-blend — isolates the two signal changes):**

| Metric | V3.1 (linear) | V3.2 (linear) | Delta |
|---|---|---|---|
| Alpha/yr | 5.55% | 6.43% | **+0.88pp** |
| Alpha t-stat | 2.57 | 2.81 | +0.24 |
| Sharpe | 0.676 | 0.698 | **+0.022** |
| CAGR | 14.02% | 14.87% | +0.85pp |
| Max drawdown | -57.11% | -57.28% | -0.17pp (roughly flat) |

**Reading both together:** under the same measurement method, the two signal changes themselves are mildly *positive* for alpha, Sharpe and CAGR, and roughly neutral for drawdown. The primary table's apparent decline is driven almost entirely by switching V3.2's measurement from the true composite to the linear-blend proxy (a ~1.9pp/yr alpha gap that exists for V3.1 too, not something the two changes caused).

## D. Regime comparison (primary method, as requested)

| Regime | V3.1 alpha | V3.2 alpha | Delta |
|---|---|---|---|
| BULL | 3.41% (t=1.87) | 2.35% (t=1.38) | -1.06pp |
| BEAR | 20.80% (t=1.98) | 18.82% (t=1.86) | -1.98pp |
| SIDEWAYS | 9.82% (t=1.10) | 10.54% (t=1.20) | **+0.72pp** |

SIDEWAYS improves as expected (Sector RS was most negative there: -4.25%/yr). BULL and BEAR decline in the primary comparison, but — per §C — this is consistent with the linear-blend proxy gap (which is not regime-specific by construction) rather than a regime-specific effect of the two changes; it is reported as-is per the "do not re-derive" constraint.

## E. TYPE 2 (SIGNAL FAILURE) episode comparison

| Episode | V3.1 loss | V3.2 loss | Reduction | Filter active? |
|---|---|---|---|---|
| COVID 2020 | -49.27% | -48.56% | 0.71pp (1.4% of V3.1's loss) | **No — 0 TRANSITION_ACTIVE weeks in window** |
| 2022 bear | -25.26% | -23.99% | 1.27pp (5.0% of V3.1's loss) | **No — 0 TRANSITION_ACTIVE weeks in window** |

Both episodes show a small improvement, but since the filter never activates in either window (confirming Q6), **none of this improvement is attributable to the transition filter** — it is attributable to removing Sector RS's drag during these weeks, the same mechanism already characterized in `v31_regime_decomposition`. This is a materially smaller and differently-sourced improvement than the brief's claimed 23%/20% filter-driven reductions.

## F. TYPE 1 (MARKET DRIVEN) episode comparison

| Episode | V3.1 loss | V3.2 loss | Delta | Filter active? |
|---|---|---|---|---|
| GFC 2008-09 | -56.32% | -57.28% | -0.96pp (worse) | No |
| 2002 bear | -25.14% | -27.03% | -1.89pp (worse) | Yes — 2 of 24 weeks |

Both TYPE 1 episodes get slightly *worse* under V3.2 in the primary comparison — consistent with the proxy-method gap (§B/C) rather than a true effect, since GFC has zero filter activity and 2002's 2 active weeks are too few to explain a -1.89pp swing on their own.

## G. Acceptance check

| Criterion | Result |
|---|---|
| V3.2 Sharpe >= V3.1 Sharpe | **NO** (0.698 vs 0.735, primary); marginally YES method-controlled (0.698 vs 0.676) |
| V3.2 Max DD <= V3.1 Max DD | **NO** (-57.28% vs -56.32%, worse by 0.96pp) |
| Alpha within 1.5%/yr of V3.1 | YES (-1.01pp, primary) |
| TYPE 2 losses smaller | YES, but not for the reason claimed (Sector RS removal, not the filter — filter never engages) |
| No adverse interaction detected | Partially — regime calendar is confirmed independent of Sector RS (a), but the claimed filter effectiveness (the brief's core premise for Improvement 2) does not hold at all (0% engagement against both target episodes), which is itself an adverse finding about the brief, not about how the two changes interact with each other |

**VERDICT: NEEDS REVIEW.**

Reasons:
1. The brief's headline claim for Improvement 2 ("EFFECTIVE... COVID 23%, 2022 20%") is not supported by any computed artifact in this repo and is directly contradicted by the one existing test of this exact mechanism (Q6, verdict MARGINAL, 0% reduction). Re-running it here for V3.2 reproduces the same non-engagement.
2. Two of five acceptance criteria fail in the comparison as literally specified (Sharpe, Max DD), though the shortfall is largely attributable to a disclosed methodology gap (actual composite vs linear-blend proxy) rather than to the signal changes themselves — the method-controlled comparison shows the changes are mildly net-positive.
3. The TYPE 2 episode improvement that does appear is real but comes entirely from Sector RS removal, not from the transition filter as the brief frames it — the filter contributes nothing measurable to either target episode.

## H. Paper trading configuration

**Not provided.** Per the preregistration's stop condition and the task's own constraint ("do not launch Track 4 without explicit instruction after reviewing the development comparison"), and because the verdict is NEEDS REVIEW rather than LAUNCH, no Track 4 configuration is produced here.

---

## Files in this report directory

- `composite_v3_2_v1.md` — this report
- `results.json` — full computed results (both comparison methods, regime/episode breakdowns)
- `weekly_panel_v32.csv` — weekly realization_date, regime, transition_active, signal returns, V3.1/V3.2 series (actual + both linear reconstructions), factor columns

**STOP.** Reporting results per the preregistered stop condition. Track 4 is not launched. No bear overlay or crisis sleeve was run. No parameters were optimized after seeing results.
