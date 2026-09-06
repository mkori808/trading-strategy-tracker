# Hypothesis backlog breadth/MDA screen

Thresholds: MDA ceiling 4.0%/yr, event-count floor 150 (event-driven candidates only).

| Rank | Candidate | Barrier | Bets/yr | Best-case MDA %/yr | Verdict | Cause if DEAD |
|---|---|---|---:|---:|---|---|
| 1 | Sub-$300M cap cross-sectional anomalies | CAPACITY | 24132 | 0.91 | **VIABLE** | — |
| 2 | Sub-$5 stocks excluded by fund charters | MANDATE | 390 | 1.59 | **VIABLE** | — |
| 3 | Small merger arb below institutional minimum deal size | CAPACITY | 161.44 | 2.29 | **VIABLE** | — |
| 4 | Distressed / post-reorg equity | MANDATE/CAPACITY | 106.21 | 2.82 | **VIABLE** | — |
| 5 | Index addition / forced buying | MANDATE | 24.53 | 5.87 | **MARGINAL** | — |
| 6 | Spinoff shares dumped by ineligible holders | MANDATE | 20.0 | 6.50 | **MARGINAL** | — |
| 7 | Tax-loss selling reversal (December) | MANDATE/OPS | 82 | 8.15 | **DEAD** | breadth (clears the event-count floor but MDA still exceeds the ceiling) |
| 8 | Odd-lot tender provisions | OPERATIONS | 0.0 | — | **DATA_BLOCKED** | data availability |

## Sub-$300M cap cross-sectional anomalies -- VIABLE

- Barrier: CAPACITY
- Observation definition: Cross-sectional: securities with marketcap < $300M, monthly rebalance
- Raw count: 5514 securities market-wide with marketcap on 2026-09-03
- Usable count: 2011 under $300M (36.5%)
- Independence assumption: Two variants: rho=0.5 (project standard, but see note) and rho=0.15 (sensitivity -- 0.5 was calibrated on a 5-name mega-cap anchor and is not obviously the right assumption for a ~2000-name small/micro-cap cross-section, where idiosyncratic risk is proportionally much larger; reported as a range, not resolved).

| Design | Bets/yr | MDA %/yr |
|---|---:|---:|
| sub300m_cap_cross_sectional_standard_corr | 760.84 | 1.05 |
| sub300m_cap_cross_sectional_lower_corr_sensitivity | 1387.49 | 0.91 |

MEASURED, single-day market-wide snapshot (2026-09-03): 2011 of 5514 tracked securities. NOT measured: whether this count is stable across the full 28.5-year history (assumed comparable order of magnitude here; small/micro-cap listing counts DO vary with market cycles and this was not checked historically). Highest single-day raw breadth of any candidate in this pass by a wide margin.

## Sub-$5 stocks excluded by fund charters -- VIABLE

- Barrier: MANDATE
- Observation definition: Cross-sectional: securities crossing the $5 close-price threshold, monthly monitoring
- Raw count: 6310 priced securities market-wide on 2026-09-03
- Usable count: 1561 under $5 today (24.7%); ~390/yr assumed crossing
- Independence assumption: rho=0.5 (project standard) only -- see approximation caveats below before trusting this number.

| Design | Bets/yr | MDA %/yr |
|---|---:|---:|
| sub5_dollar_cross_sectional_standard_corr | 334.71 | 1.59 |

HEAVILY APPROXIMATE, weakest-evidence screen in this pass. MEASURED: a single-day market-wide snapshot (2026-09-03) found 1561 of 6310 tracked securities (includes ETFs, ADRs, SPAC units -- NOT filtered to common stock, no join against tickers.category attempted this pass) closing under $5. NOT MEASURED: the actual annual rate of stocks CROSSING the threshold (the tradable event), which is what the hypothesis needs and what a real screen would require a multi-thousand-ticker historical daily scan to get. The 25% annual-crossing-fraction and monthly-rebalance cadence are assumptions, not data. Treat this row's MDA as illustrative of order of magnitude only.

## Small merger arb below institutional minimum deal size -- VIABLE

- Barrier: CAPACITY
- Observation definition: Event: market-wide actions table, action=acquisitionof, deal value < $500M, full history 1998-2026
- Raw count: 8266
- Usable count: 4601
- Independence assumption: Three variants: rho=0.0, rho=0.5 (project standard), and rho=0.2 (sensitivity only, reflecting the conventional view that merger-arb completion risk is largely deal-specific rather than market-correlated) -- reported separately, never substituted for the standard case.

| Design | Bets/yr | MDA %/yr |
|---|---:|---:|
| merger_arb_small_deal_positions1_rho0 | 161.44 | 3.01 |
| merger_arb_small_deal_positions1_standard_corr | 161.44 | 2.29 |
| merger_arb_small_deal_positions1_low_corr_sensitivity | 161.44 | 2.60 |

acquisitionof.value is the vendor-reported total deal value in $M, confirmed against named real 2025 examples (e.g. Roche/Poseida $925.9M). 6 of 8266 deals had no value reported and were excluded from sizing. 8260 sized deals; 4601 (55.7%) below $500M, median deal size across all sized deals was measured separately at ~$377M. This table is market-wide, not S&P-500-limited -- deliberately, since 'below institutional minimum deal size' by construction targets sub-large-cap acquirers/targets the S&P 500 roster would mostly exclude.

## Distressed / post-reorg equity -- VIABLE

- Barrier: MANDATE/CAPACITY
- Observation definition: Event: market-wide actions table, action=bankruptcyliquidation, full history 1998-2026
- Raw count: 3334
- Usable count: 3027
- Independence assumption: Two rho variants reported (0.0, 0.5 standard), same positions=1 caveat.

| Design | Bets/yr | MDA %/yr |
|---|---:|---:|
| distressed_postreorg_positions1_rho0 | 106.21 | 3.71 |
| distressed_postreorg_positions1_standard_corr | 106.21 | 2.82 |

Raw count 3334 includes 307 SPAC-name-heuristic matches (SPAC trust liquidations are a different, much more common phenomenon than an operating-company Chapter 11 exit and were excluded). REMAINING DATA-SHAPE CAVEAT, not resolved by this screen: 'bankruptcyliquidation' rows have no contraticker linking an old, cancelled security to a NEW post-reorg ticker -- this action type most plausibly marks the OLD equity's cancellation, not the emergence event the hypothesis actually needs (shares of the reorganized company that a mandate-constrained holder can't yet hold). The count below is therefore an UPPER BOUND on usable events, not a confirmed usable count -- some fraction of these are pure zero-recovery liquidations with no continuing equity to buy at all.

## Index addition / forced buying -- MARGINAL

- Barrier: MANDATE
- Observation definition: Event: S&P 500 sp500-table action=added, full history 1998-2026
- Raw count: 699
- Usable count: 699
- Independence assumption: Two rho variants reported (0.0, 0.5 standard); at positions=1 rho only affects screen_design's own internal calibration anchor, not this design's bet count (pinned behavior, see tests/test_engine/test_index_deletion_screen.py).

| Design | Bets/yr | MDA %/yr |
|---|---:|---:|
| index_addition_positions1_rho0 | 24.53 | 7.73 |
| index_addition_positions1_standard_corr | 24.53 | 5.87 |

Unlike deletion, NO reason-based filtering is applied: by definition every 'added' event names a security that begins trading in the index and continues trading afterward (that is what 'added' means) -- there is no acquired/merged-style subset that stops trading. Reason breakdown for context only: {'acquired_or_merged': 370, 'market_cap_change': 184, 'spinoff': 73, 'other': 62, 'bankruptcy': 10}.

## Spinoff shares dumped by ineligible holders -- MARGINAL

- Barrier: MANDATE
- Observation definition: Event: market-wide actions table, action=spinoff, full history 1998-2026
- Raw count: 570
- Usable count: 570
- Independence assumption: Two rho variants reported (0.0, 0.5 standard), same positions=1 caveat as index addition.

| Design | Bets/yr | MDA %/yr |
|---|---:|---:|
| spinoff_dumping_positions1_rho0 | 20.00 | 8.56 |
| spinoff_dumping_positions1_standard_corr | 20.00 | 6.50 |

MARKET-WIDE count (not restricted to S&P 500 parents), a materially larger and more correct population than the backlog's original 73-event S&P-500-only estimate -- the forced-selling mechanism (holders whose mandate can't retain a newly spun-off, often small-cap security) applies to any spinoff with a widely-held parent, not just S&P 500 constituents. 'spunofffrom' (the parent-side mirror action) independently confirmed the identical count (570), consistent with one row per event per side.

## Tax-loss selling reversal (December) -- DEAD

- Barrier: MANDATE/OPS
- Observation definition: Cross-sectional: (S&P-500-constituent, year) pairs where closeadj return from Jan 1 to Nov 1 < -20%, one trade per qualifying stock per year
- Raw count: ~503 current S&P 500 constituents x 4 sampled years
- Usable count: ~82/yr (extrapolated from a 63-ticker sample)
- Independence assumption: Two variants: rho=0.5 (project standard) and rho=0.7 (sensitivity, reflecting measured extreme year-to-year clustering -- loser rate ranged 1.6% to 46.0% across just 4 sampled years, i.e. most 'losers' in a given year are largely the same macro bet).

| Design | Bets/yr | MDA %/yr |
|---|---:|---:|
| tax_loss_selling_cross_sectional_standard_corr | 12.73 | 8.15 |
| tax_loss_selling_cross_sectional_high_corr_sensitivity | 10.80 | 8.34 |

APPROXIMATION, not a full-universe measurement: sampled 63 of 503 CURRENT S&P 500 constituents (every 8th ticker alphabetically), measured Jan1-to-Nov1 return in (2022, 2023, 2024, 2025). Per-year loser rate: 2022=29/63 (46.0%), 2023=7/63 (11.1%), 2024=4/63 (6.3%), 2025=1/63 (1.6%). Average 16.3%/yr extrapolated to the full 503-name universe gives ~82 qualifying stocks/yr, ONE trade cycle per year (Dec-Jan window). Uses TODAY's S&P 500 roster as a proxy for the historical universe every year, which understates true breadth for this specific hypothesis (small/mid-caps outside the S&P 500 are the more classic tax-loss-selling candidates and are entirely excluded from this approximation) -- flagged as a conservative undercount, not a full measurement.

## Odd-lot tender provisions -- DATA_BLOCKED

- Barrier: OPERATIONS
- Observation definition: Event: company-initiated odd-lot tender offers
- Raw count: 0 (no matching action code found)
- Usable count: 0
- Independence assumption: N/A -- no data source identified.

Empirically confirmed absent, not assumed: the full distinct-action-type inventory of Sharadar's actions table, checked over both a 1-month and a full 1-year (2025) market-wide sample (44,180 rows), contains no tender-offer or odd-lot-specific code. Full observed set: dividend, listed, delisted, split, tickerchangefrom/to, relation, acquisitionby/of, namechangefrom/to, acquisitioncash, sicchangefrom/to, spacunitseparation, regulatorydelisting, bankruptcyliquidation, acquisitionstock, spacmerger, adrratiosplit, spinoff, spunofffrom, voluntarydelisting, spinoffdividend, acquisitionelectstock, acquisitionelectcash, mergerfrom/to. No other Sharadar table probed this session (tickers, stocks, daily, sp500, events) carries tender-offer structure either. `events` (SEC 8-K eventcodes) was NOT decoded this session and could theoretically contain a tender code, but that is unconfirmed, not a basis for a number.
