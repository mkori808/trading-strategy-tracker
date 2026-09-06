from datetime import date

from engine.audit_sp500_price_coverage import membership_tenures


def test_membership_tenures_merge_adjacent_records_and_preserve_reentry():
    records = [
        {"effectiveStart": "2020-01-01", "effectiveEnd": "2020-01-31", "symbols": ["A", "B"]},
        {"effectiveStart": "2020-02-01", "effectiveEnd": "2020-02-29", "symbols": ["A"]},
        {"effectiveStart": "2020-03-01", "effectiveEnd": "2020-03-31", "symbols": ["B"]},
    ]

    tenures = membership_tenures(records)

    assert tenures["A"] == [(date(2020, 1, 1), date(2020, 2, 29))]
    assert tenures["B"] == [
        (date(2020, 1, 1), date(2020, 1, 31)),
        (date(2020, 3, 1), date(2020, 3, 31)),
    ]
