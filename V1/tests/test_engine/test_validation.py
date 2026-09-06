from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from engine.validation import (
    STABILITY_MIN_FRACTION_POSITIVE,
    ValidationCheck,
    ValidationDimension,
    _equal_weight_curve,
    _finalize,
    _random_portfolios,
    _rolling_stability,
    _stability_gate_passes,
)


def _passing_power() -> ValidationDimension:
    return ValidationDimension("power", "Power", [
        ValidationCheck(
            "statistical_power", "Can resolve edge", "pass", "yes", required=True,
        ),
    ])


def test_required_unresolved_check_blocks_identified_edge() -> None:
    report = _finalize([_passing_power(),
        ValidationDimension("evidence", "Evidence", [
            ValidationCheck("beats_equal_weight", "Beats EW", "pass", "yes", required=True),
            ValidationCheck("beats_random", "Beats random", "pass", "yes", required=True),
            ValidationCheck("parameter_ridge", "Broad ridge", "pass", "yes", required=True),
            ValidationCheck("historical_stability", "Stable", "pass", "yes", required=True),
            ValidationCheck("pit_membership", "PIT integrity", "unresolved", "missing", required=True),
        ])
    ])

    assert report.verdict.identified_edge is False
    assert report.verdict.signal_edge == "Unresolved"
    assert report.verdict.forward_test_worthy is True
    assert report.verdict.blockers == ["PIT integrity"]
    assert report.verdict.blocking_checks[0]["status"] == "unresolved"


def test_all_required_checks_must_pass_without_composite_score() -> None:
    report = _finalize([_passing_power(),
        ValidationDimension("gates", "Gates", [
            ValidationCheck("coverage", "Coverage", "pass", "ok", required=True),
            ValidationCheck("benchmark", "Benchmark", "fail", "no", required=True),
        ])
    ])
    assert report.verdict.identified_edge is False
    assert report.verdict.blockers == ["Benchmark"]


def test_failed_warmup_validity_blocks_holdout_pass_from_reading_clean() -> None:
    # Regression test for two real runs (Dual Momentum x sp500_current, x
    # sp600_current) that reported chronological_oos "pass" (holdout
    # arithmetic looked fine) while warmup_validity had failed (some
    # constituents lacked the required lookback -- the DOW zero-bar case
    # this gate exists for). The holdout number is not usable evidence when
    # it was computed on incomplete data, so it must not read as a clean
    # pass even though identified_edge/forward were already blocked
    # elsewhere by warmup_validity's own required-gate status.
    report = _finalize([_passing_power(),
        ValidationDimension("performance", "Performance", [
            ValidationCheck(
                "warmup_validity", "Warmup validity", "fail",
                "2 constituent(s) lacked full starting-date warmup: DOW, XYZ",
                required=True,
            ),
        ]),
        ValidationDimension("holdout", "Holdout", [
            ValidationCheck(
                "chronological_oos", "Untouched holdout and walk-forward windows",
                "pass", "The final 20% must beat the benchmark", required=True,
            ),
        ]),
    ])

    holdout = next(
        c for d in report.dimensions for c in d.checks if c.key == "chronological_oos"
    )
    assert holdout.status == "unresolved"
    assert "warmup" in holdout.summary.lower()
    assert report.verdict.identified_edge is False


def test_holdout_pass_untouched_when_warmup_validity_passes() -> None:
    report = _finalize([_passing_power(),
        ValidationDimension("performance", "Performance", [
            ValidationCheck("warmup_validity", "Warmup validity", "pass", "ok", required=True),
        ]),
        ValidationDimension("holdout", "Holdout", [
            ValidationCheck(
                "chronological_oos", "Untouched holdout and walk-forward windows",
                "pass", "The final 20% must beat the benchmark", required=True,
            ),
        ]),
    ])

    holdout = next(
        c for d in report.dimensions for c in d.checks if c.key == "chronological_oos"
    )
    assert holdout.status == "pass"


def test_multiple_testing_failure_blocks_identified_edge_but_not_by_score() -> None:
    report = _finalize([_passing_power(),
        ValidationDimension("gates", "Gates", [
            ValidationCheck("coverage", "Coverage", "pass", "ok", required=True),
            ValidationCheck(
                "multiple_testing", "Actual search-family correction", "fail",
                "corrected p is too large", required=True,
            ),
        ])
    ])

    assert report.verdict.identified_edge is False
    assert report.verdict.blockers == ["Actual search-family correction"]


def test_report_json_uses_frontend_field_names() -> None:
    payload = _finalize([_passing_power(),
        ValidationDimension("gates", "Gates", [
            ValidationCheck("coverage", "Coverage", "pass", "ok", required=True),
        ])
    ]).to_dict()
    assert "generatedAt" in payload
    assert payload["verdict"]["identifiedEdge"] is True
    assert "identified_edge" not in payload["verdict"]
    assert payload["version"] == 5


def test_report_json_normalizes_numpy_evidence_scalars() -> None:
    report = _finalize([_passing_power(),
        ValidationDimension("measurement", "Measurement", [
            ValidationCheck(
                "measurement_integrity", "Reconciliation", "pass", "ok",
                required=True,
                value=np.float64(3.5),
                details={"reconciliationPass": np.bool_(True)},
            ),
        ]),
    ])

    payload = report.to_dict()
    json.dumps(payload)
    check = payload["dimensions"][1]["checks"][0]
    assert type(check["value"]) is float
    assert type(check["details"]["reconciliationPass"]) is bool


def test_missing_power_is_a_required_unresolved_gate() -> None:
    report = _finalize([
        ValidationDimension("gates", "Gates", [
            ValidationCheck("coverage", "Coverage", "pass", "ok", required=True),
        ])
    ])

    assert report.verdict.identified_edge is False
    assert report.verdict.signal_edge == "Unresolved"
    assert report.dimensions[0].key == "power"
    assert report.verdict.blocking_checks[0]["key"] == "statistical_power"


def test_underpowered_design_is_named_without_implying_signal_evidence() -> None:
    report = _finalize([
        ValidationDimension("power", "Power", [
            ValidationCheck(
                "statistical_power", "Can resolve edge", "fail", "MDA exceeds threshold",
                required=True, details={"selectedMdaPct": 12.0, "minimumTradableAlphaPct": 2.0},
            ),
        ]),
    ])

    assert report.verdict.identified_edge is False
    assert report.verdict.signal_edge == "Underpowered"
    assert report.verdict.headline == "Underpowered - MDA 12.00%/yr exceeds 2.00%/yr"


def test_uncomputable_mda_is_named_explicitly() -> None:
    report = _finalize([
        ValidationDimension("power", "Power", [
            ValidationCheck(
                "statistical_power", "Can resolve edge", "unresolved", "too few daily observations",
                required=True, details={"reason": "at least 30 daily return observations are required"},
            ),
        ]),
    ])

    assert report.verdict.signal_edge == "Unresolved"
    assert report.verdict.headline == "Power unresolved - MDA not computable"


def test_random_portfolio_null_is_deterministic_and_concentration_matched() -> None:
    index = pd.date_range("2020-01-01", periods=8, freq="D")
    close = pd.DataFrame({
        "A": range(10, 18),
        "B": range(20, 28),
        "C": range(30, 38),
        "D": range(40, 48),
    }, index=index, dtype=float)
    strategy_return = 50.0
    first = _random_portfolios(close, 2, strategy_return, simulations=50, seed_material="same")
    second = _random_portfolios(close, 2, strategy_return, simulations=50, seed_material="same")
    assert first == second
    assert first is not None
    assert first["simulations"] == 50
    assert 0 < first["empiricalP"] <= 1


def test_rolling_stability_reports_mean_without_best_window() -> None:
    index = pd.date_range("2010-01-01", "2018-12-31", freq="B")
    # The strategy grows faster than equal weight in every possible window.
    strategy = pd.Series((1.0005 ** pd.RangeIndex(len(index))).to_numpy(), index=index)
    equal_weight = pd.Series((1.0002 ** pd.RangeIndex(len(index))).to_numpy(), index=index)
    result = _rolling_stability(strategy, equal_weight, 2010, 2019)
    assert result is not None
    assert result["count"] >= 3
    assert result["fractionPositive"] == 1.0
    assert result["meanExcludingBestPct"] > 0


def test_rolling_stability_gate_matches_recorded_dual_momentum_evidence() -> None:
    # Pins the gate against the exact numbers recorded in
    # data/manual_validation_evidence.json ("Dual Momentum" ->
    # "historicalStability") as of 2026-08-11. If this test starts failing
    # because the threshold changed, that's expected -- update
    # STABILITY_MIN_FRACTION_POSITIVE deliberately and note why in
    # LESSONS.md. If it fails because this literal payload no longer
    # matches the JSON file, the recorded evidence changed and this copy
    # should be updated to match it, not deleted.
    stability = {
        "windows": 14,
        "positiveWindows": 12,
        "fractionPositive": 0.8571428571,
        "meanContributionPct": 10.5,
        "medianContributionPct": 9.5,
        "meanExcludingBestPct": 8.2,
        "worstContributionPct": -16.3,
        "bestContributionPct": 40.5,
    }
    assert STABILITY_MIN_FRACTION_POSITIVE == 0.70
    assert _stability_gate_passes(stability) is True

    # A strategy sitting just below the fraction-positive bar must still fail
    # even with a strong median/mean, so the gate isn't accidentally reduced
    # to only the two other conditions.
    borderline = {**stability, "fractionPositive": 0.69}
    assert _stability_gate_passes(borderline) is False

    # A negative mean-excluding-best (edge concentrated in one outlier
    # window) must fail even with fractionPositive and median both healthy.
    concentrated = {**stability, "meanExcludingBestPct": -0.1}
    assert _stability_gate_passes(concentrated) is False


def test_equal_weight_curve_keeps_missing_sleeve_out_until_it_exists() -> None:
    index = pd.date_range("2020-01-01", periods=3, freq="D")
    close = pd.DataFrame({"A": [10.0, 11.0, 12.0], "B": [None, 20.0, 22.0]}, index=index)
    curve = _equal_weight_curve(close)
    assert curve is not None
    assert curve.iloc[0] == 1.0
    assert curve.iloc[-1] == pytest.approx(1.15)
