# Frozen configuration — Dual Momentum forward test

**Frozen:** 2026-08-11
**Status:** DRAFT — the two bracketed thresholds are the user's to set. Nothing
is frozen until those are filled in and this file is committed.

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

- **STOP** if cumulative return trails equal-weight Dow by more than
  **[X pp]** at **24 months**.
- **No parameter changes before that date, for any reason.**
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

## Expectation

**Zero to a couple of percentage points a year over the index, drawdowns worse
than the index, and multi-year stretches that look broken.** That is what a real
momentum implementation looks like. The +58.6pp was a backtest.

Clicking promote starts the test. It does not act on its result.

*Not investment advice.*
