"""Insider Buying signal generation, in isolation: filing-level aggregation
(strategies/swing/insider_buy.py:filing_level_purchases), Variant A's
$/percent threshold, and Variant B's cluster resolution -- no engine, no
bars, no regime, just the EDGAR data -> signal transformation.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import pytest

from engine.data_edgar import _SCHEMA
from strategies.swing.insider_buy import (
    InsiderBuy,
    filing_level_purchases,
    passes_sector_filter,
    variant_a_signals,
    variant_b_signals,
)

CONFIG = InsiderBuy()


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.executescript(_SCHEMA)
    yield connection
    connection.close()


def _insert(conn, **overrides):
    row = dict(
        accession_no="0000000000-26-000001",
        transaction_index=0,
        issuer_cik="0000000001",
        issuer_ticker="AAA",
        issuer_name="AAA Corp",
        filer_cik="0000000002",
        filer_name="Insider One",
        filed_at="2026-03-02T10:00:00-05:00",
        signal_date="2026-03-02",
        transaction_date="2026-03-01",
        transaction_code="P",
        shares_transacted=1000.0,
        price_per_share=50.0,
        transaction_value=50_000.0,
        shares_owned_following=11000.0,
        shares_owned_before=10000.0,
        pct_change_holdings=10.0,
        ownership_nature="D",
        security_title="Common Stock",
        form_url="https://example.com",
        fetched_at="2026-03-02T10:05:00+00:00",
    )
    row.update(overrides)
    conn.execute(
        f"""INSERT INTO form4_transactions ({",".join(row)})
            VALUES ({",".join("?" for _ in row)})""",
        list(row.values()),
    )
    conn.commit()


# --- filing_level_purchases: aggregation across lots in one filing ---------


def test_filing_level_purchases_aggregates_multi_lot_filing(conn):
    """Two lots in the same filing must sum to one filing-level row, with
    pct_change_holdings computed from the EARLIEST lot's before-value, not
    a single lot's own before/after."""
    _insert(
        conn, transaction_index=0, transaction_date="2026-03-01",
        shares_transacted=500.0, price_per_share=50.0, transaction_value=25_000.0,
        shares_owned_before=10_000.0,
    )
    _insert(
        conn, transaction_index=1, transaction_date="2026-03-01",
        shares_transacted=500.0, price_per_share=52.0, transaction_value=26_000.0,
        shares_owned_before=10_500.0,  # after the first lot -- not the filing's baseline
    )
    df = filing_level_purchases(conn, ["AAA"], date(2026, 1, 1), date(2026, 12, 31))
    assert len(df) == 1
    row = df.iloc[0]
    assert row["total_value"] == 51_000.0
    assert row["total_shares"] == 1000.0
    assert row["pct_change_holdings"] == pytest.approx(10.0)  # 1000 / 10000 * 100


def test_filing_level_purchases_scopes_to_requested_symbols_and_window(conn):
    _insert(conn, issuer_ticker="AAA", signal_date="2026-03-02")
    _insert(conn, accession_no="acc2", issuer_ticker="BBB", signal_date="2026-03-02")
    _insert(conn, accession_no="acc3", issuer_ticker="AAA", signal_date="2020-01-01")  # out of window
    df = filing_level_purchases(conn, ["AAA"], date(2026, 1, 1), date(2026, 12, 31))
    assert list(df["issuer_ticker"]) == ["AAA"]


def test_filing_level_purchases_empty_symbols_returns_empty_frame(conn):
    df = filing_level_purchases(conn, [], date(2026, 1, 1), date(2026, 12, 31))
    assert df.empty


# --- Variant A: significant single buy --------------------------------------


def test_variant_a_qualifies_on_dollar_threshold():
    filings = pd.DataFrame([
        {"total_value": 2_000_000.0, "pct_change_holdings": 1.0},
        {"total_value": 100.0, "pct_change_holdings": 0.5},
    ])
    result = variant_a_signals(filings, CONFIG)
    assert len(result) == 1
    assert result.iloc[0]["total_value"] == 2_000_000.0


def test_variant_a_qualifies_on_percent_threshold_alone():
    filings = pd.DataFrame([{"total_value": 100.0, "pct_change_holdings": 15.0}])
    result = variant_a_signals(filings, CONFIG)
    assert len(result) == 1


def test_variant_a_excludes_filing_below_both_thresholds():
    filings = pd.DataFrame([{"total_value": 100.0, "pct_change_holdings": 1.0}])
    result = variant_a_signals(filings, CONFIG)
    assert result.empty


def test_variant_a_handles_missing_pct_change_as_zero():
    """A filing with no computable 'before' baseline (shares_owned_before
    missing/zero) must fail the percent leg, not error or silently pass."""
    filings = pd.DataFrame([{"total_value": 100.0, "pct_change_holdings": None}])
    result = variant_a_signals(filings, CONFIG)
    assert result.empty


# --- Variant B: cluster buy ---------------------------------------------------


def _filing_row(ticker, day, filer, accession):
    return {
        "issuer_ticker": ticker,
        "signal_date": pd.Timestamp(day),
        "filer_cik": filer,
        "accession_no": accession,
        "filed_at": f"{day}T10:00:00-05:00",
        "earliest_transaction_date": day,
    }


def test_variant_b_fires_when_three_distinct_insiders_cluster():
    filings = pd.DataFrame([
        _filing_row("AAA", "2026-03-01", "cik1", "acc1"),
        _filing_row("AAA", "2026-03-02", "cik2", "acc2"),
        _filing_row("AAA", "2026-03-03", "cik3", "acc3"),
    ])
    result = variant_b_signals(filings, CONFIG)
    assert len(result) == 1
    assert result.iloc[0]["signal_date"] == date(2026, 3, 3)
    assert result.iloc[0]["completing_accession_no"] == "acc3"
    assert result.iloc[0]["cluster_size"] == 3


def test_variant_b_does_not_fire_with_only_two_distinct_insiders():
    filings = pd.DataFrame([
        _filing_row("AAA", "2026-03-01", "cik1", "acc1"),
        _filing_row("AAA", "2026-03-02", "cik2", "acc2"),
    ])
    assert variant_b_signals(filings, CONFIG).empty


def test_variant_b_same_insider_twice_does_not_count_as_two():
    filings = pd.DataFrame([
        _filing_row("AAA", "2026-03-01", "cik1", "acc1"),
        _filing_row("AAA", "2026-03-02", "cik1", "acc1b"),  # same filer again
        _filing_row("AAA", "2026-03-03", "cik2", "acc2"),
    ])
    assert variant_b_signals(filings, CONFIG).empty


def test_variant_b_ignores_purchases_outside_the_window():
    filings = pd.DataFrame([
        _filing_row("AAA", "2026-03-01", "cik1", "acc1"),
        _filing_row("AAA", "2026-03-02", "cik2", "acc2"),
        _filing_row("AAA", "2026-03-10", "cik3", "acc3"),  # 9 days later -- outside 5-day window
    ])
    assert variant_b_signals(filings, CONFIG).empty


def test_variant_b_resets_after_firing_greedily():
    """A 4th distinct insider right after a completed cluster starts a fresh
    count rather than firing again immediately -- the documented greedy,
    non-overlapping resolution."""
    filings = pd.DataFrame([
        _filing_row("AAA", "2026-03-01", "cik1", "acc1"),
        _filing_row("AAA", "2026-03-02", "cik2", "acc2"),
        _filing_row("AAA", "2026-03-03", "cik3", "acc3"),  # completes cluster 1
        _filing_row("AAA", "2026-03-04", "cik4", "acc4"),  # only 1 in the new window
    ])
    result = variant_b_signals(filings, CONFIG)
    assert len(result) == 1


def test_variant_b_scopes_clusters_per_issuer():
    filings = pd.DataFrame([
        _filing_row("AAA", "2026-03-01", "cik1", "acc1"),
        _filing_row("BBB", "2026-03-02", "cik2", "acc2"),
        _filing_row("CCC", "2026-03-03", "cik3", "acc3"),
    ])
    # Three distinct insiders, but across three different issuers -- no cluster.
    assert variant_b_signals(filings, CONFIG).empty


# --- sector filter stub -------------------------------------------------------


def test_sector_filter_is_a_documented_noop_when_unset():
    assert passes_sector_filter("AAPL", sectors=None) is True
