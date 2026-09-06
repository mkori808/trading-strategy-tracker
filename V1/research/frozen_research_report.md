# Frozen Swing-Hypothesis Research Report

Generated from the persisted canonical records after the frozen V1 runs and
pre-registered neighbor grids. The primary window is 2021-08-14 through
2026-08-13 (first measured trading session 2021-08-16). The primary universe
is date-effective Dow membership. Results are net of the modeled spread and
commission rules in the application.

## Executive conclusion

No strategy in this batch qualifies as an identified edge. Market-Residual
Momentum and Negative Return + Volume Shock Reversal are the only results worth
carrying forward as explicitly unresolved hypotheses. Neither passes the full
gate set. All seven measured designs fail the pre-result power gate: the
cross-sectional MDA is 11.80%/year and the event-strategy MDA is 17.89%/year,
against a pre-registered 2%/year actionable advantage.

This is not proof that every effect is zero. It means this five-year design
cannot resolve a 2% annual effect, while the observed results also contain
substantive negative evidence: weak matched-benchmark economics, unstable
rolling windows, narrow parameter support, or failure against concentration-
matched random portfolios.

## Research-validity stages

1. **PIT safeguards preserved.** Dow rankers resolve date-effective membership.
   S&P 500 PIT execution stays disabled because membership reconstruction does
   not supply a price-complete, identity-safe survivor-free dataset. S&P 400/600
   and current sector classifications are not presented as PIT-safe substitutes.
2. **Execution timing enforced.** Daily close-derived signals become actionable
   at the next open. Information availability is explicit (pre-market,
   intraday, at-close, post-close). BMO events can affect the same reaction
   session; AMC events first affect the next session. Earnings AVWAP uses the
   same event-timing contract.
3. **Exposure-matched benchmarking added.** Each completed event trade has an
   execution-aligned SPY return and excess return. Runs store shared-capital
   CAGR, Sharpe, Sortino, drawdown, gross/net exposure, time in market,
   turnover, costs, matched excess, alpha, and beta. Raw SPY buy-and-hold gap is
   retained only as descriptive context.
4. **Frozen protocol enforced.** V1 definitions and neighbor grids are in
   `research/frozen_v1_protocol.json`. Canonical cells were stored before
   neighbors ran. Neighbor results cannot replace V1.
5. **Hostile validation persisted.** MDA, PIT integrity, matched benchmarks,
   rolling windows, holdout/walk-forward checks, block bootstrap, tradable-
   factor regression, parameter ridges, random top-N controls where applicable,
   cost evidence, and multiplicity remain separate visible gates.
6. **Actual search width counted.** The six implemented families contain 482
   executed configurations in the database: 8 canonical/implementation
   attempts and 474 completed neighbor attempts. Of those, 422 neighbors are
   valid current-window evidence. The 52 invalid Market-Residual attempts are
   retained and counted, not erased.

## Canonical V1 results

Percentages are percentage points. “Matched excess” is exposure-timed SPY
excess for event strategies and PIT equal-weight contribution for rankers.
“Residual alpha” is the local tradable-factor regression estimate and is shown
with its HAC t-statistic.

| Strategy | Sample | CAGR | Sharpe | Max DD | Expectancy / PF | Avg. gross exposure | Matched excess | Residual alpha | MDA | Costs | Neighbor support | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 52-Week-High Momentum | 61 rebalances | +0.51% | -0.21 | 26.62% | N/A | Target weights; daily mean not archived | -65.85% vs PIT EW | -8.43%/yr, t=-1.96 | 11.80%/yr | $175.67 | 0/71 (0.0%) | Weak / no evidence |
| Market-Residual Momentum | 61 active rebalances | +12.99% | 0.65 | 16.33% | N/A | 100% target at every rebalance | +15.71% vs PIT EW; -1.40% vs SPY | +0.31%/yr, t=0.09 | 11.80%/yr | $64.04 | 7/26 (26.9%) | Interesting, unresolved |
| Negative Return + Volume Shock Reversal | 57 trades | +4.61% | 0.31 | 1.42% | +0.124R / 1.45 | 0.70%; 10.69% time in market | -0.054% vs matched SPY | +4.47%/yr, t=3.55 | 17.89%/yr | $297.31 | 28/95 (29.5%) | Interesting, unresolved |
| Volume-Shock Continuation — Long | 286 trades | -0.63% | -0.66 | 13.19% | -0.210R / 0.73 | 3.93%; 54.63% time in market | -5.68% vs matched SPY | -1.69%/yr, t=-0.61 | 17.89%/yr | $1,427.64 | 15/53 (28.3%) | Weak / no evidence |
| Volume-Shock Continuation — Short | 274 trades | -1.08% | -0.67 | 10.79% | -0.304R / 0.63 | 3.77%; 52.87% time in market | -13.85% vs matched SPY | +1.41%/yr, t=0.47 | 17.89%/yr | $1,403.57 | 9/53 (17.0%) | Weak / no evidence |
| MAX Lottery-Return Reversal — Short | 86 trades | +1.51% | -0.38 | 16.28% | -0.010R / 0.97 | 1.37%; 23.52% time in market | -0.39% vs matched SPY | +4.28%/yr, t=1.70 | 17.89%/yr | $456.77 | 1/71 (1.4%) | Weak / no evidence |
| Volatility-Conditioned Pullback | 240 trades | +0.75% | -0.93 | 4.54% | +0.013R / 1.00 | 2.32%; 37.08% time in market | -1.25% vs matched SPY | +0.62%/yr, t=0.39 | 17.89%/yr | $1,305.81 | 21/53 (39.6%) | Weak / no evidence |

Additional canonical diagnostics:

- 52-Week-High returned +3.26% cumulatively, versus +69.10% for PIT
  equal-weight and +84.92% for SPY. Its random top-5 empirical p-value was
  0.985; the random mean was +69.04%, median +65.02%, p05 +15.89%, p95
  +138.31%, and maximum +197.81%.
- Corrected Market-Residual returned +84.81% cumulatively. It beat PIT
  equal-weight by 15.71 points but trailed identical-date SPY by 1.40 points.
  Its random top-5 empirical p-value was 0.309; random mean +69.04%, median
  +65.02%, p05 +15.89%, p95 +138.31%, and maximum +197.81%. The final 20%
  holdout was positive by 2.17 points, but only 3/5 yearly walk-forward windows
  were positive, 3/6 purged folds were positive, and the bootstrap probability
  of outperformance was 49.5%.
- Negative Return + Volume Shock had a 52.63% win rate and positive expectancy,
  but only 57 trades, 0.70% average gross exposure, essentially zero matched-SPY
  excess, failed chronological OOS, a bootstrap outperformance probability of
  18.25%, and only 29.5% economically positive neighbors. Its positive factor
  regression is therefore interesting evidence, not a passing strategy verdict.
- The remaining event strategies fail on matched excess, risk-adjusted return,
  rolling stability, parameter ridge, and multiplicity. MAX additionally assumes
  historical borrow availability; borrow fees and locates are unavailable.

## Rolling historical stability

| Strategy | Positive 3-year windows | Median contribution | Worst | Mean excluding best | Gate |
|---|---:|---:|---:|---:|---|
| 52-Week-High | 0/3 | -18.68% | -42.11% | -30.39% | Fail |
| Market-Residual | 1/3 | -6.29% | -7.51% | -6.90% | Fail |
| Negative + Volume Reversal | 0/3 | -0.475% | -0.499% | -0.487% | Fail |
| Volume Continuation — Long | 0/3 | -0.352% | -0.790% | -0.571% | Fail |
| Volume Continuation — Short | 0/3 | -1.276% | -1.494% | -1.385% | Fail |
| MAX Reversal — Short | 2/3 | +0.432% | -0.671% | -0.120% | Fail |
| Volatility Pullback | 0/3 | -0.222% | -0.244% | -0.233% | Fail |

The rule requires at least 70% positive windows, positive median contribution,
and positive mean excluding the best window. MAX reached 2/3 but failed the
70% threshold and the excluding-best condition.

## Unavailable hypotheses

| Strategy | Status | Reason |
|---|---|---|
| Earnings Announcement Return Drift | Unavailable | No complete PIT event ledger exists across historical Dow membership. A selective event sample would bias cross-sectional ranks. |
| Sector-Relative Momentum | Unavailable | No trustworthy point-in-time historical sector-classification ledger is installed. Current classifications were not substituted. |
| Overnight Idiosyncratic Shock Reversal | Unsupported with daily bars | The Open[T] gap is only observed at that open; daily bars cannot both observe the realized open and honestly fill at the same price. Neither side was fabricated. |

Cross-universe replication is also unavailable: reconstructed S&P membership
without complete historical prices and identity continuity is not a safe PIT
execution universe. Static current S&P 400/500/600 rosters were not used
backward through time.

## Evidence ranking

### Promising — deserves deeper research

None. No canonical V1 survived the complete evidence stack.

### Interesting but unresolved

1. **Market-Residual Momentum.** Positive PIT-EW contribution and positive final
   holdout, but it does not beat SPY, random top-5 selection, the parameter-ridge
   threshold, rolling stability, bootstrap confidence, factor alpha, MDA, PIT
   completeness, or multiplicity.
2. **Negative Return + Volume Shock Reversal.** Positive expectancy and a
   positive factor-regression estimate, but matched-SPY excess is approximately
   zero, sample/exposure are small, neighbors are narrow, rolling/OOS evidence
   fails, and the design is underpowered.

### Weak / no evidence

52-Week-High Momentum, both Volume-Shock Continuation sides, MAX Reversal, and
Volatility-Conditioned Pullback. Their classifications reflect benchmark,
expectancy, stability, robustness, and cost evidence—not raw return alone.

### Invalid or unavailable

EAR, Sector-Relative Momentum, and Overnight Idiosyncratic Shock Reversal are
unavailable for the data/timing reasons above. The original cash-only
Market-Residual attempt and its 26 neighbors are invalid implementation records.
A later corrected 26-neighbor batch used a one-day rolling window and is also
invalid research evidence. All 52 attempts remain in the family count.

## Potential bugs and data-quality issues discovered overnight

- **Market-Residual benchmark cutoff:** the stock engine passed history strictly
  before Open[T], while the strategy admitted SPY through T. After the five-day
  skip, the joined series was one row short and every rank was empty. This
  produced risk-free cash accrual masquerading as +19.55% performance. The
  benchmark now ends strictly before execution; sample coverage requires at
  least 80% active rebalances and measurement integrity requires nonzero traded
  notional. The invalid run is demoted, annotated, and retained.
- **Rolling neighbor window:** the neighbor harness used a date range derived
  from the wall clock. It now reads the explicit frozen window and includes it
  in experiment and result identity. The drifted cells remain counted.
- **Sparse equity-curve annualization:** event portfolios now include cash days
  across the full business-day calendar; sparse event timestamps no longer
  inflate annualization.
- **Replay input drift:** cached/vendor history can change by tiny amounts.
  Revalidation pins the stored risk-free input and permits only explicitly
  recorded replay drift below 0.001 percentage points.
- **PIT price gap:** WBA remains unresolved in the Dow ledger. Membership is
  date-effective, but this missing historical price coverage keeps the PIT gate
  failed rather than silently dropping the problem.
- **Vendor diagnostics:** yfinance may print “possibly delisted” when a refresh
  is empty even when a usable local cache is returned. This message alone is not
  treated as delisting evidence.
- **Clock consistency:** default date-range construction now uses a single
  injected clock observation, preventing midnight rollover from producing mixed
  dates.
- **Legacy environment issue:** `tests/test_engine/test_registry.py` cannot
  collect because the active Python environment's `openpyxl` import cannot find
  `defusedxml.ElementTree`. This is isolated from the engine regression suite.
- **Frontend size:** production build succeeds, but Vite reports a 725.55 kB
  JavaScript chunk (199.89 kB gzip), above its 500 kB warning threshold.

## Recommended next experiments

These are recommendations only; none was automatically implemented or tuned.

1. Extend the observation window or define a genuinely higher-frequency,
   independent decision process before doing more effect testing. The current
   designs cannot resolve a 2% annual advantage.
2. Close WBA and other historical price/identity gaps before treating the Dow
   PIT gate as complete. Do not replace missing histories with current tickers.
3. Acquire licensed or otherwise auditable PIT constituent prices and identities
   before any S&P cross-universe replication.
4. For Market-Residual Momentum, pre-register a new hypothesis only after the
   data issues are resolved; require random-control, rolling, and factor-alpha
   confirmation rather than following the best neighbor.
5. For Negative + Volume Reversal, seek a larger PIT-safe event sample and a
   complete event-classification ledger, while keeping the same exposure-matched
   benchmark and next-open timing.
6. Add historical borrow/locate/fee data before revisiting any short-side
   strategy.
7. Store common per-configuration return matrices if an actual CSCV/PBO estimate
   is desired. The current system correctly leaves PBO unresolved rather than
   inventing it from one selected curve.

## Verification and implementation inventory

- Seven canonical V1 strategies implemented and evaluated; three hypotheses
  explicitly skipped/unavailable.
- 474 neighbor rows completed with zero execution failures; 422 are valid
  current-protocol neighbor evidence.
- 655 non-legacy tests passed. The one legacy registry module did not collect
  because of the environment-specific dependency issue described above.
- Frontend TypeScript/Vite production build passed; only the chunk-size warning
  remains.
- New core modules include execution timing, matched benchmarking, frozen event
  execution, frozen protocol access, canonical and neighbor runners, validation
  backfill/replay, research governance, and the Market-Residual correction
  rebuild. Regression tests cover timing, matched benchmarks, frozen execution,
  protocol widths, neighbor identity/windowing, validation backfill, PIT paths,
  and the corrected residual-momentum execution boundary.

The next priority is data and statistical design, not another parameter search.
