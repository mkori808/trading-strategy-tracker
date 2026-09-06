"""DM/MRM Volatility-Scaled Portfolio: weighting arithmetic, look-ahead
safety, and the append-only forward ledger.

Hand-built return series throughout -- the correct weight at each reset is
arithmetic, not a property of any real strategy, so a failure here means the
overlay's own logic is wrong, never that a backtest moved.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from engine import dm_mrm_vol_scaled as vs


def _series(values: list[float], start: str = "2022-01-03") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx)


# --- inverse-vol weight arithmetic -----------------------------------------


def test_equal_volatility_produces_equal_weights():
    rng = np.random.default_rng(1)
    same = rng.normal(0, 0.01, 400)
    dm_ret, mrm_ret = _series(list(same)), _series(list(same * -1 + rng.normal(0, 1e-6, 400)))
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    assert real
    for d in real:
        assert d.dm_weight == pytest.approx(0.5, abs=0.02)


def test_lower_volatility_sleeve_gets_more_weight():
    rng = np.random.default_rng(2)
    calm = rng.normal(0.0003, 0.003, 400)   # low vol
    wild = rng.normal(0.0003, 0.03, 400)    # 10x the vol
    dm_ret, mrm_ret = _series(list(wild)), _series(list(calm))
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    assert real
    # DM is the wild (high-vol) sleeve -> must get the SMALLER weight.
    assert all(d.dm_weight < 0.5 for d in real)


def test_weights_sum_to_one():
    rng = np.random.default_rng(3)
    dm_ret = _series(list(rng.normal(0.0002, 0.02, 500)))
    mrm_ret = _series(list(rng.normal(0.0001, 0.015, 500)))
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    for d in decisions:
        assert d.dm_weight + d.mrm_weight == pytest.approx(1.0, abs=1e-9)


def test_inverse_vol_formula_reproduced_by_hand():
    """One concrete reset checked against the formula arithmetic directly,
    not against the module's own output at a neighboring point."""
    rng = np.random.default_rng(4)
    dm_ret = _series(list(rng.normal(0, 0.01, 400)))
    mrm_ret = _series(list(rng.normal(0, 0.02, 400)))
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    d = real[5]
    v_dm, v_mrm = d.dm_trailing_vol_pct / 100, d.mrm_trailing_vol_pct / 100
    expected_w_dm = (1 / v_dm) / (1 / v_dm + 1 / v_mrm)
    assert d.dm_weight == pytest.approx(expected_w_dm, rel=1e-9)


# --- look-ahead safety -------------------------------------------------------


def test_same_day_return_does_not_affect_its_own_weight():
    """Inject an enormous one-day move ON a reset date. If the weight
    computed for that date changed, the day's own return leaked into its
    own allocation -- the exact bug this test exists to catch."""
    rng = np.random.default_rng(5)
    base = list(rng.normal(0.0002, 0.01, 400))
    dm_ret = _series(base)
    mrm_ret = _series(list(rng.normal(0.0001, 0.01, 400)))
    decisions_before = vs.compute_weight_path(dm_ret, mrm_ret)

    reset_date = next(d.date for d in decisions_before if d.sufficient_data)
    idx = dm_ret.index.get_loc(pd.Timestamp(reset_date))
    perturbed = dm_ret.copy()
    perturbed.iloc[idx] = 5.0  # a +500% single-day return, injected ON the reset date
    decisions_after = vs.compute_weight_path(perturbed, mrm_ret)

    before = next(d for d in decisions_before if d.date == reset_date)
    after = next(d for d in decisions_after if d.date == reset_date)
    assert after.dm_weight == pytest.approx(before.dm_weight, rel=1e-9)
    assert after.dm_trailing_vol_pct == pytest.approx(before.dm_trailing_vol_pct, rel=1e-9)


def test_future_returns_do_not_affect_past_weights():
    """Everything after a given reset can change arbitrarily without
    altering that reset's own decision."""
    rng = np.random.default_rng(6)
    dm_ret = _series(list(rng.normal(0.0002, 0.01, 400)))
    mrm_ret = _series(list(rng.normal(0.0001, 0.012, 400)))
    decisions_a = vs.compute_weight_path(dm_ret, mrm_ret)

    mid = len(dm_ret) // 2
    altered = dm_ret.copy()
    altered.iloc[mid:] = rng.normal(0.05, 0.2, len(dm_ret) - mid)  # blow up the future
    decisions_b = vs.compute_weight_path(altered, mrm_ret)

    mid_date = dm_ret.index[mid]
    for da, db in zip(decisions_a, decisions_b):
        if pd.Timestamp(da.date) >= mid_date:
            break
        assert da.dm_weight == pytest.approx(db.dm_weight, rel=1e-9), da.date


def test_reset_timestamp_uses_trailing_window_ending_before_it():
    rng = np.random.default_rng(7)
    dm_ret = _series(list(rng.normal(0.0002, 0.01, 400)))
    mrm_ret = _series(list(rng.normal(0.0001, 0.012, 400)))
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    real = [d for d in decisions if d.sufficient_data]
    d = real[3]
    idx = dm_ret.index.get_loc(pd.Timestamp(d.date))
    window = dm_ret.iloc[idx - vs.VOL_LOOKBACK_SESSIONS: idx]
    expected_vol = float(window.std() * np.sqrt(vs.TRADING_DAYS_PER_YEAR) * 100)
    assert d.dm_trailing_vol_pct == pytest.approx(expected_vol, rel=1e-6)


# --- warm-up and missing data ------------------------------------------------


def test_first_reset_has_no_trailing_window():
    rng = np.random.default_rng(8)
    dm_ret = _series(list(rng.normal(0, 0.01, 300)))
    mrm_ret = _series(list(rng.normal(0, 0.01, 300)))
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    assert decisions[0].sufficient_data is False
    assert decisions[0].dm_weight == 0.5


def test_insufficient_history_holds_prior_weight_not_a_crash():
    """Fewer than VOL_LOOKBACK_SESSIONS common sessions exist at all."""
    dm_ret = _series([0.001] * 10)
    mrm_ret = _series([0.001] * 10)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    assert all(not d.sufficient_data for d in decisions)
    assert all(d.dm_weight == 0.5 for d in decisions)


def test_misaligned_sleeve_dates_use_only_the_intersection():
    rng = np.random.default_rng(9)
    dm_idx = pd.bdate_range("2022-01-03", periods=300)
    mrm_idx = dm_idx[5:-5]  # MRM starts later and ends earlier
    dm_ret = pd.Series(rng.normal(0, 0.01, len(dm_idx)), index=dm_idx)
    mrm_ret = pd.Series(rng.normal(0, 0.01, len(mrm_idx)), index=mrm_idx)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    for d in decisions:
        assert pd.Timestamp(d.date) in mrm_idx


# --- independent reconstruction ---------------------------------------------


def test_build_portfolio_matches_a_hand_rolled_reconstruction():
    """A SECOND, differently-structured implementation (vectorized, not the
    module's own loop) must agree with build_portfolio's NAV."""
    rng = np.random.default_rng(10)
    dm_ret = _series(list(rng.normal(0.0003, 0.015, 500)))
    mrm_ret = _series(list(rng.normal(0.0002, 0.012, 500)))
    nav, decisions = vs.build_portfolio(dm_ret, mrm_ret)

    weight_by_date = {d.date: (d.dm_weight, d.mrm_weight) for d in decisions}
    common_idx = dm_ret.index.intersection(mrm_ret.index).sort_values()
    hand_nav = np.empty(len(common_idx))
    hand_nav[0] = 1.0
    v_dm, v_mrm = 0.5, 0.5
    for i in range(1, len(common_idx)):
        v_dm *= 1 + dm_ret.iloc[i]
        v_mrm *= 1 + mrm_ret.iloc[i]
        key = str(common_idx[i].date())
        if key in weight_by_date:
            w_dm, w_mrm = weight_by_date[key]
            total = v_dm + v_mrm
            v_dm, v_mrm = total * w_dm, total * w_mrm
        hand_nav[i] = v_dm + v_mrm
    np.testing.assert_allclose(nav.values, hand_nav, rtol=1e-12)


def test_deterministic_reproduction_of_the_allocation_path():
    rng = np.random.default_rng(11)
    dm_ret = _series(list(rng.normal(0.0002, 0.01, 400)))
    mrm_ret = _series(list(rng.normal(0.0001, 0.01, 400)))
    a = vs.compute_weight_path(dm_ret, mrm_ret)
    b = vs.compute_weight_path(dm_ret, mrm_ret)
    assert [d.to_dict() for d in a] == [d.to_dict() for d in b]


# --- incremental turnover ----------------------------------------------------


def test_turnover_is_zero_when_relative_volatility_never_changes():
    dm_ret = _series([0.001, -0.001] * 250)
    mrm_ret = _series([0.0005, -0.0005] * 250)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    turnover = vs.incremental_turnover(decisions)
    if turnover["avgMonthlyTransferPct"] is not None:
        assert turnover["avgMonthlyTransferPct"] == pytest.approx(0.0, abs=1e-6)


def test_turnover_reflects_a_known_weight_swing():
    rng = np.random.default_rng(12)
    calm_then_wild_dm = list(rng.normal(0, 0.005, 200)) + list(rng.normal(0, 0.05, 200))
    steady_mrm = list(rng.normal(0, 0.012, 400))
    dm_ret, mrm_ret = _series(calm_then_wild_dm), _series(steady_mrm)
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    turnover = vs.incremental_turnover(decisions)
    assert turnover["maxMonthlyTransferPct"] > 5.0  # a real regime shift moves real weight


# --- forward ledger: append-only, immutable, cutoff-enforced -----------------


def _decision(day: str, **overrides) -> vs.ForwardDecision:
    base = dict(
        decisionDate=day, recordedAt=datetime.now().isoformat(),
        dmTrailingVolPct=15.0, mrmTrailingVolPct=12.0,
        dmTargetWeight=0.45, mrmTargetWeight=0.55,
        dmHoldings={"AAPL": 0.2}, mrmHoldings={"MSFT": 0.2},
        transactionCostBps=5.0, note="test",
    )
    base.update(overrides)
    return vs.ForwardDecision(**base)


def test_ledger_accepts_a_decision_after_the_cutoff(tmp_path):
    path = tmp_path / "ledger.json"
    vs.record_forward_decision(_decision("2026-09-01"), path=path)
    rows = vs.load_forward_ledger(path)
    assert len(rows) == 1
    assert rows[0]["decisionDate"] == "2026-09-01"


def test_ledger_rejects_a_decision_on_or_before_the_development_cutoff(tmp_path):
    path = tmp_path / "ledger.json"
    with pytest.raises(ValueError, match="development cutoff"):
        vs.record_forward_decision(_decision("2026-08-21"), path=path)
    with pytest.raises(ValueError, match="development cutoff"):
        vs.record_forward_decision(_decision("2020-01-01"), path=path)
    assert vs.load_forward_ledger(path) == []


def test_ledger_is_append_only_and_refuses_to_overwrite(tmp_path):
    path = tmp_path / "ledger.json"
    vs.record_forward_decision(_decision("2026-09-01", dmTargetWeight=0.40), path=path)
    with pytest.raises(ValueError, match="already recorded"):
        vs.record_forward_decision(_decision("2026-09-01", dmTargetWeight=0.99), path=path)
    rows = vs.load_forward_ledger(path)
    assert len(rows) == 1
    assert rows[0]["dmTargetWeight"] == 0.40  # the original entry, untouched


def test_ledger_preserves_prior_entries_across_appends(tmp_path):
    path = tmp_path / "ledger.json"
    vs.record_forward_decision(_decision("2026-09-01"), path=path)
    vs.record_forward_decision(_decision("2026-10-01"), path=path)
    rows = vs.load_forward_ledger(path)
    assert [r["decisionDate"] for r in rows] == ["2026-09-01", "2026-10-01"]
