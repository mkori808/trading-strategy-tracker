# Sharadar contract validation

Generated: `2026-09-06T01:23:42.919267+00:00`

> Diagnostic evidence only. This report did not publish a PIT bundle, alter a universe, or run a strategy.

## Verdict

**CONDITIONAL — paid entitlement active, but 1 symbol(s) need a per-symbol reconstruction (engine/reconstruct_adjustment.py) before vendor closeadj is trusted for them**

Detected entitlement 'full_history_or_broader'; measuredSymbols=29/29, discrepancies=['HON'], adjustmentStatus=PRESENT. Buying broader coverage does not resolve a same-window closeadj discrepancy -- see engine/reconstruct_adjustment.py and reports/sharadar_contract_validation/reconstruction/.

## Detected entitlement

- Tier: `full_history_or_broader`
- API error: `none`

The current-Dow classification is empirical: recent WMT data were available, a genuinely non-Dow active stock (TSLA) was denied, and pre-2021 WMT history returned no rows. The repository comparison roster is the older 29-name `dow_pit` roster, so former constituents INTC and DOW are outside the observed free entitlement.

| Probe | Symbol | Rows | Error |
|---|---|---:|---|
| `dowRecent` | WMT | 33 | none |
| `nonDowRecent` | TSLA | 33 | none |
| `dowPre2021` | WMT | 21 | none |

A zero-row or inaccessible capability is never counted as a pass.

## Contract field mapping

| Area | Required field | Status | Sharadar source | Reason |
|---|---|---|---|---|
| manifest | `survivorshipFree` | **AMBIGUOUS** | `tickers/actions/stocks population audit` | Provider claims 99%, not a contract-level completeness proof |
| manifest | `delistedSecuritiesIncluded` | **AMBIGUOUS** | `tickers.isdelisted + actions` | Requires inactive-population reconciliation |
| manifest | `delistingReturnsIncluded` | **MISSING** | `none` | No explicit delisting-return field was located |
| manifest | `tickerHistoryIncluded` | **AMBIGUOUS** | `tickers.permaticker + actions` | Actions are documented, but permanent-ID continuity was not empirically verified |
| manifest | `corporateActionsIncluded` | **PRESENT** | `actions` | Actions table supplies dated typed events |
| manifest | `pointInTimeSecurityTypes` | **MISSING** | `tickers.category` | Category is a current snapshot; no date-effective security-type history was located |
| manifest | `historicalVolumeIncluded` | **PRESENT** | `stocks.volume` | Daily split-adjusted volume is supplied |
| manifest | `source` | **PRESENT** | `API endpoint` | Sharadar is an attributable source |
| manifest | `snapshotId` | **AMBIGUOUS** | `lastupdated/fetch timestamp` | No immutable vendor snapshot identifier is documented; ingestion must hash/version the retrieved artifacts |
| manifest | `coverageStart` | **AMBIGUOUS** | `measured table dates` | Must be measured across every normalized artifact, not copied from marketing history |
| manifest | `coverageEnd` | **AMBIGUOUS** | `measured table dates` | Must be measured across every normalized artifact, not copied from marketing history |
| manifest | `schemaVersion` | **PRESENT** | `normalizer constant 1` | The local normalized bundle controls its schema version |
| manifest | `priceBasis` | **AMBIGUOUS** | `stocks.closeadj plus imputed adjusted O/H/L` | Target is total_return_adjusted_ohlcv; close is direct but adjusted O/H/L are imputed and as-of adjustment behavior must be accepted explicitly |
| security_history | `delisting_reason` | **AMBIGUOUS** | `actions.action/value` | Delisting reasons are action rows, not a directly observed complete normalized field |
| security_history | `effective_end` | **AMBIGUOUS** | `tickers.firstpricedate/lastpricedate + actions` | Price endpoints and action dates exist, but are not themselves complete ticker/listing effective intervals |
| security_history | `effective_start` | **AMBIGUOUS** | `tickers.firstpricedate/lastpricedate + actions` | Price endpoints and action dates exist, but are not themselves complete ticker/listing effective intervals |
| security_history | `exchange` | **AMBIGUOUS** | `tickers.exchange` | Latest exchange is present, but historical venue changes are not supplied |
| security_history | `is_acquired` | **AMBIGUOUS** | `actions.action/contraticker` | Acquisition events and counterparties exist, but normalized issuer-level coverage must be audited |
| security_history | `is_common_stock` | **AMBIGUOUS** | `tickers.category` | Category can classify the current security, but point-in-time security-type intervals were not located |
| security_history | `is_us_listed` | **AMBIGUOUS** | `tickers.exchange` | Exchange is current-only; Sharadar explicitly says historical primary-listing venues are unavailable |
| security_history | `known_at` | **MISSING** | `none` | No announcement/known-at timestamp is documented for identity or listing changes |
| security_history | `security_id` | **AMBIGUOUS** | `tickers.permaticker` | tickers.permaticker exists, but no accessible ticker-change lineage proved continuity |
| security_history | `security_type` | **AMBIGUOUS** | `tickers.category` | Category can classify the current security, but point-in-time security-type intervals were not located |
| security_history | `ticker` | **AMBIGUOUS** | `tickers.ticker + actions` | Current ticker exists and actions documents changes, but a complete date-effective interval must be reconstructed |
| daily | `Close` | **PRESENT** | `stocks.closeadj` | Fully split/dividend/spinoff-adjusted close is delivered directly |
| daily | `DelistingReturn` | **MISSING** | `none` | No explicit delisting-return field exists in stocks, actions, or daily |
| daily | `High` | **AMBIGUOUS** | `stocks.high * stocks.closeadj / stocks.close` | Sharadar supplies split-adjusted OHLC and fully adjusted close; total-return OHLC must be imputed |
| daily | `Low` | **AMBIGUOUS** | `stocks.low * stocks.closeadj / stocks.close` | Sharadar supplies split-adjusted OHLC and fully adjusted close; total-return OHLC must be imputed |
| daily | `Open` | **AMBIGUOUS** | `stocks.open * stocks.closeadj / stocks.close` | Sharadar supplies split-adjusted OHLC and fully adjusted close; total-return OHLC must be imputed |
| daily | `RawClose` | **PRESENT** | `stocks.closeunadj` | Documented unadjusted close |
| daily | `Volume` | **PRESENT** | `stocks.volume` | Directly documented/observed field |
| daily | `date` | **PRESENT** | `stocks.date` | Directly documented/observed field |
| daily | `security_id` | **AMBIGUOUS** | `stocks.ticker -> tickers.permaticker` | stocks is ticker-keyed; the join is safe only after ticker-change and ticker-reuse lineage tests pass |
| daily_market_cap | `MarketCap` | **PRESENT** | `daily.marketcap` | Sharadar's daily table delivers per-date market cap directly; conditionally mandatory in engine/pit_all_stocks.py only once a minimum-market-cap filter is requested (market_cap_available) |

## Tier capability

| Check | Status | Reason |
|---|---|---|
| `dow_recent_prices` | **PRESENT** | The detected entitlement exposes enough scope to run this check; result still requires validation |
| `pre_2021_history` | **PRESENT** | The detected entitlement exposes enough scope to run this check; result still requires validation |
| `non_dow_coverage` | **PRESENT** | The detected entitlement exposes enough scope to run this check; result still requires validation |
| `delisting_returns` | **UNTESTABLE-ON-THIS-TIER** | No explicit DelistingReturn field is exposed |
| `historical_delisted_population` | **PRESENT** | The detected entitlement exposes enough scope to run this check; result still requires validation |

## Adjustment-method semantics

Source: [Sharadar FAQ](https://sharadar.com/docs/faqs) and [stocks schema](https://sharadar.com/docs/stocks).

1. Unadjusted: `closeunadj` is delivered. O/H/L are imputed as split-adjusted O/H/L × `closeunadj / close`; tape volume is imputed as `volume × close / closeunadj`.
2. Split-adjusted: `open`, `high`, `low`, `close`, and `volume` are delivered directly.
3. Split/dividend/spinoff-adjusted: only `closeadj` is delivered. O/H/L are imputed as split-adjusted O/H/L × `closeadj / close`; volume remains split-adjusted and is not adjusted for dividends or spinoffs.

Empirical WMT 3-for-1 test: **PRESENT** — Sharadar split-adjusted history is retroactively adjusted for the later split even when the query ends before it; query bounds are not an as-of adjustment boundary.

## Dow close cross-check

| Symbol | Vendor rows | Overlap | Measured window | Median abs. % diff | Max abs. % diff | Latest abs. % diff | Status |
|---|---:|---:|---|---:|---:|---:|---|
| MMM | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.030474 | 0.537689 | 0.000001 | **PRESENT** |
| GS | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.467711 | 0.496297 | 0.000001 | **PRESENT** |
| NKE | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 1.062353 | 1.078338 | 0.000004 | **DISCREPANCY** |
| AXP | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.010722 | 0.026424 | 0.000001 | **PRESENT** |
| HD | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.763186 | 0.781490 | 0.000004 | **DISCREPANCY** |
| PG | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.024763 | 0.041089 | 0.000002 | **PRESENT** |
| AMGN | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.012094 | 0.592890 | 0.000003 | **PRESENT** |
| HON | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 3.236685 | 3.696962 | 0.000000 | **DISCREPANCY** |
| CRM | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.027982 | 0.028618 | 0.000004 | **PRESENT** |
| AAPL | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.005247 | 0.048900 | 0.000000 | **PRESENT** |
| INTC | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.008708 | 0.078339 | 0.000003 | **PRESENT** |
| TRV | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.004633 | 0.014266 | 0.000002 | **PRESENT** |
| BA | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.000002 | 0.284631 | 0.000000 | **PRESENT** |
| IBM | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.026863 | 0.165888 | 0.000000 | **PRESENT** |
| UNH | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.021581 | 0.036165 | 0.000004 | **PRESENT** |
| CAT | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.007728 | 0.046144 | 0.000000 | **PRESENT** |
| JNJ | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.011646 | 0.493298 | 0.000004 | **PRESENT** |
| VZ | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.024387 | 0.124738 | 0.000001 | **PRESENT** |
| CVX | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.021227 | 0.872719 | 0.000003 | **PRESENT** |
| JPM | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.016595 | 0.051098 | 0.000004 | **PRESENT** |
| V | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.005331 | 0.010984 | 0.000002 | **PRESENT** |
| CSCO | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.020076 | 0.037702 | 0.000003 | **PRESENT** |
| MCD | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.707987 | 0.718887 | 0.000001 | **DISCREPANCY** |
| KO | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.013950 | 0.031613 | 0.000000 | **PRESENT** |
| MRK | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.016033 | 0.037074 | 0.000001 | **PRESENT** |
| WMT | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.003074 | 0.240844 | 0.000001 | **PRESENT** |
| DOW | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 1.050681 | 1.160472 | 0.000002 | **DISCREPANCY** |
| MSFT | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.004279 | 0.197059 | 0.000002 | **PRESENT** |
| DIS | 1265 | 1265 | 2021-08-23 to 2026-09-04 | 0.011320 | 0.015495 | 0.000002 | **PRESENT** |

The comparison is reported per symbol. Any median divergence above 0.5% is named as a discrepancy and is not averaged away.

For all currently flagged symbols, the latest common close agrees within 0.5%. That time pattern is consistent with the two cached adjusted series having different corporate-action/dividend adjustment vintages; it does not prove that either historical series is wrong, so the discrepancies remain unresolved rather than being waived.

## Raw bundle fields versus normalized PIT contract

Sharadar's paid bundle does include the raw ingredients for much of this work: the official actions documentation lists ticker changes, listing and delisting dates, delisting reasons, acquisition counterparties, spinoffs, and relations between securities, while stocks provides deep daily history. The findings above use **MISSING** or **AMBIGUOUS** when the application cannot yet derive and validate the stricter normalized field (for example a date-effective interval with a known-at timestamp, or a reproducible terminal delisting return). This is a normalization/semantic gate, not a claim that the paid bundle contains no delisting or corporate-action records.

## Permanent identity

Status: **AMBIGUOUS**.

`permaticker` is documented as permanent, but the contract does not accept that claim until an accessible ticker-change lineage retains the same identifier. The current test found 0 ticker-change action rows and 0 verified examples.

## What one paid month would answer

- full history before the free tier's five-year boundary
- non-Dow active and inactive security coverage
- permaticker continuity across historical ticker changes and ticker reuse
- historical delisted-security population and delisting-reason coverage
- cash/stock acquisition and other terminal-value completeness
- whether an explicit or reproducible delisting return can satisfy DelistingReturn
- full-period daily market-cap coverage for size filters
- coverage and identity behavior around mergers, spinoffs, relistings, and OTC transitions

The paid tier still does not automatically pass the contract. In particular, absence of an explicit delisting-return field, current-only exchange/category metadata, and missing known-at timestamps may remain structural product gaps after purchase.

## Official documentation

- [stocks](https://sharadar.com/docs/stocks)
- [tickers](https://sharadar.com/docs/tickers)
- [actions](https://sharadar.com/docs/actions)
- [daily](https://sharadar.com/docs/daily)
- [faq](https://sharadar.com/docs/faqs)
