"""Regression tests directly on the Strategy_Research_Tracker_v2 workbook,
guarding against the stale references this cleanup corrected. Skips cleanly
if the tracker file isn't present at the expected repo-relative location."""
from __future__ import annotations

from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

ROOT = Path(__file__).resolve().parents[2]  # trading-strategy-tracker/
CANDIDATES = sorted(ROOT.glob("Strategy_Research_Tracker_v2*.xlsx"))

pytestmark = pytest.mark.skipif(not CANDIDATES, reason="tracker workbook not found in repo root")


@pytest.fixture(scope="module")
def wb():
    return openpyxl.load_workbook(CANDIDATES[-1], data_only=True)


def _all_strings(ws):
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str):
                yield c.coordinate, c.value


def test_no_stale_checkpoint_date(wb):
    for sheet in wb.sheetnames:
        for coord, v in _all_strings(wb[sheet]):
            assert "2026-03-04" not in v, f"stale checkpoint date in {sheet}!{coord}"


def test_no_unverified_bear_high_vol_figure(wb):
    for sheet in wb.sheetnames:
        for coord, v in _all_strings(wb[sheet]):
            assert "89.4%/yr t=4.21" not in v, f"uncorrected unverified figure in {sheet}!{coord}"


def test_no_active_buy_new_lows_spec(wb):
    """'buy new LOWS' may only appear inside a note that also explains it was
    a typo/error -- never as a bare active instruction."""
    ss = wb["Strategy Summary"]
    for coord, v in _all_strings(ss):
        if "buy new LOWS" in v:
            assert "typo" in v.lower() or "error" in v.lower(), (
                f"Strategy Summary!{coord} contains an unqualified 'buy new LOWS'"
            )


def test_gap_fade_not_silently_added_to_v32(wb):
    for sheet in wb.sheetnames:
        for coord, v in _all_strings(wb[sheet]):
            assert "5th signal in composite V3.2 at 26-week checkpoint" not in v, (
                f"stale Gap Fade -> V3.2 auto-merge language in {sheet}!{coord}"
            )


def test_no_broken_hex_color_status_cells(wb):
    bsl = wb["Behavioral Signals Library"]
    hex_like = {"FCE4D6", "FFF9C4"}
    for coord, v in _all_strings(bsl):
        assert v not in hex_like, f"{coord} still holds a raw hex color instead of a status label"


def test_current_state_sheet_exists_and_first(wb):
    assert wb.sheetnames[0] == "Current State & Objective"


def test_v2_holdout_row_not_labeled_falsified(wb):
    rd = wb["Run Detail"]
    # Run Status column is AB; row 34 is the V2 holdout row.
    assert rd["AB34"].value == "UNRESOLVED / UNDERPOWERED"


def test_transition_filter_row_marked_superseded(wb):
    rd = wb["Run Detail"]
    assert "SUPERSEDED" in (rd["AB49"].value or "")


def test_run_detail_has_spy_cagr_and_spread_columns(wb):
    rd = wb["Run Detail"]
    headers = {rd.cell(4, c).value for c in range(1, rd.max_column + 1)}
    assert any(h and "SPY CAGR" in h for h in headers)
    assert any(h and "CAGR Spread" in h for h in headers)
