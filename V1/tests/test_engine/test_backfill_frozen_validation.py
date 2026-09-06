from engine.backfill_frozen_validation import (
    _update_multiple_testing, summarize_neighbors,
)
from engine.validation import _rolling_matched_trade_stability
from datetime import date
from types import SimpleNamespace
import pandas as pd


def test_neighbor_ridge_requires_complete_coverage_and_broad_positive_economics(monkeypatch):
    rows = [
        {
            "strategy_name": "52-Week-High Momentum", "status": "completed",
            "supports_hypothesis": 1 if index < 50 else 0,
            "benchmark_excess_pct": 1.0 if index < 50 else -1.0,
            "expectancy_r": None, "trades": None,
        }
        for index in range(71)
    ]
    monkeypatch.setattr(
        "engine.backfill_frozen_validation.logging_db.frozen_neighbor_results",
        lambda _family: rows,
    )
    summary = summarize_neighbors(
        "52-Week-High Momentum", canonical_supports=True, canonical_sign=1,
    )
    assert summary["status"] == "pass"
    assert summary["details"]["coverageComplete"] is True
    assert summary["details"]["positiveEconomicNeighbors"] == 50

    rows.pop()
    incomplete = summarize_neighbors(
        "52-Week-High Momentum", canonical_supports=True, canonical_sign=1,
    )
    assert incomplete["status"] == "fail"
    assert incomplete["details"]["coverageComplete"] is False


def test_multiple_testing_uses_actual_family_width():
    check = {
        "status": "pass", "value": 0.01,
        "details": {"naiveBootstrapP": 0.01},
    }
    _update_multiple_testing(check, "stable-family", 72)
    assert check["value"] == 0.72
    assert check["status"] == "fail"
    assert check["details"]["actualConfigurationsEvaluated"] == 72


def test_sparse_history_uses_exact_interval_trade_excess_not_buy_and_hold():
    rows = []
    for year in range(2021, 2026):
        for month in range(1, 11):
            rows.append({
                "ExitTime": pd.Timestamp(year=year, month=month, day=15),
                "ExcessVsSPY": 0.002,
            })
    result = SimpleNamespace(per_symbol={
        "AAA": SimpleNamespace(trades=pd.DataFrame(rows)),
    })
    stability = _rolling_matched_trade_stability(
        result, date(2021, 8, 14), date(2026, 8, 13),
    )
    assert stability["count"] == 3
    assert stability["fractionPositive"] == 1.0
    assert stability["method"].startswith("equal-weight mean of exact-interval")
