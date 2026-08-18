# U.S. All Stocks — Point-in-Time data bundle

This directory is intentionally data-free. The application will not replace a
missing survivorship-free dataset with today's constituents.

Install a licensed, normalized bundle here with:

- `manifest.json`
- `security_history.parquet`
- `daily/` containing one or more Parquet files, or `daily.parquet`

Use permanent security identifiers (for example CRSP PERMNO), never ticker as
the primary key. Tickers may be reused and may change during one security's
life.

## `manifest.json`

Required fields:

```json
{
  "schemaVersion": 1,
  "source": "licensed source and product name",
  "snapshotId": "immutable export/version identifier",
  "coverageStart": "1996-01-01",
  "coverageEnd": "2026-08-12",
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

## `security_history.parquet`

One row per date-effective identity/status interval:

`security_id`, `ticker`, `effective_start`, `effective_end`, `known_at`,
`is_us_listed`, `is_common_stock`, `security_type`, `exchange`, `is_acquired`,
`delisting_reason`.

`known_at` is the first date the status was available. Eligibility requires
both an effective interval and `known_at` strictly before the execution date.

## Daily Parquet columns

`security_id`, `date`, `Open`, `High`, `Low`, `Close`, `RawClose`, `Volume`,
`DelistingReturn`.

The capitalized OHLCV series must be split-, dividend-, corporate-action-, and
delisting-return-adjusted. `RawClose` is the historical close used only for the
$5 and dollar-volume filters. Optional `MarketCap` must be a historical value
known on that date; current market capitalization is invalid.

`DelistingReturn` may be null on ordinary observations but must contain the
source's delisting return on the relevant terminal record. The adjusted return
series must incorporate it exactly once.

When `MarketCap` is present, the engine classifies each security from the last
observation strictly before the decision date (`engine/pit_market_cap.py`).
The default nominal bands are disclosed in code (mega $200B, large $10B, mid
$2B, small $250M); they are research labels, not reconstructed S&P index
eligibility rules. Missing historical cap remains missing and is never filled
with today's `yfinance.info.marketCap` snapshot.

The bundle is rejected if it contains no delisted securities or if any required
provenance assertion is absent. Run `python -m engine.pit_all_stocks` for the
machine-readable readiness report.
