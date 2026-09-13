# V3.3 DRAWDOWN VELOCITY FILTER (attempt 2) — Diagnostic Gate (STOP)

Hypothesis: `V2/research/preregistrations/v33_drawdown_velocity_v1.json` (committed before the SPY 4-week-return series or filter states were computed).

## Result: diagnostic gate FAILS for both episodes. Stopping here per the preregistered stop condition.

## A. Trigger diagnostic

**Total FILTER_ON weeks: 92 / 1251 (7.35% of sample) — not overactive (<20%).**

Sub-period breakdown:

| Sub-period | Weeks ON | Total weeks | % |
|---|---|---|---|
| 2001-2008 | 45 | 416 | 10.8% |
| 2009-2016 | 22 | 418 | 5.3% |
| 2017-2024 | 25 | 417 | 6.0% |

This trigger is far less active than the vol-level trigger (7.35% vs 25.9% of all weeks in `v33_vol_filter_v1`) — it responds only to genuinely sharp 4-week SPY declines, not to elevated-but-not-crashing volatility. Overactivity is not a concern here.

**COVID 2020:**
- Locked onset: **2019-12-20**
- First FILTER_ON: **2020-03-06** — **11 weeks after** onset
- Fires within 4 weeks: **NO**

8-week window around onset — `spx_4wk_ret` stays *positive* (+2% to +4.7%) throughout the entire displayed window (2019-10-25 to 2020-02-14); SPY had not yet begun its rapid decline. The actual >7%-in-4-weeks move only happens in the back half of February into March 2020, well outside the 4-week diagnostic bar measured from the locked portfolio-drawdown onset.

**2022 bear:**
- Locked onset: **2021-11-12**
- First FILTER_ON: **2022-02-04** — **12 weeks after** onset
- Fires within 4 weeks: **NO**

Same pattern — `spx_4wk_ret` oscillates between -3.9% and +7.0% through the displayed window (2021-09-17 to 2022-01-07), never crossing the -7% threshold. The 2022 bear's sharper legs down (which do eventually trip the trigger) start in earnest around Jan-Feb 2022, roughly 12 weeks after the locked portfolio-drawdown onset date.

## B. Diagnostic gate

Per `v33_drawdown_velocity_v1.json`'s locked `diagnostic_gate`: *"If the trigger does not fire within 4 weeks of onset for EITHER target episode, STOP after the diagnostic... Do not proceed to full evaluation."*

- COVID 2020: 11 weeks — **fails**
- 2022 bear: 12 weeks — **fails**

**Both fail. Stopping here, as preregistered.** Full-cycle comparison, TYPE 2/GFC/sub-period tables, and the falsification verdict are **not computed**.

## C. Pattern across all three trigger attempts

| Trigger | COVID weeks-after-onset | 2022 weeks-after-onset | Overactive? |
|---|---|---|---|
| Q6: BULL→BEAR regime label | never fires | never fires | No (0.64% of weeks) |
| V3.3 attempt 1: vol level (75th/60th pct) | 11 | 5 | Yes (25.9% of weeks) |
| V3.3 attempt 2: drawdown velocity (-7%/-3%) | 11 | 12 | No (7.35% of weeks) |

A consistent structural fact emerges across all three designs: the locked portfolio-drawdown "onset" dates (2019-12-20, 2021-11-12) precede the *market's* actual sharp-decline phase by roughly 8-12 weeks in both episodes — the portfolio peaks and begins a slow bleed well before SPY itself breaks down violently. No trigger built on SPY's own price/vol behavior can fire within 4 weeks of *that* onset, because SPY has not yet moved enough to trigger anything at that point; SPY is still near its own highs. This is a property of how the two onset dates were originally defined (portfolio NAV peaks, not market peaks), not a flaw specific to either trigger design tried so far.

This suggests any market-price-based trigger (vol, velocity, or otherwise) checked against these two specific locked onset dates under a 4-week bar is structurally unlikely to pass, regardless of the trigger's exact functional form — the bar and the onset definition are in tension with each other for these two episodes specifically.

## D. What this does not resolve

This is not evidence that a fast-response IBS-protection mechanism is worthless — only that no trigger tested so far (regime label, vol level, drawdown velocity) satisfies the specific "within 4 weeks of the locked portfolio-drawdown onset" bar for both target episodes. Two different classes of next step exist, neither undertaken here per the no-redesign-after-seeing-results constraint:
- Redefine the diagnostic bar or the onset reference point (e.g., measure from SPY's own price peak/breakdown rather than the portfolio's NAV peak — flagged as a plausible reading in `v33_vol_filter_v1` for COVID specifically), or
- Accept that this class of trigger cannot front-run these two episodes within 4 weeks, and evaluate a longer-tolerance version (e.g., "does the filter still meaningfully reduce loss even if it engages at week 5-12, given the episode itself runs 13-34 weeks") as its own explicitly preregistered study, not a loosening of this one.

Neither is decided or attempted here.

---

## Files in this report directory

- `v33_drawdown_velocity_v1.md` — this report
- `diagnostic_results.json` — full diagnostic numbers
- `weekly_states.csv` — full 1251-week series: realization_date, signal_date, spx_4wk_ret, filter_state

**STOP.** Trigger diagnostic gate failed for both target episodes against the locked onset dates. Full-cycle comparison, episode tables, GFC tradeoff, and falsification verdict were not computed. No thresholds or lookback windows were redesigned or re-optimized after seeing this result. Reporting and waiting for instruction.
