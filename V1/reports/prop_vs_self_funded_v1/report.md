# Optimized DM: Prop versus Self-Funded Capital Efficiency v1

**Classification: depends on available capital. Historical evidence remains inconclusive for a live-capital allocation.**

The strategy, execution owner, prop rules, and prior prop artifacts are unchanged. Comparator B is omitted because no legitimate personal stop/restart rule was preregistered.

## Same-exposure comparison

| Operating point | Prop expected net | Prop median | Prop annual breach | Self-funded capital | Self-funded expected | Self-funded median | Self-funded annual loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.20x / trailing to breakeven | $2,949 | $2,766 | 3.7% | $20,000 | $12,267 | $12,505 | 0.3% |
| 0.25x / static | $5,216 | $5,183 | 2.9% | $25,000 | $15,334 | $15,631 | 0.3% |

## $35,000 target frontier

Accounts are identical and perfectly correlated. Adding accounts scales dollars, not independent bets; breach probability therefore does not diversify.

| Point | Structure | Accounts | Exposure | Expected net | Median net | P(net ≥ $35k) | Capital committed |
|---|---|---:|---:|---:|---:|---:|---:|
| 020_trailing | prop | 1 | $20,000 | $2,949 | $2,766 | 0.0% | $519 |
| 020_trailing | self_funded_a | 1 | $20,000 | $12,267 | $12,505 | 0.0% | $20,000 |
| 020_trailing | prop | 5 | $100,000 | $14,745 | $13,831 | 8.8% | $2,594 |
| 020_trailing | self_funded_a | 5 | $100,000 | $61,336 | $62,525 | 87.7% | $100,000 |
| 020_trailing | prop | 10 | $200,000 | $29,490 | $27,662 | 41.3% | $5,188 |
| 020_trailing | self_funded_a | 10 | $200,000 | $122,671 | $125,049 | 96.6% | $200,000 |
| 025_static | prop | 1 | $25,000 | $5,216 | $5,183 | 0.0% | $514 |
| 025_static | self_funded_a | 1 | $25,000 | $15,334 | $15,631 | 0.0% | $25,000 |
| 025_static | prop | 5 | $125,000 | $26,081 | $25,913 | 32.6% | $2,572 |
| 025_static | self_funded_a | 5 | $125,000 | $76,670 | $78,156 | 92.5% | $125,000 |
| 025_static | prop | 10 | $250,000 | $52,163 | $51,825 | 65.2% | $5,145 |
| 025_static | self_funded_a | 10 | $250,000 | $153,339 | $156,312 | 97.7% | $250,000 |

## Interpretation

Self-funding is mechanically cleaner once sufficient own capital is available: no challenge/reset fees, payout split, or rule-based termination. Prop access commits only fees and can therefore be more capital-efficient for a capital-constrained trader, but the contractual stopout path remains and multiple identical accounts do not diversify it.

Daily OHLC historical breach tests remain approximate. The separate five-minute prospective ledger is the authoritative path-evidence upgrade and starts without backfill.

No scale or account count is selected by this report.

## Break-even capital and downside

The profit-equivalence capital below answers how much self-funded capital would have the same *expected* profit as one prop account at the simulated per-dollar return. It does not match exposure. Matching exposure still requires the full $20,000 or $25,000.

| Point | Profit-equivalence own capital | Own capital for $35k expected profit | Self-funded P05 profit | P99 max drawdown | P(max DD at least $6k) |
|---|---:|---:|---:|---:|---:|
| 020_trailing | $4,808 | $57,063 | $4,529 | $7,353 | 4.5% |
| 025_static | $8,504 | $57,063 | $5,661 | $9,191 | 18.6% |

## Approved sensitivities

| Point | Cost calibration | Prop rule | Expected prop net | Annual breach |
|---|---|---|---:|---:|
| 020_trailing | observed execution overlay | static | $2,958 | 0.6% |
| 020_trailing | observed execution overlay | trailing to breakeven | $2,949 | 3.7% |
| 025_static | observed execution overlay | static | $5,216 | 2.9% |
| 025_static | observed execution overlay | trailing to breakeven | $5,177 | 15.0% |
| 020_trailing | registered costs only | static | $3,228 | 0.5% |
| 020_trailing | registered costs only | trailing to breakeven | $3,218 | 3.5% |
| 025_static | registered costs only | static | $5,576 | 2.7% |
| 025_static | registered costs only | trailing to breakeven | $5,535 | 14.4% |

## Infrastructure audit

| Requirement | Status | Active | Remaining gap |
|---|---|---:|---|
| Frozen historical prop simulation | verified | no | none |
| Prop fees, payout split, challenge/funded transitions, reset lifecycle | implemented_and_tested | yes | none |
| Observed slippage and partial-fill calibration | calibrated | yes | Aggregate approximation, not an order-level historical replay. |
| Actual Alpaca account-equity stream | collector_active_not_started | yes | No backfill; closed/offline intervals remain blank. |
| Locked strategy fingerprint and one-account ownership | verified | yes | none |
| Historical strategy return/exposure series | available_selection_contaminated | no | One selected development year; not independent forward evidence. |
| Intraday breach path | approximate_historical_prospective_pending | yes | Daily bars cannot identify cross-name simultaneity or high/low ordering. |
| Self-funded historical comparator | implemented_by_this_study | no | none |
| Self-funded prospective ledger | implemented_not_started | yes | Must accumulate strictly prospectively. |
| Correlated 1-to-10 account capital-efficiency frontier | implemented_by_this_study | no | Perfect correlation supplies no diversification. |
| Self-funded stop/restart comparator B | omitted | no | No legitimate preregistered restart rule exists; none was invented. |

## Evidence separation

Historical results are the selected 250-session development sample plus 5,000 synthetic moving-block paths. Prospective prop and self-funded ledgers consume the actual parent brokerage mark stream, remain append-only, and are not pooled with historical results. All referenced frozen artifact hashes were verified unchanged before and after this run.
