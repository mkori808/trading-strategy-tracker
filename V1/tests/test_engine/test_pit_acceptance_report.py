from engine.pit_acceptance_report import build_report


def test_acceptance_report_is_blocked_and_evidence_only() -> None:
    report = build_report()
    assert report["status"] == "PIT_BLOCKED"
    assert report["enablement"] == "PIT_UNIVERSE_DISABLED"
    assert report["backtestsRun"] is False
    assert len(report["tenCaseEvidence"]) == 10
    assert all(row["knownAt"] == "UNRESOLVED" for row in report["tenCaseEvidence"])
    assert any(row["component"] == "Terminal delisting value" and row["status"] == "FAIL" for row in report["acceptanceMatrix"])

