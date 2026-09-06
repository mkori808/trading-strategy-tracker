"""Forward-test operating infrastructure for the frozen DM/MRM strategies:
multi-sleeve provenance, append-only decision/outcome ledgers, forward-only
NAV, and the scorecard. None of this touches the frozen weighting formula
in engine/dm_mrm_vol_scaled.py -- these tests exist to prove the SEPARATE
infrastructure around it behaves correctly.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from engine import dm_mrm_forward as fwd
from engine import dm_mrm_vol_scaled as vs
from engine import execution


def _series(values, start="2022-01-03"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx)


def _long_history(seed=1, n=400):
    rng = np.random.default_rng(seed)
    dm = _series(list(rng.normal(0.0003, 0.015, n)))
    mrm = _series(list(rng.normal(0.0002, 0.012, n)))
    return dm, mrm


# --- multi-sleeve provenance -------------------------------------------------


def test_decision_snapshot_records_both_sleeves_separately():
    dm_ret, mrm_ret = _long_history()
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    reset_date = pd.Timestamp(next(d.date for d in decisions if d.sufficient_data))
    snap = fwd.build_decision_snapshot(
        dm_ret, mrm_ret, reset_date,
        dm_holdings={"AAPL": 0.2, "MSFT": 0.2}, mrm_holdings={"NVDA": 0.2},
    )
    assert snap.dmSleeve["strategyName"] == "Dual Momentum"
    assert snap.mrmSleeve["strategyName"] == "Market-Residual Momentum"
    assert snap.dmSleeve["holdings"] == {"AAPL": 0.2, "MSFT": 0.2}
    assert snap.mrmSleeve["holdings"] == {"NVDA": 0.2}
    # Provenance is honest about there being no single persisted run behind
    # a sleeve at decision time.
    assert "Dual Momentum" in snap.dmSleeve["strategyVersion"]


def test_snapshot_weight_matches_the_frozen_module_exactly():
    """The new module must never compute its own weight -- it can only
    relay what engine.dm_mrm_vol_scaled computed."""
    dm_ret, mrm_ret = _long_history(seed=2)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    target = pd.Timestamp(real[3].date)
    snap = fwd.build_decision_snapshot(dm_ret, mrm_ret, target, {}, {})
    assert snap.dmTargetWeight == pytest.approx(real[3].dm_weight, rel=1e-12)
    assert snap.mrmTargetWeight == pytest.approx(real[3].mrm_weight, rel=1e-12)


def test_snapshot_records_the_exact_volatility_window():
    dm_ret, mrm_ret = _long_history(seed=3)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    target = pd.Timestamp(real[2].date)
    snap = fwd.build_decision_snapshot(dm_ret, mrm_ret, target, {}, {})
    idx = dm_ret.index.intersection(mrm_ret.index).sort_values()
    pos = idx.get_loc(target)
    assert snap.volWindowEndDate == str(idx[pos - 1].date())
    assert snap.volWindowStartDate == str(idx[pos - vs.VOL_LOOKBACK_SESSIONS].date())


# --- development cutoff -----------------------------------------------------


def test_decision_on_or_before_cutoff_is_rejected(tmp_path):
    dm_ret, mrm_ret = _long_history(seed=4, n=1300)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    early = pd.Timestamp(real[0].date)
    snap = fwd.build_decision_snapshot(dm_ret, mrm_ret, early, {}, {})
    forced = fwd.CompositeDecisionSnapshot(
        **{**snap.to_dict(), "decisionDate": fwd.DEVELOPMENT_CUTOFF.isoformat()}
    )
    with pytest.raises(ValueError, match="development cutoff"):
        fwd.record_decision(forced, path=tmp_path / "decisions.json")


def test_first_post_cutoff_reset_is_accepted(tmp_path):
    """Constructing a snapshot dated the day after the cutoff must succeed
    -- the natural first eligible forward decision."""
    dm_ret, mrm_ret = _long_history(seed=5, n=400)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    snap_source = fwd.build_decision_snapshot(dm_ret, mrm_ret, pd.Timestamp(real[5].date), {}, {})
    after_cutoff = fwd.CompositeDecisionSnapshot(
        **{**snap_source.to_dict(),
           "decisionDate": (fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1)).isoformat()}
    )
    fwd.record_decision(after_cutoff, path=tmp_path / "decisions.json")
    rows = fwd.load_decisions(tmp_path / "decisions.json")
    assert len(rows) == 1


# --- immutable decision ledger: append-only ---------------------------------


def test_decision_ledger_refuses_duplicate_date(tmp_path):
    path = tmp_path / "decisions.json"
    dm_ret, mrm_ret = _long_history(seed=6, n=400)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    snap_source = fwd.build_decision_snapshot(dm_ret, mrm_ret, pd.Timestamp(real[5].date), {}, {})
    day = (fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=10)).isoformat()
    first = fwd.CompositeDecisionSnapshot(**{**snap_source.to_dict(), "decisionDate": day, "dmTargetWeight": 0.3})
    fwd.record_decision(first, path=path)
    second = fwd.CompositeDecisionSnapshot(**{**snap_source.to_dict(), "decisionDate": day, "dmTargetWeight": 0.9})
    with pytest.raises(ValueError, match="already recorded"):
        fwd.record_decision(second, path=path)
    rows = fwd.load_decisions(path)
    assert len(rows) == 1 and rows[0]["dmTargetWeight"] == 0.3


def test_outcome_recording_never_touches_the_decision_file(tmp_path):
    """The regression test for section 7: appending a realized outcome must
    leave the original decision snapshot byte-for-byte unchanged."""
    decision_path = tmp_path / "decisions.json"
    outcome_path = tmp_path / "outcomes.json"
    dm_ret, mrm_ret = _long_history(seed=7, n=400)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    snap_source = fwd.build_decision_snapshot(dm_ret, mrm_ret, pd.Timestamp(real[5].date), {}, {})
    day = (fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=5)).isoformat()
    snap = fwd.CompositeDecisionSnapshot(**{**snap_source.to_dict(), "decisionDate": day})
    fwd.record_decision(snap, path=decision_path)
    before = decision_path.read_text()

    fwd.record_outcome(
        fwd.RealizedOutcome(
            decisionDate=day, asOfDate=day, recordedAt=datetime.now().isoformat(),
            dmRealizedReturnPct=1.2, mrmRealizedReturnPct=-0.4, combinedRealizedReturnPct=0.5,
        ),
        path=outcome_path,
    )
    after = decision_path.read_text()
    assert before == after
    assert fwd.load_outcomes(outcome_path)[0]["combinedRealizedReturnPct"] == 0.5


def test_outcome_ledger_refuses_duplicate_key(tmp_path):
    path = tmp_path / "outcomes.json"
    outcome = fwd.RealizedOutcome(
        decisionDate="2026-09-01", asOfDate="2026-09-01", recordedAt=datetime.now().isoformat(),
        dmRealizedReturnPct=1.0, mrmRealizedReturnPct=1.0, combinedRealizedReturnPct=1.0,
    )
    fwd.record_outcome(outcome, path=path)
    with pytest.raises(ValueError, match="already recorded"):
        fwd.record_outcome(outcome, path=path)


def test_outcome_ledger_rejects_development_or_predecision_dates(tmp_path):
    with pytest.raises(ValueError, match="development cutoff"):
        fwd.record_outcome(fwd.RealizedOutcome(
            decisionDate=fwd.DEVELOPMENT_CUTOFF.isoformat(), asOfDate="2026-09-01",
            recordedAt=datetime.now().isoformat(), dmRealizedReturnPct=0, mrmRealizedReturnPct=0,
            combinedRealizedReturnPct=0), path=tmp_path / "outcomes.json")
    with pytest.raises(ValueError, match="cannot precede"):
        fwd.record_outcome(fwd.RealizedOutcome(
            decisionDate="2026-09-01", asOfDate="2026-08-31", recordedAt=datetime.now().isoformat(),
            dmRealizedReturnPct=0, mrmRealizedReturnPct=0, combinedRealizedReturnPct=0),
            path=tmp_path / "outcomes.json")


def test_future_information_cannot_alter_a_prior_decisions_weight():
    """Same look-ahead guarantee as the frozen module, checked again at
    this module's own boundary: extending the return history far past a
    decision date must not change that decision's recorded weight."""
    dm_ret, mrm_ret = _long_history(seed=8, n=400)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    target = pd.Timestamp(real[10].date)
    snap_short = fwd.build_decision_snapshot(dm_ret.loc[:target], mrm_ret.loc[:target], target, {}, {})

    rng = np.random.default_rng(99)
    extended_dm = pd.concat([dm_ret, _series(list(rng.normal(0.5, 1.0, 50)), start=dm_ret.index[-1] + pd.Timedelta(days=1))])
    snap_long = fwd.build_decision_snapshot(extended_dm, mrm_ret, target, {}, {})
    assert snap_short.dmTargetWeight == pytest.approx(snap_long.dmTargetWeight, rel=1e-12)


# --- forward-only NAV --------------------------------------------------------


def test_nav_rejects_a_session_on_or_before_cutoff(tmp_path):
    path = tmp_path / "nav.json"
    with pytest.raises(ValueError, match="development cutoff"):
        fwd.append_nav_row(fwd.DEVELOPMENT_CUTOFF, {k: 0.01 for k in fwd.NAV_SERIES_KEYS}, path=path)


def test_first_forward_session_seeds_all_series_at_the_same_base(tmp_path):
    path = tmp_path / "nav.json"
    first_day = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1)
    row = fwd.append_nav_row(first_day, {k: 0.05 for k in fwd.NAV_SERIES_KEYS}, path=path)
    for k in fwd.NAV_SERIES_KEYS:
        assert row[k] == fwd.NAV_BASE  # seeded, NOT yet compounded by that day's return


def test_nav_compounds_correctly_on_subsequent_sessions(tmp_path):
    path = tmp_path / "nav.json"
    d1 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1)
    d2 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=2)
    fwd.append_nav_row(d1, {k: 0.0 for k in fwd.NAV_SERIES_KEYS}, path=path)
    row2 = fwd.append_nav_row(d2, {"dm": 0.10, "mrm": -0.05, "fiftyFifty": 0.025, "volScaled": 0.02, "spy": 0.01}, path=path)
    assert row2["dm"] == pytest.approx(110.0)
    assert row2["mrm"] == pytest.approx(95.0)


def test_nav_refuses_out_of_order_or_duplicate_dates(tmp_path):
    path = tmp_path / "nav.json"
    d1 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=5)
    fwd.append_nav_row(d1, {k: 0.0 for k in fwd.NAV_SERIES_KEYS}, path=path)
    with pytest.raises(ValueError, match="already recorded"):
        fwd.append_nav_row(d1, {k: 0.0 for k in fwd.NAV_SERIES_KEYS}, path=path)
    earlier = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1)
    with pytest.raises(ValueError, match="chronologically"):
        fwd.append_nav_row(earlier, {k: 0.0 for k in fwd.NAV_SERIES_KEYS}, path=path)


def test_comparison_set_and_benchmark_are_fixed():
    assert fwd.COMPARISON_SET == (
        "Dual Momentum", "Market-Residual Momentum", "50/50 DM/MRM (frozen)",
        "Vol-Scaled DM/MRM (frozen)",
    )
    assert fwd.BENCHMARK == "SPY"


def test_forward_status_exposes_each_series_append_only_nav_history(tmp_path):
    nav_path = tmp_path / "nav.json"
    d1 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=3)
    d2 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=4)
    fwd.append_nav_level_row(d1, {key: 100.0 for key in fwd.NAV_SERIES_KEYS}, nav_path)
    fwd.append_nav_level_row(d2, {key: 101.0 for key in fwd.NAV_SERIES_KEYS}, nav_path)

    status = fwd.forward_stack_status(
        nav_path=nav_path,
        decisions_path=tmp_path / "decisions.json",
        operations_path=tmp_path / "operations.json",
    )

    dm = next(row for row in status["series"] if row["key"] == "dm")
    assert dm["history"] == [
        {"date": d1.isoformat(), "nav": 100.0},
        {"date": d2.isoformat(), "nav": 101.0},
    ]


def test_forward_status_exposes_frozen_position_decisions_by_strategy(tmp_path):
    dm_ret, mrm_ret = _long_history(seed=31, n=400)
    real = [decision for decision in vs.compute_weight_path(dm_ret, mrm_ret) if decision.sufficient_data]
    source = fwd.build_decision_snapshot(
        dm_ret, mrm_ret, pd.Timestamp(real[4].date),
        dm_holdings={"AAA": .6, "BBB": .4}, mrm_holdings={"CCC": 1.0},
    )
    decision_day = (fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=3)).isoformat()
    decision = fwd.CompositeDecisionSnapshot(**{**source.to_dict(), "decisionDate": decision_day})
    decisions_path = tmp_path / "decisions.json"
    fwd.record_decision(decision, decisions_path)

    status = fwd.forward_stack_status(
        nav_path=tmp_path / "nav.json",
        decisions_path=decisions_path,
        operations_path=tmp_path / "operations.json",
    )

    dm = next(row for row in status["series"] if row["key"] == "dm")
    blend = next(row for row in status["series"] if row["key"] == "fiftyFifty")
    assert dm["positionHistory"] == [{
        "date": decision_day, "holdings": {"AAA": .6, "BBB": .4},
        "source": "frozen_decision",
    }]
    assert blend["positionHistory"][0]["holdings"] == {
        "AAA": pytest.approx(.3), "BBB": pytest.approx(.2), "CCC": pytest.approx(.5),
    }


# --- scorecard: too-early gating, review gate -------------------------------


def test_scorecard_with_zero_observations():
    scorecard = fwd.build_scorecard(pd.DataFrame(columns=["date", *fwd.NAV_SERIES_KEYS]).set_index("date"), [])
    assert scorecard["observations"] == 0
    assert "No Forward Observations Yet" in scorecard["status"]
    assert scorecard["reviewStatus"] == "Too Early for Formal Review"


def test_scorecard_withholds_annualized_figures_before_the_threshold():
    idx = pd.bdate_range(fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1), periods=30)
    nav = pd.DataFrame({k: np.linspace(100, 103, 30) for k in fwd.NAV_SERIES_KEYS}, index=idx)
    scorecard = fwd.build_scorecard(nav, [])
    assert scorecard["status"] == "Too Early to Evaluate"
    for key in fwd.NAV_SERIES_KEYS:
        assert scorecard["series"][key]["annualizedBlock"] == "Too early to evaluate"
        assert "sharpe" not in scorecard["series"][key]
    assert scorecard["volScaledVsDm"]["sharpeDiff"] == "Too early to evaluate"


def test_scorecard_computes_annualized_figures_past_the_threshold():
    idx = pd.bdate_range(fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1), periods=260)
    rng = np.random.default_rng(20)
    nav = pd.DataFrame(
        {k: 100 * (1 + rng.normal(0.0003, 0.01, 260)).cumprod() for k in fwd.NAV_SERIES_KEYS}, index=idx,
    )
    scorecard = fwd.build_scorecard(nav, [])
    assert scorecard["status"] != "Too Early to Evaluate"
    for key in fwd.NAV_SERIES_KEYS:
        assert "sharpe" in scorecard["series"][key]
        assert "annualizedReturnPct" in scorecard["series"][key]


def test_review_gate_before_and_after_twelve_months():
    assert fwd.review_status(100) == "Too Early for Formal Review"
    assert fwd.review_status(251) == "Too Early for Formal Review"
    assert fwd.review_status(252) != "Too Early for Formal Review"
    assert "not a verdict" in fwd.review_status(300)


def test_scorecard_never_returns_validated():
    idx = pd.bdate_range(fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1), periods=400)
    rng = np.random.default_rng(21)
    nav = pd.DataFrame(
        {k: 100 * (1 + rng.normal(0.0008, 0.006, 400)).cumprod() for k in fwd.NAV_SERIES_KEYS}, index=idx,
    )
    scorecard = fwd.build_scorecard(nav, [])
    assert "Validated" not in scorecard["status"]
    assert "Validated" not in scorecard["reviewStatus"]


def test_scorecard_separates_development_and_forward_periods():
    idx = pd.bdate_range(fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1), periods=30)
    nav = pd.DataFrame({k: np.linspace(100, 101, 30) for k in fwd.NAV_SERIES_KEYS}, index=idx)
    scorecard = fwd.build_scorecard(nav, [])
    assert scorecard["developmentPeriod"] == {
        "start": fwd.DEVELOPMENT_START.isoformat(), "end": fwd.DEVELOPMENT_CUTOFF.isoformat(),
    }
    assert scorecard["forwardPeriod"]["start"] > fwd.DEVELOPMENT_CUTOFF.isoformat()


# --- automatic completed-session advancement -------------------------------


def _forward_source(end="2026-09-04"):
    idx = pd.bdate_range("2026-05-01", end)
    dm = pd.Series(100 * np.cumprod(1 + np.resize([.002, -.001, .001], len(idx))), index=idx)
    mrm = pd.Series(100 * np.cumprod(1 + np.resize([.001, .0005, -.0005], len(idx))), index=idx)
    spy = pd.Series(100 * np.cumprod(1 + np.resize([.001, -.0002], len(idx))), index=idx)
    reset = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).first().tolist()
    dm_reb = pd.DataFrame({"date": reset, "holdings": [{"A": .5, "B": .5}] * len(reset)})
    mrm_reb = pd.DataFrame({"date": reset, "holdings": [{"A": .5, "C": .5}] * len(reset)})
    return {"dmEquity": dm, "mrmEquity": mrm, "spyClose": spy,
            "dmRebalances": dm_reb, "mrmRebalances": mrm_reb,
            "dmCurrentHoldings": {"A": .5, "B": .5},
            "mrmCurrentHoldings": {"A": .5, "C": .5}}


def test_automatic_advance_is_complete_idempotent_and_restart_safe(tmp_path):
    paths = dict(decisions_path=tmp_path / "decisions.json", nav_path=tmp_path / "nav.json",
                 operations_path=tmp_path / "operations.json")
    factory = lambda _day: _forward_source()
    first = fwd.advance_completed_sessions(source_factory=factory, latest_session=date(2026, 9, 4), **paths)
    before_decisions = paths["decisions_path"].read_bytes()
    before_nav = paths["nav_path"].read_bytes()
    operations = fwd._read_json(paths["operations_path"], {})
    operations["lastAttemptAt"] = "2026-08-01T00:00:00-04:00"
    fwd._atomic_json_write(paths["operations_path"], operations)
    second = fwd.advance_completed_sessions(source_factory=factory, latest_session=date(2026, 9, 4), **paths)
    assert first["appendedSessions"] == len(pd.bdate_range("2026-08-24", "2026-09-03"))
    assert second == {"status": "up_to_date", "appendedSessions": 0,
                      "lastSession": "2026-09-03", "sourceSession": "2026-09-04",
                      "finalizationLagSessions": 1}
    assert paths["decisions_path"].read_bytes() == before_decisions
    assert paths["nav_path"].read_bytes() == before_nav
    refreshed = fwd._read_json(paths["operations_path"], {})
    assert refreshed["lastAttemptAt"] != "2026-08-01T00:00:00-04:00"
    assert refreshed["lastSuccessAt"] == refreshed["lastAttemptAt"]
    assert refreshed["advanceResult"] == second
    nav = fwd.load_forward_nav(paths["nav_path"])
    assert nav.index[0].date() > fwd.DEVELOPMENT_CUTOFF
    assert nav.index.is_unique and nav.index[-1].date() == date(2026, 9, 3)
    assert refreshed["latestCompletedSession"] == "2026-09-04"
    assert refreshed["latestFinalizedSession"] == "2026-09-03"


def test_atomic_json_write_retries_past_a_transient_permission_error(tmp_path, monkeypatch):
    """OneDrive's sync client briefly opens a file it is uploading, which can
    turn a perfectly good replace into a `PermissionError` at the last step.
    Observed in production masking the real advance_completed_sessions
    failure behind a second, unrelated crash while trying to persist the
    error itself (2026-08-30). The write must retry past that, matching
    engine/data.py:_replace_with_retry's existing behavior for the same
    OneDrive-lock scenario."""
    from engine import data as data_module

    target = tmp_path / "state.json"
    attempts = []
    real_replace = data_module.Path.replace

    def flaky_replace(self, dst):
        attempts.append(self)
        if len(attempts) < 3:
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(self, dst)

    monkeypatch.setattr(data_module.Path, "replace", flaky_replace)
    fwd._atomic_json_write(target, {"ok": True})

    assert len(attempts) == 3
    assert fwd._read_json(target, {}) == {"ok": True}


def test_old_source_revision_is_alerted_not_rewritten(tmp_path):
    paths = dict(decisions_path=tmp_path / "decisions.json", nav_path=tmp_path / "nav.json",
                 operations_path=tmp_path / "operations.json")
    fwd.advance_completed_sessions(source_factory=lambda _day: _forward_source(), latest_session=date(2026, 9, 4), **paths)
    before = paths["nav_path"].read_bytes()
    changed = _forward_source("2026-09-07")
    changed["dmEquity"].loc[pd.Timestamp("2026-08-25")] *= 1.2
    with pytest.raises(RuntimeError, match="mutation"):
        fwd.advance_completed_sessions(source_factory=lambda _day: changed, latest_session=date(2026, 9, 7), **paths)
    assert paths["nav_path"].read_bytes() == before
    assert "mutation" in fwd._read_json(paths["operations_path"], {})["lastError"]


def test_volscaled_tolerates_a_small_residual_but_still_advances(tmp_path):
    """volScaled is a rolling-volatility-derived series (compute_weight_path
    depends on the entire historical lookback, not just one day's return),
    unlike the other four pure compounded-return series -- so it alone
    tolerates a small residual, the kind ordinary, now-mitigated historical
    price-cache drift can leave behind (see NAV_SERIES_TOLERANCE), without
    blocking the scheduler."""
    paths = dict(decisions_path=tmp_path / "decisions.json", nav_path=tmp_path / "nav.json",
                 operations_path=tmp_path / "operations.json")
    fwd.advance_completed_sessions(source_factory=lambda _day: _forward_source(), latest_session=date(2026, 9, 4), **paths)
    rows = fwd._read_json(paths["nav_path"], [])
    rows[3]["volScaled"] += 0.005  # inside NAV_SERIES_TOLERANCE["volScaled"] (1e-2)
    fwd._atomic_json_write(paths["nav_path"], rows)

    result = fwd.advance_completed_sessions(
        source_factory=lambda _day: _forward_source("2026-09-07"),
        latest_session=date(2026, 9, 7), **paths,
    )
    assert result["status"] == "advanced"


def test_volscaled_beyond_tolerance_still_raises_and_names_the_series(tmp_path):
    paths = dict(decisions_path=tmp_path / "decisions.json", nav_path=tmp_path / "nav.json",
                 operations_path=tmp_path / "operations.json")
    fwd.advance_completed_sessions(source_factory=lambda _day: _forward_source(), latest_session=date(2026, 9, 4), **paths)
    rows = fwd._read_json(paths["nav_path"], [])
    rows[3]["volScaled"] += 1.0  # well beyond NAV_SERIES_TOLERANCE["volScaled"]
    fwd._atomic_json_write(paths["nav_path"], rows)

    with pytest.raises(RuntimeError, match="volScaled"):
        fwd.advance_completed_sessions(
            source_factory=lambda _day: _forward_source("2026-09-07"),
            latest_session=date(2026, 9, 7), **paths,
        )


def test_any_deviation_in_the_other_four_series_still_raises_immediately(tmp_path):
    """Only volScaled gets a looser tolerance -- dm/mrm/fiftyFifty/spy must
    stay reproducible to ~machine precision, since each is a pure compounded
    single-day return with no rolling-window sensitivity."""
    paths = dict(decisions_path=tmp_path / "decisions.json", nav_path=tmp_path / "nav.json",
                 operations_path=tmp_path / "operations.json")
    fwd.advance_completed_sessions(source_factory=lambda _day: _forward_source(), latest_session=date(2026, 9, 4), **paths)
    rows = fwd._read_json(paths["nav_path"], [])
    rows[3]["mrm"] += 1e-4  # tiny, but far above the 1e-8 tolerance for this series
    fwd._atomic_json_write(paths["nav_path"], rows)

    with pytest.raises(RuntimeError, match="'mrm'"):
        fwd.advance_completed_sessions(
            source_factory=lambda _day: _forward_source("2026-09-07"),
            latest_session=date(2026, 9, 7), **paths,
        )


def test_confirmed_amendment_changes_effective_view_without_rewriting_raw_nav(tmp_path):
    nav_path, amendments_path = tmp_path / "nav.json", tmp_path / "amendments.json"
    d1 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=3)
    d2 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=4)
    fwd.append_nav_level_row(d1, {key: 100.0 for key in fwd.NAV_SERIES_KEYS}, nav_path)
    fwd.append_nav_level_row(d2, {key: 99.0 for key in fwd.NAV_SERIES_KEYS}, nav_path)
    raw_before = nav_path.read_bytes()
    amendment = fwd.NavAmendment(
        amendmentId="confirmed-dividend-1", recordedAt="2026-08-27T12:00:00-04:00",
        reasonCode="confirmed_corporate_action", explanation="Test dividend adjustment",
        evidence={"symbol": "JNJ", "cashDividendPerShare": 1.34},
        replacements=[{"date": d2.isoformat(), "series": "mrm", "priorValue": 99.0,
                       "replacementValue": 99.1, "delta": 0.1}],
    )
    fwd.record_nav_amendment(amendment, amendments_path)
    effective = fwd.load_effective_forward_nav(nav_path, amendments_path)
    assert nav_path.read_bytes() == raw_before
    assert fwd.load_forward_nav(nav_path).loc[pd.Timestamp(d2), "mrm"] == 99.0
    assert effective.loc[pd.Timestamp(d2), "mrm"] == 99.1
    with pytest.raises(ValueError, match="already recorded"):
        fwd.record_nav_amendment(amendment, amendments_path)


def test_missing_valid_session_fails_instead_of_silently_skipping(tmp_path):
    source = _forward_source()
    source["mrmEquity"] = source["mrmEquity"].drop(pd.Timestamp("2026-08-25"))
    with pytest.raises(RuntimeError, match="refusing to skip"):
        fwd.advance_completed_sessions(
            source_factory=lambda _day: source, latest_session=date(2026, 9, 4),
            decisions_path=tmp_path / "decisions.json", nav_path=tmp_path / "nav.json",
            operations_path=tmp_path / "operations.json")


def test_forward_decision_window_and_overlap_are_persisted(tmp_path):
    paths = dict(decisions_path=tmp_path / "decisions.json", nav_path=tmp_path / "nav.json",
                 operations_path=tmp_path / "operations.json")
    fwd.advance_completed_sessions(source_factory=lambda _day: _forward_source(), latest_session=date(2026, 9, 4), **paths)
    decisions = fwd.load_decisions(paths["decisions_path"])
    september = next(row for row in decisions if row["decisionDate"] == "2026-09-01")
    assert september["volWindowEndDate"] < september["decisionDate"]
    assert september["volLookbackSessions"] == vs.VOL_LOOKBACK_SESSIONS
    state = fwd._read_json(paths["operations_path"], {})["currentBlend"]
    # A appears in both sleeves and is one aggregated exposure, not two rows.
    assert list(state["combinedHoldings"]).count("A") == 1
    assert state["combinedHoldings"]["A"] == pytest.approx(.5)


def test_checkpoint_labels_are_reporting_only():
    assert fwd.maturity_checkpoint(19)["next"]["sessions"] == 20
    assert fwd.maturity_checkpoint(126)["label"] == "Too Early to Evaluate"
    assert fwd.maturity_checkpoint(252)["label"] == "First annualized forward review eligible"


def test_mrm_shadow_is_blocked_inside_order_execution_boundary():
    result = execution.execute_rebalance("Market-Residual Momentum", "test", force=True)
    assert result["status"] == "blocked_research_shadow"


# --- live intraday mark (engine/shadow_live_mark.py wiring) -----------------


def _seed_operations(path, holdings):
    import json
    path.write_text(json.dumps({"currentBlend": holdings}), encoding="utf-8")


def test_live_marks_returns_all_four_series_keys(tmp_path, monkeypatch):
    nav_path = tmp_path / "nav.json"
    ops_path = tmp_path / "operations.json"
    d1 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1)
    fwd.append_nav_level_row(d1, {key: 100.0 for key in fwd.NAV_SERIES_KEYS}, nav_path)
    _seed_operations(ops_path, {
        "dmHoldings": {"AAPL": 1.0}, "mrmHoldings": {"MSFT": 1.0},
        "fixedCombinedHoldings": {"AAPL": 0.5, "MSFT": 0.5},
        "combinedHoldings": {"AAPL": 0.4, "MSFT": 0.6},
    })
    monkeypatch.setattr("engine.execution_db.automation_config", lambda: {})
    monkeypatch.setattr("engine.shadow_live_mark.quotes_module.get_quotes", lambda symbols: {
        s: {"symbol": s, "price": 110.0} for s in symbols
    })
    monkeypatch.setattr("engine.shadow_live_mark.data_module.get_bars", lambda symbol, interval, start, end: pd.DataFrame(
        {"Close": [100.0]}, index=[pd.Timestamp(end)],
    ))

    marks = fwd.live_marks(nav_path=nav_path, operations_path=ops_path)

    assert set(marks) == {"dm", "mrm", "fiftyFifty", "volScaled", "spy"}
    for key in marks:
        assert marks[key]["available"] is True
        assert marks[key]["inProgress"] is True
        # Every held symbol moved 100 -> 110, i.e. +10%, regardless of weights.
        assert marks[key]["equity"] == pytest.approx(110.0)


def test_live_marks_with_no_finalized_nav_is_unavailable(tmp_path):
    nav_path = tmp_path / "nav.json"
    ops_path = tmp_path / "operations.json"
    _seed_operations(ops_path, {"dmHoldings": {"AAPL": 1.0}})

    marks = fwd.live_marks(nav_path=nav_path, operations_path=ops_path)

    assert marks["dm"]["available"] is False
    assert marks["mrm"]["available"] is False


def test_live_marks_prefers_broker_equity_for_dm_when_the_paper_account_matches(tmp_path, monkeypatch):
    nav_path = tmp_path / "nav.json"
    ops_path = tmp_path / "operations.json"
    d1 = fwd.DEVELOPMENT_CUTOFF + pd.Timedelta(days=1)
    fwd.append_nav_level_row(d1, {key: 100.0 for key in fwd.NAV_SERIES_KEYS}, nav_path)
    _seed_operations(ops_path, {"dmHoldings": {"AAPL": 1.0}, "mrmHoldings": {}, "fixedCombinedHoldings": {}, "combinedHoldings": {}})

    monkeypatch.setattr("engine.execution_db.automation_config", lambda: {"Dual Momentum": {
        "enabled": True, "params": "{}", "validation_run_id": 33,
    }})
    monkeypatch.setattr("engine.execution_db.selected_symbols_for", lambda name: ["AAPL"])
    monkeypatch.setattr(
        "engine.alpaca_trading.get_account",
        lambda: {"available": True, "equity": 103_000.0, "lastEquity": 100_000.0},
    )
    monkeypatch.setattr(
        "engine.strategy_identity.identify_execution_config",
        lambda *a, **k: {"fingerprintMatches": True},
    )

    marks = fwd.live_marks(nav_path=nav_path, operations_path=ops_path)

    assert marks["dm"]["available"] is True
    assert marks["dm"]["source"] == "alpaca_paper_account"
    assert marks["dm"]["equity"] == pytest.approx(103.0)  # 100 baseline * 1.03 growth
    assert marks["dm"]["profitLossPct"] == pytest.approx(3.0)
