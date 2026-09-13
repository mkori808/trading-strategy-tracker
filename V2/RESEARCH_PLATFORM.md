# V2 Research Platform

`Systematic_Equity_Research_Roadmap.txt` is the authoritative specification for this phase.

## Architecture and capability map

| Area | Existing implementation | Roadmap status after this change |
|---|---|---|
| PIT data | Full Sharadar SQLite bundle; listing intervals; delisted securities; PIT universe filters; ARQ filing dates; daily OHLCV and market cap | Reused. IBS conditioning fails closed on missing price/liquidity/history and uses exact ARQ publication dates. |
| Signal library | Nine daily signals in `strategies/signal_library.py`; additional overnight and intraday libraries | Preserved. The conditioning program has a separate registry and does not add a composite. |
| Backtests | Monthly factor studies, weekly composites, daily reversal cohorts, ADV cost tiers, size/liquidity analysis | Extended with next-open-to-close IBS conditioning, paired contrasts, costs, capacity, horizon decay, and subperiod/regime analysis. |
| Governance | JSON preregistrations; exhausted three-use holdout budget; timestamped non-overwriting artifacts | Extended with executable/preregistration drift checks and a required used-history acknowledgement. Historical outputs are labeled development evidence, never fresh validation. |
| Metrics/reporting | Shared performance metrics plus strategy-specific reports and CSV/pickle results | Conditioning outputs include CSV, strict JSON, Markdown, frozen spec, factor/SPY inputs, file hashes, database metadata, and git provenance. |
| Paper trading | Frozen IBS, Composite V2, and Composite V3.1 virtual-fill tracks with Monday-open observations | Deliberately unchanged. The new program neither imports nor writes their state. |
| Tests | Sharadar builder and short-term reversal unit tests | Added timing, PIT mutation, event-date, independent-registry, cost, taxonomy, and preregistration-drift tests. |

## IBS liquidity-vs-information program

The executable is `strategies/ibs_conditioning.py`; `scripts/run_ibs_conditioning.py` is its command-line entry point. The frozen specification is `../research/ibs_conditioning_preregistration.json`.

The ten registered hypotheses are evaluated separately:

1. earnings-event status
2. overnight gap share of the absolute daily path
3. abnormal volume
4. turnover
5. trailing liquidity
6. intraday range
7. idiosyncratic volatility
8. market capitalization
9. total-volatility bucket
10. market regime

The signal is one-day IBS at close. Execution is the next session's open through that session's close. Continuous conditioners use daily cross-sectional terciles. IBS quintile thresholds are calculated once across the eligible universe before condition buckets are applied, so every subgroup evaluates the same definition of an extreme IBS observation.

The primary test is the paired difference between the IBS Q1-minus-Q5 spread in the preregistered favorable and adverse condition buckets. Classification uses only `SUPPORTED`, `FALSIFIED`, or `UNRESOLVED / UNDERPOWERED`.

The ten-test family uses a Bonferroni-adjusted absolute t threshold of 2.81 and requires at least 250 paired days. These labels describe historical development evidence only. They do not restore or create a historical holdout.

## Run contract

From the repository root:

```powershell
python3.11 V2/scripts/run_ibs_conditioning.py --acknowledge-used-historical-data
```

The default run is locked to 2001-01-01 through 2018-12-31. The runner downloads daily FF5, momentum, and adjusted SPY data. Frozen local inputs can instead be supplied with `--factors-csv` and `--spy-csv`.

Each run creates a new directory below `V2/reports/ibs_conditioning/`; prior results are never overwritten. Review `report.md` first, then `hypothesis_contrasts.csv`, `bucket_metrics.csv`, `results.json`, and `run_manifest.json`.

## First development run: 2026-09-10

The corrected artifact is `V2/reports/ibs_conditioning/20260910T142457Z_397369113e/`. It is historical development evidence only; its manifest records the source database, frozen specification hash, and output hashes.

The gross IBS Q1-minus-Q5 spread supports the temporary-pressure interpretation for low overnight-gap share (t=10.54), low abnormal volume (t=5.52), low turnover (t=9.81), low idiosyncratic volatility (t=3.64), and low total volatility (t=3.79). The expected small/illiquid pattern was contradicted: liquid and larger-cap buckets had stronger gross spreads. Earnings-event, intraday-range, and bull-versus-bear contrasts remain unresolved under the familywise threshold.

Most importantly, none of these gross spread findings establish an investable long-only strategy at the specified daily execution. The costed next-open-to-close Q1 legs have roughly 504 annual two-way turnover and deeply negative net CAGRs under the ADV-tier cost model. They must not be added to a composite or used to modify Paper Tracks 1-3. This result shifts the next work toward execution-frequency and turnover design, while retaining the cross-sectional conditioning findings as mechanism evidence.

The earlier `20260910T063224Z_aaf3b1e0e4` bundle is retained unchanged. Its market-regime test attempted a paired bull-minus-bear comparison even though those regimes are mutually exclusive by date, producing zero pairs. The corrected run changed only this comparator to an HAC difference in means; the other nine contrasts reproduced exactly.

## Residual momentum development result: 2026-09-10

Three separately frozen residual-momentum hypotheses (60 sessions, 126 sessions, and t-251 through t-20) were evaluated on 2001-2018 only. The corrected artifact is `V2/reports/residual_momentum/20260910T203505Z/`; its manifest freezes the preregistration hashes, database metadata, correction provenance, and every result-file hash. The incomplete `20260910T163536Z` bundle is superseded because its long-window implementation used t-271 through t-20, classified on long-only rather than Q5-minus-Q1 factor alpha, and failed before writing its report.

All three hypotheses are `UNRESOLVED / UNDERPOWERED`. The t-251 through t-20 signal was directionally strongest, with a 1.40% annualized primary spread (HAC t=0.40), mean 20-session IC of 0.0134, and 2.10% Q5-minus-Q1 factor alpha (t=0.61), but it was far below the preregistered familywise t threshold of 2.39. Long-only implementations had 300%-559% annual turnover, 57%-61% maximum drawdowns, and approximately 0.94-0.95 return correlation with Composite V3.1. No residual-momentum variant is promoted, no composite is changed, and 2019+ remains unevaluated by this program.

## Remaining roadmap sequence

After the conditioning run is interpreted and frozen, the next independent program is a prospective IBS execution-frequency experiment (daily, every two days, weekly, buffered daily, and turnover-constrained daily). It must not modify Paper Tracks 1-3. Event continuation, prospective short selection with observed borrow data, capacity work, constrained nonlinear interactions using validated predictors, and multi-sleeve portfolio construction remain later programs in the roadmap order.

## Known data limitations

- ARQ contains a publication date but no announcement timestamp; before-open and after-close earnings cannot be separated.
- Historical quoted spreads, order-book depth, and realized market impact are absent. ADV-tier costs are estimates.
- Historical borrowability and fees are absent; no historical short implementation claim is made.
- The Sharadar identifier is a ticker, not CRSP PERMNO continuity.
- The default Python command on this workstation currently resolves Python 3.12 against a Python 3.11 user-site package path. Use a coherent environment (or the installed Python 3.11 interpreter) before a full run.
