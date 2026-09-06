from __future__ import annotations

import json

import pytest

from engine.pit_all_stocks import (
    MARKET_CAP_COLUMN,
    REQUIRED_DAILY_COLUMNS,
    REQUIRED_MANIFEST_FIELDS,
    REQUIRED_MANIFEST_TRUE,
    REQUIRED_SECURITY_COLUMNS,
)
from engine.reconstruct_adjustment import (
    ReconstructionInputs,
    event_diagnostics,
    reconstruct_adjusted_close,
)
from engine.sharadar_client import SharadarClient
from engine.validate_sharadar_contract import (
    AMBIGUOUS,
    DOCUMENTED_COLUMNS,
    MISSING,
    PRESENT,
    UNTESTABLE,
    analyze_split_semantics,
    contract_field_findings,
    find_identity_evidence,
    manifest_findings,
    market_cap_finding,
    tier_capabilities,
)


def test_every_contract_requirement_is_read_from_the_loader_contract() -> None:
    findings = contract_field_findings(
        DOCUMENTED_COLUMNS, tier="free_5y_current_dow", identity_verified=False
    )
    by_area = {
        area: {row["field"] for row in findings if row["area"] == area}
        for area in ("security_history", "daily")
    }
    assert by_area["security_history"] == REQUIRED_SECURITY_COLUMNS
    assert by_area["daily"] == REQUIRED_DAILY_COLUMNS

    manifest = manifest_findings(
        DOCUMENTED_COLUMNS, tier="free_5y_current_dow", identity_verified=False
    )
    assert {row["field"] for row in manifest} == {
        *REQUIRED_MANIFEST_TRUE,
        *REQUIRED_MANIFEST_FIELDS,
        "schemaVersion",
        "priceBasis",
    }


def test_free_tier_incapable_checks_are_never_reported_as_passes() -> None:
    rows = {row["check"]: row for row in tier_capabilities("free_5y_current_dow")}
    for key in (
        "pre_2021_history",
        "non_dow_coverage",
        "delisting_returns",
        "historical_delisted_population",
    ):
        assert rows[key]["status"] == UNTESTABLE
        assert rows[key]["status"] != PRESENT

    fields = contract_field_findings(
        DOCUMENTED_COLUMNS, tier="free_5y_current_dow", identity_verified=False
    )
    delisting = next(row for row in fields if row["field"] == "DelistingReturn")
    assert delisting["status"] == UNTESTABLE

    unknown = tier_capabilities("unknown")
    assert all(row["status"] != PRESENT for row in unknown)


def test_permaticker_needs_both_tickers_and_linking_action() -> None:
    one_sided = find_identity_evidence(
        [{"action": "tickerchangefrom", "ticker": "OLD", "contraticker": "NEW"}],
        [{"ticker": "NEW", "permaticker": "123"}],
    )
    assert one_sided["verified"] is False

    verified = find_identity_evidence(
        [{"action": "tickerchangefrom", "ticker": "OLD", "contraticker": "NEW"}],
        [
            {"ticker": "OLD", "permaticker": "123"},
            {"ticker": "NEW", "permaticker": "123"},
        ],
    )
    assert verified["verified"] is True
    assert verified["examples"][0]["permaticker"] == "123"


def test_wmt_split_pins_adjustment_and_future_action_semantics() -> None:
    # Recorded from the authenticated Sharadar free-tier response on
    # 2026-09-05. WMT's 2024-02-26 3:1 split breaks the raw price by ~3x while
    # split-adjusted and fully adjusted prices remain continuous. The separate
    # query ending 2024-02-23 returned the same pre-split rows, already divided
    # by three, proving query `to` is not an as-of adjustment cutoff.
    around = [
        {"date": "2024-02-23", "closeunadj": 175.56, "close": 58.52, "closeadj": 56.919},
        {"date": "2024-02-26", "closeunadj": 59.60, "close": 59.60, "closeadj": 57.970},
    ]
    pre_event_only = [
        {"date": "2024-02-22", "closeunadj": 175.41, "close": 58.47, "closeadj": 56.871},
        {"date": "2024-02-23", "closeunadj": 175.56, "close": 58.52, "closeadj": 56.919},
    ]
    result = analyze_split_semantics(around, pre_event_only)

    assert result["status"] == PRESENT
    assert result["checks"] == {
        "unadjustedShowsSplitDiscontinuity": True,
        "splitAdjustedIsContinuous": True,
        "fullyAdjustedIsContinuous": True,
        "preEventOnlyResponseAlreadyIncludesFutureSplit": True,
    }


def test_adjusted_ohl_is_ambiguous_because_sharadar_requires_imputation() -> None:
    findings = contract_field_findings(
        DOCUMENTED_COLUMNS, tier="full_history_or_broader", identity_verified=False
    )
    by_field = {row["field"]: row for row in findings if row["area"] == "daily"}
    assert by_field["Open"]["status"] == AMBIGUOUS
    assert "imputed" in by_field["Open"]["reason"]
    assert by_field["Close"]["status"] == PRESENT
    assert by_field["RawClose"]["status"] == PRESENT


def test_market_cap_is_reported_separately_and_conditionally() -> None:
    # MARKET_CAP_COLUMN is deliberately excluded from REQUIRED_DAILY_COLUMNS:
    # engine.pit_all_stocks only raises on a missing MarketCap column once a
    # minimum-market-cap filter is requested, so it must never be folded into
    # the always-required daily-column set the loader enforces unconditionally.
    assert MARKET_CAP_COLUMN not in REQUIRED_DAILY_COLUMNS

    present = market_cap_finding({"daily": {"marketcap", "ticker", "date"}})
    assert present["status"] == PRESENT
    assert present["field"] == MARKET_CAP_COLUMN
    assert present["source"] == "daily.marketcap"

    missing = market_cap_finding({"daily": {"ticker", "date"}})
    assert missing["status"] == MISSING


def test_reconstruction_uses_split_adjusted_basis_not_raw_price() -> None:
    # A dividend dated BEFORE a later split must have its percentage impact
    # computed against the split-adjusted price (raw / cumulative split
    # factor in force at that date), not the raw price -- confirmed
    # empirically against every one of HON's real distribution events (see
    # the module docstring). Raw closeunadj=100 the day before the dividend;
    # a later 0.5-ratio split means split-adjusted price there is
    # 100/0.5=200, so a $1 dividend is a 0.5% cut (200->199), not the ~1%
    # cut a raw-price basis would wrongly compute (100->99).
    inputs = ReconstructionInputs(
        stocks_rows=[
            {"date": "2024-01-01", "closeunadj": 100.0},
            {"date": "2024-01-02", "closeunadj": 99.0},
            {"date": "2024-01-03", "closeunadj": 50.0},
        ],
        action_rows=[
            {"date": "2024-01-02", "action": "dividend", "value": 1.0},
            {"date": "2024-01-03", "action": "split", "value": 0.5},
        ],
    )
    frame = reconstruct_adjusted_close(inputs)
    assert frame.loc["2024-01-01", "reconstructedClose"] == pytest.approx(199.0)


def test_hon_event_diagnostics_isolate_the_2026_06_29_compound_event() -> None:
    # Recorded from the authenticated Sharadar free-tier response on
    # 2026-09-05, reduced to the rows event_diagnostics actually reads.
    # HON's 21 ordinary/simple distribution events match the split-adjusted-
    # basis formula to within a few basis points; only the compound event
    # (a 0.5-ratio "split" coincident with the HONA spinoffdividend) is off,
    # by ~3.26% -- and that residual alone explains the ~3.24% aggregate
    # median divergence this project's Dow cross-check found for HON,
    # rather than 22 small disagreements spread evenly across five years.
    stocks_rows = [
        {"date": "2022-11-09", "close": 417.26, "closeadj": 417.26 / 0.469, "closeunadj": 208.63},
        {"date": "2022-11-10", "close": 417.26, "closeadj": 417.26 / 0.469 * 1.00479, "closeunadj": 208.63},
        {"date": "2026-06-26", "close": 464.42, "closeadj": 235.02, "closeunadj": 232.21},
        {"date": "2026-06-29", "close": 227.80, "closeadj": 227.12, "closeunadj": 227.80},
    ]
    action_rows = [
        {"date": "2022-11-10", "action": "dividend", "value": 2.06},
        {"date": "2026-06-29", "action": "split", "value": 0.5},
        {"date": "2026-06-29", "action": "spinoffdividend", "value": 221.01},
    ]
    diagnostics = {
        row["date"]: row
        for row in event_diagnostics(ReconstructionInputs(stocks_rows, action_rows))
    }
    assert abs(diagnostics["2022-11-10"]["residualPct"]) < 0.05
    compound = diagnostics["2026-06-29"]
    assert compound["action"] == "spinoffdividend"
    assert compound["residualPct"] == pytest.approx(3.2607, abs=0.01)


def test_client_cache_is_separate_and_never_contains_api_key(tmp_path, monkeypatch) -> None:
    client = SharadarClient(api_key="secret-key", cache_dir=tmp_path, requests_per_second=1000)
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda table, parameters: {"count": 1, "data": [{"ticker": "WMT"}]},
    )

    first = client.query("tickers", ticker="WMT")
    second = client.query("tickers", ticker="WMT")

    assert first.from_cache is False
    assert second.from_cache is True
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    assert "secret-key" not in files[0].read_text(encoding="utf-8")
    assert "secret-key" not in files[0].name
    assert json.loads(files[0].read_text(encoding="utf-8"))["data"] == [{"ticker": "WMT"}]
