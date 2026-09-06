# Structural-barrier hypothesis backlog

Research framing (unchanged from the session that created this file, 2026-09-05):
every strategy this repo has tested to date is a published technical rule on
29 mega-caps -- the most competitive combination available. The productive
question is not "where are institutions not looking" (they have looked
almost everywhere) but **"where are institutions looking and structurally
UNABLE TO ACT?"** Barriers, in descending order of strength: MANDATE
(a holder must trade regardless of price) > CAPACITY (too small for large
AUM) > OPERATIONS (inconvenient execution) > CAREER RISK (underperforms for
long stretches). "Nobody has thought of it" is not a barrier and is not on
this list.

This file exists so hypothesis selection is not re-derived under the
disappointment of a closed result. Do not start the next entry without
stopping to preregister it first, per `CLAUDE.md`'s existing research
governance.

## Status

Every remaining candidate was screened in one pass on 2026-09-06
(`engine/hypothesis_backlog_screen.py`, `reports/hypothesis_backlog_screen/`)
before any of them got a preregistration or a backtest, following the same
"cheapest decisive test first" rule that closed index deletion. See
"Backlog-wide breadth screen" below for the full table and the standing rule
it produced.

| Hypothesis | Barrier | Status | Best-case MDA %/yr |
|---|---|---|---:|
| Index deletion / forced selling | MANDATE | **SCREENED OUT 2026-09-05** | 11.45 |
| Sub-$300M cap cross-sectional anomalies | CAPACITY | **VIABLE 2026-09-06** | 0.91 |
| Sub-$5 stocks excluded by fund charters | MANDATE | **VIABLE 2026-09-06 (weak evidence)** | 1.59 |
| Small merger arb below institutional minimum deal size | CAPACITY | **SCREENED OUT 2026-09-06 (see below)** | 4.10-5.40 (corrected) |
| Distressed / post-reorg equity | MANDATE/CAPACITY | **VIABLE 2026-09-06 (data-shape caveat)** | 2.82 |
| Index addition / forced buying | MANDATE | **MARGINAL 2026-09-06** | 5.87 |
| Spinoff shares dumped by ineligible holders | MANDATE | **MARGINAL 2026-09-06** | 6.50 |
| Tax-loss selling reversal (December) | MANDATE/OPS | **SCREENED OUT 2026-09-06** | 8.15 |
| Odd-lot tender provisions | OPERATIONS | **DATA_BLOCKED 2026-09-06** | n/a |

## Index deletion / forced selling -- closed, negative screen result

**Hypothesis.** Mechanical selling by benchmark-constrained holders around an
S&P 500 deletion's EFFECTIVE date may temporarily push the deleted security
below a price justified by unconstrained investors, producing a subsequent
abnormal reversal. Entry was to be preregistered as a fixed offset from the
EFFECTIVE date (not the announcement date), holding only the POST-deletion
reversal direction.

**Barrier and persistence thesis.** MANDATE: an index-tracking or
benchmark-constrained fund must reduce/eliminate a position on deletion for
compliance, not because it expects a low return -- the mandate does not
disappear once the effect is known, only the price impact might shrink as
capital positions ahead of it. Known confounder: this is among the most
published effects in finance (documented since the 1980s) with substantial
evidence of decay; the pre-registered design required a mandatory sub-period
split for exactly this reason.

**Screen result (`engine/index_deletion_screen.py`,
`reports/index_deletion_screen/report.md`, run 2026-09-05): STOP.**

- Valid population: Sharadar's `sp500` table (full history back to 1998,
  discovered and validated this session -- see
  `research/PIT_DATA_REQUIREMENTS.md`'s 2026-09-05 update) gives 696 total
  `removed` events 1998-2026, machine-classified by the table's own `note`
  field into acquired/merged (370), market-cap-change (184), spinoff (73),
  bankruptcy (10), other (59). **Only the 184 market-cap-change removals are
  usable for THIS hypothesis** -- an acquired/merged removal usually means
  the security stops trading entirely at deal close (empirically confirmed
  on three real 2015 examples, SWY/LO/COV: `lastpricedate` equals the
  removal date), leaving no post-deletion price series to test a reversal
  against.
- Universe scope was decided BEFORE running the screen, per this project's
  own anti-post-hoc-narrowing rule: S&P 400/600 membership tables were
  probed and confirmed absent from the current Sharadar entitlement
  (`sp400`/`sp600`/`midcap400`/`smallcap600`/`indices` all return HTTP 403).
  This is a real, pre-existing data-availability constraint, not a scope
  narrowed after seeing a disappointing 500-only result.
- **Event-count hard-stop gate: PASSED** (184 usable events >= the
  preregistered 150 minimum).
- **Minimum-detectable-alpha hard-stop gate: FAILED, decisively.** Across
  three independently-defensible mappings of "184 events over 28.5 years"
  onto `engine/power_curve.py:screen_design`'s (positions, rebalances)
  model -- including the empirical minimum across all of them -- the best
  case is **11.45%/yr**, nearly 3x the preregistered 4%/yr ceiling. The
  modern-era (2016-2026) sub-window, run for the mandatory decay check, is
  worse (~17-20%/yr), consistent with capital having positioned ahead of
  this well-known effect over time, but the full-history number alone
  already fails the gate -- sub-period decay is not the reason this closed.
- Root cause, same shape as the Dual Momentum breadth finding already in
  `LESSONS.md`'s 2026-08-XX power-curve entry: **breadth, not data quality
  or engine correctness, is the binding constraint.** ~6.5 usable
  single-name events per year, even at full S&P 500 scale, is simply too
  few independent bets for any annualized effect size worth trading to
  clear statistical significance in a realistic sample length.
- Per Phase 8/11 instructions: no backtest was built, the 4%/yr threshold
  was not relaxed, and the full PIT store described in
  `research/PIT_DATA_REQUIREMENTS.md` (Phases 3-7 of the originating task)
  was not implemented for this hypothesis -- building it would have been
  infrastructure for a backtest that cannot produce an interpretable result
  regardless of data quality.

**What would have to change for this to become viable again**: a
fundamentally larger event population. Pooling S&P 500 with S&P 400/600
deletions (data currently unavailable, see above) would at best triple the
event count, which only reduces MDA by roughly sqrt(3) =~ 1.7x -- from
~11.4%/yr to ~6.6%/yr, still short of the 4%/yr bar. This hypothesis is not
"waiting on more data" in any small sense; it needs an order-of-magnitude
larger event population (e.g. Russell 2000 reconstitution, which has
vastly higher turnover) or a materially larger effect size than the
published literature suggests still exists, to be worth re-screening.

## Notes for whoever picks the next entry (superseded in part, see below)

- **Do not treat "index addition" as automatically viable just because it
  shares a barrier with the closed deletion hypothesis.** The event count
  is nearly identical (699 vs. 696 over the same period) and the same
  breadth math applies almost unchanged -- run `screen_design` again with
  addition-specific numbers before writing a preregistration; do not assume
  the answer.
  **CORRECTED 2026-09-06**: the raw event counts are indeed nearly
  identical, but the USABLE counts are not -- deletion's usable population
  is filtered down to 184 (market-cap-change only, since an M&A removal
  stops trading), while addition needs no such filter (every addition is,
  by definition, a security that continues trading) and keeps its full 699.
  That difference alone moved addition from "screened out" territory to
  MARGINAL (5.87%/yr best-case MDA vs. deletion's 11.45%/yr) -- still short
  of the 4%/yr ceiling, but for a different, weaker reason than deletion
  failed. The caution to re-screen rather than assume was correct; the
  specific prediction that it "applies almost unchanged" was not.
- **Spinoff-related removals (73 events) start from an even smaller
  population** than the 184 that already failed here. Screen before
  building.
  **CORRECTED 2026-09-06**: this was scoped too narrowly -- 73 was the
  S&P-500-ONLY spinoff-related removal count. The actual mechanism (a
  spinoff forcing sale by holders whose mandate can't retain the new,
  often small-cap security) applies market-wide, not just to S&P 500
  parents. Sharadar's market-wide `actions` table (`action=spinoff`) gives
  570 events over the same period, ~20/yr -- still MARGINAL (6.50%/yr), not
  viable, but for a much better-supported reason than the original
  narrow-universe guess would have given.
- Hypotheses with genuinely different breadth mechanics --
  continuously-eligible cross-sectional screens (sub-$5, sub-$300M cap,
  tax-loss-selling) rather than discrete annual corporate/index events --
  are structurally more promising for this project's realistic sample
  lengths, precisely because they are not capped at a few hundred events
  across three decades. Consider prioritizing one of those next.
  **CORRECTED 2026-09-06**: two of three cleared, but "cross-sectional" was
  never the actual mechanism -- tax-loss-selling is ALSO cross-sectional and
  still screened out (8.15%/yr), because it was constrained to the
  S&P-500-only universe and rebalances only once a year with heavily
  clustered (same-macro-factor) losers. See "Backlog-wide breadth screen"
  below for the corrected general rule.

## Backlog-wide breadth screen, 2026-09-06

Run before any of the remaining seven candidates got a preregistration or a
backtest (`engine/hypothesis_backlog_screen.py`, full detail in
`reports/hypothesis_backlog_screen/report.md`). Same 4%/yr MDA ceiling and
150-usable-event floor as the index deletion screen, for comparability;
`screen_design` is reused unchanged.

| Rank | Candidate | Barrier | Bets/yr | Best-case MDA %/yr | Verdict |
|---|---|---|---:|---:|---|
| 1 | Sub-$300M cap cross-sectional anomalies | CAPACITY | 24,132 | 0.91 | **VIABLE** |
| 2 | Sub-$5 stocks excluded by fund charters | MANDATE | 390 | 1.59 | **VIABLE (weak evidence)** |
| 3 | Small merger arb below institutional minimum deal size | CAPACITY | 161 | 2.29 | **VIABLE** |
| 4 | Distressed / post-reorg equity | MANDATE/CAPACITY | 106 | 2.82 | **VIABLE (data-shape caveat)** |
| 5 | Index addition / forced buying | MANDATE | 24.5 | 5.87 | MARGINAL |
| 6 | Spinoff shares dumped by ineligible holders | MANDATE | 20.0 | 6.50 | MARGINAL |
| 7 | Tax-loss selling reversal (December) | MANDATE/OPS | 82 | 8.15 | DEAD |
| 8 | Odd-lot tender provisions | OPERATIONS | 0 | n/a | DATA_BLOCKED |

Evidence quality is NOT uniform across the four VIABLE rows -- ranked by
confidence, not just by MDA:

1. **Merger arb small-deal** has the cleanest data: `acquisitionof.value` is
   a directly vendor-reported total deal size in $M, cross-checked against
   named real 2025 examples. 55.7% of 8,260 sized market-wide acquisitions
   over 1998-2026 fall under $500M (median deal size ~$377M across ALL
   sized deals, i.e. the median M&A target in this dataset is already
   "small"). Clear CAPACITY barrier (large arb funds need meaningfully
   sized positions relative to fund AUM; a $200M deal cannot absorb enough
   capital to matter) with an obvious, durable persistence thesis --
   fund-size economics do not change if the effect becomes known.
2. **Distressed / post-reorg equity** is measured but has a real data-shape
   gap: `bankruptcyliquidation` action rows (3,027 non-SPAC events over
   1998-2026, ~106/yr) have no `contraticker` linking an old, cancelled
   security to a new post-reorg one -- this action most plausibly marks the
   OLD equity's cancellation, not the emergence event the hypothesis
   actually needs. The reported count is an UPPER BOUND on usable events,
   not a confirmed one.
3. **Sub-$300M cap anomalies** has the largest raw breadth by a wide margin
   (2,011 of 5,514 market-wide securities under $300M on a single 2026-09-03
   snapshot) but is not yet a hypothesis -- it is a universe. A specific
   cross-sectional signal (momentum? reversal? quality dispersion?) still
   needs to be chosen before this is preregisterable, and the CAPACITY
   barrier claim itself needs an ADV/liquidity check (via
   `engine/pit_all_stocks.py`'s existing `capacity_diagnostics`) to confirm
   these names are genuinely too small for institutions rather than merely
   index-ineligible.
4. **Sub-$5 stocks** is the weakest of the four: the $5 level count (1,561
   of 6,310 securities) is real, but the actual tradable event -- a stock
   CROSSING the threshold -- was never measured, only assumed at a labeled
   25%/yr crossing rate. Treat its MDA as illustrative of order of
   magnitude only, not as a number to preregister against yet.

### Standing rule: test result

The proposed rule was **"event-driven hypotheses on single index families
are structurally out of reach, and cross-sectional hypotheses are where a
question can actually be resolved."** This screen partially confirms it but
also breaks it in both directions, and the breaks point at the real
mechanism:

- **Breaks the rule, viable side**: merger arb small-deal and distressed
  equity are both EVENT-DRIVEN, not cross-sectional, and both cleared
  VIABLE. What they have in common is not their shape -- it's that both
  draw events from the ENTIRE market (~5,500+ securities), not from a single
  ~500-name index family. Market-wide event populations run 100-160+/yr;
  single-index-family ones (deletion, addition, S&P-500-scoped spinoffs)
  are capped around 6-25/yr because that is roughly how much a ~500-member
  index actually reshuffles in a year, no matter which side of the
  reshuffle you screen.
- **Breaks the rule, dead side**: tax-loss-selling reversal is
  CROSS-SECTIONAL and still screened out DEAD. It was cross-sectional but
  narrow (S&P-500-only universe, ~500 names), infrequent (rebalances once a
  year, not monthly), and its "positions" cluster hard on the same macro
  factor (measured: loser rate ranged from 1.6% in a calm year to 46.0% in
  2022) -- so its nominal 82 positions/yr behave like far fewer independent
  bets. Cross-sectional framing did not save it.

**Corrected standing rule, written here because the evidence supports it:**
breadth is governed by (1) the size of the universe a mechanism's
population is drawn from and (2) how frequently genuinely new,
non-redundant observations arrive -- not by whether a hypothesis is framed
as "event-driven" or "cross-sectional." A single-index-family event
hypothesis is structurally capped by that index's own reshuffle rate
(observed: ~6-25/yr for S&P 500 addition/deletion/spinoff, regardless of
which side is screened). A market-wide event population or a broad,
frequently-rebalanced cross-section both clear the ceiling easily; a
narrow, infrequently-rebalanced, factor-correlated cross-section does not
clear it just because it is nominally "cross-sectional." **Future
candidates should be triaged on universe size and rebalance/arrival
frequency first, and only secondarily on the event-driven/cross-sectional
label** -- the label was a reasonable first-pass heuristic (3 of 4 VIABLE
rows and the DATA_BLOCKED row are consistent with it) but is not the
underlying variable and should not be trusted on its own for the next
hypothesis picked from this list.

### Recommendation: small merger arb below institutional minimum deal size

Best combination of data quality (directly measured, vendor-reported deal
values, cross-checked against real examples), a specific and already
well-defined mechanism (no further signal-design work needed the way
sub-$300M's "anomalies" still requires), a durable CAPACITY barrier
(fund-size economics), and an MDA comfortably under the ceiling across
every correlation assumption tried (2.29-3.01%/yr). Sub-$300M cap
anomalies has more raw breadth margin and is worth keeping as the backup
candidate, but it is a universe waiting for a specific signal, not yet a
hypothesis.

**What this candidate would require in PIT store terms before it could
run** (none of this has been built -- this is a scope statement, not a
plan to execute):

- **Universe**: market-wide, not S&P-500-scoped -- thousands of securities,
  not the ~29-500 name scale every other engine in this repo operates at.
  A materially larger identity-management burden (permanent IDs, ticker
  reuse) than `research/PIT_DATA_REQUIREMENTS.md`'s Phase 3 design assumed.
- **Prices**: daily OHLCV for target AND acquirer around announcement and
  close, specifically for small/mid-cap targets -- a population this
  session's delisted-price-coverage check did NOT verify (that check used
  three S&P 500 mega-cap M&A examples, SWY/LO/COV, a different and easier
  population than a $200M small-cap target). Needs its own coverage check
  before being trusted.
- **Corporate actions**: `acquisitionof`/`acquisitionby`/`acquisitioncash`/
  `acquisitionstock` rows already give dated, sized, market-wide events --
  the best-covered input this candidate needs.
- **Announcement vs. close timing**: the `actions` table's date appears to
  be the close/completion date; a real backtest needs the ANNOUNCEMENT date
  too (entry point), which may require a supplementary source (SEC 8-K
  `events` table eventcodes, not decoded this session, or another feed).
- **Deal-completion risk**: some announced small deals fail to close --
  needs explicit handling (a preregistration decision, not a data problem
  per se, but the PIT store needs to expose deal-outcome, not just
  deal-value).

Per this session's scope: **no PIT store, no backtest, and no
preregistration were started for this or any candidate.** This is a
ranked feasibility table and a recommendation only.

## Follow-up, 2026-09-06: merger arb screened out on closer inspection; pivoted to sub-$300M momentum

The recommendation above was tested before being acted on, per the same
"cheapest decisive test first" rule that closed index deletion. Two
questions were asked of merger arb before writing a preregistration:

1. **Does Sharadar carry a clean announcement date?** Yes, partially.
   `events` (Material Corporate Events, 8-K-sourced) carries eventcode
   `11`, which behaves like SEC Item 1.01 ("Entry into a Material
   Definitive Agreement") -- cross-validated against real price action
   rather than trusted from the schema alone (confirmed cleanly on
   Poseida Therapeutics: +228% same-day return exactly on its code-`11`
   filing). But coverage is not universal: 27% of a 45-deal 2020-2025
   sample had NO code-`11` event at all (mostly foreign private issuers,
   who file 6-K instead of 8-K), and stock-for-stock deals show no price
   jump at all -- mechanistically explained (a swap ratio doesn't re-rate
   the target the way cash does), not a data gap, and independently
   confirmed via the separate `acquisitioncash`/`acquisitionstock` action
   rows. Coverage holds back to at least the mid-2000s.
2. **Does the ORIGINAL screen's breadth survive once that eligibility
   filter is actually applied?** No. The original 2.29%/yr MDA used all
   161 sub-$500M deals/year unconditionally. Restricting to what the study
   actually needs -- a verifiable announcement date (confirmed real price
   jump) AND a cash deal (so the position needs no acquirer-side hedge) --
   drops the eligible rate to ~50/year, and MDA moves to **4.10%/yr under
   the project's own standard rho=0.5 assumption, 4.66%/yr at rho=0.2
   (the merger-arb-specific low-correlation case), 5.40%/yr at rho=0**.
   Fails the 4%/yr ceiling under every correlation assumption tried,
   reversing the original VIABLE verdict. Widening to <$1B (3.71%/yr) or
   <$2B (3.44%/yr) recovers viability, but at that size large arb funds
   routinely participate, weakening the CAPACITY-barrier claim the
   hypothesis depends on -- a breadth-vs-mechanism tradeoff, not a fix.

**Lesson for the rest of this backlog**: the original screen's headline
numbers for EVERY event-driven candidate used the raw, unconditional event
count. Any candidate whose entry timing or position construction needs an
eligibility filter (a verifiable date, a specific deal/consideration type,
a liquidity floor) should expect its real breadth to be smaller than the
headline, sometimes enough to flip the verdict. Distressed/post-reorg
equity carries an analogous unresolved caveat (no confirmed link from a
`bankruptcyliquidation` row to a continuing, tradable post-reorg security)
and should not be trusted at its original 2.82%/yr until it receives the
same scrutiny.

**Decision: pivoted to sub-$300M cap cross-sectional anomalies**, given
its much larger raw margin (0.91%/yr headroom vs. merger arb's
already-thin 2.29%/yr). That margin was tested, not just assumed: the
original screen's own MDA was ALSO an oversimplification (it modeled the
strategy as if it held the entire ~2,000-name eligible universe at once,
rather than a realistic ranked subset) -- recomputed against a genuine
held-portfolio size (top 20-75 names), MDA runs 2.41-3.38%/yr, still
comfortably viable. A liquidity/history floor was measured directly (28.6%
of a 70-name sample passes $500,000/day median dollar volume with 200+
days of history) and does not remove enough breadth to threaten that
margin. Point-in-time integrity of `daily.marketcap` was spot-checked
against a real delisted security (SWY, delisted 2015-01-29) and found
continuously accurate through its own actual last trading day -- the
exact survivorship risk that sank the free-data efforts elsewhere in this
repo does not reproduce here, at least not on this one check.

**Preregistered**: `research/microcap_momentum_v1_preregistration.json`.
12-1 month cross-sectional momentum, percentile-based (bottom quintile)
universe definition rather than a fixed nominal threshold (a $300M cutoff
held constant since 1998 does not mean the same thing in 1998 vs. 2026
dollars), a small 4-configuration parameter family (portfolio size 30/50
x liquidity floor $500K/$250K), falsification bound to the SAME
factor-regression discipline that closed the original Dual Momentum
question (residual alpha after MKT/SMB/HML/UMD must clear a 95% CI
excluding zero and must not collapse once SMB is included -- i.e. this
must not turn out to be a size bet wearing a momentum label), and an
80/20 chronological development/holdout split via the existing
`research_governance.chronological_evidence` machinery.

## Follow-up, 2026-09-06: Phase 1 store built clean; two decisive findings paused Phase 2

Phase 1 (`engine/build_microcap_pit_store.py`, `data/pit_us_all_stocks/`)
built the monthly point-in-time eligible-universe history across the full
1998-12-01 to 2026-09-05 span (334 months, zero errors) and resolved two
open items honestly: Sharadar's `daily.marketcap` genuinely has zero
coverage before 1998-12-01 (not an error -- confirmed exact boundary), and
the RAW percentile-based eligible population is stable throughout
(minimum 996 names in any month, comfortably above the preregistered
30/50 portfolio sizes).

Before Phase 2 (per-ticker price/liquidity ingestion), the preregistration
was amended three ways -- legitimate before any result is seen, not
after: (1) the claim was stated precisely (momentum not captured by
TRADEABLE factors due to a capacity barrier, not "momentum works in
general") with an explicit size/liquidity-decile monotonicity test added
as a co-equal falsification condition alongside the factor-regression
alpha test; (2) a real cost model was built (Corwin-Schultz high-low
spread estimator, since the existing `engine/data.py` spread tiers bottom
out at "<$300M/day -> 5bps," roughly 600-1000x too coarse for a
$500K-$1M/day universe), with OPTIMISTIC/BASE/STRESS regimes and a
COST_SENSITIVE classification; (3) the MDA's population assumption was
checked against real historical data rather than left as an unverified
extrapolation from today's snapshot.

**Two decisive findings came out of that amendment work, both reported
before touching Phase 2, per this session's explicit "say so before
running anything" instruction:**

1. **Cost sanity check**: measured Corwin-Schultz spreads on 8 real names
   at the liquidity floor (median 84.25bps round-trip). At realistic
   monthly momentum-portfolio turnover (>=50%, unmeasured but consistent
   with academic and this project's own experience), the OPTIMISTIC-regime
   (lower-bound) annualized cost (5.05%/yr at 50% turnover) already
   EXCEEDS the entire 3.04%/yr top-30 detectable effect. Only very low
   turnover (<=~25%/month) stays under the ceiling.
2. **Liquidity population instability**: the $500,000/day floor's pass
   rate was NOT stable across history -- 0% in a 2010 sample (0/25), 0% in
   2015 (0/23), 12% in 2020 (3/25), vs. 28.6% today. The preregistered
   development window (1998-12 through 2020-12) could not have supported
   30/50-name portfolios under this floor for most of its span.

Both findings point toward design changes (a percentile-based liquidity
floor instead of a fixed nominal one, mirroring the fix already applied to
the market-cap threshold; a shorter or differently-weighted study window;
measuring rather than assuming turnover) -- none of which were chosen here,
since doing so after seeing that they fix the problem would be exactly the
after-the-fact parameter selection this project's preregistration
discipline exists to prevent. **Phase 2 ingestion and the Phase 2
acceptance gates (delisting, identity-at-scale, price integrity, lookahead)
were not started.** Full detail in the preregistration's `STATUS_amended`
block.

## Follow-up, 2026-09-06: the first cost screen was wrong, and correcting it flips most of the backlog

The first cost pass (`engine/hypothesis_cost_screen.py` v1, 8-15 tickers,
Corwin-Schultz only) found every surviving candidate COST_DOMINATES. Asked
to increase samples, validate the estimator independently, recheck the
turnover assumption against actual holding structure, and screen the
wider S&P 500+400+600 family, three of those four checks found real
problems with the FIRST pass, not with the candidates:

1. **Corwin-Schultz alone is not trustworthy at this sample size.**
   Validated against Abdi-Ranaldo (2017, an independently-derived
   close/high/low estimator) on ultra-liquid mega-caps with a well-known
   real spread (~0.5-2bps): AAPL returned 18.5bps on Corwin-Schultz vs.
   0.0bps on Abdi-Ranaldo over the same year. Corwin-Schultz's 2-day range
   comparison misattributes genuine large-range trading days (real
   volatility, not spread) as spread -- worse on higher-volatility names,
   which describes most of this backlog. Both estimators are now reported
   for every candidate; a result is only trusted where they agree.
2. **The first pass conflated event arrival rate with portfolio turnover.**
   Event-driven candidates used their own events/year figure (built for
   the MDA/breadth calculation) as if it were ALSO the annual round-trip
   count -- correct only in the coincidental case where exactly one
   capital slot exists and its holding period exactly equals
   1/events-per-year. In general (Little's-law argument, see the module
   docstring), annual cost per dollar of capital is `spread /
   holding_period_years`, independent of the event rate -- a strategy with
   106 events/year but a 9-month typical hold turns over ~1.3x/year, not
   106x/year. The first pass's 174%/yr distressed-equity cost figure was
   this bug, not a real finding.
3. **Corrected result: most candidates are COST_OK; only two remain
   cost-dead.** With real holding-period assumptions (disclosed per
   candidate, not fitted to pass) and both estimators required to agree:

| Candidate | MDA %/yr | CS/AR spread (bps) | Holding period | Annual cost CS/AR %/yr | Verdict |
|---|---:|---:|---|---|---|
| Index addition | 5.87 | 26.9/17.2 | 0.06yr | 4.53/2.89 | COST_OK (still MARGINAL on breadth alone) |
| Spinoff dumping | 6.50 | 42.0/20.0 | 0.16yr | 2.65/1.26 | COST_OK (still MARGINAL on breadth alone) |
| **Distressed / post-reorg** | **2.82** | 118.7/17.7 | 0.71yr | **1.66/0.25** | **COST_OK -- strongest surviving candidate** |
| Sub-$5 stocks | 1.59 | 120.4/102.7 | 0.17yr | 7.22/6.16 | COST_DOMINATES (confirmed) |
| Sub-$300M momentum | 3.04 | 131.6/116.9 | 0.17yr | 7.89/7.01 | COST_DOMINATES (confirmed, more robustly than before) |
| S&P 500 family (all reasons) | 4.16 | 22.3/0.0 | 0.06yr | 3.74/0.00 | COST_OK, breadth just barely MARGINAL |

Index deletion, merger arb, and tax-loss selling were already dead on
breadth and are cost-irrelevant (MOOT) -- but for the record, their cost
profiles would ALSO have been fine in isolation (2.5-6%/yr), confirming
their failures were genuinely about breadth, not compounded by an
overstated cost problem.

**Sub-$5 stocks and sub-$300M momentum are now confirmed cost-dead on
STRONGER evidence than before** (larger samples, both estimators agreeing)
-- not weakened by the correction, strengthened.

4. **S&P 500+400+600 family: partially data-blocked, and the available
   part is a near-miss.** Re-confirmed (two independent doc fetches plus
   direct HTTP probes on a dozen guessed table names) that Sharadar has no
   `sp400`/`sp600`/midcap/smallcap/Russell/DJIA table under any name -- a
   genuine product-scope gap, not an entitlement problem (the paid
   `full_history_or_broader` tier works correctly on every table Sharadar
   actually sells). The available S&P-500-only version (all
   addition/deletion reasons combined, not just market-cap-change) landed
   at **4.16%/yr MDA -- barely over the 4% ceiling** -- while its cost is
   essentially free (3.74%/yr Corwin-Schultz, **0.00%/yr Abdi-Ranaldo**,
   consistent with these being large, liquid, index-eligible names). If
   S&P 400/600 data were ever sourced elsewhere, tripling the event
   population would reduce MDA by roughly sqrt(3) =~ 1.73x, to
   approximately 2.4%/yr -- comfortably viable, at near-zero cost. **This
   is the single most promising unresolved thread in the entire backlog,
   blocked purely by data availability, not by mechanism or economics.**
   Finding that data (a free community tracker, similar to what
   `data/sp500_pit_free/` already used for S&P 500, or a different paid
   source) is worth pursuing before any other backlog item.

Full detail, every sample ticker, both estimators per candidate:
`reports/hypothesis_cost_screen/report.md`.

## Follow-up, 2026-09-06: distressed/post-reorg equity is DATA_BLOCKED on identity, not breadth or cost

The remaining "VIABLE (caveat)" candidate's caveat was investigated
directly: can a pre-bankruptcy issuer be linked to its post-reorganization
equity using only point-in-time information? `engine/reorg_identity_screen.py`,
tested against 10 real, independently-verifiable bankruptcy cases (Hertz,
Frontier, Chesapeake/Expand Energy, GM, Washington Mutual, Sears, SVB
Financial, Party City, Rite Aid, Bed Bath & Beyond) -- never a synthetic or
assumed sample.

**What works, confirmed on real data:**
- `actions.bankruptcyliquidation` + same-day `delisted` is a clean, reliable
  filing-adjacent event marker (confirmed on all 7 cases where the company
  was ever listed).
- The SEC CIK embedded in `tickers.secfilings` is an externally-auditable
  signal that correctly distinguishes "same legal registrant, restructured
  equity" (Hertz, Frontier, Chesapeake -- same CIK across pre/post
  permatickers) from "genuinely different entity" (GM's liquidation +
  NewCo, Washington Mutual's ticker reused by an unrelated company --
  different CIK in both cases). 10/10 ground-truth cases were correctly
  classified this way.

**What does not work, also confirmed on real data:**
- `relatedtickers` and the `tickerchangefrom`/`tickerchangeto` pair
  immediately preceding a bankruptcy delisting are NOT corporate-succession
  claims -- they are Sharadar's own internal relabeling of the pre-bankruptcy
  ticker to a bankruptcy-suffixed one. Trusting them literally produces a
  textbook false link (Washington Mutual's WAMUQ points to "WM", now Waste
  Management Inc) and separately MISSES a real, correct link (Frontier's
  FTRCQ never mentions its actual successor FYBR). Unreliable in both
  directions.
- **CIK matching VERIFIES a candidate link; it does not DISCOVER one.**
  Every one of the 10 ground-truth cases required already knowing which
  ticker to check. No field points forward from an old bankrupt security to
  its successor, and none points backward from a new listing to its
  bankrupt predecessor. A blind, market-wide discovery process would
  require building a full CIK index across every Sharadar-tracked security
  and matching candidates by (CIK, date proximity) -- real, nontrivial
  infrastructure, not built in this feasibility pass per the explicit
  instruction not to process the whole historical universe before a
  ground-truth check.

**Status: DATA_BLOCKED.** Not because linkage is impossible in principle
(the CIK method is sound and 10/10 verified) but because it cannot be
RELIABLY ESTABLISHED AT SCALE with the fields and infrastructure available
today -- discovering candidates still requires either external per-case
research or a full CIK-index-building effort that was deliberately not
undertaken here. The previous 2.82%/yr MDA and COST_OK cost verdict are
NOT carried forward: they described the raw `bankruptcyliquidation`
population (106/yr), not a population that can actually be identified,
linked, and entered without look-ahead. No backtest, no holdout, and no
population recount at scale were performed, per the task's explicit
instruction not to rescue the hypothesis by widening or guessing.

Full ground-truth table, CIK evidence per case, and the verification code:
`engine/reorg_identity_screen.py`, `tests/test_engine/test_reorg_identity_screen.py`.

## Pre-research triage funnel, 2026-09-06

`engine/edge_candidate_triage.py` now provides the lightweight standing funnel
for the backlog. It composes existing evidence rather than replacing it:
observability/identity first, `screen_design` breadth second, corrected
dual-estimator holding-period cost third, mechanism tier fourth, then a human
gate. It does not backtest, preregister, inspect protected returns, or write a
second research ledger. Full output is in
`reports/edge_candidate_triage/report.md` and `.json`.

### Consolidated existing backlog

| Candidate | Observability | MDA %/yr | Annual cost CS/AR %/yr | Final status |
|---|---|---:|---:|---|
| Index deletion | OBSERVABLE | 11.45 | moot | SCREENED_OUT_BREADTH |
| Small merger arb | OBSERVABLE | 4.10 after eligibility | moot | SCREENED_OUT_BREADTH |
| Sub-$300M momentum | OBSERVABLE | 3.04 | 7.89 / 7.01 | SCREENED_OUT_COST |
| Sub-$5 stocks | OBSERVABLE | 1.59 | 7.22 / 6.16 | SCREENED_OUT_COST |
| Tax-loss reversal | OBSERVABLE | 8.15 | moot | SCREENED_OUT_BREADTH |
| Distressed/post-reorg | DATA_BLOCKED | withdrawn | withdrawn | DATA_BLOCKED |
| Index addition | OBSERVABLE | 5.87 | 4.53 / 2.89 | MARGINAL |
| Spinoff dumping | OBSERVABLE | 6.50 | 2.65 / 1.26 | MARGINAL |
| Odd-lot tenders | DATA_BLOCKED | — | — | DATA_BLOCKED |
| S&P 500+400+600 migrations | PARTIAL | 4.16 for 500-only proxy | 3.74 / below-resolution | DATA_BLOCKED |

### New mechanism candidates (no returns tested)

| Candidate | Mechanism tier | Observability | Cheap-screen result |
|---|---:|---|---|
| Ex-dividend tax/clientele pressure | 3 | OBSERVABLE | 6,743 nominal 2025 common-stock events collapsed to 248 date clusters; 10-year MDA 3.12%; COST_SENSITIVE at 2.80% CS versus AR below resolution |
| S&P 500 equal-weight quarterly reset | 1 | OBSERVABLE | ~2,000 rows/year are only four common batches; MDA 17.36%; SCREENED_OUT_BREADTH |
| Fed stress-test capital-distribution constraint | 1 | OBSERVABLE | one common annual event despite many banks; MDA ~40%; SCREENED_OUT_BREADTH |
| Reg SHO Rule 201 restriction | 1 | PARTIAL | DATA_BLOCKED on official historical trigger timestamps/consolidated intraday state |
| IPO lockup expiration | 1 | PARTIAL | DATA_BLOCKED on exact prospectus terms and waivers; 180 days cannot be assumed |
| Leveraged-ETF daily rebalance pressure | 1 | PARTIAL | DATA_BLOCKED on historical daily NAV/shares and derivative exposure |
| Lagged 13F crowded exits | 2 | PARTIAL | DATA_BLOCKED; filings lag quarter-end by up to 45 days and no PIT fund-flow source is installed |

**Decision: no candidate reached `STRONG_CANDIDATE`, so none is approved for
preregistration.** The ranked next questions are deliberately smaller than a
study: resolve quoted-cost/holding-horizon feasibility for ex-dividend events;
audit one source for PIT S&P 400/600 changes; and check 3–5 leveraged-ETF
sponsors for usable historical NAV/share files. Stop if those inputs are not
bounded and verifiable. Do not promote a Tier-1 narrative around a failed
observability gate.

### Capability-based data readiness (2026-09-06)

The provider layer is now evaluated by capability rather than one universal
binary PIT gate. This changes data readiness only; it does not change any
research/statistical status or run a strategy.

| Hypothesis | Data readiness | Main blocker |
|---|---|---|
| S&P 500 index deletion | `DATA_READY` | None for declared S&P 500 membership, daily prices, and reconstructed adjusted prices |
| Sub-$300M cap cross-sectional momentum | `DATA_READY` | None for daily market cap, filing-date PIT fundamentals, prices, and reconstructed adjustments |
| Distressed / post-reorg equity | `DATA_BLOCKED` | Critical successor-security linkage and complex reorganization chain are `UNSUPPORTED`; terminal economics are `PARTIAL` |
| S&P 400 migrations | `DATA_BLOCKED` | `sp400_membership` is `UNSUPPORTED` under the Sharadar entitlement |
| S&P 600 migrations | `DATA_BLOCKED` | `sp600_membership` is `UNSUPPORTED` under the Sharadar entitlement |
| Ordinary delisted momentum with ambiguous episodes excluded | `DATA_READY` | Delisted history is usable only with explicit incomplete-episode disclosure |
| Momentum across ticker changes | `DATA_READY_WITH_LIMITATIONS` | Ticker history and permanent identity are `PARTIAL`; ambiguous joins must be excluded |

The adjusted-price carve-out is explicit and unchanged: reconstructed prices
from `closeunadj` plus `actions` are canonical, while vendor `closeadj` is only
an independent cross-check. Full capability evidence is in
`reports/data_readiness/`.

### Follow-up source and harvestability checks (2026-09-06)

The cheap checks were completed without return inspection, backtesting,
holdout consumption, or preregistration:

| Check | Result | Decision |
|---|---|---|
| Ex-dividend quoted execution | Five-session CS drag 11.22%/yr; AR below resolution; IEX median 364.39 bps; AAPL/MSFT sanity failed (MSFT 6.80 bps > 5) | Remains `COST_SENSITIVE`; do not add quote infrastructure unless consolidated NBBO/TAQ becomes available |
| S&P 400/600 PIT source breadth | pitindex S&P 600 starts 2021-03-26; indexkit roughly 2019-11; complete family history needs ~10.26 years under the conservative breadth projection | `STOP_SCREENED_OUT_BREADTH`; require an exhaustive identity-safe ledger before building |
| Leveraged ETF sponsor data | ProShares historical CSV contains daily NAV, shares outstanding, and AUM; derivative exposure and independent sponsor coverage remain unverified | Remains `DATA_BLOCKED`; no reset-flow model yet |

Reports: `reports/ex_dividend_feasibility/`,
`reports/source_breadth_audit/`, and regenerated
`reports/edge_candidate_triage/`.

### Follow-up estimator validation (2026-09-06)

Task 1 was run as the only permitted next step. Five mechanically selected
$50M-$500M US common stocks were measured over 253 Sharadar daily OHLCV rows
and compared with Alpaca IEX quoted bid/ask observations on 2026-09-03:
`PZG`, `ZUMZ`, `NERV`, `XPER`, and `GFUZ`. Only one of ten estimator/name
comparisons (XPER Abdi--Ranaldo) was within 2x of the quoted reference; the
other estimates materially understated quoted spreads (and Corwin--Schultz
also overstated XPER). Quote counts ranged from 8 to 144, with no API errors.

The result is **METHODOLOGY_BROKEN** for this liquidity tier. Corwin--Schultz
and Abdi--Ranaldo remain diagnostic fields, not a primary or cross-check cost
model. The recommended replacement is observed consolidated/NBBO quoted
spreads or a calibrated licensed microstructure field. Per the gate, Task 2
(index-addition cost re-evaluation) and Task 3 (breadth) were not run.
Evidence: `reports/spread_estimator_validation/report.md` and `report.json`.

### Index-addition breadth screen (2026-09-06)

Using the raw Sharadar `sp500` table and the unchanged `screen_design()`
assumptions, there are **699 total and 699 usable `added` events** from
1998-01-09 through 2026-08-18 (28.68 years; **24.38 events/year**). The
existing deletion screen has no separate price-availability or concurrency
filter; applying that scope unchanged therefore does not silently invent a
PIT eligibility filter.

| Correlation case | MDA %/yr | Result vs 4% ceiling |
|---|---:|---|
| Zero (rho=0) | 7.73 | Fail |
| Realistic project standard (rho=0.5) | 5.87 | Fail / marginal |
| Pessimistic (rho=0.9) | 5.28 | Fail / marginal |

The table contains `added`, `removed`, `historical`, and `current` rows, but
no distinguishable migration action. `contraticker` appears on addition rows
and can identify apparent add/remove pairs; it does not establish movement
between the unavailable S&P 400/600 universes. Migrations therefore remain a
separate unresolved data problem and were not merged into MDA.

**Verdict: MARGINAL under realistic correlation, not viable against the 4%
ceiling.** No backtest, return inspection, holdout, or PIT-store build was
started. Evidence: `reports/index_addition_screen/`.

### Liquidity-tier breadth/cost mapping (2026-09-06)

The full Sharadar snapshot used was **2026-09-03**. Eligibility was common
stock, NYSE/NASDAQ/AMEX/NYSEMKT, price at least $1, and at least 200 trading
rows. No market-cap floor was applied. The resulting 4,718 securities were
sorted into ten equal-count deciles by trailing 63-session median dollar
volume.

Using `screen_design()` with 12 monthly rebalances, top-quintile sizing, and
rho=0.3, each decile has approximately 2.17 independent bets/year and 2.17%
MDA/year (below the 4% ceiling). Corwin--Schultz spreads were corrected by
1.5x, 2x, and 3x, with annual round-trip cost defined as corrected spread × 2
× 12 / 10,000. All ten deciles remain below MDA under all three corrections;
the result is **FAVORABLE across the measured liquidity spectrum**, subject to
the explicit rough-bias assumption and the absence of return testing.

The measured decile ranges and full tables are in
`reports/liquidity_decile_map/report.json`. This is a mapping result only:
no hypothesis, backtest, return inspection, holdout, or universe commitment
was made. A future tier hypothesis still requires PIT membership/security
identity, adjusted prices/actions, delisting-inclusive terminal economics,
and preregistration of lookback/skip, top-quintile sizing, rebalance calendar,
cost model, and liquidity eligibility before seeing returns.
