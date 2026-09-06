# Point-in-time audit

Audit date: 2026-08-13

This audit distinguishes a membership list from a tradable, survivor-free
backtest dataset. A dated roster can prevent future constituents from entering
early, but it does not recover the returns of deleted, acquired, bankrupt, or
ticker-reused securities.

## Findings

| Area | Finding | Status |
|---|---|---|
| Dow membership | `data/universe_membership.json` contains 14 sourced date-effective intervals from 2000 onward. `engine/runner.py` applies them only to cross-sectional runs selected as `dow_pit`. Standard per-symbol engines still use the registered flat 29-name list. | Partial |
| S&P 500 membership | A reproducible importer now compresses the open `fja05680/sp500` dated rosters into 695 intervals from 1996-01-02 through 2026-08-13. Forward replay covers 694 transition dates / 1,530 add-delete legs and reconciles to the current 503-security roster after one separately sourced 2026-08-05 FERG-for-EA tail event. The source is approximate and non-official. | Membership built; price audit pending |
| S&P 400 / 600 membership | The runnable `*_current` entries apply today's complete rosters through history. The hidden `*_pit` entries are small current-member samples, not PIT universes. No viable complete free historical source is installed. | Unresolved |
| Crypto, international ETF, futures-proxy, SPY universes | These are fixed instruments/proxies rather than changing constituent indexes. PIT membership is correctly N/A. The “futures” entry is explicitly an ETF-proxy equity basket, not futures contracts. | Correctly scoped |
| U.S. all-stocks PIT | The loader requires permanent security IDs, `known_at`, effective listing intervals, delisting metadata, and adjusted/raw price fields. It is non-runnable until the documented licensed bundle exists. | Architecture ready; data absent |
| Delisted securities | Dow discloses seven unavailable historical names and excludes them. The completed S&P provider audit checked all 1,207 distinct historical ticker strings: 778 returned some local/Yahoo history, 429 returned none, 637 do not span every membership tenure plus required warmup, and 52 have disjoint/reused identities. Yahoo's current-symbol lookup cannot disambiguate those identities. | Blocking |
| Point-in-time market cap | The generic fundamentals feed exposes today's `marketCap` and explicitly labels it non-PIT. The all-stocks loader can consume a daily `MarketCap` column and uses only observations strictly before the signal date, but the licensed bundle is absent. Registered index universes have no historical shares-outstanding/market-cap series. | Blocking for cap-ranked research |
| Corporate actions / prices | Yahoo daily data is fetched with `auto_adjust=True`; Alpaca intraday requests `Adjustment.ALL`. The dividend screen separately uses unadjusted closes for yield denominators. These choices handle ordinary splits/dividends, but they do not guarantee final delisting returns, cash merger consideration, spinoff allocation, or identity-safe ticker reuse. Several comments still describe the pre-fix 20:00/previous-day daily timestamp convention and should be cleaned up. | Partial |
| Earnings timestamps (PEAD) | `earnings_dates()` preserves timezone-aware timestamps, but `positive_earnings_dates()` truncates them to `date`. PEAD therefore cannot distinguish before-market-open from after-market-close reports; an AMC report can be assigned to the already-completed session. AVWAP's earnings anchors perform the same date truncation. | Look-ahead bug |
| Execution timing | Standard backtests use completed-bar signals and `backtesting.py` next-open fills. Cross-sectional portfolios rank bars strictly before a rebalance date and fill that date's open. Pairs queue close signals to the next open. Overnight Hold intentionally enters at the qualifying close and exits next open; that close fill remains an optimistic modeling assumption unless a market-on-close cutoff is represented. | Mostly causal; overnight assumption disclosed |
| Historical sector classification | Current GICS sector labels exist in current-constituent sources. No date-effective GICS history is installed. Sector ETF rotation does not require company classifications, but any stock-level historical sector filter would leak today's classifications. | Unresolved |

## Fix priority

1. Fix PEAD/AVWAP event-time causality before any new earnings-event result is trusted.
2. Acquire an identity-safe deleted-security price source for the 637 incomplete S&P 500 tenures (including 429 with no Yahoo history) and resolve the 52 reused-ticker identities. Do not enable the PIT gate merely because membership replay passes.
3. Wire ledgers only into engine paths that can apply membership on every decision date; keep other engines explicitly unresolved.
4. Add historical shares outstanding or vendor-supplied market cap with an availability timestamp before enabling market-cap classification.
5. Obtain delisted-price/corporate-action coverage (or keep the evidence gate blocked) before interpreting the S&P 500 result as survivor-free.
6. Keep S&P 400/600 current-roster runs available for screening, but never present them as historical PIT tests.
7. Add historical GICS only when a strategy actually depends on company-sector membership; until then render that check N/A or unresolved by engine.

## S&P 500 source disclosure

The imported source is `fja05680/sp500` at commit
`c31ac3cc56f28cf9a02b4e694eff7ceab596a0ff`, MIT licensed, SHA-256
`0a56d59ab5b6313064df9484312cd048a0103341195e5a6154feb6ccb74066e8`.
Its maintainer describes it as a compilation of an older research dataset and
manually maintained changes, and notes that Wikipedia's selected changes are
not complete. It is therefore suitable for transparent exploratory research,
not a substitute for an official/licensed constituent and delisted-security
feed.
