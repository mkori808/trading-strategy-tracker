# V3.3 VOLATILITY FILTER — Trigger Diagnostic (STOP)

Hypothesis: `V2/research/preregistrations/v33_vol_filter_v1.json` (committed before the vol series, thresholds, or filter states were computed).

## Result: diagnostic gate FAILS. Stopping here per the preregistered stop condition.

## A. Trigger diagnostic

**Total FILTER_ON weeks: 324 / 1251 (25.9% of sample)**

Sub-period breakdown:

| Sub-period | Weeks ON | Total weeks | % | Overactive (>20%)? |
|---|---|---|---|---|
| 2001-2008 | 79 | 416 | 19.0% | No |
| 2009-2016 | 105 | 418 | 25.1% | **Yes** |
| 2017-2024 | 140 | 417 | 33.6% | **Yes** |

The vol trigger is on for roughly a quarter of the full sample and over a third of 2017-2024 — far more often than the regime-label trigger in Q6 (0.64% of weeks). This is the expected tradeoff of a faster, content-free vol signal vs a slower, more selective regime label, and it is flagged here as OVERACTIVE in two of three sub-periods per the preregistered >20% rule.

**COVID 2020 trigger:**
- Locked episode onset (same portfolio-drawdown peak used throughout this research thread, from `v31_deepening_q2q3` / `composite_v3_2`): **2019-12-20**
- First FILTER_ON: **2020-03-06** — **11 weeks after** the locked onset
- Trigger fires within 4 weeks: **NO**

8-week window around the locked onset (full table in `weekly_vol_states.csv`):

| realization_date | vol20 | vol_75th | vol_60th | state |
|---|---|---|---|---|
| 2019-12-20 | 7.96% | 15.24% | 11.55% | FILTER_OFF |
| 2019-12-27 | 7.72% | 15.24% | 11.45% | FILTER_OFF |
| 2020-01-03 | 7.49% | 15.24% | 11.45% | FILTER_OFF |
| 2020-01-10 | 7.25% | 15.24% | 11.45% | FILTER_OFF |
| 2020-01-17 | 7.22% | 15.24% | 11.45% | FILTER_OFF |
| 2020-01-24 | 7.23% | 15.24% | 11.39% | FILTER_OFF |
| 2020-01-31 | 8.18% | 15.24% | 11.32% | FILTER_OFF |
| 2020-02-07 | 11.99% | 15.24% | 11.32% | FILTER_OFF |
| 2020-02-14 | 13.52% | 15.24% | 11.32% | FILTER_OFF |
| 2020-02-21 | 13.36% | 15.24% | 11.39% | FILTER_OFF |
| 2020-02-28 | 14.11% | 15.24% | 11.45% | FILTER_OFF (close, still under 75th) |

**Important diagnostic finding:** the locked episode onset (2019-12-20) is a *portfolio* drawdown peak, not SPY's own price peak — SPY itself kept making new highs for another ~8 weeks, topping around **2020-02-14** before the crash accelerated. Measured against SPY's own price peak instead, FILTER_ON at 2020-03-06 is only **~2.9 weeks** later — close to the brief's claimed "within 2 weeks." Measured against the locked portfolio-drawdown onset that this entire research thread (Q6, composite_v3_2) has used for "the COVID episode," it is **11 weeks** later. Both readings are reported; the locked, previously-established onset is used for the gate decision below, since redefining "onset" only after seeing which definition makes the brief's claim true would be exactly the kind of post-hoc parameter-shopping this study's own preregistration (and Q6's precedent) rules out.

**2022 bear trigger:**
- Locked episode onset (same as above): **2021-11-12**
- First FILTER_ON: **2021-12-17** — **5 weeks after** the locked onset
- Trigger fires within 4 weeks: **NO** (off by 1 week under this reading; the brief's claimed "within 3 weeks" is off by 2 weeks under any onset reading checked)

## B. Diagnostic gate

Per `v33_vol_filter_v1.json`'s locked `diagnostic_gate`: *"If the trigger never fires within 4 weeks of onset in EITHER target episode, STOP after the diagnostic... do not proceed to the full evaluation."*

- COVID 2020: fires at 11 weeks — **fails the 4-week bar**
- 2022 bear: fires at 5 weeks — **fails the 4-week bar**

**Both** episodes fail, so the gate is not met. **Stopping here, as preregistered.** Full-cycle comparison, TYPE 2/TYPE 1 episode tables, and the falsification verdict are **not computed** — producing them after a failed gate would violate the study's own locked stop condition, and any subsequent claim of "EFFECTIVE" would be exactly as unverifiable as the brief's original 23%/20% claim for Q6.

**Important distinction from Q6:** this is not the same failure mode as the regime-label trigger. The vol trigger does eventually activate inside both episode windows (week 11 of a ~14-week COVID episode; week 5 of a ~34-week 2022 episode) — it is not a 0%-engagement non-event like Q6. It is a **too-slow** trigger relative to the specific 4-week early-warning bar this brief and this preregistration both set, not a trigger that never engages at all. Whether "fires eventually, well after the worst of the drawdown for COVID" but "fires reasonably early relative to a 34-week 2022 episode" would still provide meaningful risk reduction is an open, unanswered question — deliberately left unanswered here, since answering it would require running the full evaluation the gate is designed to block until the timing question is resolved on its own terms (i.e., via a redesigned trigger, not via loosening this gate).

## C. What would be needed to proceed

Per the brief's own framing ("If trigger never fires in either episode... The trigger needs further redesign"), a next step — **not undertaken by this study** — would be a new, separately preregistered hypothesis with a redefined trigger, e.g.:
- A shorter realized-vol lookback (5-10 days instead of 20) for faster response to acceleration, or
- A vol *rate-of-change* trigger (e.g. vol20 crossing its own 4-week-ago value by some margin) rather than a level-vs-trailing-percentile trigger, or
- Reconciling which "onset" definition should gate the 4-week check — the portfolio-drawdown peak used throughout this thread, or SPY's own price peak (which the brief's 2-week/3-week claims appear to implicitly assume for COVID, though not clearly for 2022).

None of these are implemented or tested here, per the constraint against redesigning the trigger after seeing this result.

---

## Files in this report directory

- `v33_vol_filter_v1.md` — this report
- `diagnostic_results.json` — full diagnostic numbers (sub-period activation, both episode timing readings)
- `weekly_vol_states.csv` — full 1251-week series: realization_date, signal_date, vol20, vol_75th, vol_60th, filter_state

**STOP.** Trigger diagnostic gate failed for both target episodes against the locked, previously-established episode onset dates. Full-cycle comparison, episode tables, and falsification verdict were not computed. No parameters were redesigned or re-optimized after seeing this result. Reporting and waiting for instruction.
