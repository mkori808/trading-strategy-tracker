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
