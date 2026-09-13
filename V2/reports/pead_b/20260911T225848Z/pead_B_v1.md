# PEAD — SUB-HYPOTHESIS B

Hypothesis: `V2/research/preregistrations/pead_B_v1.json` (committed before any return computation). Reuses `strategies/earnings_continuation.py`'s universe/event-anchoring/gap-definition code verbatim; adds the 10-day horizon, the sequential-earnings exclusion, the 2020+ regime check, and leave-one-crisis-out.

**Not a retest of the falsified A/A' hypotheses.** A/A' measured the announcement-day return itself (open-to-close, and open-to-next-close) — both falsified. This measures drift starting from the announcement day's *close*, with zero overlap.

## Methodology correction found and fixed before reporting

`strategies/ibs_conditioning._annualized_mean()` — the shared helper used throughout this project — computes `(1+mean)**252 - 1`, which is only valid when each observation is a 1-trading-day return. Every series in this study is a 5/10/20-trading-day *cumulative* return, so that exponent overstates the annualized figure by roughly 5x/10x/20x (an initial run showed a nonsensical +81%/yr on the 20-day horizon). Fixed locally in `strategies/pead_b.py` with a horizon-aware `(1+mean)**(252/horizon_days) - 1`. This does **not** change any t-statistic (those come from HAC tests on the raw mean returns, untouched) — only the displayed annualized percentages. Flagging this because `earnings_continuation_A_v1`'s own persisted H3(5-day)/H4(20-day) "annualized" figures inherited the same issue; this note is for the audit trail, not something corrected retroactively in that already-closed study.

## A. Event coverage

| Metric | Value |
|---|---|
| Total SF1 earnings filings seen (2001-2024) | 621,642 |
| Valid gap measurements after all eligibility + sequential-earnings exclusion | 157,375 (25.3%) |
| Unique tickers | 4,961 |
| Events/year average (full universe) | 6,558 |
| LONG bucket (gap > +2%) | 17,899 events (11.37% of valid measurements) |
| AVOID bucket (gap < -2%) | 15,789 events (10.03% of valid measurements) |

## B. 2020+ regime check (run first) — primary threshold, all three series

| Horizon | Series | Full sample (2001-2024) | Pre-2020 (2001-2019) | Post-2020 (2020-2024) |
|---|---|---|---|---|
| H1 (5d) | LONG | -8.0%/yr, t=-1.15 | +1.0%/yr, t=0.12 | **-31.8%/yr, t=-2.35** |
| H1 (5d) | AVOID | -12.6%/yr, t=-1.86 | -14.2%/yr, t=-1.95 | -7.4%/yr, t=-0.45 |
| H1 (5d) | **SPREAD** | **+15.5%/yr, t=2.00** | **+28.8%/yr, t=3.08** | -15.1%/yr, t=-1.15 |
| H2 (10d) | LONG | +0.7%/yr, t=0.13 | +6.7%/yr, t=1.10 | -16.4%/yr, t=-1.52 |
| H2 (10d) | AVOID | -9.4%/yr, t=-1.86 | -8.5%/yr, t=-1.58 | -12.2%/yr, t=-1.00 |
| H2 (10d) | **SPREAD** | **+13.7%/yr, t=2.86** | **+16.7%/yr, t=3.00** | +5.4%/yr, t=0.59 |
| H3 (20d) | LONG | +2.9%/yr, t=0.72 | +6.1%/yr, t=1.34 | -6.6%/yr, t=-0.77 |
| H3 (20d) | AVOID | -0.4%/yr, t=-0.10 | +0.2%/yr, t=0.05 | -2.4%/yr, t=-0.24 |
| H3 (20d) | SPREAD | +4.8%/yr, t=1.57 | +6.8%/yr, t=1.92 | -0.7%/yr, t=-0.12 |

**Regime verdict, by horizon (spread series, the economically complete measure):**
- H1: full-sample **significant** (t=2.00) → post-2020 collapses and **reverses sign** (t=-1.15). **HISTORICAL_ARTIFACT pattern.**
- H2: full-sample **significant** (t=2.86) → post-2020 collapses to near-zero (t=0.59, still positive but not significant). **HISTORICAL_ARTIFACT pattern.**
- H3: full-sample marginal (t=1.57, below the 2.0 bar) → post-2020 near-zero (t=-0.12). Unclear/weak throughout, not even historically robust.

**But the separate, more important finding for a long-only implementation:** the **LONG leg alone never reaches t>1.5 in any window** — full-sample, pre-2020, or post-2020, on any of the three horizons (best case: H3 pre-2020, t=1.34). The full-sample "significance" in the spread is driven substantially by the **AVOID (miss) leg underperforming** (t=-1.86 to -1.95 pre-2020/full-sample on H1/H2) — the same "spread significance comes from the wrong leg" pattern this project has already documented for inverse-momentum (row 16 of the tracker). A signal whose monetizable (long-only) leg never clears significance, even in its best historical period, is not a promising long-only second engine regardless of what happens in 2020+.

**Gate check: post-2020 t < 1.0 on ALL THREE horizons (spread series)?** H1 spread t=-1.15 (< 1.0 ✓), H2 spread t=0.59 (< 1.0 ✓), H3 spread t=-0.12 (< 1.0 ✓). **YES — fatal, per the preregistered stop condition.**

## C-F. Full characterization / factor regression / leave-one-crisis-out / size analysis

**NOT RUN.** Per the preregistration's locked stop condition, this study stops at the regime check because all three horizons fail the post-2020 gate on the spread series. Running the remaining, more expensive analyses (3x3 threshold/horizon table, FF5+UMD regression, leave-one-crisis-out, size terciles) on a signal that has already collapsed in the live regime — and whose long-only leg was never significant even historically — would not change the verdict and is exactly the kind of continued-investigation-past-a-failed-gate this research governance explicitly avoids.

## G. Falsification verdict

Applying the preregistered criteria precisely (not just the stop-gate's shorthand label):

| Criterion | Result |
|---|---|
| Full-sample significant on at least one horizon (spread, t>2.0)? | **YES** — H1 (t=2.00), H2 (t=2.86) |
| 2020+ t > 1.5 on the same horizon(s)? | **NO** — H1 post-2020 t=-1.15, H2 post-2020 t=0.59 |
| H1 or H2 **long-leg** t > 2.0 (the VIABLE bar)? | **NO** — long-leg never exceeds t=1.34 anywhere |
| H1, H2, AND H3 all t < 1.5 at the primary threshold? | True for the **long leg** (max t=1.34) but **not** true for the spread (H1=2.00, H2=2.86) |

**VERDICT: HISTORICAL_ARTIFACT**, with an additional, independently disqualifying finding: **the long-only-implementable leg of this signal was never viable even in its best historical period.** The spread measure shows the textbook pattern already seen in `earnings_continuation_A_v1` (full-sample significant, 2020+ collapse/reversal) — so this is corroborating evidence for the SAME regime-decay finding the prior study made, not a new, independent discovery of decay. But unlike a case where a genuinely tradeable long-only signal decayed, here the long leg specifically (the only side of this signal a long-only portfolio could actually hold) was weak throughout all 24 years, full-sample and every subperiod. This is a stronger and more final reason to close this line than the 2020+ decay alone: there is no historical period in which PEAD sub-hypothesis B, implemented long-only with the gap-direction proxy at the 2% threshold, would have been a viable addition.

**This result does not reopen or re-litigate earnings_continuation_A/A' (still correctly FALSIFIED for the announcement-day return) — it is a separate, newly-falsified claim about the subsequent multi-day window, arrived at independently.**

## Recommendation

PEAD sub-hypothesis B, as specified, is **not** a viable orthogonal second engine. This does not mean post-earnings drift is impossible in this data — it means this specific construction (earnings-gap-direction as the surprise proxy, long-only, 5/10/20-day windows, 2% primary threshold) does not clear the bar. A materially different construction (e.g., an actual earnings-surprise magnitude from analyst estimates rather than the gap-direction proxy, or a long-short implementation that monetizes the avoid-leg's underperformance via a short sleeve) would be a **new, separately preregistered hypothesis**, not a parameter tweak of this one — consistent with the research-governance constraint against continued parameter-searching a closed line. Per item 14's backlog, the next PEAD-adjacent idea (if pursued) should be registered as its own study, not a retry of sub-hypothesis B.

---

## Files in this report directory

- `pead_B_v1.md` — this report
- `results.json` — coverage and the long-leg-only regime check as originally computed by `scripts/run_pead_b.py`
- `regime_check_full.json` — the complete long/avoid/spread regime-check table (all numbers cited above)
- `events.csv` — full event-level panel (157,375 rows) used for all of the above

**STOP.** FALSIFIED/HISTORICAL_ARTIFACT per the preregistered gate. No PEAD paper track built, no holdout consumed, V3.2 and paper-trading Tracks 1-3 untouched, no thresholds or horizons re-optimized after seeing results. Reporting and waiting for instruction.
