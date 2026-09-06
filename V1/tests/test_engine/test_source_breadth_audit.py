from datetime import date

import pytest

from engine.source_breadth_audit import build_report, projected_mda, years_required


def test_projected_mda_declines_with_more_history() -> None:
    assert projected_mda(date(2010, 1, 1)) < projected_mda(date(2021, 1, 1))


def test_required_history_is_about_ten_years() -> None:
    assert years_required() == pytest.approx(10.27, abs=0.02)


def test_source_audit_is_evidence_only_and_fails_closed() -> None:
    report = build_report()
    assert report["verdict"] == "STOP_SCREENED_OUT_BREADTH"
    assert report["backtestsRun"] is False
    assert report["holdoutConsumed"] is False
    assert report["preregistrationsCreated"] is False
    assert all(row["status"] != "COMPLETE" for row in report["sources"])

