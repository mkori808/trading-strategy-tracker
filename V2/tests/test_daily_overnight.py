"""Guardrails for the isolated prospective Track 4 daily IBS experiment."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


PAPER = Path(__file__).resolve().parents[1] / "paper_trading"
sys.path.insert(0, str(PAPER))
import daily_overnight_engine as daily


def test_target_replacement_is_symmetric_and_bounded():
    assert daily._target_replacement(set(), {"A"}) == 1.0
    assert daily._target_replacement({"A", "B"}, {"A", "B"}) == 0.0
    assert daily._target_replacement({"A", "B"}, {"B", "C"}) == 0.5


def test_inactive_state_refuses_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(daily, "STATE_DIR", tmp_path)
    first = daily.init_state("ibs_daily_overnight_full", 100_000, pd.Timestamp("2026-09-18"))
    assert first["launch_authorized"] is False
    assert first["active_overnight"] is None
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        daily.init_state("ibs_daily_overnight_full", 100_000, pd.Timestamp("2026-09-18"))


def test_append_only_ledger_rejects_duplicate_record_date(monkeypatch, tmp_path):
    monkeypatch.setattr(daily, "LEDGER_DIR", tmp_path)
    row = {"record_date": "2026-09-18", "fill_date": "2026-09-21"}
    daily._append_ledger("ibs_daily_overnight_full", row)
    with pytest.raises(RuntimeError, match="immutable ledger"):
        daily._append_ledger("ibs_daily_overnight_full", row)


def test_record_is_blocked_without_explicit_launch_authorization(monkeypatch):
    monkeypatch.setattr(daily, "load_state", lambda track: {"launch_authorized": False})
    with pytest.raises(RuntimeError, match="not authorized"):
        daily.record_close_target(
            "ibs_daily_overnight_full", {"daily_overnight": {"launch_authorized": False}}, pd.Timestamp("2026-09-18")
        )


def test_lookahead_invalid_timing_blocks_launch_and_costs_are_fixed():
    cfg = {
        "daily_overnight": {
            "launch_authorized": True,
            "execution_audit": {"status": "LOOKAHEAD_INVALID"},
            "cost_sensitivity_bps_round_trip": [2, 5, 10],
        }
    }
    assert daily.launch_authorized(cfg) is False
    assert daily.cost_sensitivities(cfg) == [
        {"round_trip_bps": 2.0, "annualized_cost_pct": 5.04},
        {"round_trip_bps": 5.0, "annualized_cost_pct": 12.6},
        {"round_trip_bps": 10.0, "annualized_cost_pct": 25.2},
    ]
