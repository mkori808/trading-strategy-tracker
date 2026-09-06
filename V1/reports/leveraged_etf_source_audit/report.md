# Leveraged-ETF sponsor data audit

> Diagnostic only: no returns, backtest, holdout, or preregistration was used.

Verdict: **DATA_BLOCKED_PARTIAL_UNLOCK**

ProShares’ historical NAV CSV exposes daily NAV, shares outstanding, and AUM,
so the earlier assumption that all historical scale data was absent was too
strong. That still does not establish historical derivative positions or
notional exposure, nor does it establish an independent sponsor cross-check.

| Sponsor | What was verified | Status |
|---|---|---|
| [ProShares](https://prod.proshares.com/resources/data-downloads) | Historical NAV, shares outstanding, and AUM fields; daily holdings/split/performance links | PARTIAL |
| [Direxion](https://www.direxion.com/product/daily-sp-500-bull-bear-3x-etfs) | Daily target, current NAV, and current daily-holdings download | PARTIAL |

Remaining blockers are historical derivative notional/position data, an
independent sponsor archive, and a validated timestamp/flow convention. No
reset-flow model or strategy build is justified by this source check.
