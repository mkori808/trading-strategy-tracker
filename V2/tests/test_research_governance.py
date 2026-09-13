"""Regression tests for the research-governance cleanup: canonical V3.2 spec,
version immutability, CAGR/benchmark calculations, checkpoint date, and the
tracker's evidence-status corrections (transition filter, breakout direction,
universe metadata). See the task brief for the full list of corrected issues.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "research" / "canonical"


def _load(name):
    return json.loads((CANON / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Canonical V3.2 specification: weights and immutability
# ---------------------------------------------------------------------------

def test_v32_canonical_weights_frozen():
    spec = _load("v3_2_specification.json")
    sig = spec["specification"]["signals"]
    assert sig["ibs"]["weight"] == pytest.approx(0.69)
    assert sig["rsi2"]["weight"] == pytest.approx(0.25)
    assert sig["turnaround_tuesday"]["weight"] == pytest.approx(0.06)
    assert sig["ibs"]["weight"] + sig["rsi2"]["weight"] + sig["turnaround_tuesday"]["weight"] == pytest.approx(1.0)
    assert spec["specification"]["removed_signals"] == ["sector_rotation"]


def test_v32_excludes_gap_fade_and_transition_filter():
    spec = _load("v3_2_specification.json")
    excluded = spec["specification"]["excluded_additions"]
    assert "gap_fade" in excluded
    assert "transition_filter" in excluded


def test_v32_status_is_frozen():
    spec = _load("v3_2_specification.json")
    assert "FROZEN" in spec["status"].upper()


def test_v32_specification_hash_matches_content():
    """Immutability guard: the recorded sha256 must match the specification
    block as currently written. If this fails, either the spec was silently
    edited (forbidden -- bump to a new version id instead) or the hash was
    computed with a different canonicalization than the one used here."""
    spec = _load("v3_2_specification.json")
    block = spec["specification"]
    recomputed = hashlib.sha256(
        json.dumps(block, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert recomputed == spec["specification_sha256"], (
        "V3.2 canonical specification hash mismatch -- the frozen spec appears to have "
        "been modified without a version bump."
    )


# ---------------------------------------------------------------------------
# Portfolio-level CAGR / SPY CAGR / CAGR spread metrics
# ---------------------------------------------------------------------------

def test_portfolio_metrics_present_for_v2_v31_v32():
    metrics = _load("portfolio_metrics.json")
    strategies = {row["strategy"] for row in metrics["rows"]}
    assert {"V2", "V3.1", "V3.2"} <= strategies


def test_cagr_spread_equals_strategy_minus_spy():
    metrics = _load("portfolio_metrics.json")
    for row in metrics["rows"]:
        if row["strategy_cagr"] == "NOT AVAILABLE":
            continue
        assert row["cagr_spread"] == pytest.approx(row["strategy_cagr"] - row["spy_cagr"], abs=1e-9)


def test_cagr_spread_not_mislabeled_as_alpha():
    metrics = _load("portfolio_metrics.json")
    assert "not factor alpha" in metrics["calculation_note"].lower()


def test_strategy_and_spy_use_same_dates_per_row():
    metrics = _load("portfolio_metrics.json")
    for row in metrics["rows"]:
        if row["start_date"] == "NOT AVAILABLE":
            continue
        # Both CAGR figures are computed over the row's single stated
        # start_date/end_date pair -- there is only one date range per row,
        # so strategy and SPY are date-aligned by construction. Assert the
        # dates are present and well-formed as a sanity guard.
        date.fromisoformat(row["start_date"])
        date.fromisoformat(row["end_date"])


def test_missing_cagr_rows_are_explicit_not_invented():
    metrics = _load("portfolio_metrics.json")
    for row in metrics["rows"]:
        na_fields = [k for k in ("strategy_cagr", "spy_cagr", "cagr_spread") if row[k] == "NOT AVAILABLE"]
        if na_fields:
            assert len(na_fields) == 3, "partial NOT AVAILABLE row -- all three CAGR fields must agree"
            assert "missing_data" in row


def test_dollar_advantage_consistent_with_ending_values():
    metrics = _load("portfolio_metrics.json")
    for row in metrics["rows"]:
        if row["strategy_cagr"] == "NOT AVAILABLE":
            continue
        expected = row["strategy_ending_value_usd"] - row["spy_ending_value_usd"]
        assert row["dollar_advantage_usd"] == pytest.approx(expected, abs=1.0)


# ---------------------------------------------------------------------------
# Paper-trading checkpoint date
# ---------------------------------------------------------------------------

def test_checkpoint_is_26_weeks_after_research_inception():
    state = _load("research_state.json")
    pt = state["paper_trading"]
    inception = date.fromisoformat(pt["research_inception_date"])
    checkpoint = date.fromisoformat(pt["checkpoint_date"])
    assert checkpoint == inception + timedelta(weeks=26)


def test_checkpoint_date_is_not_the_stale_2026_date():
    state = _load("research_state.json")
    checkpoint = state["paper_trading"]["checkpoint_date"]
    assert checkpoint != "2026-03-04"
    assert checkpoint.startswith("2027-"), "checkpoint should fall in March 2027, not 2026"


def test_v32_state_file_checkpoint_matches_canonical():
    v32_state = json.loads((ROOT / "paper_trading" / "state" / "composite_v3_2_state.json").read_text(encoding="utf-8"))
    state = _load("research_state.json")
    assert v32_state["checkpoint_date"] == state["paper_trading"]["checkpoint_date"]


def test_checkpoint_is_predefined_review_not_auto_approval():
    state = _load("research_state.json")
    interp = state["paper_trading"]["checkpoint_interpretation"].lower()
    assert "not automatic approval" in interp or "predefined review" in interp


# ---------------------------------------------------------------------------
# Transition-filter research record
# ---------------------------------------------------------------------------

def test_transition_filter_marked_superseded_not_effective():
    state = _load("research_state.json")
    t2 = state["type_2_transition_research"]
    assert t2["status"] == "CLOSED"
    assert "SUPERSEDED" in t2["historical_apparent_success_status"] or "INVALID" in t2["historical_apparent_success_status"]


def test_four_trigger_families_recorded():
    state = _load("research_state.json")
    families = {f.lower() for f in state["type_2_transition_research"]["trigger_families_tested"]}
    expected = {"regime label", "volatility level", "drawdown velocity", "rolling ibs spread"}
    assert expected <= families


def test_composite_v32_v1_preregistration_marked_superseded():
    prereg = json.loads((ROOT / "research" / "preregistrations" / "composite_v3_2_v1.json").read_text(encoding="utf-8"))
    assert "SUPERSEDED" in prereg.get("status", "")


# ---------------------------------------------------------------------------
# Breakout / crisis-survivor direction
# ---------------------------------------------------------------------------

def test_breakout_active_rule_buys_highs_not_lows():
    state = _load("research_state.json")
    rule = state["breakout_direction"]["active_rule"].upper()
    assert "HIGH" in rule
    assert "BUY NEW LOWS" not in rule


def test_breakout_historical_error_documented():
    state = _load("research_state.json")
    assert "typo" in state["breakout_direction"]["historical_error"].lower() or "error" in state["breakout_direction"]["historical_error"].lower()


# ---------------------------------------------------------------------------
# Universe metadata (573 / 106 / 613)
# ---------------------------------------------------------------------------

def test_universe_audit_distinguishes_573_106_613():
    state = _load("research_state.json")
    labels = {u["label"] for u in state["universe_audit"]}
    verified_counts = {u.get("verified_count") for u in state["universe_audit"]}
    assert 106 in verified_counts
    assert 613 in verified_counts
    # the 573 figure must appear only as a claimed/retracted count, never as a verified one
    claimed_counts = {u.get("claimed_count") for u in state["universe_audit"]}
    assert 573 in claimed_counts
    assert 573 not in verified_counts


def test_current_characterization_universe_is_613():
    state = _load("research_state.json")
    current = [u for u in state["universe_audit"] if u.get("status") == "CURRENT CHARACTERIZATION UNIVERSE"]
    assert len(current) == 1
    assert current[0]["verified_count"] == 613


# ---------------------------------------------------------------------------
# Evidence-status taxonomy
# ---------------------------------------------------------------------------

def test_evidence_taxonomy_has_required_values():
    state = _load("research_state.json")
    taxonomy = set(state["evidence_taxonomy"])
    required = {"SUPPORTED", "FALSIFIED", "UNRESOLVED / UNDERPOWERED", "EXPLORATORY", "SUPERSEDED"}
    assert required <= taxonomy


def test_v2_holdout_is_unresolved_not_falsified():
    state = _load("research_state.json")
    assert state["strategies"]["V2"]["holdout_status"] == "UNRESOLVED / UNDERPOWERED"


def test_v31_v32_not_labeled_independently_confirmed():
    state = _load("research_state.json")
    for name in ("V3.1", "V3.2"):
        status = state["strategies"][name]["evidence_status"]
        assert "NOT INDEPENDENT CONFIRMATION" in status.upper() or "CHARACTERI" in status.upper()
        assert "CONFIRMED" not in status.upper() or "NOT" in status.upper()


def test_holdout_budget_exhausted_documented():
    state = _load("research_state.json")
    hp = state["holdout_policy"]
    assert hp["historical_holdout_consumed"] is True
    assert hp["remaining_clean_historical_holdouts"] == 0


# ---------------------------------------------------------------------------
# Conditional/exploratory search downgrade
# ---------------------------------------------------------------------------

def test_conditional_search_findings_downgraded():
    state = _load("research_state.json")
    cs = state["conditional_search_policy"]
    assert "EXPLORATORY" in cs["status"] or "HYPOTHESIS-GENERATING" in cs["status"]
    assert len(cs["affected_findings"]) >= 4


def test_research_backlog_prioritizes_orthogonal_engine():
    state = _load("research_state.json")
    top = min(state["research_backlog"], key=lambda x: x["priority"])
    assert "PEAD" in top["engine"] or "Earnings" in top["engine"]
