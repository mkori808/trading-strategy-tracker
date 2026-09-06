from engine.validate_pit_supplements import validate_rows


def test_supplements_fail_closed_when_required_provenance_is_missing() -> None:
    result = validate_rows([{"security_id": "123", "known_at": "2020-01-01"}])
    assert result["publishable"] is False
    assert "terminal_value_per_share" in result["errors"][0]


def test_valid_supplement_requires_source_and_is_publishable_alone() -> None:
    result = validate_rows([{
        "security_id": "123",
        "known_at": "2020-01-01",
        "terminal_value_per_share": 2.0,
        "terminal_value_source": "court distribution record",
        "terminal_value_type": "cash_distribution",
        "source_type": "COURT_FILING",
        "source_title": "Distribution order",
        "source_organization": "Bankruptcy court",
        "source_date": "2020-01-01",
        "retrieval_date": "2026-09-06",
        "source_identifier": "https://example.test/order",
        "evidence_section": "Order paragraph 4",
        "confidence": "HIGH",
    }])
    assert result["publishable"] is True
    assert result["validRows"] == 1


def test_duplicate_ids_and_negative_values_are_rejected() -> None:
    row = {
        "security_id": "123", "known_at": "2020-01-01", "terminal_value_per_share": 1,
        "terminal_value_source": "source", "terminal_value_type": "cash",
        "source_type": "FILING", "source_title": "title", "source_organization": "org",
        "source_date": "2020-01-01", "retrieval_date": "2026-09-06",
        "source_identifier": "id", "evidence_section": "section", "confidence": "HIGH",
    }
    result = validate_rows([row, row | {"terminal_value_per_share": -1}])
    assert result["publishable"] is False
    assert len(result["errors"]) == 1
