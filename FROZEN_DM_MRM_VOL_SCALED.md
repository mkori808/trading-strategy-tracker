# Frozen configuration — DM/MRM Volatility-Scaled Portfolio forward test

**Frozen:** 2026-08-22
**Status:** FROZEN. Not edited after this point. Changing this file after
commit invalidates the forward test — same discipline as
`FROZEN_DUAL_MOMENTUM.md`, whose "adjusting three months in because it's
lagging converts an out-of-sample test back into an in-sample one" warning
applies here without modification.

## What this is

A capital-allocation overlay on top of two already-frozen, already-canonical
strategies. It contains **no signal logic of its own** — the only research
variable is how much capital each sleeve gets, decided from each sleeve's
own trailing volatility. Implementation: `engine/dm_mrm_vol_scaled.py`.

## Underlying strategies (unchanged, unmodified)

| sleeve | config |
|---|---|
| Dual Momentum | 189-session lookback, top 5, monthly rebalance, registered `EQUITY_UNIVERSE`, existing cost model — identical to `FROZEN_DUAL_MOMENTUM.md` |
| Market-Residual Momentum | registered canonical parameters, as currently defined in `strategies/registry.py` |

Any future change to either sleeve's parameters, universe, or signal logic
creates a **new version** of this combined strategy, never an edit to this
one.

## The weighting rule (frozen before any forward observation existed)

At each monthly reset date *t*:

```
vol_DM(t)  = std(DM daily returns, 60 sessions strictly before t) * sqrt(252)
vol_MRM(t) = std(MRM daily returns, 60 sessions strictly before t) * sqrt(252)
w_DM(t)  = (1 / vol_DM(t)) / (1/vol_DM(t) + 1/vol_MRM(t))
w_MRM(t) = 1 - w_DM(t)
```

- **Volatility lookback:** 60 trading sessions. Not to be changed without a
  new version ID.
- **Estimator:** sample standard deviation of daily returns, annualized by
  `sqrt(252)`. No exponential weighting, no shrinkage.
- **No floor, no cap, no epsilon** beyond IEEE754 — an implicit
  "insufficient data" fallback exists (see below) but no discretionary
  bound was added after viewing results.
- **Missing-data / warm-up behavior:** before 60 sessions of common history
  exist, or if either sleeve's trailing volatility is exactly zero or
  undefined, the reset is skipped and the portfolio holds its PRIOR weight
  (50/50 at inception, since nothing else exists to hold). This was true of
  the original implementation and is preserved exactly, not added
  retroactively.
- **Reset schedule:** the union of both sleeves' own monthly rebalance
  dates — first common session of each calendar month. Never a schedule
  invented for the overlay.
- **Between resets:** buy-and-hold. Each sleeve's dollar value drifts with
  its own return; the realized weight drifts from target until the next
  reset. This is a real two-sleeve capital split with countable turnover,
  not a daily-reweighted overlay.
- **Execution timing:** weights computed from data available through the
  prior session are applied AT the reset session (no one-session
  implementation lag is built in — see the timing-sensitivity diagnostic
  below, which found no material dependence on this choice).
- **Transaction costs:** the historical research result assumes the
  underlying sleeves' own cost models only. Overlay-level reallocation
  costs were audited (0–50bps grid) and found immaterial (Sharpe 0.910 →
  0.896 at 50bps) but are NOT charged in the frozen headline research
  number; a cost-inclusive variant would be a separate reported figure, not
  a spec change.
- **Benchmark:** SPY, as an external investment comparison only — never the
  prop-account objective, per the rest of this project's convention.

## Historical (development) research result

Development window: **2021-08-23 → 2026-08-21**, 1,255 common sessions, 57
of 60 monthly resets had sufficient trailing data (the first 3 fell in the
60-session warm-up).

| metric | value |
|---|---|
| CAGR | 16.30% |
| Annualized volatility | 13.96% |
| Sharpe (rf = 3.67%, the canonical DM run's own rate) | 0.91 |
| Sortino | 1.35 |
| Max drawdown | −12.94% |
| Worst day | −5.54% |
| Worst rolling 20 sessions | −9.64% |
| Worst month | −7.25% |
| Prop max-survival sizing (100k/5%/2%/80%/$500/static/funded) | ~0.33x |
| Modeled expected net prop payout at that sizing | ~$3,929/yr |

**Independent audit: PASSED.** A third, independently-structured
reconstruction (log-return cumulative arithmetic, not the multiplicative
loop either the original ad hoc script or the shipped module uses) matched
the shipped module's NAV to machine precision (max absolute difference
0.0 across 1,254 sessions). A look-ahead injection test (perturbing a real
reset date's own return to +300% and confirming that date's weight was
unaffected) passed. Full audit trail: see the research-ledger entry below.

## Development / research data cutoff

```
Development data ends: 2026-08-21
```

Every session on or before this date was examined while this specification
was designed and audited. **None of it can serve as a forward-test
observation for this strategy.** The forward test begins strictly after
this date. No historical subperiod is relabeled as "unseen."

## Forward-test comparison set

Tracked side by side, identical timestamps/data/execution/cost treatment:

1. Pure canonical Dual Momentum
2. Pure canonical Market-Residual Momentum
3. Frozen 50/50 DM/MRM (the simpler control)
4. Frozen volatility-scaled DM/MRM (this strategy)

SPY remains an external benchmark only. This set is fixed; no competitor is
added or removed based on performance.

## Preregistered forward-test hypotheses

**Primary:** Does the volatility-scaled DM/MRM portfolio produce better
risk-adjusted performance than pure Dual Momentum on genuinely unseen
future data?

**Secondary:** Does volatility scaling provide meaningful improvement over
the simpler fixed 50/50 portfolio?

Neither is preregistered as an expected "win" — both are open questions.

## Preregistered evaluation metrics (not a single pass/fail threshold)

- Sharpe difference vs. DM
- Max-drawdown difference vs. DM
- Volatility difference vs. DM
- CAGR/return retention vs. DM
- Sharpe difference vs. 50/50
- Drawdown difference vs. 50/50
- Economic tradeoff: return sacrificed per unit of risk reduction

These are **fixed at freeze time** and will not be altered after observing
forward outcomes.

## Minimum evaluation horizon

- **Before 12 months:** status is **Too early to evaluate**. No annualized
  statistic is reported without an explicit small-sample warning.
- **At 12 months:** an interim *descriptive* review is permitted — not a
  validation verdict.
- **Promotion to any status stronger than "Forward testing"** requires
  materially more data, preferably spanning multiple market regimes. One
  good year does not produce "Validated."

## No retrospective tuning

Once registered, the following are FROZEN and will not change based on
forward performance: the 60-session volatility lookback, the inverse-vol
weighting formula, the monthly reset rule, and both underlying strategies'
parameters. Ideas such as a different lookback, capped weights, volatility
targeting, correlation-aware weights, risk parity, or different fixed
proportions are separate future research strategies with their own version
IDs and their own validation process — never edits to this file.

## Research status labels (this document uses only these)

`Historical candidate` → `Audit passed` → `Frozen` → `Forward testing` →
(`Too early to evaluate` while horizon < 12 months) → `Forward evidence
supportive` / `Forward evidence mixed` / `Forward evidence adverse`.

**`Validated` is never used on the strength of an attractive historical
backtest alone.**

## Current status

**FROZEN — FORWARD TESTING.** Registration date 2026-08-22. See
`engine/dm_mrm_vol_scaled.py` for the implementation and
`logs/dm_mrm_vol_scaled_forward.json` for the append-only forward ledger
(empty until the first post-cutoff monthly reset).
