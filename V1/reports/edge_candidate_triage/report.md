# Edge candidate discovery and pre-research triage

Generated: `2026-09-05T23:39:48.304350+00:00`

> Evidence-only triage. No strategy return was tested, no protected holdout was read, and no hypothesis was preregistered.

## Outcome

No candidate is approved for preregistration. One observable candidate is COST_SENSITIVE; the other leading mechanisms need bounded data audits.

The pipeline reused `screen_design`, the existing backlog breadth report, the corrected dual-estimator/holding-period cost report, and the identity feasibility work. It did not create a second backtester, database, or dashboard.

## Existing backlog triage

| Candidate | Observability | Effective breadth/yr | MDA %/yr | Annual cost | Tier | Status | Reason |
|---|---|---:|---:|---|---:|---|---|
| Index addition / forced buying | OBSERVABLE | 24.53 | 5.87 | 4.53% CS / 2.89% AR | 1 | **MARGINAL** | Breadth MDA is 5.87%/yr; cost is below MDA but not enough to cure detectability. |
| Spinoff shares dumped by ineligible holders | OBSERVABLE | 20.00 | 6.50 | 2.65% CS / 1.26% AR | 1 | **MARGINAL** | MDA 6.50%/yr is above the target despite acceptable measured costs. |
| S&P 500+400+600 additions, deletions, and migrations | PARTIAL | — | 4.16 | 3.74% CS / < estimator resolution AR | 1 | **DATA_BLOCKED** | The 500-only proxy is marginal (4.16% MDA); the actual family population cannot be enumerated. |
| Odd-lot tender provisions | DATA_BLOCKED | — | — | — | 1 | **DATA_BLOCKED** | Population count and PIT terms are not yet measurable. |
| Distressed / post-reorg equity | DATA_BLOCKED | — | — | — | 2 | **DATA_BLOCKED** | The prior breadth and cost passes cannot be carried forward to a discoverable population. |
| Small merger arb below institutional minimum deal size | OBSERVABLE | 161.44 | 4.10 | 1.40% CS / 0.82% AR | 2 | **SCREENED_OUT_BREADTH** | Honest eligibility filtering raised MDA to 4.10%/yr, above the fixed target. |
| Tax-loss selling reversal (December) | OBSERVABLE | 10.80 | 8.15 | 0.25% CS / < estimator resolution AR | 2 | **SCREENED_OUT_BREADTH** | Effective breadth is annual and highly correlated; MDA is 8.15%/yr. |
| Index deletion / forced selling | OBSERVABLE | 5.35 | 11.45 | 6.02% CS / 2.51% AR | 1 | **SCREENED_OUT_BREADTH** | MDA 11.45%/yr exceeds the 4% target. |
| Sub-$5 stocks excluded by fund charters | OBSERVABLE | 334.71 | 1.59 | 7.22% CS / 6.16% AR | 2 | **SCREENED_OUT_COST** | Both estimators put annual cost far above MDA. |
| Sub-$300M cap cross-sectional anomalies (momentum) | OBSERVABLE | 760.84 | 3.04 | 7.89% CS / 7.01% AR | 4 | **SCREENED_OUT_COST** | Both spread estimators imply annual cost above MDA. |

## Newly discovered candidates

| Candidate | Observability | Nominal events/yr | Effective bets/yr | MDA %/yr | Annual cost | Tier | Main confounder | Verdict |
|---|---|---:|---:|---:|---|---:|---|---|
| Ex-dividend tax/clientele pressure | OBSERVABLE | 6743.00 | 248.00 | 3.12 | 2.80% CS / < estimator resolution AR | 3 | dividend yield, value, quality, microstructure/discrete prices | **COST_SENSITIVE** |
| IPO lockup-expiration selling pressure | PARTIAL | — | — | — | — | 1 | none obvious at triage | **DATA_BLOCKED** |
| Leveraged-ETF daily rebalance pressure | PARTIAL | 252.00 | — | — | — | 1 | none obvious at triage | **DATA_BLOCKED** |
| Reg SHO Rule 201 short-sale restriction pressure | PARTIAL | — | — | — | — | 1 | none obvious at triage | **DATA_BLOCKED** |
| Lagged 13F crowded-exit pressure | PARTIAL | — | — | — | — | 2 | momentum, size, liquidity, common ownership | **DATA_BLOCKED** |
| S&P 500 equal-weight quarterly reset pressure | OBSERVABLE | 2000.00 | 4.00 | 17.36 | — | 1 | none obvious at triage | **SCREENED_OUT_BREADTH** |
| Fed stress-test capital-distribution constraint | OBSERVABLE | 22.00 | 1.00 | 40.09 | — | 1 | none obvious at triage | **SCREENED_OUT_BREADTH** |

## Ranked shortlist for review

1. **Ex-dividend tax/clientele pressure — COST_SENSITIVE.** Observable and broad even after collapsing 6,743 nominal 2025 events to 248 date clusters; 10-year MDA is 3.12%. It does not advance because the 20-session cost estimate is 2.80% under Corwin-Schultz while Abdi-Ranaldo is below resolution, and factor/microstructure explanations are material.
2. **S&P 500+400+600 migrations — DATA_BLOCKED BUT POTENTIALLY WORTH UNLOCKING.** The liquid 500-only proxy is marginal at 4.16% MDA and low measured cost. The only next action justified is a bounded source audit for 400/600 PIT changes.
3. **Leveraged-ETF daily rebalance pressure — DATA_BLOCKED BUT POTENTIALLY WORTH UNLOCKING.** Tier-1 daily mandate in liquid underlyings, but historical NAV/shares and derivative exposure are missing. Check a few sponsor files before building anything.

The ex-dividend follow-up used a five-session mechanism horizon: Corwin-Schultz implies 11.22% annualized drag, Abdi-Ranaldo is below resolution, and one-venue IEX quotes failed the mega-cap sanity gate. The S&P family source audit also found current free 400/600 histories too short for the 4% MDA target. ProShares historical NAV/shares/AUM are available, but leveraged-ETF derivative exposure remains unverified.

**No candidate reached `STRONG_CANDIDATE`; none should be preregistered yet.**

## Dead versus potentially unlockable

### Dead / screened out

- **Small merger arb below institutional minimum deal size** — Honest eligibility filtering raised MDA to 4.10%/yr, above the fixed target.
- **Tax-loss selling reversal (December)** — Effective breadth is annual and highly correlated; MDA is 8.15%/yr.
- **Index deletion / forced selling** — MDA 11.45%/yr exceeds the 4% target.
- **S&P 500 equal-weight quarterly reset pressure** — Roughly 2,000 security rows are only four common rule/batch events; MDA is far above 4%.
- **Fed stress-test capital-distribution constraint** — Many bank rows share one annual supervisory event and macro scenario; MDA is roughly 40%/yr.
- **Sub-$5 stocks excluded by fund charters** — Both estimators put annual cost far above MDA.
- **Sub-$300M cap cross-sectional anomalies (momentum)** — Both spread estimators imply annual cost above MDA.

### Data-blocked but potentially worth a bounded unlock check

- **S&P 500+400+600 additions, deletions, and migrations** (MEDIUM) — Acquire or construct PIT S&P 400 and 600 change ledgers; no strategy engine work is needed.
- **IPO lockup-expiration selling pressure** (MEDIUM) — Parse a bounded sample of 424B4/S-1 prospectuses and 8-K waivers before deciding whether market-wide extraction is justified.
- **Leveraged-ETF daily rebalance pressure** (SMALL/MEDIUM) — Test whether sponsor NAV/share files provide stable daily history for 3-5 major funds; do not build a broad ETF crawler first.
- **Reg SHO Rule 201 short-sale restriction pressure** (SMALL discovery audit; potentially LARGE acquisition) — Audit exchange/FINRA historical SSR lists; NYSE public halt-style history is only one year, so long-history coverage is uncertain.

### Data-blocked with large or undefined scope

- **Lagged 13F crowded-exit pressure** (LARGE) — A 13F ingestion path is bounded, but a PIT fund-flow source is a separate unresolved dependency.
- **Odd-lot tender provisions** (MEDIUM) — A bounded SEC Schedule TO search/parser plus manual term validation on a sample.
- **Distressed / post-reorg equity** (LARGE) — Build a market-wide CIK/date-proximity candidate index, then independently validate every proposed old/new security link.

## Source-supported mechanism notes

### Ex-dividend tax/clientele pressure

- Mechanism: Investors with different tax and income mandates may transact around ex-dates for non-price-maximizing reasons.
- Affected participant: Tax-sensitive and income-mandated holders
- Constraint: Tax treatment and income eligibility
- Expected action/distortion: Temporary price pressure around the ex-date
- Persistence prior: Tax and mandate differences persist, but published evidence disputes how much is tax versus microstructure.
- Required data: cash-dividend action, PIT common-stock identity, ex-date, prices, liquidity
- Sources: [source 1](https://sharadar.com/docs/actions), [source 2](https://onlinelibrary.wiley.com/doi/pdf/10.1111/j.1540-6261.1982.tb03598.x), [source 3](https://doi.org/10.1016/S0304-405X(97)00041-X). `FACTOR_EXPLANATION_RISK`.

### IPO lockup-expiration selling pressure

- Mechanism: Insiders and pre-IPO holders are contractually barred from selling until a prospectus-specific release date.
- Affected participant: Insiders and pre-IPO holders
- Constraint: Underwriter lockup agreement
- Expected action/distortion: Supply increase when locked shares become saleable
- Persistence prior: Contract terms are binding, though waivers and durations vary.
- Required data: IPO prospectus, lockup duration, effective date, waivers, shares unlocked, identity-safe prices
- Sources: [source 1](https://www.sec.gov/answers/lockup.htm).

### Leveraged-ETF daily rebalance pressure

- Mechanism: Daily-target leveraged and inverse funds must reset derivative exposure each day.
- Affected participant: Leveraged/inverse ETF sponsors
- Constraint: Prospectus daily-return objective
- Expected action/distortion: Directional close-period hedge demand
- Persistence prior: Daily target and fund flows determine required exposure independent of expected next-day return.
- Required data: historical daily ETF NAV/AUM/shares, leverage objective, holdings/swaps/futures, underlying intraday data
- Sources: [source 1](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/sec), [source 2](https://www.proshares.com/resources/geared-faqs).

### Reg SHO Rule 201 short-sale restriction pressure

- Mechanism: A 10% decline activates a price test restricting short-sale execution for the rest of the day and next day.
- Affected participant: Short sellers and broker-dealers
- Constraint: SEC Rule 201 execution restriction
- Expected action/distortion: Temporary imbalance in short-sale supply and execution priority
- Persistence prior: The rule is mandatory and trigger-based.
- Required data: consolidated intraday trades, prior close, official SSR activation timestamp, quotes/spreads
- Sources: [source 1](https://www.sec.gov/news/speech/2010/spch022410tap-shortsales.htm).

### Lagged 13F crowded-exit pressure

- Mechanism: Fund outflows or mandate changes can force managers holding the same liquid names to sell.
- Affected participant: Delegated asset managers
- Constraint: Redemptions and mandate compliance
- Expected action/distortion: Temporary common-position fire-sale pressure
- Persistence prior: Agency and flow constraints recur, but the contemporaneous flow is not public.
- Required data: 13F acceptance timestamps, holdings, manager identity, fund flows, security identity
- Sources: [source 1](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f). `FACTOR_EXPLANATION_RISK`.

### S&P 500 equal-weight quarterly reset pressure

- Mechanism: Equal-weight trackers must reset every constituent toward 0.2% each quarter.
- Affected participant: Equal-weight index funds
- Constraint: Published benchmark weighting rule
- Expected action/distortion: Common close-auction demand tied to weight drift
- Persistence prior: Tracking-error mandate requires the reset even when flows are predictable.
- Required data: PIT S&P 500 membership, rebalance calendar, reference-date prices, historical fund AUM/shares
- Sources: [source 1](https://www.spglobal.com/spdji/en/education/article/sp-500-equal-weight-index-faq/).

### Fed stress-test capital-distribution constraint

- Mechanism: Stress-capital requirements constrain dividends and repurchases at large banks.
- Affected participant: Large bank holding companies
- Constraint: Regulatory capital requirements
- Expected action/distortion: Capital-distribution repricing around result disclosure
- Persistence prior: Supervisory capital rules are binding.
- Required data: official result timestamps, bank roster, capital requirements, prices
- Sources: [source 1](https://www.federalreserve.gov/publications/dodd-frank-act-stress-test-publications.htm).

## Next research questions

1. Do not spend infrastructure capacity on ex-dividend execution modeling unless consolidated NBBO/TAQ becomes available; the one-venue IEX sanity check failed.
2. The bounded PIT S&P 400/600 source audit is insufficient on breadth; revisit only with an identity-safe, exhaustive family ledger covering at least 10.3 years.
3. ProShares exposes historical NAV/shares/AUM, but leveraged-ETF reset-flow magnitude remains blocked until derivative exposure and an independent sponsor cross-check are established.

Stop after those cheap checks. Do not choose a holdout, inspect candidate returns, tune rules, or preregister until a candidate reaches `STRONG_CANDIDATE` and receives explicit human approval.
