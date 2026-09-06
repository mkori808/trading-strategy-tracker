# Capability registry and hypothesis data readiness

> Capability-only diagnostic: no strategies, returns, MDA, or holdouts were inspected.

## Capability registry

| Capability | Status | Safe use | Unsafe use |
|---|---|---|---|
| `daily_prices` | **SUPPORTED** | Daily price studies with identity-safe ticker windows | Blind ticker stitching |
| `adjusted_prices` | **SUPPORTED_WITH_LIMITATIONS** | Reconstructed adjusted close with action audit | Using vendor closeadj as unquestioned canonical series |
| `unadjusted_prices` | **SUPPORTED** | Raw execution-price and terminal-price comparison | Treating raw prices as total return |
| `active_security_universe` | **SUPPORTED_WITH_LIMITATIONS** | Current and date-bounded active security research | Assuming current category/exchange is historical PIT |
| `delisted_price_history` | **SUPPORTED_WITH_LIMITATIONS** | Studies using final valid observation with incomplete-episode disclosure | Silently dropping delisted positions |
| `basic_corporate_actions` | **SUPPORTED** | Split/dividend adjustment and action diagnostics | Assuming every action encodes terminal consideration |
| `ticker_history` | **PARTIAL** | Ticker-scoped windows with independent checks | Ticker similarity or action pair as proof of continuity |
| `permanent_identity` | **PARTIAL** | Within-security joins after edge-case audit | Successor or reused-ticker stitching |
| `terminal_delisting_economics` | **PARTIAL** | Ordinary studies that exclude ambiguous episodes | Assuming last price equals shareholder terminal value |
| `successor_security_linkage` | **UNSUPPORTED** | None without external lineage evidence | Post-reorg continuity or successor returns |
| `complex_reorganization_chain` | **UNSUPPORTED** | None | Distressed/post-reorg portfolio accounting |
| `pit_fundamentals` | **SUPPORTED** | As-reported filing-date fundamentals | Back-projecting current snapshots |
| `market_cap_daily` | **SUPPORTED** | Percentile size universes within coverage | Fixed threshold before coverage start |
| `sp500_membership` | **SUPPORTED** | S&P 500 membership event studies | S&P 400/600 inference |
| `sp400_membership` | **UNSUPPORTED** | None | S&P 400 PIT studies |
| `sp600_membership` | **UNSUPPORTED** | None | S&P 600 PIT studies |
| `security_type_classification` | **PARTIAL** | Current snapshot screens | Historical common-stock eligibility without supplements |
| `exchange_listing_history` | **PARTIAL** | Date-bounded exchange studies with exclusions | Assuming current exchange applies historically |

## Backlog readiness

| Hypothesis | Readiness | Blockers | Limitations |
|---|---|---|---|
| `sp500_index_deletion` | **DATA_READY** | — | — |
| `microcap_momentum` | **DATA_READY** | — | — |
| `distressed_post_reorg` | **DATA_BLOCKED** | successor_security_linkage: UNSUPPORTED; complex_reorganization_chain: UNSUPPORTED | terminal_delisting_economics: PARTIAL |
| `sp400_migrations` | **DATA_BLOCKED** | sp400_membership: UNSUPPORTED | — |
| `sp600_migrations` | **DATA_BLOCKED** | sp600_membership: UNSUPPORTED | — |
| `ordinary_delisted_momentum` | **DATA_READY** | — | — |
| `ticker_change_momentum` | **DATA_READY_WITH_LIMITATIONS** | — | ticker_history: PARTIAL; permanent_identity: PARTIAL |

Adjusted prices retain the explicit carve-out: `reconstruct_adjustment.py` is canonical and vendor `closeadj` is cross-check only.
