"""Minimal regression tests for build_sharadar_db.py's deterministic logic.
Run with: python -m pytest V2/scripts/test_build_sharadar_db.py
No network calls -- pure functions only."""
from __future__ import annotations

from build_sharadar_db import _sqlite_columns_for, _sqlite_safe, _year_chunks


def test_sqlite_safe_passes_through_normal_values() -> None:
    assert _sqlite_safe(42) == 42
    assert _sqlite_safe("hello") == "hello"
    assert _sqlite_safe(None) is None
    assert _sqlite_safe(3.14) == 3.14


def test_sqlite_safe_converts_oversized_int_to_string_without_losing_precision() -> None:
    # Regression pin: a real fundamentals row triggered
    # "OverflowError: Python int too large to convert to SQLite INTEGER"
    # on first run. The exact value must survive, just as text.
    huge = 2**70 + 12345
    result = _sqlite_safe(huge)
    assert isinstance(result, str)
    assert int(result) == huge


def test_sqlite_safe_boundary_values_stay_as_int() -> None:
    assert _sqlite_safe(2**63 - 1) == 2**63 - 1
    assert _sqlite_safe(-(2**63)) == -(2**63)
    assert isinstance(_sqlite_safe(2**63), str)


def test_year_chunks_spans_full_range_inclusive() -> None:
    chunks = _year_chunks("2000-01-01", "2002-06-15")
    assert chunks == [
        ("2000-01-01", "2000-12-31"),
        ("2001-01-01", "2001-12-31"),
        ("2002-01-01", "2002-06-15"),
    ]


def test_year_chunks_single_year() -> None:
    assert _year_chunks("2020-03-01", "2020-09-01") == [("2020-03-01", "2020-09-01")]


def test_sqlite_columns_for_preserves_order_and_unions_across_rows() -> None:
    rows = [{"a": 1, "b": 2}, {"a": 1, "c": 3}]
    assert _sqlite_columns_for(rows) == ["a", "b", "c"]


def test_sqlite_columns_for_empty_rows() -> None:
    assert _sqlite_columns_for([]) == []
