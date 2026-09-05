from datetime import date

from engine.build_sp500_pit_free_symbol_map import IntervalRow, build_symbol_map


def test_single_tenure_clean_ticker_gets_standard_reason():
    rows = build_symbol_map([IntervalRow("AAPL", date(2000, 1, 1), None, "qzzcl")])
    assert rows == [{
        "historical_symbol": "AAPL",
        "vendor_symbol": "AAPL",
        "start_date": "2000-01-01",
        "end_date": "",
        "reason": "standard",
    }]


def test_multi_tenure_ticker_is_flagged_ambiguous_not_silently_merged():
    rows = build_symbol_map([
        IntervalRow("RIG", date(2000, 1, 1), date(2010, 1, 1), "qzzcl"),
        IntervalRow("RIG", date(2015, 1, 1), None, "qzzcl"),
    ])
    assert len(rows) == 2
    assert all(row["reason"] == "identity_ambiguous_multi_tenure" for row in rows)


def test_legacy_bankruptcy_suffix_ticker_is_flagged_not_corrected():
    rows = build_symbol_map([IntervalRow("LEHMQ", date(1998, 1, 1), date(2008, 9, 17), "qzzcl")])
    assert rows[0]["vendor_symbol"] == "LEHMQ"  # not silently stripped to LEH
    assert rows[0]["reason"] == "legacy_suffix_needs_verification"


def test_legitimate_q_ending_ticker_is_flagged_but_not_altered():
    """CPQ (Compaq) and HPQ (Hewlett-Packard) are real tickers, not bankruptcy
    artifacts -- they still get flagged for empirical verification (the
    module cannot tell the two cases apart from the ticker string alone),
    but vendor_symbol must never be mutated on the strength of the flag."""
    rows = build_symbol_map([IntervalRow("HPQ", date(2000, 1, 1), None, "qzzcl")])
    assert rows[0]["vendor_symbol"] == "HPQ"
    assert rows[0]["reason"] == "legacy_suffix_needs_verification"


def test_both_flags_can_apply_together():
    rows = build_symbol_map([
        IntervalRow("DOWQ", date(2000, 1, 1), date(2005, 1, 1), "qzzcl"),
        IntervalRow("DOWQ", date(2015, 1, 1), None, "qzzcl"),
    ])
    assert all(
        "identity_ambiguous_multi_tenure" in row["reason"]
        and "legacy_suffix_needs_verification" in row["reason"]
        for row in rows
    )
