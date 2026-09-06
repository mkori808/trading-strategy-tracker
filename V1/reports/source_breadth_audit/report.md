# S&P 400/600 source breadth audit

> Diagnostic only: no returns, backtest, holdout, or preregistration was used.

Verdict: **STOP_SCREENED_OUT_BREADTH**

The bounded free S&P 400/600 sources do not provide enough complete independent history to clear a 4% MDA target; official releases remain unbounded for automated reconstruction.

Using the existing 500-only MDA of 4.16% over 28.5 years and a conservative three-fold event-family breadth multiplier, a complete history needs about **10.26 years** to reach the 4% MDA ceiling.

| Source | Coverage | Projected MDA | Status |
|---|---|---:|---|
| [pitindex](https://github.com/arielNacamulli/pitindex) | sp500: 2005-01-03, sp400: 2011-11-20, sp600: 2021-03-26 | 5.49% | PARTIAL_INSUFFICIENT_HISTORY |
|  | The composite 1500 floor is the S&P 600 start; project is community-maintained and documents incomplete historical changes. |  |  |
| [indexkit](https://github.com/kovagent/indexkit) | sp600: 2019-11-01 | 4.90% | PARTIAL_INSUFFICIENT_HISTORY |
|  | Community reconstruction using N-PORT, sponsor files, and Wayback; does not establish a complete 2000-era family ledger. |  |  |
| [S&P DJI press releases](https://press.spglobal.com/2026-03-06-Vertiv-Holdings%2C-Lumentum-Holdings%2C-Coherent%2C-and-EchoStar-Set-to-Join-S-P-500-Others-to-Join-S-P-100%2C-S-P-MidCap-400-and-S-P-SmallCap-600) | individual announcements | — | NOT_MACHINE_BOUNDED |
|  | Primary announcements confirm individual changes but no exhaustive, normalized historical archive was established in this audit. |  |  |

Do not build a family-index strategy pipeline unless an identity-safe, exhaustive change ledger covering at least the required horizon is obtained.
