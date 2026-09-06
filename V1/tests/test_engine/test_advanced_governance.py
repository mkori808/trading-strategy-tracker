from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from engine import execution_db, forward_experiments, logging_db
from engine.advanced_validation import (
    _daily_returns,
    probability_backtest_overfitting,
    purged_cv_evidence,
    statistical_power_evidence,
)


def test_daily_returns_do_not_fabricate_weekend_observations() -> None:
    index = pd.to_datetime([
        "2026-08-07 10:00", "2026-08-07 16:00",
        "2026-08-10 10:00", "2026-08-10 16:00",
        "2026-08-11 16:00",
    ])
    equity = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=index)

    returns = _daily_returns(equity)

    assert list(returns.index) == [pd.Timestamp("2026-08-10"), pd.Timestamp("2026-08-11")]
    assert len(returns) == 2
from engine.cross_sectional import run_cross_sectional_backtest
from engine.data_quality import audit_frame
from engine.universe_ledger import load_manual_membership_evidence, resolve_schedule
from strategies.cross_sectional import CrossSectionalStrategy


def test_data_quality_rejects_duplicate_and_impossible_ohlc(daily_bars_factory) -> None:
    bars = daily_bars_factory(closes=[100, 101, 102])
    bad = pd.concat([bars, bars.iloc[[-1]]]).sort_index()
    bad.iloc[0, bad.columns.get_loc("High")] = 1.0

    critical, _, details = audit_frame("BAD", bad, "1d")

    assert details["duplicateTimestamps"] == 1
    assert any("duplicate" in issue for issue in critical)
    assert any("OHLC" in issue for issue in critical)


def test_data_quality_ignores_only_machine_scale_adjusted_ohlc_noise(
    daily_bars_factory,
) -> None:
    bars = daily_bars_factory(closes=[100, 101, 102])
    close_col = bars.columns.get_loc("Close")
    high_col = bars.columns.get_loc("High")
    bars.iloc[0, close_col] = 100.0
    bars.iloc[0, high_col] = np.nextafter(100.0, -np.inf)

    critical, _, details = audit_frame("ROUNDING", bars, "1d")

    assert not any("OHLC" in issue for issue in critical)
    assert details["invalidOhlcRows"] == 0

    bars.iloc[0, high_col] = 99.99
    critical, _, details = audit_frame("MATERIAL", bars, "1d")

    assert any("OHLC" in issue for issue in critical)
    assert details["invalidOhlcRows"] == 1


def test_purged_cv_and_pbo_use_time_ordered_out_of_sample_splits() -> None:
    index = pd.bdate_range("2018-01-01", periods=600)
    benchmark = pd.Series(10_000 * np.power(1.0002, np.arange(len(index))), index=index)
    strategy = pd.Series(10_000 * np.power(1.0008, np.arange(len(index))), index=index)
    passed, details = purged_cv_evidence(strategy, benchmark, family_searches=3)

    rng = np.random.default_rng(42)
    curves = {}
    for arm, drift in enumerate((0.0001, 0.0003, 0.0005, 0.0007)):
        returns = drift + rng.normal(0, 0.005, len(index))
        curves[f"arm-{arm}"] = pd.Series(10_000 * np.cumprod(1 + returns), index=index)
    pbo_pass, pbo = probability_backtest_overfitting(curves)

    assert isinstance(passed, bool)
    assert details["fractionPositiveFolds"] == 1.0
    assert pbo_pass in {True, False}
    assert 0 <= pbo["probabilityBacktestOverfitting"] <= 1
    assert pbo["splits"] > 1


def test_statistical_power_gate_uses_residual_mda_before_observed_return() -> None:
    index = pd.bdate_range("2018-01-01", periods=1_260)
    equity = pd.Series(10_000 * np.power(1.0005, np.arange(len(index))), index=index)
    passed, details = statistical_power_evidence(
        equity,
        minimum_tradable_alpha_pct=10.0,
        factor_details={
            "minimumDetectableResidualAlphaPct": 12.0,
            "annualResidualAlphaPct": 10.34,
            "residualAlphaCi95LowPct": -1.42,
            "residualAlphaCi95HighPct": 22.10,
        },
        effective_independent_bets=54.0,
        assumed_pairwise_correlation=0.35,
    )

    assert passed is False
    assert details["selectedMdaPct"] == 12.0
    assert details["minimumTradableAlphaPct"] == 10.0
    assert details["observedResidualAlphaPct"] == 10.34
    assert details["effectiveIndependentBets"] == 54.0


def test_pit_schedule_changes_the_tradeable_universe_by_date(tmp_path, daily_bars_factory) -> None:
    ledger = tmp_path / "membership.json"
    ledger.write_text(json.dumps({
        "version": 1,
        "universes": {
            "index": [
                {"effectiveStart": "2024-01-01", "effectiveEnd": "2024-01-31",
                 "symbols": ["A"], "unfetchableOrDelisted": [], "source": "snapshot-1",
                 "priceCoverageComplete": True},
                {"effectiveStart": "2024-02-01", "effectiveEnd": "2024-04-30",
                 "symbols": ["B"], "unfetchableOrDelisted": [], "source": "snapshot-2",
                 "priceCoverageComplete": True},
            ],
        },
    }), encoding="utf-8")
    schedule = resolve_schedule("index", date(2024, 1, 1), date(2024, 4, 30), path=ledger)
    assert schedule is not None

    class _AllAvailable(CrossSectionalStrategy):
        name = "PIT"
        timeframe = "1mo"

        def rebalance(self, universe_bars, as_of):
            return {symbol: 1 / len(universe_bars) for symbol in universe_bars} if universe_bars else {}

    bars = {
        "A": daily_bars_factory(closes=[100 + i for i in range(90)], start="2024-01-01"),
        "B": daily_bars_factory(closes=[50 + i for i in range(90)], start="2024-01-01"),
    }
    result = run_cross_sectional_backtest(
        "PIT", _AllAvailable(), schedule.symbols, date(2024, 1, 1), date(2024, 4, 30),
        bars_by_symbol=bars, membership_at=schedule.membership_at, universe_key="index",
    )

    january = result.rebalances.iloc[0]["holdings"]
    later = result.rebalances.iloc[-1]["holdings"]
    assert set(january) == {"A"}
    assert set(later) == {"B"}
    assert result.pit_membership_applied is True


def _forward_report() -> dict:
    return {
        "version": 5,
        "dimensions": [],
        "verdict": {
            "headline": "Identified edge", "forwardTestWorthy": True,
            "productionCapitalWorthy": False, "lifecycleStage": "paper_eligible",
            "blockers": [],
        },
        "research": {
            "manifest": {"runFingerprint": "frozen-abc", "config": {"topN": 5}},
            "validationSpec": {"primaryBenchmark": "EW", "primaryCriterion": "positive contribution"},
        },
    }


def test_manual_pit_evidence_is_loaded_as_provenance_not_a_ledger(tmp_path) -> None:
    evidence_path = tmp_path / "manual.json"
    evidence_path.write_text(
        '{"strategies":{"Dual Momentum":{"pointInTimeMembership":'
        '{"testRan":true,"gateConclusion":"warning"}}}}',
        encoding="utf-8",
    )

    evidence = load_manual_membership_evidence("Dual Momentum", evidence_path)
    assert evidence is not None
    assert evidence["pointInTimeMembership"]["testRan"] is True
    assert load_manual_membership_evidence("Unknown", evidence_path) is None


def test_locked_forward_experiment_promotes_then_demotes_and_disables(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(execution_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(execution_db, "DB_PATH", tmp_path / "execution.db")
    run_id = logging_db.log_portfolio_run(
        strategy_name="Dual Momentum", symbols=["A", "B"], start=None, end=None,
        final_equity=11_000, return_pct=10, cagr_pct=5, max_drawdown_pct=4,
        sharpe=1, sortino=1, risk_free_rate=0.03,
    )
    logging_db.attach_validation("portfolio_runs", run_id, _forward_report())
    experiment = forward_experiments.start(
        "Dual Momentum", run_id, min_calendar_days=0, min_observations=1,
    )
    promoted = forward_experiments.record_observation(
        experiment["id"], as_of=date.today(), strategy_return_pct=5,
        benchmark_return_pct=2, trade_count=20,
    )
    assert promoted["status"] == "forward_validated"
    row = logging_db.portfolio_run_history("Dual Momentum")[0]
    assert row["lifecycle_stage"] == "production_eligible"

    execution_db.set_enabled("Dual Momentum", True, "2026-01-01T00:00:00")
    demoted = forward_experiments.record_observation(
        experiment["id"], as_of=date.today() + timedelta(days=1),
        strategy_return_pct=-20, benchmark_return_pct=0, trade_count=25,
    )
    assert demoted["status"] == "falsified"
    assert execution_db.is_enabled("Dual Momentum") is False
    row = logging_db.portfolio_run_history("Dual Momentum")[0]
    assert row["lifecycle_stage"] == "suspended"


def test_fill_calibration_uses_only_reconciled_fills(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(execution_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(execution_db, "DB_PATH", tmp_path / "execution.db")
    conn = execution_db.get_connection()
    with conn:
        run_id = conn.execute(
            "INSERT INTO rebalance_runs (strategy_name, rebalance_date, trigger_source, triggered_at, status) "
            "VALUES ('S', '2026-01-01', 'manual', '2026-01-01', 'completed')"
        ).lastrowid
        for index in range(5):
            conn.execute(
                "INSERT INTO orders (rebalance_run_id, symbol, side, order_kind, client_order_id, status, "
                "filled_avg_price, reference_price, filled_qty, expected_qty) VALUES (?, 'AAA', 'buy', "
                "'notional', ?, 'filled', 100.1, 100.0, 1.0, 1.0)",
                (run_id, f"order-{index}"),
            )
    conn.close()

    calibration = execution_db.fill_calibration("AAA")

    assert calibration["calibrated"] is True
    assert calibration["fills"] == 5
    assert calibration["medianAdverseSlippageBps"] == pytest.approx(10.0)
