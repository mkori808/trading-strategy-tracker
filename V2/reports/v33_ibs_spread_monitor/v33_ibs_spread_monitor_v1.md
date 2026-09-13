# V3.3 IBS SPREAD MONITOR (attempt 3) — FALSIFIED

Hypothesis: `V2/research/preregistrations/v33_ib_spread_monitor_v1.json` (committed before the trigger series was computed; underlying `ibs_spread` values reused verbatim from `v31_deepening_q2q3`).

## Diagnostic gate

**Total FILTER_ON weeks: 516 / 1251 (41.25% of sample)** — well above the 25% elevated-activity flag.

Avg `ibs_spread` in FILTER_ON weeks: **+0.225%**
Avg `ibs_spread` in FILTER_OFF weeks: **+0.230%**
**Noise check: FAIL** — the two averages are essentially identical (both positive), meaning FILTER_ON weeks are *not* systematically worse IBS weeks than FILTER_OFF weeks. The trigger is not distinguishing genuine signal failure from noise.

Sub-period breakdown:

| Period | Weeks ON | Total | % |
|---|---|---|---|
| 2001-2008 | 122 | 416 | 29.3% |
| 2009-2016 | 205 | 418 | 49.0% |
| 2017-2024 | 189 | 417 | 45.3% |

**COVID 2020 (onset 2019-12-20):** first FILTER_ON = **2019-12-20 itself, 0 weeks after onset**. Fires within 4 weeks: **YES**.
**2022 bear (onset 2021-11-12):** first FILTER_ON = **2021-11-12 itself, 0 weeks after onset**. Fires within 4 weeks: **YES**.

**Diagnostic timing verdict: PASS.** This is the first of the four V3.3 attempts to satisfy the 4-week timing bar for both episodes — as expected, a trigger built on the signal's own performance responds immediately rather than waiting for SPY's price/vol to catch up (weeks 11/12 and 11/5 in the two SPY-based attempts).

However, PASS on timing is not sufficient — per the preregistration, the noise check is a mandatory part of the falsification criteria, evaluated alongside (not gated out by) the timing pass. Since it fails outright, the study proceeds to full evaluation as instructed, but the outcome below should be read knowing the trigger's ON/OFF split does not correspond to genuinely bad vs. good IBS weeks.

## Full-cycle comparison (2001-2024)

| Metric | A (V3.2) | B (V3.3 attempt 3) | Delta |
|---|---|---|---|
| CAGR | 14.93% | 13.60% | -1.33pp |
| Alpha/yr (FF5+UMD, HAC) | 6.50% | 5.62% | -0.88pp |
| Alpha t-stat | 2.84 | 2.67 | -0.17 |
| Sharpe | 0.700 | 0.687 | -0.013 |
| Max drawdown | -57.28% | -53.78% | +3.50pp (better) |
| Worst rolling 12mo | -50.49% | -47.55% | +2.94pp (better) |

Alpha cost (0.88pp) is within the 2%/yr tolerance; Sharpe (-0.013) is within the -0.05 tolerance. Max drawdown and worst-12mo both improve — but this is consistent with simply running at reduced IBS weight 41% of the time (a broad de-risking effect), not with well-timed protection, given the noise-check failure above.

## TYPE 2 episode comparison

| Episode | A loss | B loss | Reduction | >= 15%? |
|---|---|---|---|---|
| COVID 2020 | -48.56% | -44.51% | **8.33%** | **NO** |
| 2022 bear | -23.99% | -22.96% | **4.31%** | **NO** |

Both episodes fall **below the 10% floor** for even a MARGINAL rating. Filter was ON for 12/13 weeks of the COVID window and 17/33 weeks of the 2022 window — the trigger is active most of the time during both episodes (consistent with it also being on ~41-49% of all weeks generally), so the small improvement is plausibly just the mechanical effect of running lighter IBS weight through a period that was going to be bad regardless, not evidence of timely, discriminating protection.

## TYPE 1 check

| Episode | A loss | B loss | Delta | Filter ON weeks |
|---|---|---|---|---|
| GFC 2008-09 | -57.28% | -53.78% | +3.50pp (better) | 40 / 86 (46.5%) |
| 2002 bear | -27.44% | -25.82% | +1.62pp (better) | 5 / 24 (20.8%) |

Unlike the two SPY-price-based triggers (which either never fired during GFC or fired heavily because vol was extreme), this trigger fires in **46.5%** of GFC weeks — consistent with the regime-decomposition finding that IBS still had *positive* alpha in BEAR regimes overall, yet the spread-monitor trigger is flagging nearly half of GFC as "signal failing." Combined with the noise-check failure, this is a second piece of evidence that the trigger's ON state is not a reliable read on whether IBS is actually failing — it fires broadly across both good and bad bear-market weeks alike.

## Sub-period breakdown

| Period | A alpha | B alpha | Delta | Filter ON% |
|---|---|---|---|---|
| 2001-2008 | 15.48% | 14.56% | -0.92pp | 29.3% |
| 2009-2016 | 2.81% | 2.01% | -0.80pp | 49.0% |
| 2017-2024 | 2.10% | 1.33% | -0.77pp | 45.3% |

Filter is on roughly a third to a half of all weeks in every sub-period — far more than "occasional crisis protection," consistent with a trigger that is not selective.

## Noise check result

Avg spread in FILTER_ON weeks: **+0.225%** (target: < -0.2%)
Avg spread in FILTER_OFF weeks: **+0.230%**
**Trigger is firing on signal failure, not noise: NO.**

## Falsification verdict

| Criterion | Result |
|---|---|
| Diagnostic timing gate | PASS (both episodes, 0 weeks lag) |
| COVID reduction >= 15% | NO (8.3pp) |
| 2022 reduction >= 15% | NO (4.3pp) |
| Alpha cost < 2%/yr | YES (0.88pp) |
| Sharpe >= A - 0.05 | YES (-0.013) |
| Noise check passes | **NO** |

**VERDICT: FALSIFIED.** Both TYPE 2 reductions are below the 10% floor required even for a MARGINAL rating, and the noise check fails independently and decisively — the trigger's ON/OFF split (41% of all weeks) does not correspond to genuinely bad vs. good IBS weeks (avg spread ~0.225% vs ~0.230%, both positive). The modest drawdown/Sharpe improvements in the full-cycle and episode tables are best explained as the generic effect of running the composite at lower IBS weight nearly half the time, not as evidence of a working early-warning mechanism.

## Honest statement

This is the fourth attempt at a mechanical TYPE 2 protection trigger for V3.2, and the fourth failure, via four structurally different mechanisms:

1. **Regime label (Q6):** never engages — too coarse, blocked by intervening SIDEWAYS weeks.
2. **Vol level (attempt 1):** engages, but 5-11 weeks too late, and overactive.
3. **Drawdown velocity (attempt 2):** engages 11-12 weeks too late — no better than vol level despite being a more "direct" market-based measure.
4. **IBS spread self-monitor (attempt 3):** engages immediately (0 weeks lag — the timing problem is solved), but the trigger itself doesn't discriminate — it's on 41% of the time and fires on weeks that are, on average, no worse for IBS than the weeks it's off.

Across the four attempts, the timing problem and the discrimination problem have never been solved simultaneously: the two market-based triggers are timely enough only when the target episodes have already been running for months, and the one immediately-responsive trigger tried here isn't actually selective. This pattern — four independently-reasonable designs, four failures for different reasons — is more consistent with there being no simple, mechanically clean, weekly-frequency trigger that both leads these two specific episodes and discriminates genuine IBS failure from ordinary week-to-week noise, than with any one design being fixable by further parameter tweaking.

**Recommendation: accept V3.2 (V3.1 minus Sector RS, confirmed +0.88pp/yr alpha, method-controlled) as the current best specification.** Per this study's own preregistered instruction, TYPE 2 protection via a simple mechanical trigger is not pursued further within this line of research. Separate, explicitly preregistered bear-overlay or crisis-sleeve research — mechanisms that don't rely on a single weekly on/off trigger threshold — would be a different class of study, not a fifth variant of this one.

---

## Files in this report directory

- `v33_ibs_spread_monitor_v1.md` — this report
- `full_results.json` — diagnostic, full-cycle, episode, and sub-period numbers
- `weekly_states.csv` — full 1251-week series: realization_date, signal_date, ibs_spread, rolling_spread_4wk, filter_state, ret_A, ret_B

**STOP.** Falsified per the preregistered criteria (noise check fails; both TYPE 2 reductions below the 10% MARGINAL floor). No thresholds were optimized after seeing results, and no fifth trigger redesign was attempted within this study. Reporting and waiting for instruction.
