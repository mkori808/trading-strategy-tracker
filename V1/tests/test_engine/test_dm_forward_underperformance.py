from datetime import date

import pandas as pd
import pytest

from engine import dm_forward_underperformance as audit


def _inputs():
    rows = [
        {"date": "2026-08-17", "equity": 100.0},
        {"date": "2026-08-18", "equity": 99.0},
        {"date": "2026-08-19", "equity": 100.0},
    ]
    labels = {
        "2026-08-18": {"spy_trend": True, "market_volatility": False, "cross_sectional_dispersion": True},
        "2026-08-19": {"spy_trend": False, "market_volatility": True, "cross_sectional_dispersion": False},
    }
    states = pd.DataFrame(
        {"state": ["bullish_persistence", "pullback"]},
        index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
    )
    spy = {"2026-08-18": -0.005, "2026-08-19": 0.02}
    return rows, labels, states, spy


def test_compiler_excludes_intraday_inception_and_identifies_relative_miss():
    rows, labels, states, spy = _inputs()
    observations = audit.compile_observations(
        rows, date(2026, 8, 17), labels, states, spy, {}, {}
    )

    assert [row["date"] for row in observations] == ["2026-08-18", "2026-08-19"]
    assert observations[0]["underperformedSpy"] is True
    assert observations[0]["absoluteLoss"] is True
    assert observations[1]["underperformedSpy"] is True
    assert observations[1]["absoluteLoss"] is False


def test_locked_prospective_values_override_unlocked_reconstruction():
    rows, labels, states, spy = _inputs()
    locked = {
        "2026-08-19": {
            "account_return": 0.03, "spy_return": 0.01,
            "return_source": "locked_source", "spy_trend": 1,
            "market_volatility": 0, "cross_sectional_dispersion": 1,
        }
    }
    observations = audit.compile_observations(
        rows, date(2026, 8, 17), labels, states, spy, locked, {}
    )
    row = observations[1]

    assert row["accountReturn"] == 0.03
    assert row["activeReturn"] == pytest.approx(0.02)
    assert row["underperformedSpy"] is False
    assert row["returnSource"] == "locked_source"
    # The synthetic dates predate the real preregistration cutoff; mark this
    # row explicitly to exercise the report's live locked-count presentation.
    row["evidenceClass"] = "prospective_locked"
    payload = {"observations": observations, "summary": audit.analyze(observations)}
    assert "only 1 currently sealed prospective observation(s)" in audit._report(payload)


def test_analysis_compares_condition_base_rates_not_only_bad_day_counts():
    rows, labels, states, spy = _inputs()
    observations = audit.compile_observations(
        rows, date(2026, 8, 17), labels, states, spy, {}, {}
    )
    # Make the second observation outperform so the trend condition has a 100% vs 0% split.
    observations[1]["underperformedSpy"] = False
    summary = audit.analyze(observations)
    trend = next(row for row in summary["conditionSummary"] if "200-day" in row["condition"])

    assert trend["rateTrue"] == 1.0
    assert trend["rateFalse"] == 0.0
    assert trend["riskDifference"] == 1.0
    assert trend["inferenceAllowed"] is False
