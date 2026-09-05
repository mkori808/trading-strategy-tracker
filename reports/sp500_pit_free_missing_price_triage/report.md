# sp500_pit_free_v1 missing-price triage

Diagnostic-only. Does not change the pre-registered 98% coverage threshold
(`research/sp500_pit_free_coverage_preregistration.json`), any strategy
code, `engine/runner.py`, or `universes/sp500_pit_free_v1.json`'s
eligibility. Produced by `engine.triage_sp500_pit_free_missing_prices`.

## Answer to the core question

**The failure to reach 98% coverage is primarily a structural limitation of
the free data sources, not a fixable symbol/data-resolution problem** — with
one honest carve-out: a small slice (≤27 tickers) looks like a transient,
recoverable fetch issue rather than a real gap, but even fully recovering it
would not close the ~0.6 percentage-point shortfall from the observed peak
of 97.42% to the 98% bar.

Of 576 missing/partial tickers:

| Category | Count | Meaning |
|---|---:|---|
| `bankruptcy_delisting_or_provider_gap` | 349 | Tenure ended with no substitution signal; a real delisting/bankruptcy and an ordinary provider gap are **indistinguishable** from ticker-only data |
| `ticker_change_merger_or_acquisition_likely` | 170 | Same-day 1:1 REMOVE+ADD in `reconciliation.csv` (a real corporate-action footprint) — **explained, not resolved**: we still don't have the removed ticker's own price history |
| `provider_coverage_gap` | 27 | Currently-active, unambiguously real securities (BK, HES, IPG, K, MMC, WBA, ...) with **zero** fetchable data this session |
| `symbol_mapping_attempted_no_candidate_data` | 28 | Legacy `...Q` suffix stripped, candidate ticker also unfetchable |
| `symbol_mapping_candidate_found_identity_unverifiable` | 2 | Legacy suffix stripped, candidate **has** data — but identity is unconfirmed (see warning below) |
| `membership_defect_candidate` | 0 | None found — no tenure happened to end exactly at the primary/secondary handoff boundary among missing tickers |
| `unresolved` | 0 | Every ticker matched a category |

**Zero tickers were safely resolved.** The deterministic resolution this
triage was scoped to attempt — strip a trailing `Q` and check the same
provider (yfinance) — never produces identity-verified matches, and testing
it surfaced a concrete false positive worth designing around going forward.

## The one resolution attempt, and why it was rejected

`CPQ` (Compaq Computer, delisted 2002) strips to `CP` — a ticker with
decades of real, fetchable data. But `CP` is **Canadian Pacific Railway**,
an unrelated company that has traded under that symbol the entire time.
Data existing under a stripped candidate is not evidence of correct
identity, because none of this pipeline's sources (qzzcl, leandroloi, or
the yfinance price feed) carry a company name, CUSIP, or FIGI to
cross-check against. `SUNEQ → SUNE` (SunEdison) is plausibly a real match
by outside knowledge, but this triage treats it identically to `CPQ → CP`
rather than picking and choosing which coincidences to trust — that would
be guessing, not verification.

**Both candidates are reported as `identityVerified: false`.** Applying
either blindly into a strategy backtest would silently substitute one
company's returns for another's. A real fix requires a company-identity
crosswalk — the "alternate data" this triage was explicitly scoped not to
pursue.

## Concentration: how much of the problem is a few names?

- Smallest set of tickers responsible for **50%** of missing rebalance-date
  observations: **131 tickers**.
- Smallest set responsible for **80%**: **286 tickers**.
- The top 10 by missing-observation count (`BK, HES, IPG, K, MMC, WBA, MRO,
  CMA, PKI, BLL`) — all long-tenured (316–352 of 353 rebalance dates) —
  together account for only ~6.2% of total missing observations.

**There is no small, dominant set of names to fix.** Even resolving the top
20 tickers perfectly would leave the shortfall almost untouched; the
missing observations are broadly distributed across hundreds of names, most
tied up in the indistinguishable-terminal-event bucket above.

## Survivorship bias check

| | Removed (delisted) | Still active | Removed share |
|---|---:|---:|---:|
| Covered tickers (OK/PARTIAL) | 148 | 491 | 23.16% |
| Missing tickers | 530 | 12 | **97.79%** |

**Verdict: `SURVIVORSHIP_BIAS_RISK`.** A 74.6 percentage-point gap. Missing
price data is overwhelmingly concentrated in names that left the index,
which is exactly the direction that would flatter a naive backtest — any
strategy that can't get an exit price for a stock right before or during
its removal silently drops the trade that would have hurt it most. This is
not a hypothetical: it is the measured shape of this dataset's gaps.

## The `provider_coverage_gap` transient-issue finding

The 27 `provider_coverage_gap` tickers are unambiguously real, currently
S&P 500-listed, liquid securities. Checked directly during this triage:

- Batch download: 26 of 27 returned zero rows; `BR` (Broadridge) succeeded.
- Individual `yfinance.download('BK', ...)`: `HTTP 404 — Quote not found for
  symbol: BK`.
- `yfinance.Ticker('BK').history(...)`: same failure, different code path.
- Control check in the same session: `AAPL` and `MSFT` both succeeded
  immediately.

This pattern — real, liquid tickers failing across multiple yfinance call
paths while common names succeed in the same session — is evidence of a
**transient, plausibly session-level rate-limiting issue** (this pipeline
made well over a thousand yfinance requests today across the price-ingestion
commit and this triage), not a permanent data gap. A calmer, spread-out
retry on a later day could plausibly recover some of these 27. It would not
materially change the 98% verdict: even 100% recovery of all 27 is a small
fraction of the 576-ticker shortfall.

## Why the membership ledger ends 2025-05-17, and whether it can be extended

- **qzzcl (primary source):** last repository commit 2024-11-13; no dated
  snapshot newer than the April 2023 file used here has ever been
  published. Dead as a primary source since 2023-03-20, independent of when
  this pipeline runs.
- **leandroloi (secondary source):** last repository commit
  `2025-06-18T12:46:28Z`, confirmed live against the GitHub API during this
  triage — identical to the commit already used to build the ledger. No
  newer upstream data exists.
- A related precision note: leandroloi's raw file's last **row** is
  2025-06-18, one month later than the reported `coverageEnd` of
  2025-05-17. This is not missing data — `changed_snapshots()` in
  `engine/build_sp500_pit_free_membership.py` intentionally collapses a
  trailing snapshot that re-confirms an unchanged roster, keeping only the
  last real transition date. 2025-06-18 is a later re-confirmation the
  ledger doesn't currently credit — a real, minor precision gap worth fixing
  in that builder, but **not done here** (out of this triage's scope).
- **Extending to today without changing methodology: not possible.** Both
  upstream sources are exhausted; the only way to reach 2026-08-31 without
  waiting for the maintainers is for this pipeline to run its own live
  Wikipedia-membership scrape (the same technique leandroloi's own
  `sp500.py` uses) — which introduces a **third, self-maintained** data
  source into a design deliberately built and disclosed as two
  independently-sourced, cross-checked trackers. That's a methodology
  change requiring its own decision, not something to do silently.

## Two bugs found and fixed while building this triage

Both are disclosed here for transparency, since they materially changed
early (wrong) results before being caught by inspection:

1. **False identity match**: an early version treated a fetchable
   stripped-suffix candidate as "resolved." Caught by the CPQ→Canadian
   Pacific Railway counterexample; fixed by never accepting data-existence
   alone as identity proof (see above).
2. **Still-open tenures misclassified as removed**: `intervals.csv` tenures
   that survive the qzzcl→leandroloi source handoff are written with a
   concrete `effective_end` equal to the ledger's own coverage end (not an
   empty/`None` value), by design in
   `engine/build_sp500_pit_free_membership.py`. An early classifier
   treated any concrete end date as a real removal, which put BK, HES, IPG,
   K, MMC, and 22 other obviously-current securities into the
   bankruptcy/delisting bucket. Caught by inspection (these are extremely
   liquid, unmistakably active names) and fixed by treating
   `end_date == ledger_end` as still-open, matching the survivorship-bias
   check's already-correct handling of the same boundary.

## Where to look

- `data/sp500_pit_free/audits/missing_price_triage.json` — full structured result
- `data/sp500_pit_free/audits/missing_price_triage_by_ticker.csv` — one row per missing/partial ticker: category, evidence, required and missing rebalance dates
- `data/sp500_pit_free/audits/missing_price_triage_by_date.csv` — one row per rebalance date: missing-member count and exact tickers
- `data/sp500_pit_free/audits/missing_price_triage_required_dates.csv` — long-format (ticker, date) required-membership pairs
- `data/sp500_pit_free/audits/coverage_by_date_after_resolution.csv` / `coverage_by_year_after_resolution.csv` — identical to the pre-existing coverage tables by construction (zero verified resolutions to apply)
