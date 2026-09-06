from engine.build_pit_evidence_worklist import build_worklist


def test_worklist_has_two_field_specific_rows_per_case() -> None:
    report = build_worklist()
    assert report["unresolvedFieldCount"] == 20
    assert len(report["rows"]) == 20
    assert {row["field_needed"] for row in report["rows"]} == {"known_at", "terminal_value_per_share"}
    assert all(row["current_status"] == "UNRESOLVED" for row in report["rows"])

