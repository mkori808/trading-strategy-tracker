# Lessons

A running log of things found while building and reviewing this project that
are worth remembering — mostly backtesting/quant-methodology mistakes, kept
here instead of just in chat history because they'll recur if forgotten.
Newest entries at the top.

---

## 2026-07-20 (cont'd 5) — Dual Momentum's drawdown diagnosed; a vol-targeting overlay and a Pairs blend both tried, neither improves it; a recent-window Sharpe worth watching, not trusting yet

**Context:** Dual Momentum is the strongest result in the project (Sharpe
0.41, the only positive Sharpe of any strategy, and it beat SPY's own
buy-and-hold return: +100.3% vs +82.6% over the same 5-year window) but
misses the shortlist's Sharpe > 0.5 bar, carrying a 23.3% max drawdown.
Dug into the drawdown and tried two ways to improve it.

**The drawdown's cause is structural, not a data artifact.** It bottoms
exactly on 2025-04-07 (the tariff-shock selloff) while the strategy held 5
equal-weighted, fully-invested large caps (MMM, WMT, GS, IBM, AAPL) set at
the 2025-04-01 rebalance — six days before the shock, with zero exposure
scaling. `DualMomentum.rebalance()` is binary: either 100%-in-top-N or
fully cash if nothing clears the absolute filter; there's no volatility-
based sizing in between.

**Fix attempt 1: a vol-targeting overlay** (`engine/timing_filters.py:
VolTargetedCrossSectional`) scales rebalance weights by
`min(1.0, target_vol / SPY_realized_vol)`, capped so it only ever reduces
exposure. It works exactly as designed — max DD 23.3%→16.7% at a 12%
target — but **Sharpe gets WORSE, not better** (0.41→0.30), and every
target tried (10%/12%/15%) showed the same pattern. Root cause: Dual
Momentum only rebalances monthly, so the vol signal read at the 2025-04-01
rebalance couldn't see a shock landing 2-6 days later — the overlay cuts
exposure broadly during ordinary elevated-vol months (a real opportunity
cost) without being fast enough to dodge the one sudden event that caused
the actual drawdown. A monthly-cadence vol overlay cannot fix a sub-month
shock; this would need faster rebalancing (which trades off against
transaction costs) to have a chance, not a differently-tuned target.

**Fix attempt 2: blending with Pairs / Stat Arb as a diversifier.**
Genuinely negatively correlated (daily-return correlation -0.26 over the
window both strategies share), which is exactly what a diversifier should
be. But Pairs' own canonical result is negative (-14.0% return, Sharpe
-0.83 over the same window) — its own negative expectancy costs more than
the correlation buys back. Even a 90/10 Dual-Momentum/Pairs blend
(smallest tested) drags Sharpe from 0.582 down to 0.550; by 30% Pairs the
blend clearly underperforms standalone Dual Momentum on every metric.
**A negatively-correlated but money-losing diversifier is not free
insurance — verify the diversifier's own expectancy before blending, not
just its correlation.**

**Notable, NOT yet trustworthy: restricted to the same ~2.5-year window
Pairs is even tradeable in (2024-01-17 onward, forced by the blend-fairness
requirement above), Dual Momentum ALONE shows Sharpe 0.582 and beats SPY
(+70.2% vs +60.7%) — clearing the shortlist bar that its full 5-year
number (0.41) misses.** This fell out of doing the blend comparison
honestly (both arms had to share one window), not from hunting for a
better sub-window after the fact. It's a real number, but ~30 monthly
rebalances is a thin sample to base a verdict on, and CLAUDE.md's own rule
applies: an unstressed/underpowered result is not a verdict. Worth
tracking as this window lengthens (does the recent-period Sharpe hold up
as 2026-2027 rebalances accumulate, or mean-revert back toward the 5-year
0.41?) rather than treating it as evidence the earlier 2021-2023 period was
somehow the "wrong" period to include.

---

## 2026-07-20 (cont'd 4) — Timing gates (FOMC days, SPY vol regime) tested on every positive-expectancy strategy; portfolio-engine verdicts wired in; two stale-status bugs found

**Context:** a second externally-written analysis (a "what I'd backtest
next" list) recommended entry-condition filters — SPY above its 200-day MA,
relative strength, volume, earnings growth, volatility regimes, avoiding
FOMC days, breadth confirmation. Cross-checked against this file first:
regime/trend-template gating, universe swaps, and capacity sweeps were
already run and already failed to produce a shortlist candidate, so only
the genuinely untested items were built — an **FOMC-day exclusion** and an
**SPY volatility-regime gate** (`engine/timing_filters.py`, comparison in
`engine/compare_timing_filters.py`, no logging_db writes, same firewall as
every other comparison script). The earnings-growth filter was skipped
deliberately: yfinance fundamentals are snapshot-only, and the Dividend
Hybrid entry below documents why that screen is biased toward the answer.

**Both gates are direction-agnostic (unlike the long-only regime gate) and
look-ahead-safe by construction:** FOMC meeting dates are published by the
Fed roughly a year ahead (hardcoded 2021–2026 calendar, 8 meetings/year,
guarded by a test and a ValueError past the last covered year), so "is
today an FOMC day" is knowable intraday with no prior-session shift. The
vol gate ranks SPY's 21-day realized vol against its trailing 252 days
(right-aligned everything; causality asserted with a truncation test) and
partitions at the 70th percentile into complementary "calm"/"storm" arms
rather than two overlapping cherry-picks. Overnight Hold can't take the
`Strategy` wrapper (close→open engine), so `run_overnight_backtest` gained
an optional `entry_allowed` predicate — None is byte-identical to before.

**Result across all 9 positive-expectancy strategies (4 arms each):**

- **FOMC gate: a no-op with a haircut.** Blocks 6.3% of window days,
  removes 1–8% of trades, moves expectancy by at most ±0.02R, never
  changes a status. Whatever is holding these strategies below the bar,
  it is not Fed-day event risk.
- **Vol calm (entries only ≤ p70): mildly harmful for most.** Expectancy
  fell on 6 of 9 (Pullback +0.025→+0.014, Breakout +0.106→+0.084, PEAD
  +0.119→+0.014); only 9/21 Crossover (+0.089→+0.110) and Sector Rotation
  (+0.096→+0.139) improved.
- **Vol storm (entries only > p70): better per-trade quality on every
  mean-reversion strategy** — Connors RSI2 +0.042→+0.094 with PF
  1.28→1.65, Oversold Bounce +0.163→+0.181, IBS +0.048→+0.066, PEAD
  +0.119→+0.182, Overnight Hold +0.012→+0.027 with max DD 20.9%→12.7% —
  and worse on the trend entries (9/21: +0.089→+0.062). Thesis-consistent:
  mean reversion wants fear, trend wants calm.
- **But no arm flipped any status.** Alpha stayed negative in every arm of
  every strategy, and every storm arm's Sharpe *worsened* (e.g. Connors
  -2.74→-6.92) — the mechanical exposure-collapse effect CLAUDE.md's
  "decompose risk-adjusted ratios" bullet describes, since storm cuts
  trade counts 70–80%. A better-expectancy, worse-Sharpe, still-negative-
  alpha arm is a *characterization* of when the edge concentrates, not a
  strategy improvement. Verdict: still zero strategies clearing the
  shortlist bar; the storm result is worth remembering if a high-vol
  regime-switching idea ever comes up, but it is not itself a candidate.

**Two stale-status bugs found while wiring verdicts, same root cause as
the (cont'd 2) entry below — old rows shadowing new instrumentation:**

1. **Dual Momentum / Pairs showed a blank "Backtested" forever.** The
   `portfolio_runs` table had no status/benchmark columns, so the
   leaderboard hardcoded "Backtested" and 0 trades. Added
   `benchmark_return_pct` (SPY buy-and-hold, same window) + `status`
   (`engine/metrics.py:portfolio_status()`, same Sharpe>0.5 / beats-
   benchmark tiers) via append-only migration. Real verdicts: Dual
   Momentum "+100.3% but SPY did +82.6% with Sharpe 0.41 → hold"; Pairs
   "-13.2% while SPY did +60.7% → drop". "Backtested" had been hiding a
   losing strategy. `best_portfolio_run_per_strategy()` needed the same
   prefer-instrumented-rows tier as its standard sibling — measured: the
   old Pairs row outranked the instrumented re-run by 0.0003 Sharpe.
2. **Overnight Hold's "shortlist" on the Compare tab was a stale string,
   not a current verdict.** Its best-Sharpe canonical row (2026-07-19
   00:02, sharpe -0.6612878) was logged hours before the Sharpe gate was
   added to the status logic and won the ranking by 0.0000002 Sharpe over
   the corrected re-run (-0.6612900, "hold"). The leaderboard now
   recomputes status from each row's stored *numbers* with current logic
   (`derive_status()`), keeping the logged string as the historical
   record. Consequence worth stating plainly: **no strategy on the board
   currently clears the shortlist bar.** A status string computed at log
   time is a fact about the logic of that day; re-ranking across runs
   spanning a logic change must recompute, not trust strings.

**Also fixed:** `data/XLC_1d.parquet` was corrupt (ArrowInvalid on read) —
it had been silently breaking the live scanner's poll loop for a day and
then hard-crashed the Sector Rotation comparison arm. Deleted (regenerable
cache); a corrupted cache file surfacing in two unrelated code paths is
the "same root cause, different symptom" pattern worth checking for first.

---

## 2026-07-20 (cont'd 3) — Breakout from Consolidation, mid-cap Lab sweep: 10 variants, alpha negative in all 10

**Context:** an externally-written analysis (not generated by this project's own chat assistant or any script here) was pasted in, claiming a 224-trade mid-cap Lab-tab run "confirms a true, persistent edge in mid-cap equities" and recommending concentrating position size to 20-25%/trade, capping at 4-5 concurrent positions, and layering multiple strategies to push exposure to 70-80%. Verified the underlying numbers directly against `logging_db.run_history("Breakout from Consolidation")` before reacting -- they were real (224 trades, +0.205R expectancy, PF 1.37, exactly matched a logged run), so the issue wasn't fabrication, it was the interpretation and the recommendation built on top of it.

**All 10 non-canonical runs logged today (2026-07-20, 11:51-12:56) were part of the same sweep** -- a mix of Dow-29 and `MIDCAP_UNIVERSE` symbol sets at varying parameter settings, none of them the registered canonical config:

| run_at | universe | trades | ExpR | PF | Alpha % | Exposure % |
|---|---|---|---|---|---|---|
| 11:51 | Dow subset | 190 | 0.166 | 1.41 | -3.13 | 20.1 |
| 11:53 | Dow subset | 202 | 0.017 | 1.02 | -5.73 | 20.0 |
| 12:32 | Dow-29 | 309 | 0.195 | 1.33 | -4.60 | 30.1 |
| 12:35 | Dow-29 | 193 | 0.307 | 1.52 | -4.76 | 36.9 |
| 12:39 | Dow-29 | 174 | 0.315 | 1.69 | -4.16 | 38.8 |
| 12:51 | Dow-29 | 236 | 0.104 | 1.28 | -3.84 | 14.6 |
| 12:53 | mid-cap subset | 90 | 0.109 | 1.18 | -5.47 | 27.7 |
| 12:54 | mid-cap subset | 125 | 0.271 | 1.47 | -4.67 | 36.7 |
| 12:56 | MIDCAP_UNIVERSE (27) | 239 | 0.172 | 1.28 | -5.86 | 42.4 |
| 12:56 | MIDCAP_UNIVERSE (27) | 224 | 0.205 | 1.37 | -5.52 | 45.7 |

**Expectancy positive and profit factor above 1.0 in every one of the 10 variants -- and alpha negative in every one of the 10 variants**, regardless of universe or parameter setting (range -3.13% to -5.86%). Buy-and-hold on whichever symbol set was in play beat the strategy in all ten. This is the same shortlist bar (Sharpe > 0.5 **and** alpha > 0) already established throughout this file being failed consistently, not occasionally -- and it reproduces, on a fresh sweep, the exact conclusion the 2026-07-17 mid-cap entry already reached ("Zero strategies flipped from failing to clearing the Sharpe/alpha bar on the smaller-cap universe").

**The "concentrate position size to fix cash drag" recommendation runs backwards against this project's own capacity-sweep findings, not toward them.** The 2026-07-16 sensitivity-sweep entries in this file show concentrating capital into fewer positions produces a Sharpe curve that looks great at low trade counts and degrades as more of the strategy's real signal gets captured (Range Trading: portfolio Sharpe 53.82 at 5 concurrent positions -> -5.07 at 29, same strategy, same window -- a small-sample illusion, not an early glimpse of edge). Nothing in today's sweep tested concentrated sizing directly, so recommending it as a fix is speculative on top of an already-negative-alpha result, not a next step actually supported by anything run here.

**Verdict, unchanged from 2026-07-17: mid-cap testing has not produced a strategy clearing the shortlist bar.** This sweep doesn't change that -- it adds a tenth and eleventh confirming data point (Dow-29 variants included) that a positive expectancy/profit-factor pair without positive alpha is not evidence of edge, it's evidence the strategy is riding a rising universe less efficiently than just holding it. None of today's runs were promoted to canonical.

---

## 2026-07-20 (cont'd 2) — best_run_per_strategy() was surfacing pre-instrumentation runs, silently hiding alpha on the Compare tab

The gap flagged as a "follow-up someday" in this file's own previous entry
turned out to be real and user-visible within the same day: the Compare
tab showed alpha as "—" for about a third of strategies (VWAP Bounce,
Pullback to 21 EMA, Breakout from Consolidation, Momentum/Gap and Go,
Sector Rotation Play, Range Trading, Oversold Bounce, and others) despite
every CURRENT run of those strategies computing alpha fine.

**Root cause, confirmed by querying the DB directly rather than guessing:**
alpha_pct/beta were added to the `runs` table via an ALTER-based migration
partway through this project's history (~2026-07-16 16:00-17:00, per
`SELECT DISTINCT strftime('%Y-%m-%d %H', run_at) ...` bucketed by hour).
Rows logged before that have `alpha_pct = NULL` -- not because the
strategy has nothing to beat, but because the column didn't exist yet.
`best_run_per_strategy()` (added earlier today, ranks canonical runs by
Sharpe) had no way to know the difference between "no benchmark concept"
and "predates instrumentation" -- it just picked whichever canonical row
had the best Sharpe, and for these strategies that happened to be an old
July-16-morning run, because Sharpe also moved between then and now.

**Fixed by ranking on alpha-completeness before Sharpe**: `(alpha_pct IS
NULL) ASC` as the first `ORDER BY` key, so a row with a real alpha always
outranks one without, regardless of whose Sharpe looks better. A strategy
whose engine genuinely never computes alpha (Overnight Hold -- no
benchmark concept, see `engine/overnight.py`) has every canonical row tied
on that criterion, so it falls through to the Sharpe ranking exactly as
before -- verified no regression there (`Overnight Hold -> alpha=None`
is the same before and after). Not applied to
`best_portfolio_run_per_strategy()` -- `portfolio_runs` has no alpha_pct
column at all; Dual Momentum/Pairs' `None` alpha is a separate, already-
disclosed structural fact, not this bug.

**Generally: "rank by the metric that looks most like quality" is not
safe when that metric's presence itself correlates with which code
version produced the row.** A schema migration that backfills NULL for
old rows doesn't just mean "missing data" to a downstream ranking query --
it means "this row is from before a class of information existed," and a
ranking that doesn't account for that will systematically prefer stale
rows over current ones whenever the newer instrumentation happened to
coincide with a worse number. Two tests added
(`test_best_run_per_strategy_prefers_runs_with_alpha_over_higher_sharpe_without`,
`test_best_run_per_strategy_alpha_completeness_is_a_noop_when_no_run_has_it`)
covering both the fix and the explicit no-regression case. Full suite: 338
passed.

---

## 2026-07-20 (cont'd) — Insider Buying strategy code (Variant A/B) built on the validated EDGAR feed; sector filter deliberately a stub

Built `strategies/swing/insider_buy.py` (signal generation) and
`engine/insider_buy.py` (dedicated engine) once the EDGAR pipeline's 5
checks passed -- same split as Dividend Hybrid/Overnight Hold, and for a
similar reason: the entry signal comes from an external per-symbol event
feed (Form 4 filings, like PEAD's earnings dates), and the exit is a
genuinely FIXED 5-day hold, which `exit_signal(bars)` can't express (same
wall Turnaround Tuesday/PEAD hit). Sizing IS risk-based here (1% equity /
8% stop), unlike Dividend Hybrid's flat 10%, so this is a hybrid of both
precedents rather than a clean match to either.

**Two real accounting bugs, both caught only by running the real 1-year
Dow-29 data through it, not by the unit tests (which used synthetic
single-signal fixtures and couldn't see either).**

1. Variant B crashed outright (`TypeError` on `NaT - date`) because
   `variant_b_signals` never carried a `filed_at` column through to the
   engine -- Variant A's signals come straight from `filing_level_purchases`
   (which has it), but Variant B's come from the cluster-resolution
   function, which only ever returned issuer/date/accession/cluster
   columns. Fixed by having the cluster resolver also carry the
   *completing* filing's own `filed_at`/`earliest_transaction_date` through
   -- the right choice is the completing filing's, not an earlier cluster
   member's, since the signal is dated to the filing that closes the cluster.

2. The "mean filing lag" diagnostic computed a nonsensical negative number
   (-1.33 days) on the first real run. It was computed as filed_at minus
   *signal_date* -- but signal_date is deliberately filed_at rolled forward
   whenever a filing lands after 4pm ET, so for a large fraction of real
   after-hours filings that subtraction is structurally negative by
   construction, not by data error. The task's own filing-lag definition
   (filed date minus *transaction* date) was already correctly implemented
   and validated in `engine/data_edgar.py`'s Check 5 -- this was a second,
   badly-named, wrongly-defined metric invented for the trade-level report
   that measured something else entirely. Fixed by computing the same
   filed-minus-transaction-date definition, scoped to the signals that
   actually became trades, instead of inventing a new "signal-to-entry lag"
   concept the task never asked for.

**A third, quieter bug found the same way: `n_entries` overcounted relative
to actual trades placed.** Signals were counted as "entered" the moment
they cleared the sector/liquidity/regime gates, before the day-by-day
simulation loop had a chance to reject them for a reason those gates don't
cover -- another position already open in the same symbol (only one
position per symbol at a time), or position sizing rounding down to zero
shares. First real run: 16 "entries" resolved but only 12 trade rows
existed. Fixed by counting entries at the point a position is actually
opened in the simulation loop, and reporting the gap explicitly
(`n_blocked_concurrent_or_sizing`) rather than letting `n_entries` and
`len(trades)` silently disagree. **Generally: a diagnostic counter that
increments at signal-resolution time will drift from the simulation's real
outcome the moment the simulation has its own independent rejection
reasons -- count at the point of the actual state change, not earlier.**

**Alpha/beta had no precedent to reuse.** Overnight Hold and Dividend
Hybrid both leave alpha/beta as `None`/`NaN`, disclosed, because
`engine/backtest.py`'s alpha/beta come from backtesting.py's own internals,
unavailable to a hand-rolled chronological engine. The task explicitly
wants both computed here, so `engine/insider_buy.py:_alpha_beta` does a
plain single-factor CAPM regression (`beta = Cov(port, bench) / Var(bench)`,
`alpha = excess return - beta * benchmark excess return`) on daily-resampled
equity-curve returns against SPY -- the first engine in this project to
compute these itself rather than disclosing their absence.

**Real result on the 1-year Dow-29 sample (the window this session's
EDGAR fetch actually covers -- see the entry above): Variant A -- 20
signals, 12 entries (4 blocked by regime, 4 by same-symbol-already-open/
sizing), 12 trades, 66.7% win rate, +0.154R expectancy, profit factor 2.11,
beta 0.012 (below the task's own expected 0.05-0.15 range), Sharpe -1.68.
Variant B -- 1 signal in the entire window** (three distinct insiders
buying the same Dow-29 name within 5 days is rare even before any
sector filter; the underlying feed only has 44 total qualifying purchases
across the whole universe/window), one losing trade. Both variants report
`STATUS_SAMPLE_TOO_SMALL` and both print an explicit flag for the <30-signal
floor -- per the task's own instruction, this is reported as "the pipeline
and logic work, the sample doesn't support a verdict," not as evidence for
or against the strategy either way. Win rate above 65% (Variant A) is
flagged for scrutiny rather than treated as a good sign, per the task's own
"suspect look-ahead if dramatically above 65%" instruction -- plausible
here given a 12-trade sample and no sector filter yet, not dismissed either.

**Deliberately NOT done, disclosed rather than faked:**
- **Sector filter is a no-op.** `PRE_REGISTERED_SECTORS` is `None` --
  the Reddit post's sector list was never provided in this session, and
  the task itself prohibits choosing a list after seeing results, so there
  is nothing to pre-register from. Every run prints `SECTOR_FILTER_WARNING`
  and `logs/insider_sector_breakdown.csv` (also required by the task) is
  an explicit `NOT_AVAILABLE` placeholder, not a fabricated table -- this
  codebase has no GICS sub-industry data source at all yet, a second,
  independent reason the sector-filtered comparison can't be run even with
  a list.
- **Universe/window is 1-year Dow-29, not 5-year "broadest universe."**
  Per the same scope decision documented in the EDGAR pipeline entry above.
  Widening either is mechanical once decided -- `engine/compare_insider_buy.py`
  already takes `symbols`/`start`/`end` -- but re-running the report against
  a wider EDGAR fetch is a several-hour prerequisite, not something to do
  silently under a "build the strategy" request.

22 tests added (`tests/test_strategies/test_insider_buy.py`,
`tests/test_engine/test_insider_buy.py`) covering filing-level aggregation,
both variants' signal logic (including the greedy non-overlapping cluster
resolution and same-filer-twice-doesn't-count), entry timing, stop-vs-time
exit precedence, regime gating, sizing, and the liquidity filter. Full
suite: 336 passed.

---

## 2026-07-20 — SEC EDGAR Form 4 pipeline built and validated (engine/data_edgar.py); strategy code intentionally not started yet

Built the insider-buying data pipeline per the task's own instruction to
validate the feed before writing a single line of strategy code. Two real
bugs surfaced during validation that would have silently produced wrong
data if the checks hadn't been run for real, plus one scope decision
significant enough to flag rather than decide silently.

**The filed-at timestamp, not the transaction date, is the signal date --
this is the whole point of the pipeline.** Form 4 has a 2-business-day
filing deadline, so an insider can buy Monday and not file until
Wednesday; using the transaction date would let the backtest "know" about
a purchase before it was public. Sourced the filed-at timestamp from
EDGAR's own atom filing-history feed (`<updated>`), which already carries
the correct America/New_York UTC offset (DST-aware, SEC's own
server-side acceptance time) -- no timezone math applied to it, unlike
the dividend-timestamp mismatch bug from `engine/fundamentals.py`
(different lesson, same family: trust the source's own stamping
convention, don't re-derive it). After-hours filings (>=4pm ET) and
filings made on non-trading days (EDGAR accepts filings any day of the
week) roll forward to the next trading day using the same cached SPY
calendar every other engine module reads.

**Bug 1 -- browse-edgar's pagination hard-fails past offset ~5100.**
Discovered fetching GS's filing history: paging via `start=` 503'd
consistently past ~5100, confirmed by direct retry (not a transient
blip). Fixed by using the `dateb=` cursor (filings on/before a date) to
reset pagination back to offset 0 once a safety threshold (4000) is hit,
verified directly against EDGAR that this doesn't skip or duplicate
entries (duplicates across the reset boundary are deduped via a `seen`
set, which costs a few harmless re-fetched pages, not data).

**Bug 2 -- `owner=include` doesn't mean "Form 4s about this issuer," it
means "Form 4s involving this CIK in ANY role," and that's a real
contamination source for institutional filers.** GS's CIK, searched with
`owner=include`, returned Form 4s for BACQ, SG, and QVCGP -- none of them
Dow names, none of them GS. GS (a large broker-dealer) itself files as a
reporting owner on other companies' Form 4s when it crosses beneficial-
ownership thresholds, and `owner=include` surfaces those alongside GS's
own insider filings. Fixed two ways: switched to `owner=exclude`
(issuer-only) at the fetch-listing layer, verified directly against
EDGAR that it removes exactly the contaminating accessions and nothing
else; and added a second, independent guard at the parse/store layer
(only keep a transaction if the filing's own issuer CIK matches the
ticker being searched for) so this class of bug can't recur silently
even if a future change reintroduces `include` somewhere. Cleaned 50
already-stored contaminated rows (of 94) from the DB before re-validating.
**Generally: a URL parameter's name is not its contract -- "include the
owner" sounds like it should only ADD information, not change what
"about this company" means. Verified the fix by diffing actual accession
lists between `include`/`exclude` against the same CIK/date window,
not by reading documentation.**

**Real-world Form 4 filing volume for large financials is 100-500x an
industrial/tech Dow name's, and that changed the validation window.**
AAPL: ~90 filings/year. GS and JPM: tens of thousands/year even after
the `owner=exclude` fix (still paginating after 30+ minutes for GS alone
during a follow-up 5-year volume check) -- large banks simply have far
more Section-16 officers and 10b5-1-plan-driven filing activity than an
industrial company. The task's own validation check (Check 4) assumed "a
5-year Dow-29 window should show hundreds of Form 4 filings" -- true in
aggregate, but a 5-year fetch across all 29 names, several of them
financials, is a multi-hour-to-multi-day job at SEC's 10 req/s fair-access
ceiling. Flagged this to the user rather than silently either running a
job that could take over a day or silently shrinking scope; agreed
outcome: validate on a full REAL fetch over a 1-year Dow-29 window
instead of 5 years, revisit the full 5-year fetch as a separate longer
background job for the actual strategy backtest phase.

**A background fetch on a laptop needs `caffeinate`, and stdout needs
`-u`, or a multi-hour job can silently vanish.** The first 1-year fetch
attempt died silently ~4 hours in (after committing 20 of 29 tickers,
including GS) with no traceback anywhere -- most likely the machine
slept, which can kill a plain `nohup`'d background process outright.
Worse, its progress log was a 0-byte file despite real work having
happened, because Python fully buffers stdout when redirected to a file/
pipe and a non-graceful process death never flushes that buffer --
durable SQLite commits survived, the human-readable log of what happened
did not. Resumed cleanly thanks to the pipeline's own caching
(`fetched_accessions` skip-if-already-fetched), this time wrapped in
`caffeinate -i` and `python3 -u`. **Generally: a multi-hour unattended
job needs both "don't let the OS sleep" and "flush output eagerly" or a
crash looks identical to silence, and you can't tell which without
re-deriving state from the durable store (here, the DB) rather than the
log.**

**Validation result: all 5 checks pass on the real, cleaned 1-year Dow-29
fetch.** No look-ahead (20/20 sampled transaction dates <= filed dates),
after-hours filings correctly roll to the next trading day (41 after-hours
filings found, 20 sampled, 0 violations), every stored row is code 'P'
with positive shares (0 non-qualifying rows in the DB), filing-lag median
2.5 days / mean 186 days (long tail from a few filings with old
transaction dates bundled into one late filing -- e.g. AXP's Joabar
Raymond filing bundled seven 2023-2025 transactions into one 2026-03-13
filing), coverage 44 qualifying purchases across 12 of 29 tickers (fewer
than the task's own ">=50" expectation, but that threshold assumes 5
years of data, not 1 -- proportionally consistent, not a red flag).

**Overall win rate sanity check doesn't apply yet -- there's no strategy
result, only the raw signal feed.** That's intentional: per the task's own
instruction, strategy code (Variant A/B, sector filtering, entries/exits)
does not start until the pipeline validates, and it validates here, but
two inputs are still needed before strategy code can start: (1) a decision
on whether to run the full 5-year fetch now (background, likely several
hours given GS/JPM's true issuer-only volume is still being measured) or
build the strategy against the 1-year sample first and widen later, and
(2) the pre-registered sector list "from the Reddit post" the task
references -- never actually provided in this conversation, and Variant A
and B's sector filter can't be pre-registered from nothing. Both need the
user, not a guess.

---

## 2026-07-20 — Compare tab: leaderboard now shows each strategy's best-Sharpe canonical run, not just its most recent one

**The ask ("show the best performing run") was in tension with a
principle CLAUDE.md states explicitly** -- `latest_run_per_strategy()`
exists specifically so a Lab-tab experiment or a stale pre-bugfix run
can never silently become what the leaderboard shows, and this project
has multiple documented cases (this file, throughout) of an old run's
number being wrong because a bug got fixed after it ran. "Best" could
mean "best among honest canonical re-runs" (safe) or "best across
everything including one-off parameter sweeps" (exactly what the
canonical/experimental firewall exists to prevent). Asked the user to
choose scope rather than guess; they delegated the choice back. Went
with the safer reading: best-Sharpe **canonical-only**, keeping the
firewall intact -- `engine/logging_db.py`'s new `best_run_per_strategy()`
/ `best_portfolio_run_per_strategy()` sit alongside (not replacing)
`latest_run_per_strategy()`, since run-history views elsewhere still want
strict recency. Only one real call site needed updating
(`/api/strategies` in `api/main.py`).

**NULL Sharpe must sort last, not first or arbitrarily.** SQLite's `MAX()`
ignores NULLs, and naive `ORDER BY sharpe DESC` puts NULL last by
default too, but the ranking here uses an explicit `(sharpe IS NULL) ASC`
key first specifically so this stays true regardless of NULL-ordering
defaults, which vary across databases and are easy to get backwards --
a run with no computed Sharpe (no benchmark concept, e.g. Overnight
Hold-style engines) must never look better than a run with a real, even
negative, Sharpe just because NULL sorts oddly.

**A same-second tiebreak bug, caught by a fast test -- not a flaky
test.** `run_at` has only second resolution (already documented
elsewhere in `engine/logging_db.py` for exactly this reason), so two
`log_run()` calls in the same unit test can land in the same second.
The first version's `ORDER BY ... sharpe DESC, run_at DESC` correctly
ranked by Sharpe but fell back to arbitrary table-scan order on a full
tie, failing a test that asserted the more-recent-of-two-equal-Sharpe
runs should win. Fixed by adding `id DESC` as the final tiebreak, the
same fix `portfolio_run_history()` already uses for the identical
reason. **Generally: a test that fails intermittently or "only
sometimes" at this resolution is not flaky, it's timing-dependent on a
column whose granularity doesn't match how fast the test can call the
function under test -- add a tiebreak keyed on something monotonic
(here, the autoincrement id) rather than tightening the clock.**

**Real-data check: 14 of 21 strategies' best and latest canonical runs
differ**, several by a lot (News Fade: latest Sharpe -11.4 vs. best
+1.1; Pullback to 21 EMA: latest -1.4 vs. best +0.63). Verified live
against the running API (`/api/strategies` picked up the change via
uvicorn's reload, checked via curl) rather than trusting the unit tests
alone. Worth a follow-up someday, not today: several of these gaps are
large enough that the earlier, better-scoring run may predate a real
regression in the current code (data update, engine change) rather than
just noise -- the Compare tab now surfaces the best number, which is
what was asked for, but "best" and "still representative of the current
codebase" are not the same claim, and nothing here investigates *why* a
strategy's Sharpe moved between runs.

---

## 2026-07-20 — Param-field audit across all 24 strategies: one real bug found, one self-inflicted regression caught by the test suite

**A hardcoded warm-up guard can silently assume its own parameter's minimum
is unreachable.** `Range Trading`'s `_range()` required `len(window) >= 6`
before it would compute a range at all, but `window` is `tail(self.
range_lookback_bars)` and that field's own declared UI minimum is 5 -- so at
the slider's own minimum, the strategy could never fire, regardless of how
much history was available. Found by an audit pass looking for hardcoded
constants that should be `param_field()`s, not by anyone deliberately testing
the slider's extremes. Fixed by checking `len(window) < self.
range_lookback_bars` instead of a number disconnected from the parameter it
was supposed to gate. General shape: whenever a `param_field()`'s bounds are
declared, grep for any hardcoded guard nearby that might silently assume a
value the slider itself permits.

**Scaling a warm-up guard to match a newly-tunable period is not
automatically the safer choice -- check whether the guard was ever
protecting against a real NaN in the first place.** Scalping's confluence
check had a fixed `len(bars) < 25` guard when EMA(9)/EMA(20)/MACD(12,26,9)
were bare constants. When those periods became `param_field()`s, the
instinct was to scale the guard too (`max(ema_slow_period,
macd_slow_period) + 5`), reasoning that a longer slow period needs more
warm-up. That reasoning is wrong for this codebase's `ema()`/`macd()`:
they're `ewm()`-based, which never produces NaN for "too little" history the
way a `rolling()`/`sma()` window would -- it just takes a few periods to
numerically converge. The scaled guard required 31 bars at the new default
periods (20/26+5) versus the original 25, silently breaking
`test_sustained_uptrend_gives_bullish_confluence`/`..._bearish_confluence`
(both use exactly 30 bars) -- caught immediately by the existing test suite,
not by inspection. Reverted to the fixed `25`. Lesson: "this constant looks
related to a parameter I just made tunable" is not sufficient justification
to couple them -- check what the indicator actually does with insufficient
history before assuming a fixed guard is stale.

**A dataclass with genuine "rule parameters for one run" language in its own
docstring can still have adopted none of the `param_field()` convention.**
`Dividend Hybrid` (`strategies/swing/dividend_hybrid.py`) has ~14 numeric
screen/entry/exit thresholds, every one a bare dataclass default sourced
from a bare module constant -- the only strategy file in the book that never
picked up the pattern CLAUDE.md documents for every other one. Deeper
finding on investigation: it's not reachable from the Lab tab at all (no
`ALL_STRATEGY_NAMES` entry, no `engine/runner.py` wiring, no API endpoint,
no result view) -- it's a standalone research script in the same category
as `compare_filters.py`/`compare_universe.py`, deliberately outside the
canonical dashboard. Adding `param_field()`s to it alone would decorate a
dataclass with zero visible effect; making it genuinely user-tunable "in the
UI" is a same-sized task as last session's Dual Momentum/Pairs integration
(registry entry, bespoke runner wiring, API endpoint, a custom result view
for its two-version/mark-to-market output), not a quick follow-on. Flagged
to the user rather than silently doing the cosmetic-only half of the job;
user chose to skip it for now.

---

## 2026-07-19 (cont'd 3) — Research platform added (Screener/Movers/Market Signals/Insider Buying/Digest); a stale-cache bug and a sort-with-nulls bug, both caught before shipping

**A dataclass field cache is a schema, and an old cache is a schema
migration, not just "no data yet."** Added three quality fields
(`profit_margins_pct`, `return_on_equity_pct`, `debt_to_equity`) to
`engine/fundamentals.py:FundamentalSnapshot`. Every symbol already had a
cached JSON snapshot from before these fields existed, and
`FundamentalSnapshot(**json.loads(...))` silently accepted the missing keys
via their `= None` defaults — a smoke test on 4 symbols came back with
`qualityScore: None` for all four, which reads exactly like "no quality
data available" rather than "this cache predates the field." Fixed by
having `snapshot()` check for the new keys and refetch once if any are
missing, rather than trusting any cache that predates a schema change. Same
root cause as the `PairsResult.symbols` append-only-field lesson from
earlier this session, one layer deeper: appending a field with a safe
default prevents a *crash*, but doesn't prevent silently-wrong data unless
the loader also checks for the field's *presence*.

**Reversing a sorted-with-nulls-last array does not produce
sorted-with-nulls-last in the other direction.** `ScreenerView.tsx`'s first
sort implementation sorted ascending (nulls always pushed to the end via
explicit `if (av === null) return 1`), then called `.reverse()` for
descending order — which also reverses the nulls to the *front*, so
sorting "Valuation" descending put every symbol with no P/E data at the top
of the table, ahead of real scores. Caught via a Playwright screenshot,
not by reasoning about the code — the bug was invisible in the diff. Fixed
by keeping the null-check unconditional (`return 1`/`-1` regardless of
direction) and only flipping the sign of the *real* comparison
(`sortAsc ? cmp : -cmp`). General shape: a sort with a special case for
missing data can't be "reverse the ascending order" — the special case has
to be direction-invariant on its own.

**A live cross-sectional universe doesn't inherit the backtest universe's
survivorship-bias rules, but that's a reason to say so, not to skip
disclosure.** `RESEARCH_UNIVERSE` (94 symbols, the union of the existing
backtest universes) intentionally carries none of `EQUITY_UNIVERSE`'s
point-in-time-membership caveats, because a live screener describing
today's state has no "picked after seeing what moved" risk the way a fixed
backtest sample does. Documented as a deliberate distinction in CLAUDE.md
rather than left for a future reader to wonder whether it was an oversight.

**A stray background job can out-live the task that started it and block
unrelated work later in the same session.** A `python -m engine.data_edgar`
fetch (started earlier in this session for an unrelated check) was still
running 3+ hours later, holding a long write transaction on
`data/edgar_form4.db` that made the new `recent_purchases()` read fail with
"database is locked" even after adding `PRAGMA busy_timeout`. Root cause
wasn't the busy-timeout value — it was that the shared `connect()` helper
always re-runs `executescript(_SCHEMA)`, which itself needs to briefly
escalate to a write lock even when every `CREATE TABLE IF NOT EXISTS` is a
no-op. Fixed with a separate lightweight read connection that skips the
schema script entirely, confirmed by killing the stray job (with the user's
sign-off, since killing a process is a confirm-first action) and re-running
the same read successfully.

---

## 2026-07-19 (cont'd 2) — Anchored VWAP Breakout added; 2 trades, diagnosed as filter compounding, not a broken rule

**New strategy, new module.** `engine/avwap.py` (pure AVWAP calc + earnings-
gap/swing-low anchor selection, hand-verified against exact synthetic
expected values -- see `tests/test_engine/test_avwap.py` -- rather than a
one-off manual check against a real symbol's messy floats, which is
neither exact nor reproducible) and `strategies/swing/avwap_breakout.py`
(the strategy: cross back above AVWAP after >=5 consecutive bars below it,
volume-confirmed, exited by AVWAP cross-below / an 8% hard stop / a 60-day
time stop). First strategy whose canonical definition bakes the regime +
Trend Template gate into its own entry rule rather than treating it as an
optional overlay (`engine/runner.py`'s `_run_avwap_breakout` wraps it with
`engine.filters.build_filter_factory` directly) -- every other strategy
in the book doesn't self-filter.

**Data-availability check came back clean.** 0/29 `EQUITY_UNIVERSE` symbols
missing earnings dates, mean 20 events/symbol over the 5-year window --
comfortably inside the 20%-missing threshold, so the earnings-gap anchor
type was locked (not the swing-low fallback). `engine.run_avwap_breakout.
ANCHOR_TYPE` is a hardcoded constant recording this, not re-decided per run.

**Result: 2 trades in 5 years across the whole universe -- not an
evaluable sample, and small enough to need real diagnosis rather than
just reporting "doesn't clear the bar."** Isolated the cause with a
diagnostic-only run (not the reported result -- the locked spec requires
both filters) with the regime + Trend Template gate removed: the AVWAP
entry rule alone fires 33 times from 61 qualifying anchors (2.1/symbol,
a plausible, non-degenerate selectivity -- matches the task's own
"some fraction of ~15-20 earnings dates should qualify" sanity range).
So neither the anchor selection nor the entry rule is the weak link. The
actual cause: regime + Trend Template together pass only 13.7% of all
bar-days, and their overlap with the AVWAP entry condition (already rare,
~0.09% of all bar-days unfiltered) collapses to 2 total qualifying bars.
**Same shape of failure as Oversold Bounce's 210-trades-to-0 (see the
"Two filters with opposing theses annihilate rather than compose" entry
below), but a different cause** -- Oversold Bounce and Trend Template have
opposing theses (mean-reversion vs. trend-following); AVWAP Breakout and
Trend Template are BOTH trend-following, so this isn't a thesis conflict,
it's two independently-selective, thesis-compatible filters whose
intersection is just very small. Worth remembering as a second, distinct
way a filter combination can produce near-zero trades -- contradiction
isn't the only mechanism, compounding selectivity is another.

**Sharpe on 2 trades (-12.0) is a small-sample artifact, not a result** --
same "flag it as unreliable regardless of how good/bad the number looks"
principle as the sector-rotation exit-cadence test above, extended to a
strategy this new rather than just a variant of an existing one.

Tracker (Swing Trading tab, row 17) updated with the real numbers and this
diagnosis; not promoted to held-out validation (2 trades never reaches the
shortlist bar's 30-trade floor). Next step if this strategy is revisited:
test on a broader/more volatile universe where Trend Template pass rates
run higher, or test the regime filter alone (drop Trend Template
specifically) to see where the sample recovers -- not attempted here since
changing the locked entry rule after seeing a bad result would defeat the
point of locking it.

---

## 2026-07-19 (cont'd 2) — Weighted Voting Ensemble: built on the existing cross-sectional engine, not a new one

Implemented a regime-gated ensemble (`strategies/swing/ensemble_voting.py`,
`engine/ensemble.py`) combining Dual Momentum, PEAD, Breakout from
Consolidation, Earnings Momentum/Gap-Hold, and IBS, with dynamic
rolling-Sharpe sub-strategy weighting and inverse-ATR risk-parity sizing.
Requested as a from-scratch build (new `BaseStrategy`/`EnsembleEngine`/
`Backtester` classes) against what reads like a blank-repo prompt template.
**The real repo already had the right shape for most of this**, and using
it instead of parallel infrastructure was the main design decision:

- `strategies.cross_sectional.CrossSectionalStrategy` (`rebalance(bars,
  as_of) -> {symbol: weight}`) already exists and is exactly the interface
  an ensemble needs -- Dual Momentum has used it since 2026-07-17. Built
  `EnsembleWeightedVoting` as one more implementation of it instead of
  inventing a differently-named `BaseStrategy` ABC.
- `engine/cross_sectional.py`'s `run_cross_sectional_backtest` already runs
  a rebalance-driven backtest with an equity curve, Sharpe/Sortino/CAGR/
  drawdown -- extended it (additive, backward-compatible defaults so Dual
  Momentum's canonical numbers don't shift) with `rebalance_frequency`
  ("monthly" default preserved, "weekly" added) and `slippage_bps`/
  `commission_bps` (0.0 default preserved) instead of writing a new
  `Backtester` class. CLAUDE.md is explicit about not building a third
  engine when an existing one doesn't fall short -- here it didn't.
- The 5 sub-strategies are the real registered instances (`strategies/swing/
  breakout_consolidation.py`, `earnings_momentum.py`, `internal_bar_
  strength.py`, `pead.py`, `dual_momentum.py`), not reimplementations --
  PEAD is seeded with real per-symbol earnings dates the same way
  `engine/runner.py::_run_pead` already does it.

**The genuinely hard, non-mechanical problem: 4 of the 5 sub-strategies
only expose a boolean `entry_signal`/`exit_signal`, not a continuous
score.** The spec asked for S_i(t) in [-1, +1] without saying how to derive
one from a boolean rule. Rejected inventing an unrelated decay/confidence
curve (e.g. "score decays linearly over N days after entry") as
unfalsifiable and disconnected from what the strategy actually does.
Instead, `engine/ensemble.py::boolean_strategy_position_series` replays
each strategy's own real entry/stop/target/exit rules bar-by-bar and scores
+1.0 exactly while that strategy would genuinely be holding a position it
opened itself, 0.0 otherwise -- faithful to the registered rule, at the
disclosed cost of a close-based approximation (checked once per bar close,
not intrabar via a real bracket order, since there's no broker fill to
model for a pure scoring pass).

**"Rolling 63-day Sharpe of a sub-strategy" isn't well-defined when the
sub-strategy has no equity curve of its own** (it only emits per-symbol
signals). Defined it as the trailing-window Sharpe of an equal-weight,
daily-rebalanced basket of whatever the sub-strategy currently scores
positively -- a real, computable return series with a defensible reading
("if you'd equal-weighted whatever this sub-strategy likes right now, how
has that basket performed"), not an arbitrary number.

**Bug caught by the ensemble's own test suite before it shipped:** the
inverse-ATR risk-parity sizing's cap-and-redistribute step had a real
convergence bug -- redistributing a capped symbol's overflow onto the
remaining "uncapped" symbols could push one of *those* over the cap too
(exactly the case where one symbol's ATR is far smaller than the rest), and
a single non-iterating redistribution pass left it over the cap rather than
re-capping it. Fixed with the standard iterative water-filling procedure:
permanently fix any symbol that overflows the cap in a given pass, remove
it from the pool, and re-split only the remaining budget across whatever's
left, repeating until a pass fixes nothing new. Caught immediately by
`test_inverse_atr_weights_respects_cap_and_redistributes_to_others` (a
direct unit test on the sizing function, not just the end-to-end demo) --
the specific value of testing the pure allocation math in isolation from
regime/scoring noise.

**Not wired into `strategies/registry.py` or the webapp**, matching the
precedent already set for Dividend Hybrid: a structurally novel strategy
(here, a meta-strategy with its own weighting/sizing axes no other strategy
has) stays as directly-runnable code
(`python -m engine.demo_ensemble` for a synthetic-data sanity run) until
it's been run against real data and evaluated, rather than entering the
Compare tab's leaderboard sight unseen next to ordinary single-rule
strategies. **First real-data run** (`python -m engine.run_ensemble`, EQUITY_UNIVERSE +
SPY, 2021-07-20 to 2026-07-19, weekly rebalance, 5bps slippage): 261
rebalances (173 ACTIVE, 88 DEFENSIVE), +43.11% total return, CAGR 7.45%,
Sharpe 0.14, Sortino 0.20, max drawdown 17.32%, $688.02 total costs. **Does
not clear the Sharpe > 0.5 shortlist bar** -- same outcome as every other
strategy, universe, filter, and capacity level tried in this project so
far. Zero allocation-cap or sum violations across all 261 rebalances on
real data, matching the synthetic-data sanity checks. Not logged to
`strategy_tracker.xlsx` -- the Day/Swing tabs are the registered 19-strategy
book and Experiment History is variations *of* those strategies; this is a
new meta-strategy outside that scope, consistent with keeping it out of
`strategies/registry.py` for now.

**Known inefficiency, not yet fixed:** the real run took roughly 45-50
minutes of CPU. `EnsembleWeightedVoting.rebalance()` rebuilds every boolean
sub-strategy from scratch and replays its ENTIRE bar history from bar 0 at
every single rebalance call (`boolean_strategy_position_series` has no
memory across calls) -- with 261 weekly rebalances x 29 symbols x growing
history (up to ~1,260 daily bars) x 5 sub-strategies, that's tens of
millions of redundant bar-steps, plus PEAD re-fetching each symbol's
earnings-date cache from disk at every rebalance instead of once. Fine for
a single one-off run; would need incremental/cached per-symbol state
(compute each position series once, look up the as-of value per rebalance)
before this is practical for a parameter sweep.

---

## 2026-07-19 (cont'd) — Full 22-strategy mid-cap sweep, and two real bugs it surfaced

Completed Section 1's mid-cap comparison (2026-07-17 covered 7 of 22
strategies) by running `engine.compare_universe.main()` against all 22
registered strategies except Sector Rotation Play (structurally excluded,
same reasoning as before). **Same conclusion as before: zero strategies
clear Sharpe > 0.5 and alpha > 0 on mid-cap either.** Several flip between
"drop" and "hold" at the margins in both directions — noise around a
near-zero threshold, not a real edge appearing anywhere. Full per-strategy
numbers are in `strategy_tracker.xlsx`'s Experiment History sheet, Section 4.

**A large apparent discrepancy against Section 1 turned out to be a data
regime change, not a bug.** Section 1's Scalping mid-cap row shows Sharpe
-296.41; this run shows -41.36 for the same strategy/universe. Traced it:
Section 1's mid-cap comparison (line order in this file: the "Testing
mid-caps" entry sits *below* the "Switched intraday data to Alpaca" entry,
both dated 2026-07-17 — and since entries are prepended, lower means
*earlier* that day) ran **before** that same day's migration from yfinance's
~57-60 day intraday window to Alpaca's 730-day window. So Section 1's four
intraday day-trading rows (Scalping, VWAP Bounce, News Fade, Range Trading)
were computed on a tiny, pre-migration sample and are now superseded — this
run's numbers for those four supersede them (see Section 4's intro note).
Daily-bar strategies in Section 1 (Breakout from Consolidation, Dual
Momentum, Pairs/Stat Arb) weren't touched by that migration and reran close
to their original numbers, confirming the explanation rather than just
asserting it.

**Bug 1 — `engine/compare_universe.py` had no branch for PEAD or Overnight
Hold.** Both are special-cased in `engine/runner.py` (`_run_pead`,
`_run_overnight`) but the comparison script's `run_one_on_midcap()` never
got the equivalent branches, so it raised `ValueError: Unknown strategy` and
crashed 20 strategies into the sweep. Added both branches, calling
`run_strategy_backtest_seeded`/`run_overnight_backtest` directly against
`MIDCAP_UNIVERSE`, matching the runner's own logic minus the DB-logging call
(this script deliberately never logs, per its existing docstring).

**Bug 2 — `engine/metrics.py`'s `_status()` silently skipped its Sharpe/alpha
gate whenever *either* value was missing, not just when *both* were.**
`engine/overnight.py`'s `_symbol_stats()` explicitly documents that it never
computes alpha (`"Alpha [%]": np.nan  # not computed vs a benchmark for this
engine"` — no benchmark concept in that close→open engine). The status gate
was written as `if sharpe is not None and alpha_pct is not None`, so any
strategy with a real Sharpe but no alpha skipped the entire benchmark check
and fell through to `STATUS_POSITIVE` on bare positive expectancy alone.
**Measured impact: Overnight Hold showed "Positive expectancy - shortlist"
on the live dashboard with Sharpe -0.66** — the opposite of what the gate
exists to prevent. Fixed to gate on whichever of Sharpe/alpha is actually
present (`sharpe is None or sharpe > SHARPE_THRESHOLD`, same for alpha),
only skipping the whole gate when neither is supplied at all (preserves the
original intent for synthetic unit-test callers that compute neither). Full
suite still green (260/260). Re-ran Overnight Hold's canonical backtest
through `engine.runner.run_backtest()` afterward so the corrected status
propagates into `logging_db` and the dashboard immediately, rather than
waiting for the next unrelated run to overwrite the stale row.

**Data gap, disclosed not fixed: MKSI and MTSI have no usable intraday
history.** Alpaca's IEX feed doesn't cover them for 5-minute bars; the
yfinance fallback then hits its own 60-day intraday cap since the requested
window is 730 days. Both symbols silently drop out of every day-trading
strategy's mid-cap universe (25 effective symbols of 27) rather than erroring
loudly. Worth fixing the fallback to warn explicitly if this pattern recurs
for other symbols.

---

## 2026-07-19 — Sector Rotation Play exit-cadence test: task premise didn't match the registered strategy, and no variant cleared the shortlist bar

**A requested test's premise can be wrong about the code without being wrong
about the data.** The task asked to vary a "top-N sectors, monthly rebalance,
exit at drop-out-of-top-N" exit rule for Sector Rotation Play, with a
baseline of 117 trades / 44.4% win rate / +0.096R expectancy / 55.2% exit
efficiency. Those numbers are exactly right (verified against
`logging_db.latest_run_per_strategy()` and
`logs/sector_rotation_play_mfe_mae_summary.txt`) -- but the mechanism
described doesn't exist. The registered `SectorRotationPlay`
(`strategies/swing/sector_rotation.py`) is a per-ETF relative-strength EMA
crossover with NO cross-sectional ranking and NO rebalance calendar; its
exit is already signal-based, checked every trading day. The "top-N,
monthly rebalance" strategy that actually exists in this codebase is Dual
Momentum (`engine/cross_sectional.py`), which runs on the Dow universe and
produces equity-curve stats, not R-multiple trades -- structurally
incapable of producing "117 trades, 44.4% win rate." Flagged this to the
user before writing any code rather than either forcing a nonexistent
top-N engine onto Sector Rotation Play (which could never reproduce the
logged baseline -- the explicit "stop and debug" trigger in the task's own
instructions) or silently redefining what was being tested. Confirmed
direction with the user: keep the real entry/stop rule, redefine the four
variants purely as exit-check CADENCE (daily / every 21 trading days /
every 63 trading days / 21-day floor then daily) -- see
`engine/compare_sector_rotation_exits.py`.

**Result: no variant cleared the shortlist bar.** Sharpe stayed negative in
all four (A: -1.84, B/21d: -1.36, C/63d: -0.97, D/floor: -1.68); C had the
best Sharpe but negative expectancy (-0.14R) and only 36 trades; D had the
best expectancy (+0.17R, 101 trades) but Sharpe still deeply negative.
Exit efficiency rose with cadence (55.2% -> 71.7% -> 69.2%/57.2%) but not
monotonically, and Variant B's own loss realization ratio also rose to
90% -- coarser exit-checking doesn't just let winners run longer, it lets
losers ride further into the drawdown too before the (still-live,
untouched-across-all-variants) stop finally catches them. D looks like an
improvement over A (better expectancy AND Sharpe) but per the task's own
definition ("better than A but still negative Sharpe is an improvement,
not a finding") this doesn't promote to a candidate. Full table:
`logs/sector_rotation_exit_variants.csv`;
reasoning/flags: `logs/sector_rotation_exit_variants_notes.txt`.

**"Pending the mid-cap swap results" doesn't apply here.** The mid-cap
sweep (`engine/compare_universe.py`, see the 2026-07-17 entry below) already
ran and already explicitly excludes Sector Rotation Play -- its universe
(11 sector SPDR ETFs) has no mid-cap analog. Tracker note (T8, Swing
Trading tab) marks the strategy exhausted for this universe outright
rather than repeating a "pending" status that was never going to resolve.

**No held-out/reserved-period mechanism exists in this codebase.** The
task's fallback instruction ("if a variant clears the bar, validate once
on the held-out period") assumes a train/test split this project doesn't
have -- `daily_date_range()` is always "trailing 5 years from today," no
walk-forward or reserved chunk. Moot this time since nothing cleared the
bar, but worth building before the first strategy that does.

---

## 2026-07-18/19 — Market tab + live paper-trading monitor added (monitoring only, no order placement)

**Live alerts are NOT gated by engine/filters.py's regime/trend-template
filters, and that's deliberate.** The obvious design was to wrap each
day-trading strategy in `FilteredStrategy` before checking `entry_signal`
live, matching CLAUDE.md's "Pre-trade filters" section. But
`FilteredStrategy`/`build_filter_factory` are only ever exercised by
`engine/compare_filters.py`'s exploratory comparison — the canonical
`run_backtest()` path (what the Compare tab's numbers represent) never
routes through them. Gating live signals through filters the canonical
backtest doesn't use would make live alerts describe a different, stricter
strategy than the one whose win rate/expectancy the dashboard shows.
`engine/live_scanner.py` instead logs an alert whenever the raw
`entry_signal` fires and attaches `regime_state`/`trend_template_pass` as
*context columns* on the alert — informative, not gating. Check which path
a strategy's production numbers actually run through before assuming a
documented architectural layer is wired into all of them.

**Free-tier IEX bars lag ~16 minutes** (`engine/data.py`'s
`_ALPACA_RECENT_CUTOFF`). A live scanner polling every 30-60s against that
feed doesn't get fresher data — it just re-fetches the same already-stale
bar more often. The scanner polls on the strategies' own 5-min bar
timeframe instead, and the UI says "delayed," never "real-time."

**Never let a live/streaming fetch touch engine/data.py's parquet cache.**
That cache is what makes backtests reproducible; a live scan mid-session
would inject a partial trading day into the same file a backtest reads.
`engine/live_scanner.py` fetches directly from Alpaca's client (batched,
one request for the whole day-trading universe, sliced in memory per
strategy) and never calls `get_bars`/writes to `DATA_DIR`.

**`TradingClient(paper=True)` is hardcoded, never a toggle.** Even though
this project's `.env` was verified (via a live read-only `/v2/account`
probe against both endpoints) to hold genuine paper keys, the client
construction in `engine/alpaca_trading.py` doesn't trust that — `paper=True`
means live keys would fail auth outright rather than silently trading real
money, regardless of what ends up in `.env` later.

---

## 2026-07-18 (cont'd 4) — MFE/MAE and exit-quality diagnostics added (engine/excursion.py)

**New per-trade diagnostics, purely additive.** `engine/excursion.py` computes,
for every trade the standard per-symbol engine produces: MFE (best price
reached between entry and exit) and MAE (worst price reached), both in R so
they're directly comparable to `realized_r`; Exit Efficiency
(`realized_r / MFE_R`, winners only) measuring how much of the available
move was captured; Loss Realization Ratio (`|realized_r| / MAE_R`, losers
only) measuring how close a losing trade rode to its worst point before
exit; and Entry Slippage Distance (fill price vs. the signal bar's close,
scaled by that bar's range). None of this touches `compute_metrics`,
`BacktestMetrics`, or `logging_db` — win rate/expectancy/profit factor are
unchanged. Every `run_backtest()`/PEAD call now also writes
`logs/{slug}_mfe_mae.csv` (one row per trade) and
`logs/{slug}_mfe_mae_summary.txt` (aggregate stats + winner/loser scatter
coordinates). Not wired into Overnight Hold's close→open engine — it has no
intrabar path to walk MFE/MAE over.

**A trade whose MFE is below its own realized R (or MAE below its own
realized loss) is not a smaller number, it's a bug.** You cannot realize
more profit than the best price the trade ever reached, or lose less than
you can survive worse than its actual worst point. `compute_trade_excursions`
asserts this per trade and drops (logs an error, does not raise) any row
that violates it rather than writing a row that looks like data but is
actually a wrong `EntryBar`/`ExitBar` window or a bad `risk_per_share`
lookup silently laundered into a plausible-looking percentage.

**Real result on the two largest-sample underperformers, for context on
*why* they underperform (Sharpe/alpha), not just that they do:**
Pullback to 21 EMA (1,120 trades) has mean exit efficiency 38.4% with 78.5%
of winners below the 60% efficiency bar — most of the available favorable
move is being left on the table, not just occasionally. 9/21 EMA Crossover
(692 trades) is similar but less severe: 46.6% mean exit efficiency, 69.3%
of winners below 60%. Both strategies' loss realization ratios sit in a
plausible 50-100% range (59.3% and 67.8% mean) with no automated flags
triggered, so the exit-quality problem here reads as "exits too early,"
not "MAE/MFE computed wrong" or "stops are the problem." Neither number by
itself is a verdict — see the exposure/Sharpe decomposition rule above —
but it does point at exit rule tuning specifically as the next thing worth
testing on these two, rather than entry rule or filter changes.

---

## 2026-07-18 (cont'd 3) — strategy_tracker.xlsx: a third status, real results synced, and 4 rows with no formula chain at all

**Status formula gained a third tier.** The tracker's Status column only ever
distinguished positive vs. negative R-expectancy — it had no way to express
what's turned out to be the *majority* engine verdict, "positive expectancy
but underperforms cash/benchmark" (Sharpe ≤ 0 vs. risk-free). Sharpe isn't a
column in this workbook (it lives in the engine's output, not the tracker),
so the new 3-tier formula uses **expectancy > 0.1R** as an explicit, disclosed
proxy for "shortlist," not just > 0 — bare positive expectancy already proved
insufficient (see the 2026-07-16 "shortlist didn't survive a benchmark
comparison" entry). This is a coarser gate than the real Sharpe/alpha check
and disagrees with it in both directions (some 0.1R+ rows here still fail
Sharpe > 0.5 in the engine) — the Shortlist tab's legend now says so
explicitly, so this tab is read as a first pass, not the final word.

**Real results synced from the engine's own logging DB, not re-typed by
hand.** Trades/Wins/Avg Win/Avg Loss/dates were pulled with a script reading
`engine.logging_db.latest_run_per_strategy()` directly — the same table the
webapp's Compare tab reads — so the tracker and the dashboard can't drift
apart from a transcription error. Sample-too-small strategies (Momentum/Gap
and Go, 8 trades) got only a trade count, Wins/Avg Win/Avg Loss left blank
on purpose, per instruction — with fewer than 30 trades those numbers aren't
worth displaying even approximately.

**Bug found while wiring this up: 4 rows had no calculation chain at all.**
Pivot-Level ETF Reversal, PEAD, Turnaround Tuesday, and Overnight Hold —
all added after the tracker's Status/Expectancy/Profit-Factor formulas were
first set up — were missing the M/N/Q/R/S formulas entirely (not blank
inputs with working formulas — genuinely no formula in those cells, and no
yellow "fill me in" formatting either). Entering a trade count into any of
these rows would have done nothing; Status would sit blank forever no matter
what was logged. Backfilled the same formula chain and input-cell formatting
used everywhere else. **Lesson: when a tracker/spreadsheet gets new rows
added ad hoc (a new strategy dropped in without going through whatever
process set up the first N rows), check that the row actually has the same
live formula chain as its siblings — a row that LOOKS complete (has a name,
description, entry rule) can still be structurally inert.**

**Overnight Hold's number in the main tracker is a scoped null-control test,
not the strategy's registered default.** Per explicit instruction, run
against `EQUITY_UNIVERSE` (Dow-29) only — the strategy's actual registered
default is `ETF_AND_EQUITY_UNIVERSE` (sector ETFs + Dow, 41 symbols), which
would have been a *different number* in the same cell. Result: Sharpe -0.75,
ExpR +0.009R — near zero, the expected outcome for an unfiltered
close-to-open drift test with realistic spread costs (neither a suspiciously
generous cost model nor an overnight spread assumption that's too wide).
Both runs (Dow-29 scoped and the real registered-default ETF+Dow run,
Sharpe -0.66 on 28,142 trades) are logged in `logging_db` and in the new
Experiment History sheet; only the Dow-29 one is in the main row, with a
note (column T) making the scope explicit so it isn't mistaken for the
canonical result later.

**New "Experiment History" sheet holds every non-canonical variation already
run** (mid-cap universe swap, portfolio capacity sweep, pre-filter
comparison) — transcribed from LESSONS.md prose and `logs/filter_comparison.csv`
directly, not reconstructed from memory, so a future re-run of any of these
(the mid-cap sweep specifically, requested as the next step) has a real
baseline to diff against instead of starting cold or accidentally re-deriving
numbers that drift slightly from what was actually measured.

---

## 2026-07-18 (cont'd 2) — Lab tab: every strategy's parameters were hardcoded module constants, not fields

**Context:** the user wants the webapp to be a "one-stop-shop" for testing variations of existing strategies — different tickers, date ranges, and rule parameters — without editing Python. That turned out to require touching all 19 strategy files: every one of them read its rule parameters (`PULLBACK_ATR_TOLERANCE`, `RANGE_MINUTES`, `RSI_THRESHOLD`, ...) as bare module-level constants referenced directly by name inside `entry_signal`/`stop_price`/etc. Nothing was ever passed through a constructor, so there was no field for a UI to bind to at all.

**Fix, mechanical but real: convert every strategy to a `@dataclass`.** Each module constant became `field_name: type = param_field(default, label=..., minimum=..., maximum=..., step=..., help=...)` (new `strategies/params.py`), and every method body switched from the bare constant to `self.field_name`. `param_field()`'s metadata is what turns a plain dataclass field into a UI-describable one — `describe_params(cls)` reads `dataclasses.fields()` and only surfaces fields carrying that metadata, so structural/injected fields (`benchmark_bars` on Sector Rotation, `positive_earnings` on PEAD, `risk_free_rate` on Dual Momentum) are excluded by construction, not by a separate allow-list that could drift.

**Regression discipline mattered more than the mechanical conversion itself.** The whole point is that the registered DEFAULT behavior must be byte-identical after wrapping every constant in a dataclass field — a UI that lets you tune parameters is worthless if turning the dial to "default" silently isn't the default anymore. Ran the full suite after every batch of ~8 files rather than at the end; two existing tests broke because they imported the old module constants directly (`from strategies.swing.dual_momentum import LOOKBACK_TRADING_DAYS`) — fixed by reading the dataclass's own default (`DualMomentum.lookback_trading_days`) instead, which is also a nice regression guard: the test now fails if the field's default ever drifts from what the test expects, not just if the import path changes.

**Two real bugs the tests caught, not just refactor mechanics:**
- `strategies.params._kind_of` initially checked `field_type is bool` — but every strategy module has `from __future__ import annotations` (PEP 563), so a dataclass field's `.type` is the *string* `"bool"` at introspection time, not the type object. Every param schema silently classified everything as `"str"` until a smoke test (not a unit test — printing real schemas and eyeballing them) caught it. Lesson already in this log, showing up again in a new place: don't trust output shapes without printing and checking them, especially where postponed annotation evaluation is involved.
- `latest_run_per_strategy()`'s SQL joined on `run_at` (second resolution) without also filtering the outer side on `is_canonical = 1`. A canonical and a non-canonical run logged in the same second (trivial in a fast test, vanishingly rare but not impossible in real usage in a script that runs several backtests in a tight loop) could match the same join row, and Python's dict-building (`{row["strategy_name"]: row for row in rows}`) would silently keep whichever came last — possibly the experiment, not the canonical result. A unit test with two `log_run()` calls back-to-back caught it immediately; fixed by adding `WHERE r.is_canonical = 1` to the outer query too, which costs nothing and removes the ambiguity outright rather than relying on the subquery alone.

**Custom runs must never contaminate the canonical view.** Added `is_canonical` to `engine/logging_db.py`'s `runs` table (migrated + backfilled — every row logged before this feature existed really was canonical, since there was no other kind yet). `latest_run_per_strategy()` — what the dashboard's leaderboard reads — filters to canonical only, so running a one-off parameter sweep can never silently replace what "Pullback to 21 EMA" shows as its result. Verified this live, not just in unit tests: ran a custom 2-symbol, tweaked-parameter backtest through the real API, then immediately hit `/api/strategies` and `/api/backtest` with no body — the canonical numbers (1122 trades, 0.025 expectancy) were completely unaffected by the experiment (1016 trades, 0.014 expectancy) that had just run seconds earlier. This is the same "don't let a comparison run shadow the canonical result" principle already applied to `compare_universe.py`/`compare_filters.py`/`compare_dividend_hybrid.py`, now enforced at the schema level instead of by convention (a new script can't forget to follow the convention; a new column with a `WHERE` clause can't be skipped).

**A day-trading vs. swing-trading default date range difference, confirmed correct by driving the actual UI:** selecting Opening Range Breakout (day-trading) showed a 2-year default window (Alpaca intraday history depth); switching to Pullback to 21 EMA (swing) correctly switched to 5 years. This isn't a new behavior — `engine/universe.py`'s existing `intraday_date_range()`/`daily_date_range()` split — but it's now visible and correct end-to-end through a brand-new code path (`GET /api/params` → `RunConfigPanel`), which is exactly the kind of thing that's easy to get subtly wrong (e.g. accidentally hardcoding one date range for the whole UI) without actually clicking through it in a browser.

**Why this matters generally:** "let the user tune X" is never just a UI form — it's an audit of every place X was assumed fixed. Here that meant 19 files, a join query nobody had reason to stress-test before, and a type-introspection assumption that silently broke under a Python language feature (`from __future__ import annotations`) already used throughout the codebase. All three were caught by running things — full test suite after every batch, a printed smoke-test of real output shapes, and finally a real headless browser clicking through the actual feature — not by reasoning about the diff.

---

## 2026-07-18 (cont'd) — Dividend Hybrid: the "no stop is safe, the dividend is a floor" thesis could not be tested, and the reasons are more interesting than a result

Built a fundamentals feed (`engine/fundamentals.py`), the strategy's screen
and entry rules (`strategies/swing/dividend_hybrid.py`), a two-version
portfolio engine (`engine/dividend_hybrid.py`) and the A/B comparison
(`engine/compare_dividend_hybrid.py`). Version A = take profit at the entry
yield %, no stop, hold losers indefinitely. Version B = same target, 8% hard
stop. Four separate things went wrong before any number meant anything.

### Two data bugs that silently produced plausible-looking garbage

**Bar timestamps and dividend timestamps use different conventions.**
`engine/data.py` localizes daily bars with `tz_localize("UTC")` then converts
to New York, which stamps each session **20:00 on the previous calendar
day**. yfinance stamps dividends 09:30 on the payment date. The first
implementation matched payment dates to bar dates, so nearly every payment
missed its bar — and the output was not an error, it was **VZ, a company
that has raised its dividend every year, reported as cutting it on 379
bars**. Fixed by summing payments over calendar windows via `searchsorted`
on real timestamps. **Generally: joining two time series on a derived date
key is only safe if you have checked how each one is stamped. A join that
silently drops 90% of one side produces numbers, not errors.**

**`auto_adjust=True` makes prices unusable as a yield denominator.** The
project's bars are dividend-adjusted, so historical closes are back-adjusted
downward; dividing a real dividend by one overstates historical yield badly
on exactly the high-yield names this strategy screens for. The fundamentals
module keeps its own small **unadjusted** close cache used only for the yield
ratio, while fills still use the adjusted bars every other strategy uses.
**Generally: adjusted prices are correct for computing returns and wrong for
any level-sensitive ratio. Both can be true in the same codebase.**

A third, smaller one: quarterly payments against a 365-day window
intermittently catch 3 payments instead of 4 and show a phantom ~25% cut —
24 such bars on AAPL, which has never cut. Cut detection now requires the
decline to persist 21 bars. After that: zero false positives on AAPL, KO,
JNJ, MSFT, VZ, IBM, CVX, and correct detection of INTC's 2023 cut, MMM's
2024 post-spinoff reset, DOW's 2025 cut and BA's suspension.

### The spec's daily proxy encoded a contradiction, not an approximation

The real entry wants a pullback to the **5-minute EMA20**; the spec
approximated it as "the day's close within 0.5% of the **daily SMA20**."
Those are not the same quantity at a different resolution — a 5-minute EMA20
is a ~100-minute average that hugs price within tenths of a percent, while a
20-*day* average sits a **median 3.55% away from price on gap-up days**
(p25 1.69%, p75 6.15%, measured across the Dow). Requiring a >1% gap up AND
a close within 0.5% of the 20-day average asks for two things that co-occur
on 0.64% of bars, and **the full strategy produced exactly zero trades over
five years and 29 symbols**. Not selectivity — a contradiction.

**Generally: when approximating an intraday rule with daily data, check that
the substituted quantity has the same SCALE, not just the same name. "EMA20"
appearing in both rules concealed a ~7x difference in typical distance from
price.** A second trigger (gapped up, faded from the open, held above the
prior close) was added alongside the literal one, not instead of it.

### The survivorship bias didn't just skew the sample — it deleted it

Splitting the screen into point-in-time fields (real dividend history) and
snapshot fields (today's `Ticker.info`) was supposed to *measure* the bias.
The measurement came back sharper than expected: **all six symbols that ever
pass the point-in-time screen are eliminated by the snapshot screen**, so
the "full screen" arm produced zero trades in every configuration.

Worse, it removes precisely the informative cases. **INTC fails today's
payout-ratio test with a 0.0% payout — a direct consequence of the very
dividend suspension the thesis needs to be tested against. MMM fails on
today's −39.7% EPS growth.** Both dividend-cutters, both screened out by
data from after the fact. **Generally: "not point-in-time" understates it.
Screening on today's fundamentals doesn't add noise to a historical test, it
systematically removes the falsifying observations — the bias is aimed
directly at the conclusion.**

### Version A's win rate is 100% by construction

With no stop, the only way a trade closes is by hitting its target.
Version A's closed trades were 15 of 15 winners. Reported alone that is a
meaningless statistic, so still-held positions are marked to market and
included in the headline metrics, with the flattering closed-only view
available but never the default.

Result on the only configuration that produced a sample (screen-only entry,
point-in-time screen — **not the strategy as specified**):

| | Version A (no stop) | Version B (8% stop) |
|---|---|---|
| Trades | 16 (15 closed, 1 still held) | 24 |
| Win rate | 93.8% (closed-only: 100%) | 66.7% |
| Expectancy (R) | +0.506 | +0.089 |
| Profit factor | 7.99 | 1.21 |
| Total return | +6.2% | +1.3% |
| Worst unrealized DD | −17.9% | −10.6% |
| Sharpe | −1.84 | −2.82 |

**Version A "wins" — and the result is not usable.** Both samples are under
30 trades. Both Sharpes are negative, so both lose to cash. And the decisive
point: **the thesis was never actually stressed.** No Version A trade went
beyond 20% underwater; the >30% and >40% buckets are empty. The take profit
equals the entry yield (4–7%), so positions exit on any modest bounce and
the "hold as a dividend investor" branch — the entire risk of the strategy —
barely engaged in this window. Version A also took **16 trades to Version
B's 24**, because capital sits locked in losers that never exit: a real cost
of the no-stop rule that shows up as fewer opportunities, not as a loss.

The one case where the floor was genuinely tested: **INTC entered
2023-05-04 at $30.75, was held through a real dividend cut, and exited at
target ~5 weeks later for a gain — after which the stock fell to $17.67,
42% below entry.** A winning trade that says nothing about the risk taken;
the dividend the position depended on was removed mid-hold and the exit was
timing luck.

**Verdict: the no-stop thesis is neither validated nor refuted here.** The
honest answer to "does the dividend floor make a stop unnecessary" is that
this universe and window cannot answer it, and reporting Version A's +0.506R
as support would be reading a 16-trade sample that never encountered the
scenario the thesis is about.

### A modelling bug the tests caught

A position entered at today's open had its stop/target checked only from the
*next* bar, so a same-day collapse was invisible and Version B's stop-outs
were understated. Found by a unit test constructing a bar that spans both
stop and target. Entries now fill first and every open position is evaluated
against that same bar.

---

## 2026-07-18 — Pre-filters (Minervini Trend Template + market regime): a trend filter cannot rescue a mean-reversion strategy

Added two gating layers that run *before* any strategy's `entry_signal()`:
an 8-point Minervini Trend Template per symbol (`engine/trend_template.py`)
and a SPY-based Bullish/Neutral/Bearish regime classifier
(`engine/regime.py`), wired in by `engine/filters.py`. Ran the whole swing
book both ways on the same universe, window, cost model and risk-free rate
(`engine/compare_filters.py`).

**Wiring: a wrapper beat a parameter.** The obvious implementation is an
`entry_filter=` argument threaded through `engine/backtest.py`. Instead
`FilteredStrategy` wraps a strategy and re-implements the same
`strategies.base.Strategy` interface, so it drops into the *existing*
`run_strategy_backtest_seeded` with **zero changes to any existing file** --
engine, portfolio simulator, cost model, metrics, logging DB and API all see
an ordinary strategy. Generally: when a cross-cutting behavior can be
expressed as "same interface, different answer," a decorator keeps the
blast radius at zero where a parameter spreads through every call site.

**The filters are genuinely selective, which had to be measured, not
assumed.** SPY spent 59.2% Bullish / 24.5% Neutral / 16.3% Bearish over the
5-year window, and the trend template passed a mean of **16.3%** of the
29-name universe per scan date (348 of 1,701 scan dates had *zero*
candidates). CLAUDE.md asks for these counts specifically, and they're what
separates "the filter is working" from "the filter is a no-op" -- both of
which otherwise look like a changed trade count.

### Oversold Bounce went from 210 trades to exactly 0 -- and that's correct

The instinct on a filter that zeroes out a strategy is "bug." It isn't. The
trend template requires price above the 50/150/200-day SMAs and within 25%
of the 52-week high; RSI<30 requires roughly the opposite. Measured directly
across 5 sample symbols: **198 bars with RSI<30, 2,089 bars passing the
template, and 0 bars where both were true.** The two conditions are close to
mutually exclusive by construction.

**Why this matters generally:** stacking filters is not additive risk
management -- a filter encodes a thesis, and two filters with *opposing*
theses don't combine into a stricter version of either, they annihilate.
The Minervini template is a trend/momentum selector. Putting it in front of
a mean-reversion entry doesn't make the mean-reversion strategy pickier,
it deletes it. Check filter/strategy thesis compatibility *before* reading
the resulting metrics as a verdict on the strategy.

### Every Sharpe got worse -- and it's mostly an artifact of low exposure

Sharpe fell in **11 of 11** strategies that still traded, some absurdly
(Breakout from Consolidation -1.79 -> -80.9; IBS -0.93 -> -18.6). Read
naively that says the filters select strictly worse trades. Checked instead
of assumed, on Breakout from Consolidation:

| | unfiltered | filtered |
|---|---|---|
| Exposure | 20.7% of time | **4.8%** |
| CAGR | 0.238% | **0.046%** |
| Sharpe | -1.81 | **-80.95** |

with the window's real risk-free rate at **3.60%**. The filtered strategy is
in cash ~95% of the time earning 0.046% while T-bills paid 3.60%, and its
volatility collapses along with its exposure -- so Sharpe's numerator is
pinned near -3.55% while its denominator goes toward zero. That is a real
economic criticism of the filtered strategy (idle capital has a cost) but it
is *not* evidence the surviving trades are worse.

**Why this matters generally:** a ratio whose denominator is shrinking
faster than its numerator will produce dramatic numbers that feel like a
finding. Any filter that cuts trade count 70-100% mechanically drives
exposure toward zero, and every risk-adjusted ratio computed against a
non-zero risk-free rate will blow up as a result. Decompose the ratio before
narrating it -- exposure and CAGR told the real story here, and the -80.9
was arithmetic, not insight.

### Trade counts fell 50-100%, recreating the small-sample trap on purpose

Filtered trade counts: 0 to 364, from 42 to 1,638 before. Three strategies
dropped under the 30-trade reliability threshold (Oversold Bounce 0,
Earnings Momentum 11, Sector Rotation 24) and their post-filter status
regressed to "Sample too small" / "Not yet tested" -- a *less* informative
result than before the filter. This is the same trap documented repeatedly
in this log, now induced deliberately by a filter rather than by low
capacity. A pre-filter's cost is always paid in statistical power.

### What actually improved, stated without inflation

Expectancy improved in 4 of 12; alpha improved in 8 of 12 (every alpha stayed
negative); max drawdown improved in all 12 (again, an exposure effect).
**Turnaround Tuesday is the only clean improvement** -- expectancy -0.034 ->
+0.026 *and* profit factor 0.81 -> 1.11, both moving the same direction.

**Gap Fade is a trap worth naming:** expectancy -0.150 -> **+0.339** looks
like the headline result of the whole exercise, but its profit factor went
*down*, 0.871 -> 0.857 -- still below 1.0, meaning gross losses still exceed
gross wins in dollars. R-expectancy is normalized by each trade's initial
risk, so a strategy with tight stops on winners and wide stops on losers can
post positive R-expectancy while losing money. Another instance of the
scale-free-metric problem already logged for Sharpe/alpha: **when
R-expectancy and profit factor disagree in direction, believe profit factor
about the dollars.**

**Zero of twelve clear the shortlist bar (Sharpe > 0.5 and alpha > 0) with
the filters on** -- the same conclusion the project has reached under every
other variation tried. The pre-filters change *which* trades happen; they
did not manufacture an edge that wasn't there.

---

## 2026-07-17 — Added 4 tracker strategies; the interface's limits dictated the engines

Built Pivot-Level ETF Reversal, Turnaround Tuesday, Post-Earnings Drift
(PEAD), and Overnight Hold to close the gap between the tracker (23) and the
code (19). What's worth remembering isn't the strategies -- it's how the
existing Strategy interface's limits forced the shape of each:

- **The tracker didn't actually have these yet.** The user's list of 23
  lived only in a chat message; the xlsx still had 19. CLAUDE.md says import
  rules from the tracker, so rather than invent parameters silently I defined
  canonical rules, got them confirmed, then wrote them back into the tracker.
  Lesson: when the "source of truth" is missing the thing you're asked to
  build, don't quietly fabricate it -- surface the gap and restore the source.

- **`exit_signal` can't see how long a position has been held** (it only gets
  current bars). Both Turnaround Tuesday ("1-4 day hold") and PEAD ("hold
  weeks") are defined by a *time* exit the interface can't express -- same
  wall Connors RSI2 hit. Both became signal exits instead (first up-close;
  close back below the 20-EMA), disclosed in each docstring. A recurring
  pattern: time-based holds have to be re-expressed as price-based signals.

- **The per-symbol engine passes one shared strategy instance with no symbol
  identity**, so a strategy needing per-symbol external data can't look it up
  from `bars`. PEAD needs each name's *real* earnings dates -- solved with a
  per-symbol factory (`run_strategy_backtest_seeded`) that builds a fresh
  instance seeded with that symbol's positive-surprise dates. This is the
  first real earnings feed in the project (yfinance `get_earnings_dates`,
  needs `lxml`); Earnings Momentum still uses a price/volume proxy.

- **backtesting.py can't hold from a bar's close to the next bar's open.** It
  fills entries at the next open and exits on closes, so Overnight Hold's
  entire thesis (buy close, sell next open) is unrepresentable there. It got
  its own tiny engine (engine/overnight.py) that computes the close->open
  trade directly but emits the *same* SymbolBacktestResult shape, so it still
  flows through logging/API/dashboard. It has no stop (the overnight gap is
  the risk); a nominal ATR unit is used only for sizing and R-normalization,
  disclosed -- not a real stop order.

**Why this matters generally:** a clean strategy interface is worth keeping
clean, but every abstraction has an expressiveness boundary. The honest move
when a new strategy crosses it is a small dedicated engine that re-enters the
shared result shape (as pairs and cross-sectional already did), not bending
the strategy's real rules to fit the old engine. Two of four here fit the
existing engine; two didn't, and forcing them would have quietly changed what
they mean.

---

## 2026-07-17 — Switched intraday data to Alpaca: ~60 days → 2 years, but IEX volume is partial

**What changed:** day-trading backtests were capped at ~60 days of 5-min bars
because that's yfinance's hard intraday limit. Wired Alpaca's Market Data API
in as the intraday source (`engine/data.py` now routes any intraday interval
to Alpaca, keeps yfinance for daily). Measured result: a single call returned
**38,756 five-minute AAPL bars over 500 trading days (2 full years)** vs. the
~60-day ceiling before — the day-trading strategies finally get sample sizes
big enough to actually judge, instead of being permanently stamped "sample
too small."

**The catch, measured not assumed — free tier is the IEX feed only:** IEX is
~2-3% of consolidated volume. AAPL's IEX 5-min bars average ~16.8k shares;
consolidated is ~50-60M shares/day. So:
- **Prices (OHLC) are fine** for liquid names — IEX prints track the real
  market, so ORB levels, EMA/RSI, breakout logic are unaffected.
- **Volume is a partial sample**, which skews volume-dependent signals: VWAP
  (volume-weighted by construction), and relative-volume spike detection in
  News Fade and Gap-and-Go. Decision: disclose and proceed on IEX (same
  posture as the spread model), rather than pay ~$99/mo for the SIP feed
  before any volume strategy has shown promise. Kept **daily** bars on
  yfinance precisely because its daily volume is full/consolidated — better
  for the swing book than Alpaca's IEX daily would be.

**Details that would silently corrupt results if skipped:**
- `adjustment=ALL` on the Alpaca request, to match yfinance's `auto_adjust` —
  without it, splits inject fake overnight gaps.
- Filter Alpaca bars to **regular trading hours (09:30–16:00 ET)**. yfinance
  intraday is RTH-only by default and the strategies assume a 9:30 session
  open; letting pre/post-market bars through would, e.g., make ORB compute
  its "opening range" from 4am prints. Verified the filtered feed runs
  09:30→15:55 with nothing outside it.
- Graceful fallback everywhere: no keys / bad response → fall back to
  yfinance (clamped to its ~60-day cap), never crash a run.

**Why this matters generally:** more data raises *statistical power*, not
edge. This doesn't make a losing strategy win — it upgrades the day-trading
verdicts from "underpowered, can't tell" to a real answer. And a new data
source is only an improvement if you audit what's actually in it: deeper
history was the headline, but the IEX volume caveat is real and had to be
found and disclosed, not glossed over.

---

## 2026-07-17 — Live quotes and backtest data must be separate data paths

**Context:** added a Symbols tab to the dashboard (a watchlist/tracker with
live-ish prices, per-symbol charts, and a per-symbol breakdown of each
backtest). The temptation was to reuse `engine/data.py` for everything since
it already fetches prices. Resisted that on purpose.

**The rule:** `engine/data.py` owns *reproducible, cached historical bars* —
the thing every backtest number must trace back to (CLAUDE.md). Live quotes
are the opposite: real-time-ish, non-reproducible, revised, and sourced from
a delayed/partial feed (Alpaca free tier = IEX only). So live quotes live in
a separate module (`engine/quotes.py`) that **nothing in the backtest
pipeline imports**. A stray live price leaking into a backtest would silently
break reproducibility — the same run would produce different numbers
depending on when it ran. Keeping the paths physically separate makes that
mistake impossible rather than merely discouraged.

**Two smaller decisions worth keeping:**
- The quote layer degrades gracefully: no keys / no `alpaca-py` → a
  structured `{source: "unavailable", reason}` payload, never an exception.
  The whole dashboard runs off cached bars, so a missing broker key should
  disable *one column*, not the app.
- The per-symbol breakdown exists because pooled metrics hide dispersion. The
  very first strategy tested this way (Pullback to 21 EMA) had +0.50
  expectancy on AAPL and was flat-to-negative on most of the other 28 Dow
  names — a pooled "small positive expectancy" line concealed that it's
  really a bet on a handful of symbols. **Generally: any metric averaged over
  a universe can be carried by a few names; always keep a per-constituent
  view next to the aggregate before believing an edge is broad.**

**Why this matters generally:** "reproducible research" isn't a property you
add at the end — it's a property you protect by never letting
non-reproducible inputs touch the reproducible pipeline in the first place.

---

## 2026-07-17 — Testing mid-caps instead of the Dow didn't reveal hidden edge (if anything, worse)

**Context:** the working theory after the Dow-universe results was that the
Dow is "arguably the hardest place on earth to find inefficiency" — the
most heavily-traded, most-analyzed large caps in the market — and that a
real edge, if one exists for a retail participant, more plausibly lives in
less efficiently-priced, less-covered names. Built `MIDCAP_UNIVERSE` (27
names) to actually test that theory instead of just asserting it.

**Universe construction, weaker rigor than the Dow list, disclosed:**
Wikipedia has no maintained historical-components page for a 400-name,
high-turnover index the way it does for the Dow's 30 rarely-changed slots.
Started from *today's* S&P MidCap 400 membership (~280 tickers, pulled in
the table's own alphabetical order — a performance-blind ordering), then
applied an objective, data-driven filter: real price history on/before the
backtest window's start date. That correctly excluded the obvious 2021+
IPOs sitting in the current list (BROS, CART, CAVA, CRBG, DUOL, KD, KNF,
NXT), leaving 267 genuine candidates, from which every ~10th ticker
(alphabetically) was taken — 27 names, mechanically sampled, not hand-picked
in either direction. Residual bias: today's membership still selects for
"grew enough to still be mid-cap-or-larger and investable today" — not a
true 2021 snapshot the way the Dow list is.

**Bug caught along the way, unrelated to mid-caps specifically:**
`INTRADAY_LOOKBACK_DAYS` was 59, sitting exactly at yfinance's real 60-day
intraday limit. A direct test confirmed 58 days succeeds and 59 fails
outright (zero rows, not partial data) depending on time-of-day relative to
Yahoo's own rolling window — and it failed for AAPL too, so this was a
latent, pre-existing fragility in the Dow-universe pipeline the whole time,
just narrowly avoided by lucky timing until now. Fixed to 57 for a real
margin. **Lesson inside the lesson: when a parameter sits exactly at a
documented external limit, treat that as a bug waiting for the wrong
time-of-day, not a safe value** — "1 under the limit" isn't a margin, it's
a coin flip against clock skew and how the provider actually enforces the
boundary.

**The result, straight comparison (Dow expectancy/Sharpe -> Mid-cap):**

| Strategy | Dow ExpR | Mid ExpR | Dow Sharpe | Mid Sharpe |
|---|---|---|---|---|
| Scalping (3-5 min) | -0.200 | **-0.391** | -32.34 | **-296.41** |
| VWAP Bounce | -0.108 | -0.151 | -6.21 | -9.67 |
| News Fade | +0.049 | **-0.324** | -11.42 | -10.87 |
| Range Trading | -0.066 | +0.029 | -3.76 | -1.62 |
| Breakout from Consolidation | +0.101 | +0.145 | -1.81 | -1.53 |
| Dual Momentum (cross-sectional) | Sharpe 0.42 | Sharpe **0.16** | | |
| Pairs/Stat Arb | -16.8% return | **-20.4%** return | -0.86 | -0.94 |

Day-trading strategies were mostly *worse* on mid-caps, several
dramatically so (Scalping's Sharpe went from bad to off-the-charts bad).
Swing strategies were a wash — some marginally better, some marginally
worse, no consistent direction. The two structurally-different strategies
built specifically to test a different edge shape (Dual Momentum, Pairs)
were both *worse* on mid-caps, not better. **Zero strategies flipped from
failing to clearing the Sharpe/alpha bar on the smaller-cap universe.**

**Why this matters generally:** the intuitive story — "mega-caps are
efficient, so edge must hide in smaller/noisier names" — turned out to be
only half right, and maybe not right at all for *this* class of strategy.
Wider spreads and thinner liquidity in smaller names are a cost to a
directional technical strategy, not automatically an exploitable
inefficiency — that would require an edge that specifically profits from
providing liquidity or absorbing the noise (market-making, statistical
arbitrage with real edge in the *pricing model*, not just a smaller-cap
universe), not just running the same trend/mean-reversion rules on noisier
data. Trend-following and momentum approaches in particular seem to do
*better* on the smoother, more persistent trends that heavy index-fund
flow and analyst coverage produce in mega-caps — the same effect that makes
mega-caps "efficient" may be what makes their trends more followable in
the first place. A theory that sounds right is still a theory until it's
actually run.

## 2026-07-16 (cont'd 4) — Five new strategies added to the tracker; three fit the existing engine, two don't

**Context:** the strategy tracker was updated with five new swing-trading
candidates, several clearly chosen to test the "market-neutral /
cross-sectional" direction raised as a next step: Connors Mean Reversion
(RSI2), Internal Bar Strength (IBS), Gap Fade (daily), Dual Momentum, and
Pairs / Stat Arb. `strategy_tracker.xlsx` is checked in now (it was
previously untracked) so this update, and future ones, actually persist in
git history instead of living only on disk.

**Three slotted into the existing per-symbol Strategy interface cleanly**
(Connors RSI2, IBS, Gap Fade) — implemented, tested, registered, no engine
changes needed beyond adding a plain `sma()` indicator (the codebase only
had `ema()` before; Connors' methodology specifically calls for a 200-day
*simple* MA, not exponential — worth getting that distinction right since
it's the canonical definition of this well-known strategy, not an
interchangeable choice).

**Two disclosed simplifications, both following existing precedent:**
- IBS's tracker entry doesn't specify a stop rule (only a signal-based
  exit). `stop_price` is a required abstract method on every strategy here
  regardless — used the same swing-low technical stop the other
  signal-exit swing strategies already use, rather than inventing a
  strategy-specific convention.
- Gap Fade's tracker says "enter counter-gap at the open" — but this
  engine fills every strategy at the bar *after* the signal bar's close
  (see `engine/backtest.py`), and that's an engine-wide architectural fact,
  not something to special-case for one strategy. Implemented consistent
  with how News Fade already handles the identical situation, and said so
  explicitly in the docstring rather than silently under-delivering on the
  tracker's literal wording.

**Two need a real engine extension, not just a new strategy file:** Dual
Momentum (cross-sectional -- ranks the *whole universe* against itself at
each rebalance, which the current one-symbol-at-a-time `Strategy.entry_signal(bars)`
interface has no way to express) and Pairs/Stat Arb (needs two correlated
legs traded as one combined position, plus cointegration testing -- also
outside what a single-symbol interface and `backtesting.py`'s single-asset
`Backtest` class can represent). Registry and tests are intentionally left
red for these two until that engine work lands, rather than force-fitting
them into an interface that can't honestly express what they do.

**Why this matters generally:** when the tracker's own description says a
strategy is "structurally different" or "needs the cross-sectional ranking
layer" (Dual Momentum's own tracker notes say almost exactly that), take
it at its word rather than trying to approximate the idea inside an
interface built for a different shape of problem. A watered-down
single-symbol version of a cross-sectional strategy wouldn't just be
incomplete, it would be a different strategy wearing the same name -- and
its backtest numbers would mean something other than what the label claims.

### Update: both engine extensions built, and results are in for all five

Built `engine/cross_sectional.py` (rebalance-driven, target-weight
portfolio simulation) for Dual Momentum, and `engine/pairs.py`
(cointegration screening + spread z-score mean-reversion, two legs as one
position) for Pairs/Stat Arb. New dependency: `statsmodels`, for the
Engle-Granger cointegration test (`statsmodels.tsa.stattools.coint`) --
the standard implementation, not worth hand-rolling. 79 tests passing
(up from 65), all 19 tracker strategies now registered and runnable.

**Pairs/Stat Arb's train/trade split did exactly what it was built to
do.** Pair selection (`find_cointegrated_pair`) runs only on the first
half of the window; the pair actually gets traded on the second half,
never seen during selection. Real result on this project's universe: GS
and HON were genuinely cointegrated in training (p=0.0035, a real,
statistically significant relationship) -- and the pair *lost money*
(-16.8% return, Sharpe -0.86) trading it out-of-sample. That's not a
disappointing bug, it's the tracker's own risk warning ("very prone to
great in-sample / broken live") confirmed rather than papered over. A
pair-selection method that only ever got tested in-sample would have had
no way to catch this -- the split is what turned a plausible-sounding
warning into an actual, falsifiable check.

**Dual Momentum came the closest to the shortlist bar of anything tested
in this whole project.** Sharpe 0.42 (vs. the 0.5 threshold), CAGR
15.2%, +102.3% over the window, and its holdings history makes sense on
inspection -- it rotated into defensive/energy names (CVX, MRK, AMGN, KO)
through the 2022 bear market, which is exactly what a working momentum
rotation strategy should do. Still short of the bar, still not
shortlisted, but the nearest miss by a wide margin and the first result
in this project where the mechanism visibly does what it's supposed to
under a real stress period.

**Connors RSI2 and IBS both landed in the same place as almost
everything else** (positive expectancy, real sample sizes -- 843 and
1,641 trades -- but Sharpe/alpha both negative: "hold," not
"shortlist"). Gap Fade came out negative expectancy outright. None of
these five change the project's overall conclusion; Dual Momentum's
near-miss is the most interesting result to come out of testing a
genuinely different structure, which is exactly what going in this
direction was supposed to produce.

**Why this matters generally:** a validation protocol only earns its
keep when it's allowed to produce a bad answer. The GS/HON result would
have been very easy to quietly discard as "the test window was just
unlucky" -- the discipline is in reporting it as the pair-selection
method working correctly, not as a failure to explain away.

---

## 2026-07-16 (cont'd 3) — Re-run of the sensitivity sweep: capacity has a sweet spot, not a monotonic benefit

**Context:** with the cash-rationing bug fixed, re-ran the capacity/cash
sweep (5/10/20/29 concurrent positions, $10K-$100K) across six strategies.

**Scalping and VWAP Bounce: capacity genuinely isn't the constraint.** Trade
counts now scale properly with capacity (Scalping: 10 -> 46 trades taken as
cap goes 5 -> 29), confirming the earlier bug is fixed -- but portfolio
return stays pinned near zero regardless (0.0-0.1% for Scalping at every
capacity level, -0.4% to -0.7% for VWAP Bounce). Even with generous
capital and 29 open slots, there's no edge to unlock. This is the useful
negative control: it shows the sweep methodology works (capacity *can* move
the numbers, as seen elsewhere in this same run) and that these two
strategies' flat lines are a real property of the strategy, not leftover
tooling noise.

**Pullback to 21 EMA and 9/21 EMA Crossover: Sharpe peaks at moderate
capacity, then degrades.** Pullback's Sharpe goes -0.25 (cap 5) -> **+0.07**
(cap 10, the best result in the whole sweep) -> -0.55 (cap 20) -> -0.92 (cap
29). Same shape for the EMA crossover. More concurrent positions means more
simultaneously-correlated exposure -- past a point, added positions add more
correlated risk than they add diversified return, and Sharpe falls even
though gross trade count and sometimes gross return keep climbing. Note
that the *best* number anywhere in this sweep (+0.07) is still nowhere near
the 0.5 shortlist threshold -- there's a local optimum, not a hidden edge.

**Range Trading: the small-sample trap, caught in the act at the portfolio
layer.** At cap=5 (32 trades), portfolio Sharpe reads 53.82 -- spectacular,
and exactly the kind of number that would look great in a screenshot. At
cap=29 (347 trades, the same strategy, same window, just more capital to
act on more of its own signals), Sharpe is -5.07. The eye-catching number at
low capacity wasn't an early glimpse of a good strategy; it was 32 trades
worth of noise that vanished once enough of the real (negative-tilted)
signal got captured. This is the identical failure mode already documented
for Mean Reversion Scalp (portfolio Sharpe 51.81 on 7 trades) and Oversold
Bounce (1.88 on 9 trades) earlier in this log -- now demonstrated as a
single strategy's number collapsing as its own sample grows, not just a
cross-strategy comparison.

**Why this matters generally:** "run a sensitivity sweep" is necessary but
not sufficient -- the sweep itself needs a sanity check (did the swept
parameter actually move something, per the entry above) and its *results*
need the same 30-trade skepticism applied to any other number. A Sharpe
computed on single-digit trades is not more trustworthy for being labeled
"portfolio-level" instead of "per-symbol." The conclusion across the whole
project holds: no configuration found here, at any capacity or capital
level, produces a real, adequately-sampled edge.

---

## 2026-07-16 (cont'd 2) — The portfolio simulator's own capital allocator was broken

**Context:** ran a sensitivity sweep -- capacity 5/10/20/29 concurrent
positions, cash $10K/$25K/$50K/$100K -- to check whether the 5-slot cap from
the first portfolio run was starving strategies of real edge. For most
strategies it showed sensible scaling (Pullback to 21 EMA: more capacity ->
more trades taken -> better return). For two (Scalping, VWAP Bounce),
capacity had **zero effect** — 2-4 trades taken whether the cap was 5 or 29.
That's not a finding about the strategies, that's a red flag about the tool.

**The bug:** `engine/portfolio.py`'s sizing used
`size_by_cash = cash_balance // entry_price` -- the *entire* remaining
balance, not a share reserved for the slot being filled. For a wide-stop
strategy this rarely binds (risk-based sizing is the real constraint, so it
looks fine by coincidence). For a tight-stop strategy like Scalping,
risk-based sizing computes an enormous share count (small risk_per_share ->
huge size_by_risk), so the cash check becomes binding -- and the *first*
entry in any batch would claim the whole pool, leaving nothing for the
other 4 (or 28) slots regardless of how large `max_concurrent_positions`
was set. Raising the cap did nothing because the cap was never the actual
constraint; the allocator was.

**Fix:** ration cash across the *remaining open slots*
(`cash_balance / (max_concurrent_positions - len(active))`) rather than
handing the full balance to whichever entry is processed first. Disclosed,
not the only reasonable policy (it's conservative -- it budgets as if every
configured slot will eventually fill, even if in practice few usually do)
but it closes the starvation bug.

**Why this matters generally:** a null result that doesn't move when you
change the parameter that's supposed to affect it is a bug signal, not a
robustness finding. The instinct to think "capacity isn't the bottleneck
here, interesting" would have been exactly backwards -- capacity *was*
supposed to be adjustable, and the fact that adjusting it did nothing meant
the code computing "capacity" wasn't doing what its name said. **Always
verify a sensitivity analysis by checking that the swept parameter actually
moves the output for at least one case in the expected direction** before
trusting any of its readings, including the ones that seem to "make sense."

---

## 2026-07-16 (cont'd) — Closing the three gaps the quant review flagged as unfixed

**Context:** the benchmark review below identified three remaining problems
after the Sharpe/alpha fixes: a hindsight-picked universe, no real portfolio
simulation, and a cost model that was ~75x too wide for liquid names. All
three are now addressed.

### Universe: point-in-time DJIA constituents instead of 2026 hindsight picks

Replaced `SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA` with the actual Dow Jones
Industrial Average roster as of July 2021 (verified against Wikipedia's
"Historical components" page, not recalled from memory) — 29 names after
dropping WBA, which was itself removed from the Dow in 2024 for poor
performance and later taken private, so its ticker no longer resolves via
yfinance at all.

**Why this matters generally:** a "pre-registered" universe only avoids
survivorship bias if the registration criterion is itself blind to future
performance. An externally-defined, point-in-time index membership list is a
practical way to get that blindness without a paid historical-constituents
feed — you're not choosing the tickers, an index committee's rules from
years ago are. It's not a complete fix (index membership still selects for
"large and currently investable," and a name that fully delisted between
2021 and now simply can't be fetched to test against) — WBA's failure to
resolve is a live demonstration of that residual gap, not a bug to route
around by picking a substitute ticker. **Never swap in a replacement for a
data-fetch failure without checking whether the failure itself is
informative** — here, the failure *was* the signal.

### Cost model: per-symbol spread from real dollar volume, not one flat number

Replaced the flat 10bps spread with a liquidity-tiered estimate (1-5bps)
derived from each symbol's own historical average dollar volume. A first
attempt used yfinance's free-tier `Ticker.info` bid/ask quotes directly, but
those turned out to be noisy indicative quotes, not real NBBO — a spot check
showed 45bps on AAPL and 106bps on MMM, implausible for blue-chip liquidity
and worse than the flat assumption it would have replaced.

**Why this matters generally:** "use real data instead of a guess" isn't
automatically an improvement — the real data source has to actually be
trustworthy for the thing you're using it for. Point-in-time bid/ask from a
free API is a different reliability tier than historical OHLCV from the same
API; don't assume every field on a data provider's response carries the same
quality bar. Dollar volume (derived from the same OHLCV already validated
and cached) was the more defensible signal, even though it's one step more
indirect.

### Portfolio: shared-capital replay instead of N independent $10K accounts

Added `engine/portfolio.py`: replays each symbol's already-computed trades
(reusing backtest.py's validated fill logic — entry/exit price and timing
aren't re-derived) chronologically against one shared cash pool with a
concurrent-position cap, recomputing position size against *actual*
available capital at each entry instead of a fresh account per symbol. On
9/21 EMA Crossover, 689 signals fired across the 29-symbol universe; with a
5-position cap and shared capital, only 196 could actually be taken — 492
were skipped purely for lack of capacity. The per-symbol "mean Sharpe"
(-1.20) and the portfolio's real Sharpe (-0.57) also disagreed, in neither a
consistently-more-nor-less-flattering direction — which is itself the point:
you can't know which way the portfolio-level number will move without
actually computing it.

**Why this matters generally:** aggregating N independent single-asset
backtests (mean Sharpe, max drawdown) is not a portfolio backtest, no matter
how it's phrased. A real portfolio has ONE pot of capital and concurrent
exposure decisions that individual backtests never have to make — a
strategy that looks great when every signal gets its own fresh account can
look completely different once 689 signals are actually competing for 5
slots and one balance sheet.

### The full rescore, with all three fixes live

Zero of fourteen strategies clear the shortlist bar (Sharpe > 0.5 and alpha
> 0) on the honest universe/costs, same conclusion as the first rescore —
which is itself informative: **the "nothing here has a demonstrated edge"
finding was not an artifact of the biased universe or the mispriced cost
model.** It survived fixing both. Two things did change materially once the
real universe replaced the hindsight-picked one:

- **ORB flipped from clearly negative to roughly breakeven**
  (expectancy -0.162R/PF 0.80 -> +0.005R/PF 1.01 -- a materially different
  universe, not the same conclusion restated).
- **News Fade and Earnings Momentum/Gap-Hold crossed into real sample sizes**
  (13 -> 79 trades, 9 -> 42 trades) instead of "too small to call."

At the portfolio level (5 concurrent positions, $10K shared capital): almost
every strategy's raw signal count dwarfs what capacity-constrained capital
can actually act on -- Scalping fired 21,774 raw signals and the portfolio
could take 4; VWAP Bounce fired 8,496 and took 7. Two portfolio Sharpes
looked spectacular in isolation (Mean Reversion Scalp: 51.81 on 7 trades;
Oversold Bounce: 1.88 on 9 trades) -- both are the same small-sample trap
flagged earlier in this log, just at the portfolio layer instead of the
per-symbol one, and neither should be read as a real result. The one
strategy with a positive portfolio Sharpe on a real sample was ORB (0.25 on
81 trades) -- still nowhere near a level worth paper-trading.

**Why this matters generally:** running the same three fixes and getting the
same headline conclusion is a *good* outcome, not a wasted afternoon --
it's what makes the original conclusion trustworthy instead of coincidental.
And a metric computed on 7-9 trades is exactly as unreliable at the
portfolio level as it is at the per-symbol level; "portfolio-level" doesn't
grant immunity from the 30-trade rule, it just moves where the small sample
can hide.

---

## 2026-07-16 — The shortlist didn't survive a benchmark comparison

**Context:** four strategies were flagged "Positive expectancy - shortlist"
in R-multiple terms. A quant review checked them against what a professional
allocator checks first — Sharpe ratio, alpha, beta — and none of them
survived.

### The core lesson: R-expectancy is scale-free and benchmark-blind

`Expectancy (R) > 0` only says a strategy's wins-minus-losses average is
positive in units of "risk taken." It says nothing about:
- how much capital the strategy actually deploys (beta was 0.01-0.04 across
  the shortlist — these strategies barely touch the market)
- what a passive alternative would have returned over the same window
- whether the return is worth the risk relative to a *risk-free* alternative

A strategy can have positive expectancy and still be a bad use of capital.
**Every one of the five "positive" strategies had a negative Sharpe ratio
once measured against a real risk-free rate, and four of five had negative
alpha against buy-and-hold.** The R-multiple gate was shortlisting strategies
that lose to cash.

**Fix applied:** the shortlist gate now requires expectancy > 0 **and**
Sharpe (vs. a real risk-free rate) **and** positive alpha vs. buy-and-hold —
see `engine/metrics.py`. A strategy that clears only the expectancy bar gets
a new, less flattering status instead of being silently promoted.

### Bug: Sharpe/Sortino/Alpha were computed with risk_free_rate hardcoded to 0

`backtesting.py` 0.6.5 hardcodes `risk_free_rate=0.0` inside `Backtest.run()`
— it's not exposed as a parameter in this version. Every Sharpe ratio this
project had ever produced assumed cash earns nothing. Over a window where
3-month T-bills paid ~3.6% on average (and >5% for two straight years), that
assumption alone was enough to make every strategy look better than it was.

**Why this matters generally:** always check what a metrics library's
default assumptions are before trusting its output, especially for anything
finance-specific. A "Sharpe Ratio" field existing doesn't mean it's *your*
Sharpe ratio.

**Fix applied:** `engine/backtest.py` recomputes stats after `bt.run()` using
`backtesting._stats.compute_stats()` directly with a real risk-free rate,
sourced from `^IRX` (13-week T-bill) over the exact backtest window via the
existing cached-data pipeline (`engine/data.py`) — not a hand-picked
constant, an actual observed rate.

### Bug: the test universe is exactly the survivorship bias CLAUDE.md warns about

`EQUITY_UNIVERSE = [SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA]`, tested over the
last 5 years. Every single name in that list went up over that window — mean
+287%, NVDA +1035%. All the shortlisted strategies are long-only. A
long-only strategy on a universe of the last five years' biggest winners
will show a positive-looking track record almost by construction, *even
though the strategies dramatically underperformed just holding the names*
(e.g. Pullback to 21 EMA: +8.8% strategy return vs. +287% buy-and-hold, on
the same symbols, same window).

**Why this matters generally:** a "pre-registered symbol list" only prevents
survivorship bias if it was registered *before* you know how those symbols
performed. Picking today's mega-cap winners and testing them over the past
is survivorship bias with extra steps, even if the list doesn't change
mid-backtest. The tell was in the numbers the whole time — beta near zero
combined with the strategy massively lagging its own backtest universe's
buy-and-hold return.

**Not yet fixed** — needs a universe chosen *as of* the start date (2021),
including names that went nowhere or delisted, not curated with 2026
hindsight.

### Structural gap: per-symbol backtests aren't a portfolio

Each symbol runs in its own isolated $10K account. Reported "Sharpe" and
"Max Drawdown" are the *mean* and *max* of seven independent per-symbol
numbers — not a portfolio Sharpe or portfolio drawdown, because neither
accounts for correlation. Measured mean pairwise correlation across the
7-symbol universe: **0.62** (SPY vs. QQQ: 0.95). That means 258 pooled
"trades" across 7 symbols carry the statistical weight of roughly 3-4
independent bets, not 7 — the 30-trade reliability threshold is being
applied to a number that overstates independent sample size.

**Why this matters generally:** pooling trades across correlated instruments
inflates apparent sample size and hides that a bad drawdown in one name
likely coincides with bad drawdowns in the others — real portfolio risk is
understated by construction, not just by bad luck.

**Not yet fixed** — needs a real portfolio-level simulator with shared
capital and correlation-aware risk metrics.

### Bug: cost model was ~75x too wide for liquid large-caps

A flat 10 bps spread was applied to every symbol regardless of actual
liquidity. SPY's real quoted spread is roughly 0.13 bps; the model assumed
10 bps — about 75x too wide. For Scalping specifically, the modeled
round-trip cost (20 bps) was *larger than both its stop (15 bps) and its
target (22.5 bps)* — that strategy was mathematically unable to win before a
single bar of data was tested.

**Why this matters generally:** a single global cost assumption is fine for
a first pass, but always sanity-check it against the tightest stop/target
any strategy in the book uses. If the assumed cost exceeds the edge you're
trying to measure, you're not testing the strategy, you're testing the cost
model.

**Not yet fixed** — needs per-symbol spread estimates instead of one global
constant.

### Minor: R-multiples measured against the wrong entry price

Position size and risk-per-share are computed from the *signal bar's close*,
but `backtesting.py` correctly fills orders at the *next bar's open* (this
part is right — no look-ahead). The mismatch means realized risk at the
actual fill diverges from the risk the R-multiple was computed against:
median +1%, 90th percentile +16.7%, worst case +118% wider than planned.
Since R is computed against the smaller (planned) denominator, reported R is
systematically a little optimistic.

**Why this matters generally:** "no look-ahead bias" isn't just about not
peeking at future closes — it's also about making sure every number derived
*from* a signal (size, risk, R-multiple) uses the same price the strategy
will actually get filled at, not the price that triggered the signal.

**Not yet fixed** — low priority relative to the above; the direction of
every conclusion so far survives it, only the magnitude is off by a few
percent.

---

## 2026-07-16 — Engine bugs found while investigating "why does this strategy never trade"

**Context:** several day-trading strategies (Scalping, Mean Reversion Scalp,
News Fade) showed suspiciously few trades. The instinct was "the setup must
be rare." It wasn't — two separate engine bugs were suppressing real signal.

### Bug: position sizing had no cap on buying power

`size = (equity * risk_pct) // risk_per_share` sizes purely by dollar risk.
For a strategy with a tight stop (Scalping's stop was 0.15% of price), this
formula can demand a position notional many multiples of account equity —
one computed example wanted a $66K position against $10K equity. The broker
(correctly, `margin=1.0` = cash account, no leverage) rejected every such
order, but **`backtesting.py` cancels oversized absolute-share-count orders
silently — no exception, no warning** (the warning path only fires for
*fractional*-sized orders, a different code path). The entry signal fired
constantly; zero trades ever landed.

**Why this matters generally:** "the signal never fires" and "the signal
fires but the order never fills" produce an identical symptom (near-zero
trade count) from the outside. Always instrument the actual execution path
before concluding a setup is rare — count raw signal firings *separately*
from realized fills.

**Fix applied:** cap size by `equity // adjusted_entry_price` in addition to
the risk-based size, in `engine/backtest.py`.

### Bug: News Fade's lookback window crossed session boundaries

72.5% of News Fade's entries were firing in the first 15 minutes of a
session. The strategy's 21-bar ATR/volume baseline was computed over the raw
multi-day bar array, so the first bar of a new session got compared against
a baseline that was mostly *yesterday afternoon's* data — a normal overnight
gap was being misread as an intraday "news spike."

**Why this matters generally:** any rolling-window calculation on intraday
data needs to explicitly ask "does this window ever span a session
boundary?" A silent index-based lookback (`iloc[-21:-1]`) doesn't know or
care where the trading day starts — data provenance matters as much as data
correctness.

**Fix applied:** scope the lookback to `session_bars(bars)` before computing
ATR/volume, in `strategies/day/news_fade.py`. Trade count dropped from a
falsely-inflated 40 to a more honest 13 (too small to draw a conclusion
either way — which is itself the correct, honest outcome).

### General takeaway

Both bugs shared a shape: the strategy's *entry_signal* logic was fine in
isolation, but the surrounding engine (order execution, or the bars actually
fed to the strategy) silently broke the assumption the strategy was written
against. When a strategy's trade count looks implausibly low (or a metric
looks implausibly good on a tiny sample), check the machinery around the
strategy before concluding the strategy itself is bad — or good.
