# Point-in-Time / Survivorship-Free Data Requirements and Acquisition Study

Status: implementation-ready provider-neutral contract and acquisition feasibility study  
Research date: 2026-08-22  
Scope: U.S. daily equity research; no dataset was purchased, downloaded, scraped, or installed

## Executive decision

Use institutional/academic CRSP access if Georgia Tech confirms that the user and this research use are eligible. CRSP is the best fit because permanent security identity, name/ticker history, corporate actions, daily prices/returns, and explicit delisting economics live in one internally consistent product. WRDS/CRSP pricing is sales-contact-only; Georgia Tech access could change the marginal data cost from institutional pricing to zero, but current Georgia Tech product entitlement, OMSA eligibility, bulk-download rights, retention, and this project's permitted use all require verification.

If academic CRSP access is unavailable, do **not** buy a comprehensive all-stocks feed yet. First request a Norgate Platinum trial/schema demonstration and a written answer about extracting `assetid`, point-in-time Dow/S&P membership, and adjustment metadata into the local normalized bundle. Its public personal price is $630/year and its Dow/S&P coverage is deep, so it is the most economical Tier A/B candidate. It is **not activation-ready by itself**: Norgate explicitly does not provide delisting reasons/returns, index announcement dates, prior ticker history, or raw corporate-action details, and its license requires deletion of source and derived data after lapse. A narrow Tier A/B purchase is worth considering only if terminal economics can be sourced or conservatively resolved under a preregistered policy and the license permits the intended normalization.

EODHD and Massive are useful price/reference APIs, not complete survivorship-free research bundles. They lack confirmed delisting-return/final-value and historical index-membership support. The existing open S&P membership reconstruction plus either API would retain dangerous identity and terminal-value gaps. No reviewed free source satisfies the minimum contract.

The recommended sequence is therefore:

1. Verify Georgia Tech WRDS access, the CRSP subscription component, OMSA eligibility, and export/retention terms.
2. If confirmed, build Tier A first from a narrow CRSP extract plus verified Dow membership, then reuse the same normalization for Tier B and Tier C.
3. If not confirmed, validate Norgate for Tier A/B through a trial and written licensing/field confirmation before spending money.
4. Defer Tier C until Tier A ingestion and audits prove that the additional research value justifies the much larger identity and quality-control surface.

## 1. What the code actually requires today

The declared 1996–2026 coverage in `universes/us_all_stocks_pit.json` is intent metadata. It is not evidence that data exists. At this writing `data/pit_us_all_stocks/` contains only a README; `manifest.json`, `security_history.parquet`, and daily Parquet are absent, so `inspect_dataset()` correctly returns `ready: false`.

### Current security-history contract

`engine/pit_all_stocks.py` requires one row per date-effective identity/status interval with these exact columns:

| Column | Current requirement and use |
| --- | --- |
| `security_id` | Required string-like permanent security key. It keys daily bars and is the trading-universe identity. Empty values are rejected. |
| `ticker` | Required display/mapping value. Multiple tickers per `security_id` are counted as ticker changes. |
| `effective_start` | Required date/datetime. Eligibility uses `effective_start < decision_time`. |
| `effective_end` | Required date/datetime. Eligibility uses `effective_end >= decision_time`. |
| `known_at` | Required date/datetime. Eligibility uses `known_at < decision_time`. |
| `is_us_listed` | Required boolean-like. Must be true for eligibility. |
| `is_common_stock` | Required boolean-like. Must be true for eligibility. |
| `security_type` | Required descriptor/provenance field. The loader does not branch on its value today. |
| `exchange` | Required descriptor/provenance field. The loader does not branch on its value today. |
| `is_acquired` | Required boolean-like. Used in integrity diagnostics, not eligibility. |
| `delisting_reason` | Required nullable value. Used to identify/count delisted securities and in diagnostics. |

The current engine does **not** require company/security name, CUSIP, FIGI, listing date as a separate field, active flag, delisting date as a separate field, predecessor/successor IDs, exchange-effective history outside the interval rows, or a vendor identifier. Those are useful target-contract fields below, not current loader requirements.

Current behavior that must not be overstated:

- `inspect_dataset()` checks required columns, a nonempty ID, at least two IDs, and at least one non-null `delisting_reason` in the entire bundle.
- It does not currently validate interval overlap, ticker reuse across different securities, duplicate rows, full daily-to-master joins, actual coverage against the manifest, terminal economics for every delisting, or `known_at` chronology.
- `ticker_at()` first applies effective and known-at conditions but has a fallback that can return the last effective ticker without enforcing `known_at`; that fallback is display-only, not eligibility.

### Current daily-price contract

Every Parquet fragment must contain:

| Column | Current requirement and use |
| --- | --- |
| `security_id` | Required join key to security history. |
| `date` | Required trading/session date. Filtered to the requested window. |
| `Open`, `High`, `Low`, `Close` | Required capitalized OHLC. `Open` is used for rebalance fills; `Close` is used for rankings and portfolio marks. |
| `RawClose` | Required unadjusted close. Used for the minimum-price and trailing dollar-volume filters and capacity calculations. |
| `Volume` | Required historical volume. Used with `RawClose` for ADV and capacity. |
| `DelistingReturn` | Required column; nullable on ordinary days. The README requires the source's return on the terminal record and says the adjusted series must include it exactly once. |

`MarketCap` is optional. If present, it must be historical and is selected strictly before the decision date. A strategy asking for a minimum market cap is rejected when this field is absent.

The current manifest requires `priceBasis = "total_return_adjusted_ohlcv"`; therefore the current loader expects adjusted OHLCV plus `RawClose`, not raw OHLCV plus a separate action engine. It passes only adjusted OHLCV into the cross-sectional backtester. The engine does not currently load separate split, dividend, merger, spin-off, cash-receivable, or adjustment-factor tables.

### Current manifest contract

`manifest.json` must contain:

```json
{
  "schemaVersion": 1,
  "source": "vendor/product",
  "snapshotId": "immutable export/version identifier",
  "coverageStart": "YYYY-MM-DD",
  "coverageEnd": "YYYY-MM-DD",
  "priceBasis": "total_return_adjusted_ohlcv",
  "survivorshipFree": true,
  "delistedSecuritiesIncluded": true,
  "delistingReturnsIncluded": true,
  "tickerHistoryIncluded": true,
  "corporateActionsIncluded": true,
  "pointInTimeSecurityTypes": true,
  "historicalVolumeIncluded": true
}
```

These booleans are assertions, not independent proof. The target quality gates later in this document are required before a real acquired bundle should be activated.

### Current universe-membership contracts

There are two different mechanisms:

1. **All stocks:** no index ledger is required. `load_eligibility_universe()` dynamically selects date-effective U.S.-listed common stocks from `security_history`, then requires adequate prior history, raw price, trailing raw-dollar volume, and optional historical market cap. This is a security-master eligibility universe, not an index reconstruction.
2. **Index replay:** `engine/universe_ledger.py` expects `data/universe_membership.json` records with `effectiveStart`, `effectiveEnd`, a nonempty `symbols` list, `source`, and `priceCoverageComplete: true`. `unfetchableOrDelisted` is accepted as disclosure, but blocks the strict audit. Intervals are inclusive at both ends. This legacy schedule is ticker-keyed and has no `known_at` field.

Specific state:

- `dow_pit` is a runnable `partial_reconstruction_static_execution_roster` of 29 symbols. It has no complete historical membership/security-master bundle and is not survivorship-free.
- `sp500_pit` points to `data/universe_membership.json#universes.sp500`. Membership replay exists, but the local audit finds 1,207 distinct historical tickers, only 778 fetchable, 429 unfetchable, 637 with incomplete tenure coverage, and 52 reused/ambiguous tickers. `priceCoverageComplete` is false and the universe remains disabled.
- The open S&P builder records effective snapshots and source provenance, but not announcement/knowable dates. It intentionally identifies itself as approximate and unofficial.

## 2. Minimum Viable Survivorship-Free Research Bundle

The smallest high-value bundle is daily, U.S. common-equity data with stable security identity and complete terminal economics. It does not require fundamentals.

### Required for activation

- One immutable `security_id` per traded share class/security, never ticker as key.
- Date-effective ticker, name, security type, listing venue/status, and common-stock eligibility history.
- Listing/downlisting/relisting/delisting intervals and all relevant inactive securities.
- Daily regular-session raw OHLCV for every eligible security and every membership tenure, including warmup.
- A canonical adjustment representation with splits, dividends, distributions, spin-offs, and other capital events applied exactly once.
- Explicit delisting/terminal economics: cash value, successor-security conversion, authoritative delisting return, or a clearly flagged unresolved state.
- Historical index membership intervals for index-specific research, with effective date and knowable/announcement date where available.
- Manifest provenance, immutable snapshot/version, field-level semantics, coverage, hashes, vendor/license, and normalization version.
- All strict validation gates in section 14 passing.

### Optional but useful now

- `company_name`, vendor ID, share-class FIGI, composite FIGI, CUSIP/NCUSIP where licensed, predecessor/successor IDs, merger exchange ratios, and payment dates.
- Historical shares outstanding/market cap for size filters and capacity studies.
- Historical sector classification in a **separate PIT table** for Sector-Relative Momentum.
- Explicit bid/ask or spread data for improved cost modeling.

### Not needed for this phase

Fundamentals, financial statements, analyst estimates, options, news, insider data, full intraday/tick data, and earnings announcements are not needed for DM/MRM or price-only daily momentum. A PIT earnings-announcement ledger and auction/intraday bars remain separate acquisitions; buying the security-master bundle does not solve them.

## 3. Three independent acquisition tiers

### Tier A — long-history Dow / DM-MRM

Minimum: every security that was a Dow constituent during the research window; permanent IDs; complete membership intervals; daily OHLCV plus warmup; corporate-action consistency; delisting/merger terminal economics; and a cash/risk-free benchmark. All U.S. equities are unnecessary.

A 1996–2026 daily bundle for roughly the Dow-ever-member set should deliver about 360 monthly decisions at a tiny fraction of Tier C storage and identity work. It can support a fully PIT Dow history independently of all-stocks if membership and terminal economics are complete. This is the best first engineering target.

The catch is data composition: Norgate confirms Dow membership back to January 1950, a stable `assetid`, delisted names, and daily history, but does not provide index announcement dates or delisting returns/reasons. CRSP supplies superior identity and terminal economics, while Dow membership availability/package must be verified. A narrow two-source build is feasible, but joins must be on stable IDs or carefully audited ticker/name/date crosswalks—not ticker alone.

### Tier B — survivorship-free S&P 500

Minimum: complete date-effective S&P 500 membership, all 1,207 historical tickers currently found by the local ledger (resolved to permanent security IDs), every member's daily history through its entire tenure and terminal event, and warmup before entry. Official S&P SPICE advertises constituents from 1964 and adds/drops from 1989. Norgate advertises S&P 500 constituents from March 1957. The existing open ledger may remain useful as reconciliation evidence but cannot be the sole identity/price source.

Tier B is substantially smaller than all stocks but materially harder than Dow because membership churn, symbol reuse, share classes, and hundreds of delisted/unfetchable local names amplify join risk.

### Tier C — U.S. All Stocks

Minimum: all qualifying U.S.-listed common-stock share classes—not today's survivors—across the requested period, with the full security master, daily OHLCV/returns, corporate actions, listing transitions, and terminal economics. No index membership is required for the base eligibility universe. Historical market cap is optional under current strategy defaults but becomes required for cap-filtered research.

This tier unlocks the most research, but it is also the only tier where completeness cannot be plausibly checked ticker-by-ticker by hand. It demands automated identity, price, corporate-action, and delisting audits.

## 4. Blocked research mapped to minimum data

| Research project | Current blocker | Minimum data needed | Ideal data | Useful history | Priority |
| --- | --- | --- | --- | --- | --- |
| Dual Momentum Conditional Edge | Too few independent monthly clusters | Tier A membership + identity + terminal-aware daily bars | 25–30 years, known-at membership, CRSP-grade returns | 20 years workable; 25–30 preferred | Highest |
| Market-Residual Momentum Conditional Edge | Same effective-N limitation | Same Tier A bundle plus benchmark/market residual inputs | Same source/semantics for stocks and market benchmark | 20–30 years | Highest |
| DM/MRM historical portfolio validation | Five-year window covers too few regimes | Tier A daily bars and membership for both frozen sleeves | 25–30 years spanning dot-com, GFC, COVID, inflation/rate shock | At least 20 years | Highest |
| S&P 500 momentum | Membership exists but 429 names are unfetchable; 637 tenures incomplete; 52 ticker identities ambiguous | Tier B permanent-ID price/security history plus complete membership | Official membership known-at dates + CRSP-grade security data | 20–30 years | High |
| U.S. all-stock strategies | Core bundle absent | Tier C | Tier C plus historical market cap | 20–30 years | Medium after Tier A |
| Sector-Relative Momentum | No PIT sector ledger | Tier C or B **plus** date-effective sector classification | GICS/RBICS history with effective and known-at dates | 15–25 years | Medium |
| Earnings-related research | Installed event coverage is selective/not PIT | Separate PIT earnings announcement ledger keyed to security ID, with announcement timestamp and revisions | Actual/estimate/surprise plus after-hours timing | 10–20 years | Separate purchase |
| Overnight strategies | Daily-bar execution cannot model observe-open/fill-same-open honestly | Opening-auction or intraday bars and an executable timing convention | Auction prints/imbalance and quote data | Multiple regimes | Separate purchase |
| Intraday strategies | Daily PIT bundle has no intraday microstructure | Intraday bars/trades/quotes for the exact universe | SIP trades/quotes with conditions and corporate actions | Strategy-specific | Separate purchase |

Longer DM/MRM history would improve confidence in return correlation, the consistency of volatility-scaling benefits, drawdown diversification, and Sharpe improvement. It will not create many independent crises: even 30 years includes only a small number of major drawdown/inflation/liquidity regimes.

## 5. How much monthly history helps

| Years | Monthly decisions | Discovery (60%) | Validation (20%) | Holdout (20%) |
| ---: | ---: | ---: | ---: | ---: |
| 5 | ~60 | ~36 | ~12 | ~12 |
| 10 | ~120 | ~72 | ~24 | ~24 |
| 15 | ~180 | ~108 | ~36 | ~36 |
| 20 | ~240 | ~144 | ~48 | ~48 |
| 25 | ~300 | ~180 | ~60 | ~60 |
| 30 | ~360 | ~216 | ~72 | ~72 |

The split mirrors the current 60/20/20 chronological Conditional Edge protocol. It illustrates why ten years is meaningfully better than five yet still weak: a validation slice has only about 24 monthly clusters. At 20 years, validation and holdout each have about 48 raw months; at 30 years, about 72.

Tail conditioning is thinner still. In a 30-year discovery slice, the worst decile is roughly 22 months and the worst 5% roughly 11; each 72-month validation/holdout slice has only about 7 worst-decile and 3–4 worst-5% observations before missing data, purging, or overlapping holding periods. In 20 years those validation counts are roughly 5 and 2–3. Conditional tail claims therefore remain exploratory unless conditions pool economically similar states or the design changes prospectively.

None of these counts are fully independent. Monthly momentum portfolios overlap through lookbacks and holdings; market states are serially correlated; cross-sectional securities share the same market shock; and decades contain far fewer independent regimes than months. The meaningful unit is the rebalance/overlap cluster or regime, not the number of stock rows. Practical conclusion: 10 years is still weak, 20 years is workable for broad effects, and 25–30 years is preferable for discovery/validation/holdout and portfolio-diversification evidence.

## 6. Provider verification matrix

Legend: **C** Confirmed by provider/official documentation; **L** likely but not sufficiently documented for activation; **U** unclear/needs written confirmation; **N** no.

| Source | Delisted | Ticker history | Stable ID | Corporate actions | Delisting return/final value | Historical index membership | Coverage / exchanges | Daily OHLCV | Local storage / limits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CRSP U.S. Stock via WRDS | C | C | C: PERMNO | C | C, including missing-value codes and successor pointers | U/package-dependent for Dow; S&P portfolio files require entitlement check | C: NYSE/AMEX/Nasdaq, daily from 1925 | C | C download-to-PC capability; exact institution retention/bulk terms U |
| Norgate Platinum/Diamond | C, extensive but not claimed complete in earliest decades | N as a separate history; old prices are rolled under current symbol | C: `assetid` in supported environments | C in adjusted series; N for raw event details | N; recommends final available bar approximation | C: Dow from 1950, S&P 500 from 1957; no announcement dates | C major U.S. exchanges; Platinum 1990+, Diamond 1950+ | C | C proprietary local DB; exported/source/derived stock data must be deleted after lapse |
| S&P DJI SPICE/direct feed | Not a price source | C for membership changes | C identifiers in constituent packages, exact scheme U | C for index-composition events | N as equity terminal-price source | C: S&P 500 constituents from 1964, adds/drops from 1989; Dow availability likely but must be quoted | Index/membership product | N for complete member OHLCV | Downloads/feeds C; price and retention sales-agreement-only |
| Massive Stocks Advanced | C active flag and delisted date | C via experimental ticker-events endpoint | C: composite/share-class FIGI where populated | C splits/dividends; flat OHLCV itself unadjusted | N/undocumented | N | C all U.S. tickers; flat files back to 2003; plan says 20+ years | C | C flat-file download; personal noncommercial license; retention U |
| EODHD EOD All World | C ticker list and historical EOD; pre-2018 delisted names have EOD only | C symbol-change endpoint for U.S., completeness U | U; no documented permanent security master sufficient for this contract | C splits/dividends | N/undocumented | N | C 30+ years claimed; precise per-name completeness U | C | API 100k calls/day on paid personal plans; retention terms U |
| Existing open S&P reconstruction + Yahoo cache | L membership, approximate/unofficial | N | N | L adjusted prices only | N | L effective membership; no known-at | Local audit shows major gaps | L for survivors/fetchable names | $0, but fails activation |
| OpenFIGI (supplement only) | Identifier survives delisting | Current mapping, not a historical ticker ledger | C, FIGI is permanent | Identifier continuity C; no economic actions | N | N | Broad identifier coverage, exact historical completeness U | N | Free API; 25 requests/6 sec and 100 jobs/request with key |

Primary evidence:

- [CRSP research products and PERMNO](https://www.crsp.org/research/), [WRDS CRSP database fields](https://wrds-www.wharton.upenn.edu/demo/crsp/form/), and [CRSP delisting-return definitions](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/).
- [Norgate package content and pricing](https://norgatedata.com/stockmarketpackages.php), [identity/membership/terminal-value FAQ](https://norgatedata.com/data-package-faq.php), and [license/retention terms](https://norgatedata.com/subscribe/eula.php).
- [S&P DJI data licensing](https://www.spglobal.com/spdji/en/about-us/data-index-licensing/) and [SPICE historical coverage](https://www.spglobal.com/spdji/en/landing/topic/spice/).
- [Massive ticker/reference semantics](https://massive.com/docs/rest/stocks/tickers), [flat-file adjustment semantics](https://massive.com/docs/flat-files/stocks/overview), and [pricing](https://massive.com/pricing?product=stocks).
- [EODHD delisted coverage](https://eodhd.com/financial-apis/delisted-stock-companies-data-2), [pricing/limits](https://eodhd.com/pricing), and [bulk EOD behavior](https://eodhd.com/financial-apis/bulk-api-eod-splits-dividends).
- [OpenFIGI corporate-action allocation rules](https://www.openfigi.com/docs/figi-allocation-rules.pdf) and [API limits](https://www.openfigi.com/api/documentation).

## 7. Cost and licensing

| Source | Personal / academic cost | Professional / commercial cost | Important restrictions |
| --- | --- | --- | --- |
| WRDS/CRSP | Sales inquiry; possibly $0 marginal through an entitled university | Sales inquiry | Academic WRDS use is noncommercial; institution/vendor agreement governs downloads, retention, publication, and project use. |
| Norgate U.S. Platinum | $346.50/6 months or $630/year | No commercial license offered publicly | Individual personal/academic use only; two computers; proprietary Windows database; stock source and derived data deleted after lapse. |
| Norgate U.S. Diamond | $433.13/6 months or $787.50/year | No commercial license offered publicly | Same; longer history than needed for the first 1996+ bundle. |
| S&P DJI SPICE/feed | Sales inquiry required | Sales inquiry required | Constituent/index data requires subscription/license; delivery and local retention depend on contract. |
| Massive Stocks Advanced | $199/month individual; annual discount advertised but exact checkout total should be verified | Business pricing inquiry | Individual data is personal/non-business/noncommercial; redistribution prohibited; exact research storage/retention must be confirmed. |
| EODHD EOD All World | $19.99/month or $199/year; provider advertises 50% student discount for first 12 months after proof | Internal commercial use $399/month or $3,990/year | Cheap API access does not supply the missing identity/index/terminal contract. Commercial server downloads require commercial terms. |
| Open reconstruction/OpenFIGI | $0 | $0 for open identifiers; source-specific terms still apply | Engineering and unresolved-data risk dominate monetary cost. |

No one-time historical-download price was publicly found for the reviewed complete candidates. Norgate explicitly does not sell history as a stand-alone item. CRSP/WRDS and S&P are quote-only.

## 8. Georgia Tech access finding

There is credible historical evidence that Georgia Tech has used a WRDS access portal: a Georgia Tech FY2017 technology-fee report lists a Scheller “College/Campus Database, Software & Analytical Tools (through WRDS access portal)” expenditure. This is evidence of institutional WRDS use in 2017, **not confirmation of a current 2026 subscription, CRSP entitlement, OMSA eligibility, or export rights**.

WRDS currently states that master's accounts are available to full-time master's students of subscribing institutions, with web/SSH/FTP access but no permanent WRDS disk; class accounts are also possible. Whether Georgia Tech treats OMSA enrollment as eligible full-time master's access is not public. WRDS also says users can download data to their PCs, while its terms make the institution's subscription agreement controlling.

Required verification path:

1. Search Georgia Tech Library's authenticated A–Z databases for WRDS, then log in through the institution link—not a personal WRDS signup.
2. Ask the Georgia Tech WRDS representative/Scheller IT or library: “Does the current subscription include CRSP U.S. Stock daily data and delisting information?”
3. Ask whether OMSA students can create a master's/research account, whether summer access applies, and whether a faculty sponsor/class code is required.
4. Describe this as personal, noncommercial academic research and ask whether a 1996–present filtered local Parquet extract may be retained on the student's machine/OneDrive and for how long.
5. Ask whether results/code can be retained after graduation and whether source data must be deleted when affiliation ends.

Until those answers are obtained, dashboard/docs must say **Requires verification**, not “Georgia Tech access available.” Relevant public references are [WRDS account types](https://wrds-www.wharton.upenn.edu/pages/about/wrds-account-types/), [WRDS delivery options and quote-only pricing](https://wrds-www.wharton.upenn.edu/pages/about/what-wrds/), and [WRDS terms](https://wrds-www.wharton.upenn.edu/users/tou/).

## 9. Tier-specific source scores

Scores: 5 best fit, 1 poor fit. A high score is a research fit, not permission to activate without the contract gates.

### Tier A — Dow / DM-MRM

| Source/architecture | Data fit | Cost | Integration | Quality risk | Overall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Georgia Tech WRDS/CRSP + verified Dow membership | 5 | 5 if entitled | 4 | 5 | **5.0 preferred** |
| Norgate Platinum alone | 4 | 4 | 4 | 2 (no terminal returns/known-at) | **3.5 conditional** |
| Norgate membership + CRSP terminal/price data | 5 | 3 | 2 | 4 after ID audit | **4.0** |
| S&P DJI direct membership + CRSP prices | 5 | 1 | 2 | 5 | **3.5, likely uneconomic** |
| Existing data | 2 | 5 | 5 | 1 | **2.5 descriptive only** |

### Tier B — S&P 500

| Source/architecture | Data fit | Cost | Integration | Quality risk | Overall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Georgia Tech WRDS/CRSP + entitled S&P portfolio data | 5 | 5 if entitled | 4 | 5 | **5.0 preferred if confirmed** |
| CRSP prices + S&P SPICE membership | 5 | 1 | 2 | 5 | **3.5** |
| Norgate Platinum | 4 | 4 | 4 | 2 | **3.5 conditional** |
| Massive/EODHD + existing open ledger | 3 | 4 | 2 | 1 | **2.5, do not activate** |
| Existing data | 1 | 5 | 5 | 1 | **2.0 blocked** |

### Tier C — U.S. All Stocks

| Source/architecture | Data fit | Cost | Integration | Quality risk | Overall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Georgia Tech WRDS/CRSP | 5 | 5 if entitled | 4 | 5 | **5.0 preferred** |
| Direct CRSP commercial | 5 | 1/unknown | 4 | 5 | **4.0 data fit, quote required** |
| Norgate Platinum | 3 | 4 | 3 | 2 | **3.0; terminal contract fails** |
| Massive + separate actions/identity/terminal source | 3 | 2 | 1 | 2 | **2.0** |
| EODHD + separate identity/terminal source | 2 | 5 | 2 | 1 | **2.0** |
| Free/open assembly | 1 | 5 | 1 | 1 | **1.5** |

## 10. Stable-identity strategy

Keep the engine's provider-neutral string `security_id`. Populate it with the native security-level permanent ID when one source is authoritative:

- CRSP source: `security_id = PERMNO` is preferred. PERMCO is company-level and can collapse multiple share classes, so it should be an additional field, not the trading key.
- Norgate source: `security_id = norgate:<assetid>` is acceptable if the supported Python environment exposes it and license permits normalization.
- Other source: create an immutable internal UUID only after reconciling the vendor's share-class FIGI/composite FIGI, exchange, name, ticker intervals, and corporate events. Preserve every vendor ID in a crosswalk.

Add share-class FIGI and composite FIGI where available. FIGI is permanent through ticker/name changes and delisting and is useful across vendors, but OpenFIGI is a mapping service rather than a complete historical security master. It should be a crosswalk/audit key, not permission to infer missing history.

Ticker reuse, multiple share classes, relistings, mergers, acquisitions, bankruptcies, and spin-offs must create or continue IDs according to economic security continuity—not name similarity. Never splice two issuers because a ticker matches. Never collapse distinct share classes to a company ID.

### Target security-history additions (optional to loader v1, required for robust ingestion)

`company_name`, `vendor_security_id`, `vendor_company_id`, `share_class_figi`, `composite_figi`, `effective_start`, `effective_end`, `known_at`, `listing_status`, `listing_start`, `listing_end`, `delisting_date`, `predecessor_security_id`, `successor_security_id`, `relationship_type`, `source_event_id`.

## 11. Delisting and corporate-action economics

### Delisting policy

- **Cash acquisition:** stop trading at the last eligible session; create a cash receivable equal to the authoritative consideration, credited on the economic/payment date. If the engine simplifies to effective-date value, disclose that timing convention.
- **Stock acquisition/merger:** convert the holding to the successor `security_id` at the authoritative ratio, plus cash-in-lieu for fractional shares where available. Do not pretend the predecessor was sold at a nonexistent next open.
- **Bankruptcy/worthless security:** apply `-100%` only when the source explicitly establishes worthlessness. CRSP's explicit delisting-return rules/codes are suitable. Missing is not automatically zero or -100%.
- **Major-exchange delisting to OTC:** this is a listing-status transition, not necessarily economic extinction. Preserve identity. For a major-exchange-only universe, exit at the last executable major-exchange price or model the OTC transition under a fixed policy.
- **Missing final quote:** flag unresolved. A critical unresolved position/event blocks activation unless a conservative imputation policy was preregistered and separately sensitivity-tested.
- **Explicit delisting return:** compound exactly once. If the vendor's total return already includes it, retain `DelistingReturn` for audit but do not apply it again.

The current backtester removes a holding when it leaves membership and can only realize value at an available `Open`. Therefore the normalized adjusted series must already carry terminal economics for loader v1, or a future engine change must model receivables/conversions. A ticker list containing dead names is not enough.

### Corporate-action representation

Current v1 compatibility requires total-return-adjusted OHLCV plus `RawClose`, with delisting return incorporated once. That is the exact loader contract, but it has risks:

- applying an additional split/dividend/delisting event would double-adjust;
- vendor “adjusted close” conventions may not apply identically to O/H/L;
- backward total-return adjustment can alter historical absolute price/ATR/stop levels using future dividends, even when pre-event percentage returns remain invariant;
- mixing adjusted OHLC from one vendor with raw close/volume/actions from another can corrupt fills, filters, and ADV.

Recommended future canonical representation is raw regular-session OHLCV plus explicit corporate-action and terminal-event tables, from which the ingestion pipeline creates a versioned, as-of-safe split/capital-adjusted execution view and a separate total-return series. Dividends and delisting consideration are cash-flow events. Until the engine supports that representation, an installed v1 bundle must use one vendor's internally consistent adjusted OHLCV/RawClose/Volume and document every factor convention.

## 12. Effective date versus `known_at`

- **Daily bars:** the bar is knowable only after the session close. The current engine correctly uses bars strictly before the rebalance session and fills at that session's open. A separate `known_at` column is unnecessary if this availability rule is fixed.
- **Security/listing status:** needs effective interval and `known_at`; the current loader enforces both strictly before the decision.
- **Index membership:** needs effective date and announcement/known-at date when the strategy could respond before the change. Effective-date-only is acceptable only for a strategy that first uses the new roster after it is effective and never acts on advance notices. Record both when available.
- **Corporate actions:** ex/effective date drives economics; declaration/announcement date is needed only if a feature or pre-event trade can use the announcement. The adjustment engine must not expose later-known consideration early.
- **Sector classifications:** effective and known-at dates are required because vendors can restate classifications.
- **Earnings/events:** release timestamp, timezone, and known-at/revision history are mandatory; date-only is insufficient around open/close boundaries.

## 13. Storage and layout

Order-of-magnitude estimates for 1996 onward, compressed Parquet:

| Tier | Approximate rows | Normalized Parquet | Raw/staging + normalized working space |
| --- | ---: | ---: | ---: |
| Dow-ever-members | <1 million | ~50–250 MB | ~0.5–2 GB |
| S&P 500-ever-members | ~5–10 million | ~0.3–1.5 GB | ~2–6 GB |
| U.S. all stocks | ~50–120 million | ~4–15 GB | ~15–50 GB |

These are planning ranges, not quotes. For context, the repository's 1,741 current ticker-keyed daily cache files total only ~0.30 GiB, reflecting mixed/shorter survivor histories and not a comparable PIT bundle.

Use `security_history.parquet` as one compact table. For daily data, avoid one giant file and thousands of per-security files. The existing loader recursively accepts fragments and applies date filters, so year partitions with a modest security hash bucket—for example `daily/year=YYYY/bucket=00..15/part.parquet`—fit it best while bounding file counts and enabling future predicate pruning. Preserve a raw immutable staging area outside the runnable bundle; publish only audited normalized artifacts under `data/pit_us_all_stocks/`.

## 14. Proposed ingestion pipeline and strict activation gates

### Pipeline

```text
immutable vendor export
  -> raw file inventory, hashes, license/snapshot record
  -> normalized security IDs and vendor-ID crosswalk
  -> date-effective ticker/name/listing/security-type intervals
  -> corporate-action normalization
  -> delisting/successor/cash-value normalization
  -> index membership intervals and known-at timestamps (when applicable)
  -> normalized daily raw and v1-compatible adjusted bars
  -> manifest + row counts + coverage statistics + hashes
  -> automated audits + quarantined exceptions
  -> atomic publication to data/pit_us_all_stocks/
  -> registry inspection
```

### Required quality gates before `runnable: true`

1. **Manifest/provenance:** schema/version/source/snapshot/license present; artifact hashes and row counts match; actual min/max dates meet—not merely repeat—declared coverage.
2. **Duplicate identity:** one row per ID/effective interval; no duplicate daily `(security_id,date)`; no overlapping contradictory identity/status intervals.
3. **Ticker reuse:** every ticker/date maps to at most one security; reused ticker across issuers remains separate; known historical reuse fixtures pass.
4. **Identifier continuity:** ticker/name/exchange changes retain the right security ID; mergers, spin-offs, relistings, and share classes reconcile to event/crosswalk tables.
5. **Daily-master join:** every daily row joins a valid security; every eligible/membership security has required warmup and tenure bars; no orphan IDs.
6. **Missing-price:** expected trading sessions are compared with exchange calendars; suspensions/halts are distinguished from missing ingestion; membership-date gaps are below a fixed threshold and no selected holding disappears silently.
7. **Delisted-security:** all historical inactive members are present; each terminal event is cash, stock conversion, explicit return, OTC continuation, worthless, or unresolved; unresolved critical events fail activation.
8. **Terminal economics:** adjusted return reconciles to raw last trade plus explicit delisting return/value exactly once; successor/cash consideration balances.
9. **Corporate actions:** split/dividend/action factors reconcile raw to adjusted samples; OHLC invariants hold; volume uses inverse split adjustment where appropriate; no double adjustments.
10. **Membership continuity:** index intervals have no gaps/overlaps, expected roster sizes reconcile or have sourced exceptions, every add/delete transition replays, and every member resolves to a security ID.
11. **PIT/lookahead:** `known_at <= information cutoff`; deliberately delaying known-at changes eligibility; future ticker/action/sector records cannot change earlier decisions; all bars used for a decision precede execution.
12. **Coverage/survivorship:** active and inactive counts, delisting rates, entrants/exits, exchange/type distributions, and tail years are plausible and reconcile to vendor totals.
13. **Economic smoke tests:** cash acquisition, stock merger, bankruptcy, OTC transition, ticker reuse, split, ordinary dividend, special distribution, and spin-off fixtures reproduce known economics.
14. **Full-universe audit:** no missing large portions of membership, terminal returns, or identity reconciliation. Warnings cannot be converted to runnable by setting manifest booleans.

Activation must be atomic and fail closed. Presence of three files is not sufficient. Any missing delisted population, unresolved selected-member terminal event, material membership gap, or identity failure keeps the universe disabled.

## 15. Build versus buy

| Architecture | Dollar cost | Engineering effort | Data-quality risk | Maintenance | Research unlocked |
| --- | --- | --- | --- | --- | --- |
| Buy comprehensive CRSP-quality data | Quote-only; possibly zero marginal via university | Medium normalization, low source stitching | Lowest | Periodic snapshots and audits | A/B/C price research; sector/earnings/intraday still separate |
| Build narrow Dow/S&P from multiple sources | $630/year Norgate candidate plus any terminal/membership licenses; or API fees | Medium-high identity/event joins | Medium-high, especially known-at and terminal value | Multiple vendors and schema drift | A and possibly B; not Tier C |
| Continue current data | $0 | Low | Known high survivorship/identity risk | Existing cache maintenance | Descriptive Dow only; Conditional Edge power, S&P PIT, and all-stocks remain blocked |

Option 1 is economically best only with institutional access or an acceptable quote. Option 2 is rational for Tier A because the universe is small enough to audit deeply, but dangerous for S&P unless stable-ID and terminal-event joins are automated. Option 3 saves money but cannot answer the stated next research questions.

## 16. Research value by tier

- **Tier A:** approximately 20–30 years of canonical DM/MRM tests; hundreds of monthly decisions; more credible conditional-regime discovery/validation; richer DM/MRM correlation, volatility scaling, drawdown, and Sharpe analysis. It does not unlock all-stock breadth, PIT sectors, earnings, or intraday.
- **Tier B:** survivorship-free S&P momentum; wider cross-sectional robustness; more names per monthly state and more historical entrants/exits. It adds cross-sectional rows but not necessarily more independent market months than Tier A.
- **Tier C:** all-stock momentum and currently disabled broad strategies; much greater cross-sectional breadth; delisting-aware discovery and future size/liquidity research. Sector-relative work still needs PIT sectors; earnings still needs event timestamps; overnight/intraday still needs separate execution data.

## 17. Final cost-benefit recommendation

**Use institutional/academic access if available.** It is the only path that combines the best data fit with potentially low marginal cost. Treat Georgia Tech access as a gating verification task, not a fact.

If that fails, **acquire a narrower Dow/S&P dataset first**, beginning with Tier A. Norgate Platinum's $630/year personal price makes it worth a schema/license trial, but not immediate purchase and not automatic activation. Require a terminal-economics solution and written extraction/retention clarity first. Tier A can deliver most of the near-term statistical value at orders of magnitude less engineering scope than Tier C.

Defer a comprehensive commercial Tier C purchase until either CRSP access is confirmed or Tier A demonstrates that longer PIT history changes research confidence enough to justify the cost. More rows alone are not value: monthly market regimes remain the binding independent sample for the conditional questions.

## 18. Source-specific questions before any purchase

Ask every vendor for written answers and a small schema/sample—not marketing prose:

1. Can the export enumerate inactive/delisted U.S. common shares as of each historical date?
2. What permanent share-class identifier survives ticker/exchange/name changes, and how are mergers/spin-offs/relistings represented?
3. Are raw O/H/L/C, volume, total return, split factors, dividend cash flows, and delisting returns/value all available separately?
4. How are cash acquisitions, stock acquisitions, bankruptcies, OTC transitions, and missing final values encoded?
5. Does historical index membership include effective dates and announcement/known-at dates? Are temporary constituents included?
6. Which exchanges and security types are complete from 1996? What are documented gaps?
7. May the normalized Parquet bundle be stored locally, in OneDrive, backed up, and retained after subscription/affiliation ends?
8. Are personal academic research, paper publication, and later commercial use of derived results allowed?
9. Are bulk exports/API calls sufficient for the initial 1996-present load, and is there a one-time onboarding charge?
10. Will corrections revise history silently, and is an immutable snapshot/version available?

No universe should be enabled until the answers and the data pass the gates above.
