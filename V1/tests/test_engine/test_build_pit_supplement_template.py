from engine.build_pit_supplement_template import build_template


def test_template_covers_ground_truth_cases_and_is_blank() -> None:
    payload = build_template()
    assert len(payload["rows"]) == 10
    assert all(row["known_at"] == "" for row in payload["rows"])
    assert all(row["terminal_value_per_share"] is None for row in payload["rows"])

