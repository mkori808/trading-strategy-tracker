"""API validation-job orchestration without running a real backtest."""

from __future__ import annotations

from datetime import date
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from api import main


class _InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture(autouse=True)
def clean_jobs(monkeypatch, tmp_path):
    main._validation_jobs.clear()
    main._validation_job_cache.clear()
    monkeypatch.setattr(main, "_validation_executor", _InlineExecutor())
    monkeypatch.setattr(main.logging_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(main.logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(main.execution_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(main.execution_db, "DB_PATH", tmp_path / "execution.db")


def _standard_strategy() -> str:
    return next(
        name for name in main.ALL_STRATEGY_NAMES
        if not main.is_cross_sectional(name) and not main.is_pairs(name)
    )


def test_validation_job_completes_and_exposes_result(monkeypatch):
    strategy_name = _standard_strategy()
    monkeypatch.setattr(
        main,
        "run",
        lambda name, overrides=None: {"strategyName": name, "validation": {"version": 1}},
    )

    started = main.start_validation_job("standard", strategy_name)
    completed = main.validation_job(started["jobId"])

    assert completed["status"] == "completed"
    assert completed["progressPct"] == 100
    assert completed["result"]["strategyName"] == strategy_name


def test_identical_recent_job_is_reused(monkeypatch):
    strategy_name = _standard_strategy()
    calls = []
    monkeypatch.setattr(
        main,
        "run",
        lambda name, overrides=None: calls.append(name) or {"strategyName": name},
    )

    first = main.start_validation_job("standard", strategy_name)
    second = main.start_validation_job("standard", strategy_name)

    assert second["jobId"] == first["jobId"]
    assert second["reused"] is True
    assert calls == [strategy_name]


def test_job_rejects_wrong_engine():
    strategy_name = _standard_strategy()
    with pytest.raises(HTTPException) as exc:
        main.start_validation_job("pairs", strategy_name)
    assert exc.value.status_code == 400


def test_promoting_custom_run_persists_server_side_run_configuration():
    run_id = main.logging_db.log_portfolio_run(
        strategy_name="Dual Momentum",
        symbols=["AAPL", "MSFT", "NVDA"],
        start=date(2020, 1, 1), end=date(2025, 1, 1),
        final_equity=11_000.0, return_pct=10.0, cagr_pct=2.0,
        max_drawdown_pct=5.0, sharpe=0.5, sortino=0.6,
        risk_free_rate=0.03, params={"top_n": 3}, is_canonical=False,
        universe_id="dow_pit",
    )
    report = {
        "version": main.VALIDATION_REPORT_VERSION,
        "dimensions": [],
        "verdict": {
            "headline": "Forward-test worthy",
            "forwardTestWorthy": True,
            "lifecycleStage": "paper_eligible",
            "blockers": [],
        },
        "research": {
            "manifest": {"runFingerprint": "custom-run-fingerprint", "config": {"top_n": 3}},
            "validationSpec": {
                "primaryBenchmark": "SPY",
                "primaryCriterion": "positive forward contribution",
            },
        },
    }
    main.logging_db.attach_validation("portfolio_runs", run_id, report)

    result = main.set_execution_config(main.ExecutionConfigUpdate(
        strategyName="Dual Momentum", enabled=True,
        # The server must ignore this browser value and reload the run by id.
        params={"top_n": 1}, validationRunId=run_id, inceptionPolicy="adopt",
    ))

    stored = main.execution_db.automation_config()["Dual Momentum"]
    assert json.loads(stored["params"]) == {"top_n": 3}
    assert json.loads(stored["symbols"]) == ["AAPL", "MSFT", "NVDA"]
    assert stored["universe_id"] == "dow_pit"
    assert result["validationRunId"] == run_id
    assert result["inception"]["policy"] == "adopt"
    assert result["inception"]["status"] == "pending"


def test_forward_status_does_not_claim_unstarted_test(monkeypatch):
    monkeypatch.setattr(main.forward_tracking, "load_history", lambda: [])

    status = main.forward_test_status()

    assert status["status"] == "not_started"
    assert status["observationCount"] == 0
    assert status["decision"]["triggered"] is False


def test_universe_dropdown_hides_research_matrix_entry_and_groups_markets():
    schema = main.strategy_params("9/21 EMA Crossover")
    by_id = {item["id"]: item for item in schema["universes"]}

    assert "sp500_pit" not in by_id
    assert "sp500_proxy" not in by_id
    assert by_id["sp500_current"]["category"] == "S&P indexes"
    assert by_id["crypto_majors"]["category"] == "Crypto"
    assert by_id["futures_market_proxies"]["category"] == "Futures"
    assert by_id["international_markets"]["category"] == "International"
    assert by_id["us_all_stocks_pit"]["category"] == "US markets"
    assert by_id["us_all_stocks_pit"]["runnable"] is False
    assert by_id["us_all_stocks_pit"]["pitStatus"]["ready"] is False


def test_crypto_is_disabled_for_us_session_strategy_but_available_for_daily_strategy():
    day = main.strategy_params("Opening Range Breakout (ORB)")
    daily = main.strategy_params("9/21 EMA Crossover")
    day_crypto = next(item for item in day["universes"] if item["id"] == "crypto_majors")
    daily_crypto = next(item for item in daily["universes"] if item["id"] == "crypto_majors")

    assert day_crypto["runnable"] is False
    assert "24/7 crypto" in day_crypto["unavailableReason"]
    assert daily_crypto["runnable"] is True


def test_params_endpoint_exposes_execution_timing_contract():
    standard = main.strategy_params("9/21 EMA Crossover")["timing"]
    assert standard == {
        "informationAvailability": "AT_CLOSE",
        "execution": "NEXT_OPEN",
        "usesCurrentClose": True,
        "engine": "standard",
        "exceptionReason": None,
    }

    overnight = main.strategy_params("Overnight Hold")["timing"]
    assert overnight["informationAvailability"] == "PRE_MARKET"
    assert overnight["execution"] == "SAME_CLOSE"
    assert overnight["usesCurrentClose"] is False
    assert overnight["exceptionReason"]


def test_frozen_research_hypotheses_are_registered_before_execution():
    high = main.strategy_params("52-Week-High Momentum")
    assert high["implementationStatus"] == "implemented"
    assert next(item for item in high["universes"] if item["id"] == "dow_pit")["runnable"] is True
    assert next(item for item in high["universes"] if item["id"] == "sp500_current")["runnable"] is False

    sector = main.strategy_params("Sector-Relative Momentum")
    assert sector["implementationStatus"] == "unavailable"
    assert "point-in-time" in sector["unavailableReason"]

    open_shock = main.strategy_params("Overnight Idiosyncratic Shock Reversal (Long)")
    assert open_shock["implementationStatus"] == "unavailable"
    assert "Open[T]" in open_shock["unavailableReason"]


def test_strategy_list_uses_validated_shared_capital_metrics(monkeypatch):
    name = "Negative Return + Volume Shock Reversal"
    report = {
        "version": main.VALIDATION_REPORT_VERSION,
        "research": {"canonicalPortfolioMetrics": {
            "returnPct": 23.5, "cagrPct": 4.5, "sharpe": 0.31,
            "maxDrawdownPct": 1.4,
        }},
    }
    row = {
        "trades_taken": 57, "win_rate": 0.52, "avg_win_r": 0.4,
        "avg_loss_r": -0.3, "expectancy_r": 0.12, "profit_factor": 1.45,
        "sharpe": None, "alpha_pct": -70.0, "run_at": "2026-08-13T00:00:00",
        "benchmark_gap_pct": None, "benchmark_name": "SPY",
        "benchmark_window_start": None, "benchmark_window_end": None,
        "beta": None, "cagr_pct": 3.8, "max_drawdown_pct": 0.2,
        "validation_json": json.dumps(report), "edge_verdict": "Underpowered",
        "lifecycle_stage": "preregistered", "symbols": '["AAPL"]',
        "start_date": "2021-01-01", "end_date": "2026-01-01", "params": "{}",
        "measured_start": "2021-01-04", "measured_end": "2025-12-31",
    }
    monkeypatch.setattr(main, "ALL_STRATEGY_NAMES", (name,))
    monkeypatch.setattr(main, "latest_run_per_strategy", lambda: {name: row})
    monkeypatch.setattr(main, "latest_portfolio_run_per_strategy", lambda: {})
    monkeypatch.setattr(main, "strategies_awaiting_remeasurement", lambda: set())

    [payload] = main.list_strategies()

    assert payload["returnPct"] == 23.5
    assert payload["cagrPct"] == 4.5
    assert payload["sharpe"] == 0.31
    assert payload["maxDrawdownPct"] == 1.4


def test_cross_sectional_result_describes_effective_universe_and_cadence(monkeypatch):
    index = pd.DatetimeIndex(["2026-01-02", "2026-01-09"])
    result = SimpleNamespace(
        strategy_name="Dual Momentum", symbols=["EFA", "EEM", "EWJ"],
        start=date(2026, 1, 1), end=date(2026, 1, 10),
        equity_curve=pd.Series([10_000.0, 10_100.0], index=index),
        rebalances=pd.DataFrame([
            {"date": pd.Timestamp("2026-01-02"), "holdings": {"EFA": 1.0}},
        ]),
        final_equity=10_100.0, return_pct=1.0, cagr_pct=1.0,
        max_drawdown_pct=0.0, sharpe=1.0, sortino=1.0,
        risk_free_rate=0.03, run_id=1,
        incomplete_warmup={"EWJ": 12},
    )
    report = SimpleNamespace(to_dict=lambda: {"dimensions": [], "verdict": {}})
    monkeypatch.setattr(main, "run_cross_sectional", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(main, "validate_cross_sectional", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(main, "attach_validation", lambda *_args, **_kwargs: None)

    payload = main._run_cross_sectional_payload(
        "Dual Momentum",
        main.BacktestOverrides(
            universeId="international_markets",
            params={"top_n": 2, "rebalance_frequency": "weekly"},
        ),
    )

    assert payload["universeLabel"] == "International markets"
    assert payload["rebalanceFrequency"] == "weekly"
    assert payload["targetPositionCount"] == 2
    assert payload["initialRankableCount"] == 2
    assert payload["incompleteWarmupCount"] == 1
    assert len(payload["symbols"]) == 3
