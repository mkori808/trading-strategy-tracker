# Frozen configuration — Dual Momentum forward test

**Frozen:** 2026-08-11
**Status:** FROZEN. Thresholds set, committed timestamped. **Not edited after
this point.** Changing this file after commit invalidates the forward test --
preventing adjustment while the test runs is the entire purpose of writing it
down.

Everything below is fixed for the duration of the test. The point of writing it
down is that the failure mode is adjusting the config three months in because
it's lagging, which converts an out-of-sample test back into an in-sample one.

## Configuration

| field | value |
|---|---|
| strategy | Dual Momentum (cross-sectional) |
| universe | `EQUITY_UNIVERSE` — July 2021 DJIA roster, 29 names (WBA excluded) |
| lookback | 189 trading days |
| top_n | 5 |
| rebalance | monthly |
| costs | per-symbol `estimate_spread()` (~2.5bps mean), commission 0 (Alpaca) |
| metrics version | 2 |

### On `top_n`

`top_n=3` scored higher across every frequency in the 54-config surface
(208/175/212 vs 175/144/112 at lookback 189). It is NOT adopted here. The ridge
is broad at both, and switching to the higher-scoring cell after seeing the
surface is the post-hoc adjustment that converts out-of-sample into in-sample.
If it is ever changed, the reason recorded must be "the ridge is broad at 3",
never "it scored highest".

## Two benchmarks, pre-committed, and they legitimately disagree

This is the thing most likely to get muddled later, so it is stated before the
test starts rather than during a bad stretch:

- **Equal-weight Dow — the DIAGNOSTIC.** Answers *"does the ranking work?"*
  It holds all 29 names, so comparing against it controls for the universe.
- **SPY — the CAPITAL-ALLOCATION benchmark.** Answers *"should money go here
  instead of an index fund?"* You can buy SPY; you cannot buy the signal.

The temptation during a lagging stretch will be to quietly report whichever
makes the strategy look better. Both are named here so that switching is a
visible act rather than a silent one.

## Falsification criteria

**Stated against EQUAL-WEIGHT DOW, not SPY.** A SPY-relative stop would fire
during a mega-cap concentration run for reasons that have nothing to do with
whether the strategy works — measured directly in the backtest: over
2023-04 → 2024-12 the strategy trailed SPY by 16.4pp while beating equal-weight
Dow by 0.6pp. The entire shortfall was the Dow trailing the S&P (31.8% vs
48.8%). Stopping on that would have been stopping on a universe effect.

- **STOP** if cumulative return trails equal-weight point-in-time Dow by more
  than **15 percentage points** at **24 months**.
- **CONTINUE to 48 months** if not stopped at 24 (see continuation rule).
- **No parameter changes before 48 months, for any reason.**

### Why 15pp -- derived from measured dispersion, not chosen

Worst observed 3-year ranking contribution: **-12.2pp** (point-in-time roster),
**-16.3pp** (fixed roster). Volatility scales roughly with the square root of
time, so -12.2pp over 3 years corresponds to about **-10pp over 24 months**. A
stop at -10pp would therefore fire on dispersion this research has ALREADY
MEASURED and named as normal -- on noise, not evidence.

15pp sits deliberately outside that: loose enough it cannot trigger on
already-observed behaviour, tight enough that a genuine collapse (the effect
being absent rather than merely lagging) still trips it inside two years. It is
not a confidence threshold and must not be read as one.

### Continuation rule -- "not falsified" is NOT "confirmed"

At 24 months, exactly three outcomes:

1. **Trails EW PIT Dow by more than 15pp** -> STOP. Falsified. Do not tune.
2. **Ahead of EW PIT Dow** -> NOT confirmed. Continue to 48 months unchanged.
   At ~12 rebalances a year, 24 months is ~24 observations against a claimed
   +2%/yr effect with 3-year swings to -12pp. Survival at 24 months carries
   almost no evidential weight.
3. **Between the two** -> also continue unchanged. Ambiguity is the EXPECTED
   result at this horizon, not a reason to act.

Written down now because the failure mode is reading month-18 survival as
vindication and sizing up. Do not re-derive this rule later with the numbers in
front of you.
- SPY comparison is tracked but does NOT drive the stop. It is the separate,
  slower question of whether the enterprise beats indexing, and it needs years.

### Real-time diagnosis of a lagging stretch

Check equal-weight Dow against SPY. If the gap explains the shortfall, the
signal is fine and the universe is the problem. This is forecastable in a way
"momentum stopped working" is not.

## Evidence this rests on (and its limits)

| test | result |
|---|---|
| vs SPY, 5y | +58.6pp (144.1% vs 85.5%) |
| vs equal-weight Dow, 5y | **+70.1pp** |
| vs 400 random 5-of-29 portfolios | **+73.5pp** over mean; 96.5th pct; p=0.035 |
| parameter surface | broad ridge at lookback 126–252; 29/54 configs beat SPY |
| subperiods vs EW Dow | +16.3 / +0.6 / +18.1 (3 of 3, one flat) |
| subperiods vs SPY | +21.6 / −16.4 / +27.2 (2 of 3) |

The two concentration-controlled measures (+70.1 vs EW Dow, +73.5 vs random)
agree within 3pp despite different biases — EW Dow holds 29 at low
concentration, random holds 5 at the strategy's own concentration. Agreement
across both means the ranking's contribution is robust to the confound most
likely to have generated the result.

**Limits, which no backtest fixes:**

- The config was selected by grid search on this same window. Nominal p=0.035
  over ~5–10 effectively independent configs (54 nominal, heavily correlated)
  gives a corrected p of roughly **0.15–0.30**. Consistent with signal; not
  distinguishable from luck.
- One five-year window, 29 large-cap US names, a historically unusual period.
- Cross-sectional momentum is among the most published and most crowded
  factors in existence, with documented crash behaviour in sharp reversals.

## Thesis (revised 2026-08-11 after cross-universe and rolling-window tests)

**The strategy is not expected to reproduce the 2021-2026 backtest magnitude.**
Historical rolling-window evidence suggests a recurring ranking advantage within
the Dow universe of roughly **+2 to +4 percentage points per year** versus
equal-weight Dow, with occasional multi-year periods of material
underperformance. The 2021-2026 result appears to have been an unusually
favourable realization of that effect.

### What changed the thesis

**Cross-universe replication FAILED.** The frozen rule, applied unchanged to
universes it was never fitted on, did not beat its own universe:

| universe | ranking contribution |
|---|---|
| Dow (fitted) | +70.1pp |
| S&P 400 mid | **-63.9pp** |
| S&P 600 small | **-10.2pp** |

That killed the broad hypothesis (*"momentum ranking is a general stock-selection
effect"*). Absolute returns outside the Dow were 83.6% and 87.2% against SPY's
85.5% -- index-like, with no visible selection value.

**Rolling-window replication within the Dow SUCCEEDED.** Frozen config, 14
overlapping 3-year windows, 2010-2025, measured as strategy minus equal-weight
universe:

- **12 of 14 windows positive**; mean +10.5pp, median +9.5pp per 3-year window
- **+8.2pp mean excluding the single best window** -- not concentrated in one era
- range **-16.3pp to +40.5pp**

So the surviving hypothesis is universe-specific: a large-cap/Dow relative-
strength effect, which is *consistent with* the midcap/smallcap failure rather
than contradicted by it.

### Magnitude, honestly

| | ranking contribution |
|---|---|
| historical mean across 14 windows | ~**+3.4%/yr** |
| the development window | ~**+11.2%/yr** |

The development window ran about **3x the historical norm**. Both facts hold:
the effect looks real, and 2021-2026 was a good draw of it. Only the first is an
expectation.

**Normal pain, pre-committed:** a 3-year stretch at **-16pp** versus equal-weight
Dow is inside the observed historical range and is NOT grounds to stop.

### Known bias from unfetchable names -- direction stated

Three historical constituents cannot be retrieved and were excluded from BOTH
strategy and benchmark in every historical test:

| ticker | membership | why unfetchable | bias direction |
|---|---|---|---|
| `WBA` Walgreens | 2018-06 to 2024-02 | taken private 2025 | **FAVOURABLE to strategy** |
| `UTX` United Technologies | to 2020-04 | merged into RTX | unknown |
| `DWDP` DowDuPont | 2017-09 to 2019-04 | merger vehicle, split 2019 | unknown |

**WBA's exclusion flatters the result.** It was ejected in February 2024 after
sustained weakness -- exactly the kind of name a momentum rule ranks into on the
way up and is punished by on the way down. Six years of membership are missing
from a period the strategy was measured over, so the true point-in-time figure
is **likely LOWER** than the +3.25%/yr reported.

`UTX` and `DWDP` are corporate actions rather than performance ejections; their
direction is not predictable and is not claimed.

### Limits on the rolling-window evidence

- **Overlapping windows.** 14 windows stepped annually over 17 years is roughly
  **5-6 effectively independent** samples. "12 of 14" overstates it.
- **Fixed 2021 roster.** Measuring strategy-minus-EW cancels universe-level
  survivorship (both hold the same names) but does NOT prove ranking skill is
  unaffected by operating on a survivor-heavy roster. This is the largest
  remaining methodological weakness.
- DOW was excluded to obtain pre-2019 history, and a flat 2.5bps spread with a
  fixed 2% rf was used, so these figures are not directly comparable to +70.1pp.

## Remaining test before the research phase closes

**Point-in-time Dow membership.** Run the frozen rule against the constituents
that actually existed at each historical date, rather than today's roster
applied backwards. This tests the question that now matters -- *does momentum
rank effectively within the Dow as an EVOLVING blue-chip universe?* -- rather
than *can today's survivors be ranked retrospectively?*

- Survives with the same sign and broad magnitude -> forward test is justified.
- Collapses -> the survivor-roster explanation becomes the likely one, and the
  research stops.

### Sixth thesis line: OUTPERFORMANCE VS AN INDEX FUND IS NOT ESTABLISHED

**The +2%/yr is measured against equal-weight point-in-time Dow, a benchmark
that cannot be bought.** Against it stand two measured costs of harvesting it:

| component | annualized |
|---|---|
| universe drag (Dow vs S&P) | **-1.44%** |
| concentration cost (5 of 29, from the randomization test) | **-0.44%** |
| ranking contribution (historical) | **+2.00%** |
| **net vs SPY** | **+0.12%** |

The ranking edge survived every control and is **almost entirely consumed** by
the cost of harvesting it. The central estimate versus an index fund is
**approximately zero**, inside a distribution with multi-year swings of +/-12pp.

So the forward test is NOT "does this beat the market" -- the arithmetic says
probably not, and detecting +0.12%/yr would take longer than anyone will run it.
It is "does the ranking contribution hold out of sample," which is narrower and
genuinely open.

### The two historical tests are not a controlled comparison

Fixed-roster covers **2010-2023** starts; point-in-time covers **2014-2025**.
Different periods, different window counts (14 vs 10). Their agreement
(+3.4%/yr vs +3.25%/yr) is **two uncontrolled measurements landing close**, not
a controlled test of the roster effect. The correction was never isolated on a
common window, so how much of that agreement is robustness and how much is
coincidence is unresolved, and is not claimed either way.

## Scaling rule for any future universe -- stated a priori 2026-08-11

Recorded BEFORE the smallcap replication runs, so it cannot be a fit.

**Hold the SELECTION FRACTION constant, not the count.** `top_n=5` of 29 names
is a **17.2%** selection fraction. Applying "top 5" to a 600-name universe would
be 0.83% -- reaching far deeper into the ranking and concentrating far harder.
That is a different rule, not the same rule on a new universe.

    top_n(universe) = round(0.172 * len(universe))

Fraction preserves the diversification profile AND how deep into the ranking the
strategy reaches -- the two things that must be held constant for a replication
to test the signal rather than the concentration.

**Do not reopen the parameter search either way.** Changing `top_n`, lookback or
frequency now would contaminate the strongest part of the evidence, and would be
restarting the optimization loop that created the problem.

Clicking promote starts the test. It does not act on its result.

*Not investment advice.*


---

## AMENDMENT 2026-08-11 -- statistical power, appended not rewritten

The frozen CONFIG and THRESHOLDS above are unchanged and remain binding. This
section corrects a factual overstatement in the evidence, which the freeze does
not protect: a frozen file must not preserve a claim now known to be wrong.

### Minimum detectable alpha: 12.00%/yr

From the 4-factor regression (n = 58 months, residual vol 3.38%/mo):

| | value |
|---|---|
| alpha | +3.99%/yr (t = 0.69) |
| SE(alpha) | **5.85%/yr** |
| 95% CI | **[-7.01%/yr, +16.17%/yr]** |
| **minimum detectable alpha at t=2** | **12.00%/yr** |

Against tradable replicators (SPY, MTUM, VLUE, QUAL, USMV, SIZE): alpha
+10.34%/yr, t = 1.59, MDA **13.16%/yr**. MTUM loads +0.398 (t = 2.42). With six
correlated ETFs on 58 months the individual loadings are unstable
(QUAL at -1.02 is multicollinearity, not a real short-quality position) -- the
intercept and MTUM term are the interpretable parts.

### What this means, stated in both directions

**The design cannot resolve an edge worth having.** It cannot establish
+4%/yr and it cannot rule out -7%/yr. Both are inside the interval. A t of 0.69
is NOT evidence that alpha is zero -- it is evidence that this test could not
have detected alpha at any magnitude anyone would trade.

This is structural: 5 positions, monthly rebalancing, 58 months, drawn from 29
co-moving mega-caps. Roughly 5-10 semi-independent bets a year. At that breadth
an information coefficient of 0.04 -- which would be genuine skill -- is
statistically invisible.

**The correct conclusion is "underpowered", not "no edge".** The earlier wording
implied zero had been demonstrated. It had not.

### Consequence for the forward test

The 24-month stop at 15pp remains as written. But note what it can and cannot
do: it can detect a COLLAPSE (the effect being absent), and it cannot confirm
the effect exists. Nothing in a 24- or 48-month forward test at this breadth
will resolve a +2%/yr claim. That was already recorded in the continuation
rule; the MDA number is why.

---

## AMENDMENT 2026-08-12 -- which factor regression is canonical, and validation-pipeline provenance notes

The frozen CONFIG and THRESHOLDS remain unchanged and binding. This amendment
does two things a bug-fix pass surfaced: (1) states which of three now-existing
alpha-t-stat numbers actually drives the `engine/validation.py` gate, since
only one of them does and it was never written down; (2) records why two other
evidence figures in this file (rolling-stability pass/fail, the two distinct
p-values) are correct as reported, with the code-level reasoning now pinned in
`engine/validation.py` rather than only in this prose.

### Three alpha t-stats now exist for this strategy. One drives the gate.

| source | factors | frequency | n | alpha t-stat | wired to a gate? |
|---|---|---|---|---|---|
| `engine/validation.py:factor_attribution_evidence` (the engine's own automated check, `factor_residual_alpha`) | market=SPY, size=IWM-SPY, value=IWD-IWF, momentum=MTUM-SPY | **daily bars, pre-fix** | 1250 | **1.043** | **YES -- required, blocks `forward_test_worthy`/`identified_edge`** |
| Manual 4-factor script, recorded in the 2026-08-11 amendment above | same 4 proxies | monthly | 58 | 0.69 | no -- informational only |
| Manual six-ETF script (earlier session) | SPY, MTUM, VLUE, QUAL, USMV, SIZE | monthly | 58 | 1.59 | no -- informational only, and the file already notes QUAL's -1.02 loading is multicollinearity, not a real position |

**The engine's automated check is canonical**, for a reason independent of
which number happens to be more flattering: it is the only one of the three
that is (a) recomputed fresh on every canonical run rather than a one-off
script whose inputs can silently go stale, (b) fingerprinted by the run
manifest so its exact inputs are reproducible, and (c) actually wired to
`forward_test_worthy`/`identified_edge` -- the other two are read-only context
a human has to remember to consult. A gate that isn't automatically enforced
is not a gate.

**The 1.043 figure above is now known-stale and will be superseded.** It was
computed at DAILY sampling frequency (n=1250) on a strategy that rebalances
monthly -- serially dependent bars within each ~21-day holding period were
being counted as independent observations, understating the true standard
error. This was fixed by regressing at the strategy's own decision frequency
(one observation per rebalance, ~61 for this window) instead, with the HAC lag
widened to span the holding period when a daily fallback is used. Expect the
re-run number to move toward the 0.69 figure the manual monthly script already
found, since both now measure the same thing (monthly-cadence residual alpha)
by close-to-the-same method -- but it is not required to land exactly there,
since the proxy legs, window, and cost treatment still differ.

**Post-fix result, from the single re-run/re-record pass this bug-fix batch
ended with (2026-08-13):** `factor_residual_alpha` now regresses at decision
frequency (60 monthly observations, `periodsPerYear` ~12.2, `hacLag`=4) and
reports **alphaT = 1.025** (annual residual alpha +5.87%/yr, SE 5.57%/yr,
minimum detectable residual alpha 11.15%/yr). It moved from the stale
daily-frequency 1.043 toward, but not all the way to, the manual monthly
script's 0.69 -- as expected, since the two use overlapping but not identical
proxy legs and windows. The engine's automated check remains the one that
gates `forward_test_worthy`/`identified_edge`, per the "which regression is
canonical" section above; nothing here changes the frozen config or the
24-month stop.

### Rolling-stability gate: 0.70 fraction-positive is deliberate, not a default

The "12 of 14 windows positive" evidence in this file's Thesis section is read
by `engine/validation.py`'s `historical_stability` check against a threshold
of `fractionPositive >= 0.70` (plus positive median and positive
mean-excluding-best). That number is not an arbitrary round threshold: this
file's own "Limits on the rolling-window evidence" section above already notes
14 overlapping windows are roughly **5-6 effectively independent** samples
(stated in `data/manual_validation_evidence.json`'s
`effectiveIndependentWindowsLow/High`) -- so requiring 70% of the raw windows
positive is, in effective-sample terms, close to requiring 4 of the 5-6
independent draws positive. `engine/validation.py:STABILITY_MIN_FRACTION_POSITIVE`
now carries this reasoning as a code comment, and
`tests/test_engine/test_validation.py:test_rolling_stability_gate_matches_recorded_dual_momentum_evidence`
pins the gate against these exact stored numbers so a future change to the
threshold shows up as a diff instead of a silent verdict flip.

### The two p-values are different nulls, not a reconciliation problem

This file's Evidence table above already reports two different figures for a
reason -- "+73.5pp over mean; 96.5th pct; p=0.035" (concentration-matched
random-portfolio null) and the Limits section's "corrected p of roughly
0.15-0.30" (that same null, Sidak-corrected for 5-10 effectively independent
configurations searched). Both of those are now labeled explicitly in code
(`engine/validation.py`'s `beats_random`/`selection_adjusted_significance`
checks carry a `nullDefinition` field) and are DISTINCT from a third figure the
validation report also carries, `multiple_testing`'s `naiveBootstrapP`
(Bonferroni correction of a block-bootstrap resample of this strategy's own
realized returns -- a different null entirely, answering "how often would a
resample of what actually happened still beat the benchmark" rather than "how
often does a random top-N selection beat this return"). None of these three
numbers should be averaged or treated as duplicates of each other.

---

## AMENDMENT 2026-08-13 -- why the 2%/yr actionable-alpha threshold is not moved to pass the gate, recorded deliberately rather than left implicit

The frozen CONFIG remains unchanged. This records the reasoning behind
`engine/research_governance.py:MINIMUM_TRADABLE_ALPHA_PCT = 2.0` -- the
threshold every MDA figure in this file is judged against -- because the
question "why not just raise it" comes up naturally once MDA (11.15%/yr,
see the 2026-08-12 amendment) sits well above it, and the honest answer
needs writing down once rather than re-derived under pressure the next time
a result looks disappointing.

**The 2% is the smallest annual benchmark-relative edge worth trading, not
a statistical artifact.** MDA is a property of the DESIGN (breadth,
history length, correlation structure) -- what this instrument can detect.
2% is a property of the DECISION -- the smallest edge that would change what
happens with real capital. These are independent quantities measured on the
same axis; the gate is a comparison between them, not a single number tuned
for convenience.

**Raising it to clear the gate asserts a return-vs-benchmark claim the
evidence does not support.** The frozen 4-factor regression's point estimate
is +5.87%/yr with a 95% CI of roughly -5% to +17%/yr (2026-08-12 amendment).
Setting the threshold at, say, 11% to make MDA "pass" would mean asserting
"this strategy delivers 11%/yr over equal-weight Dow, sustained" -- a claim
nothing in this file's evidence supports, and one that contradicts the
published range for cross-sectional equity momentum generally (2-5%/yr in
the academic literature; an 11%/yr sustained edge in liquid large-cap US
equities does not have a documented precedent). Moving the threshold to fit
the instrument's detection limit is the same move, in the opposite
direction, as the plausibility-band-widening this project has already
rejected once (LESSONS.md: a Sharpe of 7.238 was a bug report, not a
strategy worth relaxing PLAUSIBLE_SHARPE to admit).

**Where 2% legitimately could move, and in which direction.** If the actual
decision this research informs is "trade this instead of holding SPY,"
rather than "trade this instead of equal-weight Dow," the relevant
threshold is arguably HIGHER than 2% -- compensation for concentration risk
(5 of 29-503 names) and momentum-crash exposure (documented, sharp
drawdowns in momentum factors during reversals) is not free. 3-4%/yr is a
defensible number under that framing. Note the direction: this makes the
gate HARDER to clear, never easier. There is no legitimate argument in this
file for lowering it.

**This threshold was set deliberately, not casually, at the time
`engine/research_governance.py` was built** (see MINIMUM_TRADABLE_ALPHA_PCT's
own code comment: "part of the pre-run contract, not a number inferred after
seeing a backtest"). Recorded here in fuller form so that reasoning survives
independently of the one-line code comment, and so a future session asking
"why 2%" finds an argument to engage with rather than a number to adjust.

---

## AMENDMENT 2026-08-13 -- power test: PIT ledger extended to 2000, MDA pre-registered BEFORE the run

**Pre-registration, written before `engine/dow_pit_power_test.py` executes.**
This is a POWER test, not a search or a config change: `lookback_trading_days`
(189), `rebalance_frequency` (monthly), and `top_n` (5) are exactly as frozen
above. The only thing that changes is the measured WINDOW -- 2021-08-14 to
~today (~5 years, 60 monthly decisions) becomes 2000-01-01 to ~today (~25
years, ~300 monthly decisions) -- and the SYMBOL SOURCE, via the newly
extended point-in-time ledger (`data/universe_membership.json`,
`engine/universe_ledger.py`) instead of the static July-2021 roster applied
backward.

**Prior MDA (5y, decision-frequency-corrected): 11.15%/yr** (2026-08-12
amendment above).

**Predicted MDA on the extended window: 5-7%/yr.** MDA scales roughly with
`1/sqrt(decisions)` for a fixed design (same breadth: 5 positions, monthly
cadence, ~26-29 correlated large caps at any point in time -- the ledger
narrows membership for fetchability, it does not change the strategy's
shape). ~25 years is ~5x the decisions of the 5-year window (~300 vs ~60), so
MDA should fall by roughly `sqrt(5) ~= 2.2x`: `11.15 / 2.2 ~= 5.1%/yr`,
landing inside the predicted range.

**Decision rule, fixed before the result is seen:**
- **MDA lands in 5-7%/yr** -> the design still fails the 2%/yr gate
  (`MINIMUM_TRADABLE_ALPHA_PCT`, see the amendment above for why that number
  is not moved to fit). Conclusion: monthly rebalancing on ~30 correlated
  large-cap names cannot clear a 2%/yr actionable-alpha bar at any
  *realistic* history length available for this universe -- breadth, not
  sample size, is the binding constraint, consistent with
  `engine/power_curve.py`'s earlier, independent finding.
- **MDA clears 2%/yr** -> the return figures become interpretable for the
  first time in this research programme, and are worth reading on their own
  terms rather than as a number this design could not have resolved either
  way.

**MDA is reported before returns, in that order, in the run's own output.**
Reading returns first and MDA second is exactly the ordering that let a
worse-than-uninformative result (MDA 11.15%/yr against a point estimate of
+5.87%/yr) get discussed as if it meant something, earlier in this file's own
history -- see the 2026-08-12 amendment's "what this means, stated in both
directions."

**The point-in-time ledger this run uses is itself imperfect, disclosed
rather than hidden.** Seven historical Dow constituents across 2000-2021 have
no fetchable price history via this project's data source and are excluded
from the tradeable universe for the windows they were actually members:
`EK` (Eastman Kodak, 2000-2004), `GM` (the pre-2009-bankruptcy entity,
2000-2009), `SBC` (SBC Communications, pre-rename, 2000-2005), `UTX` (United
Technologies, 2000-2020), `KFT` (Kraft Foods, 2008-2012), `DWDP` (DowDuPont,
2017-2019), `WBA` (Walgreens Boots Alliance, 2018-2021 -- taken private 2025).
EK/GM are FAVORABLE exclusions (both were in severe, well-documented decline
through their entire excluded window, the same shape as WBA's already-
established favorable exclusion). SBC/UTX/KFT/DWDP are UNKNOWN direction --
matching the prior PIT reconstruction's independent finding
(`data/manual_validation_evidence.json`'s `unknownDirectionExclusions`).
`engine/universe_ledger.py:audit_membership`'s `require_complete=False` mode
(used only by this script, never by the live app's canonical path) is what
lets a ledger this honest about its own gaps still activate point-in-time
resolution at all -- see that function's docstring for why treating a
disclosed data-availability gap the same as a ledger-quality defect would
make an honest ledger permanently unusable.

### Result (run completed 2026-08-13, reported in the order the pre-registration fixed: MDA first)

| | 5y (2021-08-14 start, static roster) | 26.6y (2000-01-01 start, PIT ledger) |
|---|---|---|
| decisions (monthly rebalances) | 60 | 323 |
| **MDA** | **11.15%/yr** | **5.26%/yr** |
| observed residual alpha | +5.87%/yr | +2.80%/yr |
| 95% CI | [-5.06%, +16.79%] | [-2.35%, +7.95%] |
| alphaT | 1.025 | 1.052 |
| clears 2%/yr gate? | No | No |

**MDA landed at 5.26%/yr -- inside the pre-registered 5-7%/yr range,
CONFIRMED.** The `1/sqrt(decisions)` scaling reasoning written down before
this run predicted ~5.1%/yr against an actual 5.26%/yr: close enough to trust
the mechanism, not just the number.

**Per the pre-registered decision rule: the design still fails the 2%/yr
actionable-alpha gate** (`viable: false`, `detectabilityMarginPct: -3.26`).
5.26x history did not buy enough power -- MDA improved 2.1x (11.15% ->
5.26%) against a naive `sqrt(5.3) ≈ 2.3x` expectation, close to the
predicted mechanism, but the absolute floor is still 2.6x higher than the
threshold that would make a result interpretable. **The conclusion fixed
before this run is now the standing one: monthly rebalancing on ~26-29
correlated large-cap names cannot clear a 2%/yr actionable-alpha bar at any
realistic history length available for this universe.** Breadth (5
positions, ~12 decisions/year, correlated large caps), not sample size, is
the binding constraint -- consistent with `engine/power_curve.py`'s earlier,
independent finding on the 5-year window. Going to 40 or 60 years of history
would not fix this: at the same `1/sqrt(decisions)` rate, even 50 years
lands MDA only around 3.8%/yr, still short of 2% -- and 50 years of Dow
history is not available regardless.

**The point estimate itself (+2.80%/yr) sits just above the 2% threshold it
cannot clear** -- consistent with, not contradictory to, "underpowered": a
95% CI of [-2.35%, +7.95%] cannot distinguish "small real edge" from "small
real drag" from "zero," which is exactly the state a design with this MDA
should produce regardless of which of those is true. This is not a weaker
version of the 5-year finding, it is the SAME finding, now with a
pre-registered, mechanism-confirmed number attached: this instrument, at
this breadth, cannot resolve a claim of this size, and 21 more years of
history was not the fix.

**Returns, read only now that MDA is on record:** +905.4% cumulative over
26.61 years, CAGR 9.13%/yr, Sharpe 0.35, Sortino 0.49, 320 rebalances, no
incomplete-warmup gaps in the traded window. Per the decision rule fixed
above, these are not claimed as evidence of an edge -- they are a return
series from an underpowered design, reported for completeness, not
interpreted as a result.

**Research programme on the frozen Dow-only config is closed on this
question.** The remaining open items in this file (point-in-time membership
survival, cross-universe replication, rolling-window stability) stand as
already recorded; this amendment closes the "is 5 years just too short"
question specifically, with the pre-registered answer: no, it was not the
history length.
