from __future__ import annotations

import numpy as np
import pandas as pd

from engine.pit_analysis import analyze_pit_result, dynamic_random_benchmarks, hac_mda


def test_hac_mda_uses_time_series_observations_not_security_count() -> None:
    index = pd.bdate_range("2000-01-03", periods=3_000)
    rng = np.random.default_rng(42)
    innovations = rng.normal(0, 0.008, len(index))
    serial = np.zeros(len(index))
    for position in range(1, len(index)):
        serial[position] = 0.35 * serial[position - 1] + innovations[position]
    spy_returns = rng.normal(0.00025, 0.009, len(index))
    strategy_returns = spy_returns + 0.00008 + serial * 0.25
    spy = pd.Series(10_000 * np.cumprod(1 + spy_returns), index=index)
    strategy = pd.Series(10_000 * np.cumprod(1 + strategy_returns), index=index)

    evidence = hac_mda(strategy, spy)

    assert evidence["observations"] == len(index) - 1
    assert 0 < evidence["effectiveSampleSize"] <= evidence["observations"]
    assert evidence["hacLags"] >= 1
    assert evidence["mdaPct"] > 0
    assert "More securities do not automatically reduce MDA" in evidence["explanation"]


def test_pit_analysis_reports_matched_benchmark_holdout_regimes_and_cost_stress() -> None:
    index = pd.bdate_range("1996-01-02", periods=7_500)
    strategy = pd.Series(10_000 * np.power(1.00045, np.arange(len(index))), index=index)
    spy = pd.Series(10_000 * np.power(1.00030, np.arange(len(index))), index=index)

    report = analyze_pit_result(
        strategy, spy, total_costs=125.0,
        pit_diagnostics={"historicallyDelistedSecuritiesUsed": 100},
    )

    assert report["strategyReturnPct"] > report["spyReturnPct"]
    assert report["annualizedBenchmarkRelativeReturnPct"] > 0
    assert len(report["annualReturns"]) >= 20
    assert any(row["label"] == "2008–2009" for row in report["regimes"])
    assert report["rollingExcess"]["3Year"]["fractionBeatingSpy"] == 1.0
    assert report["holdout"]["holdoutExcessPct"] > 0
    assert report["costStressReturnPct"]["1x"] > report["costStressReturnPct"]["3x"]


def test_dynamic_random_controls_use_membership_at_each_rebalance() -> None:
    index = pd.bdate_range("2020-01-02", periods=80)
    bars = {
        symbol: pd.DataFrame({"Close": 100 * np.power(growth, np.arange(len(index)))}, index=index)
        for symbol, growth in {"A": 1.001, "B": 1.0005, "DELIST": 0.999}.items()
    }
    calls: list[pd.Timestamp] = []

    def membership(as_of):
        calls.append(pd.Timestamp(as_of))
        return {"A", "B", "DELIST"} if as_of < index[40].date() else {"A", "B"}

    ew, random_stats = dynamic_random_benchmarks(
        bars, membership, index, rebalance_frequency="monthly", top_n=2,
        initial_equity=10_000.0, simulations=20, seed=7,
    )

    assert not ew.empty
    assert random_stats["simulations"] == 20
    assert len(random_stats["returnsPct"]) == 20
    assert calls
    assert any(stamp.date() >= index[40].date() for stamp in calls)
