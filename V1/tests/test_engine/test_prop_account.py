"""Prop-account rule engine and bootstrap simulator.

Every rule test drives `simulate_path` with a HAND-BUILT P&L sequence whose
correct outcome is arithmetic, not a property of any strategy -- so a
failure here means the rule engine is wrong, never that a backtest moved.
Simulation tests pin the seed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import prop_account as pa
from engine import prop_analysis as pan


def _config(**overrides) -> pa.PropAccountConfig:
    base = dict(
        account_size=100_000.0,
        max_total_loss_pct=0.05,
        daily_loss_limit_pct=0.02,
        payout_split=0.80,
        evaluation_fee=500.0,
        profit_target_pct=0.08,
        mode="funded",
        drawdown_type="static",
    )
    base.update(overrides)
    return pa.PropAccountConfig(**base)


# --- risk budget ----------------------------------------------------------


def test_risk_budget_is_the_binding_resource_not_account_size():
    """$100k nominal at 5% is a $5,000 loss budget. The distinction is the
    whole point of the feature, so it is asserted rather than assumed."""
    config = _config()
    assert config.risk_budget == 5_000.0
    assert config.daily_risk_budget == 2_000.0
    assert config.account_size == 100_000.0


def test_invalid_configs_are_rejected():
    with pytest.raises(ValueError):
        _config(account_size=0)
    with pytest.raises(ValueError):
        _config(max_total_loss_pct=1.5)
    with pytest.raises(ValueError):
        _config(payout_split=1.5)


# --- static drawdown ------------------------------------------------------


def test_static_drawdown_breaches_at_the_fixed_floor():
    config = _config()
    # -1,000/day: the 5th day puts equity at 95,000, exactly the floor.
    outcome = pa.simulate_path([-1_000.0] * 10, config)
    assert outcome.failed
    assert outcome.failure_reason == "max_total_loss"
    assert outcome.failure_day == 5


def test_static_floor_does_not_move_after_a_winning_run():
    """A static floor is fixed at inception. Profit does NOT raise it -- so
    an account up 10% can still be closed by falling back to 95,000."""
    config = _config()
    path = [10_000.0] + [-1_500.0] * 11  # up 10k, then bleeds to 95k on day 11
    outcome = pa.simulate_path(path, config)
    assert outcome.failed
    assert outcome.failure_reason == "max_total_loss"
    assert outcome.failure_day == 11


# --- trailing drawdown ----------------------------------------------------


# The drawdown tests below disable the daily limit (99%) on purpose. The
# engine checks the daily rule FIRST -- correctly, since that is the order a
# firm applies them -- so a path built from large single-day losses would
# terminate on the daily limit and never exercise the drawdown floor at all.
# Isolating one rule per test is what makes a failure here diagnostic.
def _dd_config(**overrides) -> pa.PropAccountConfig:
    overrides.setdefault("daily_loss_limit_pct", 0.99)
    return _config(**overrides)


def test_trailing_drawdown_follows_the_high_water_mark_up():
    config = _dd_config(drawdown_type="trailing")
    # Peak 110,000 lifts the floor to 105,000. Three 1,900 losses take
    # equity to 104,300 -- still far ABOVE the 100,000 starting balance, and
    # far above a static 95,000 floor, yet a trailing floor closes it.
    outcome = pa.simulate_path([10_000.0, -1_900.0, -1_900.0, -1_900.0], config)
    assert outcome.failed
    assert outcome.failure_reason == "max_total_loss"
    assert outcome.failure_day == 4


def test_static_survives_exactly_where_trailing_fails():
    """Same path, same numbers, one rule difference -- the clearest possible
    statement that drawdown_type is not cosmetic."""
    path = [10_000.0, -1_900.0, -1_900.0, -1_900.0]
    assert not pa.simulate_path(path, _dd_config(drawdown_type="static")).failed
    assert pa.simulate_path(path, _dd_config(drawdown_type="trailing")).failed


def test_trailing_to_breakeven_stops_lifting_at_the_starting_balance():
    """The variant most firms actually use: the floor trails up only until
    it reaches the initial balance, then freezes there."""
    config = _dd_config(drawdown_type="trailing_to_breakeven")
    # Peak 120,000 would put a PURE trailing floor at 115,000, which these
    # losses would breach. Capped at 100,000, equity 102,900 survives.
    path = [20_000.0] + [-1_900.0] * 9
    assert pa.simulate_path(path, _dd_config(drawdown_type="trailing")).failed
    assert not pa.simulate_path(path, config).failed
    # The floor still never falls below the starting balance.
    breached = pa.simulate_path(path + [-1_900.0, -1_900.0], config)
    assert breached.failed
    assert breached.failure_reason == "max_total_loss"


# --- daily loss limit -----------------------------------------------------


def test_daily_loss_limit_fires_on_a_single_session():
    config = _config()
    outcome = pa.simulate_path([1_000.0, -2_500.0, 1_000.0], config)
    assert outcome.failed
    assert outcome.failure_reason == "daily_loss_limit"
    assert outcome.failure_day == 2


def test_daily_limit_is_evaluated_per_session_not_cumulatively():
    """Three -1,500 days total -4,500 -- inside both the daily limit and the
    5,000 budget, so the account must survive. A cumulative reading of the
    daily rule would wrongly kill it."""
    outcome = pa.simulate_path([-1_500.0] * 3, _config())
    assert not outcome.failed


def test_daily_limit_boundary_is_inclusive():
    """Exactly -2,000 on a 2% limit is a breach, not a survival."""
    assert pa.simulate_path([-2_000.0], _config()).failed
    assert not pa.simulate_path([-1_999.0], _config()).failed


# --- evaluation -----------------------------------------------------------


def test_evaluation_passes_on_reaching_the_profit_target():
    config = _config(mode="evaluation")
    outcome = pa.simulate_path([4_000.0, 4_000.0, 1_000.0], config)
    assert outcome.passed_evaluation
    assert outcome.pass_day == 2
    assert not outcome.failed


def test_minimum_trading_days_delays_the_pass():
    """Hitting the target on day 2 does not pass a 10-day-minimum
    evaluation; the account keeps trading and passes later."""
    config = _config(mode="evaluation", min_trading_days=10)
    outcome = pa.simulate_path([4_000.0, 4_000.0] + [10.0] * 10, config)
    assert outcome.passed_evaluation
    assert outcome.pass_day == 10


def test_funded_mode_has_no_profit_target():
    config = _config(mode="funded")
    outcome = pa.simulate_path([4_000.0] * 5, config)
    assert not outcome.passed_evaluation
    assert not outcome.failed
    assert outcome.gross_profit == pytest.approx(20_000.0)


# --- termination ----------------------------------------------------------


def test_account_stops_at_the_breach_and_ignores_later_days():
    """A terminated account cannot recover. Counting post-breach profit is
    the easiest way to make a doomed strategy look survivable."""
    config = _config()
    outcome = pa.simulate_path([-2_500.0] + [50_000.0] * 10, config)
    assert outcome.failed
    assert outcome.days_survived == 1
    assert outcome.gross_profit == pytest.approx(-2_500.0)


# --- payout economics -----------------------------------------------------


def test_payout_split_and_fee_are_applied_to_the_trader_not_the_account():
    returns = pd.Series([0.001] * 300)
    config = _config(payout_split=0.80, evaluation_fee=500.0)
    sim = pa.PropSimulationConfig(n_paths=200, horizon_days=60, seed=7)
    result = pa.simulate(returns, config, sim, risk_multiplier=1.0)

    assert result.expected_gross_profit > 0
    assert result.expected_payout == pytest.approx(
        result.expected_gross_profit * 0.80, rel=1e-9
    )
    assert result.expected_net_payout == pytest.approx(
        result.expected_payout - 500.0, rel=1e-9
    )


def test_a_failed_account_pays_out_nothing():
    """Every path breaches, so payout is zero and the trader is out the fee
    -- the economics that make aggressive sizing a bad trade."""
    returns = pd.Series([-0.05] * 300)
    result = pa.simulate(
        returns, _config(), pa.PropSimulationConfig(n_paths=50, horizon_days=30, seed=3)
    )
    assert result.failure_prob == 1.0
    assert result.expected_payout == 0.0
    assert result.expected_net_payout == pytest.approx(-500.0)


def test_min_payout_threshold_suppresses_small_payouts():
    returns = pd.Series([0.00001] * 300)
    config = _config(min_payout=10_000.0, evaluation_fee=0.0)
    result = pa.simulate(
        returns, config, pa.PropSimulationConfig(n_paths=50, horizon_days=30, seed=5)
    )
    assert result.expected_payout == 0.0


# --- risk multiplier ------------------------------------------------------


def test_risk_multiplier_scales_pnl_linearly():
    returns = pd.Series([0.001] * 300)
    sim = pa.PropSimulationConfig(n_paths=100, horizon_days=50, seed=11)
    config = _config()
    one = pa.simulate(returns, config, sim, risk_multiplier=1.0)
    half = pa.simulate(returns, config, sim, risk_multiplier=0.5)
    assert half.expected_gross_profit == pytest.approx(
        one.expected_gross_profit / 2, rel=0.02
    )


def test_smaller_sizing_never_increases_failure_probability():
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.normal(0.0002, 0.02, 800))
    sim = pa.PropSimulationConfig(n_paths=400, horizon_days=252, seed=99)
    config = _config()
    probs = [
        pa.simulate(returns, config, sim, risk_multiplier=m).failure_prob
        for m in (0.25, 0.5, 1.0, 2.0)
    ]
    assert probs == sorted(probs)


# --- bootstrap reproducibility -------------------------------------------


def test_same_seed_reproduces_identical_results():
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0.0005, 0.01, 500))
    config, sim = _config(), pa.PropSimulationConfig(n_paths=300, seed=4242)
    first = pa.simulate(returns, config, sim)
    second = pa.simulate(returns, config, sim)
    assert first.to_dict() == second.to_dict()


def test_different_seeds_give_different_paths():
    rng = np.random.default_rng(2)
    returns = pd.Series(rng.normal(0.0005, 0.01, 500))
    config = _config()
    a = pa.simulate(returns, config, pa.PropSimulationConfig(n_paths=300, seed=1))
    b = pa.simulate(returns, config, pa.PropSimulationConfig(n_paths=300, seed=2))
    assert a.expected_gross_profit != b.expected_gross_profit


def test_block_bootstrap_preserves_streaks_that_iid_destroys():
    """A series of alternating calm and crash blocks has clustered losses.
    IID sampling scatters them; the block bootstrap must keep enough
    together to produce a materially higher failure probability."""
    # Net-positive drift overall, but the losses arrive in clusters: 60 calm
    # sessions then 10 bad ones. Consecutively those 10 are -5%, exactly the
    # loss budget; scattered among calm days they rarely accumulate.
    calm = [0.001] * 60
    crash = [-0.005] * 10
    returns = pd.Series((calm + crash) * 15)
    config = _config()
    block = pa.simulate(
        returns, config,
        pa.PropSimulationConfig(n_paths=600, horizon_days=252, block_size=10, seed=8),
    )
    iid = pa.simulate(
        returns, config,
        pa.PropSimulationConfig(n_paths=600, horizon_days=252, method="daily_iid", seed=8),
    )
    assert block.failure_prob > iid.failure_prob


# --- sizing sweep ---------------------------------------------------------


def test_sweep_identifies_sizing_points_within_thresholds():
    rng = np.random.default_rng(5)
    returns = pd.Series(rng.normal(0.0004, 0.004, 900))
    analysis = pa.sweep_sizing(
        returns, _config(), pa.PropSimulationConfig(n_paths=400, seed=21)
    )
    assert len(analysis.rows) == len(pa.DEFAULT_RISK_MULTIPLIERS)
    if analysis.max_survival:
        assert analysis.max_survival.failure_prob <= 0.05
    if analysis.conservative:
        assert analysis.conservative.failure_prob <= 0.02
    if analysis.max_survival and analysis.conservative:
        # Conservative is a strictly tighter bar, so it can never be larger.
        assert analysis.conservative.risk_multiplier <= analysis.max_survival.risk_multiplier


def test_sweep_returns_no_sizing_point_when_every_size_fails():
    returns = pd.Series([-0.04] * 400)
    analysis = pa.sweep_sizing(
        returns, _config(), pa.PropSimulationConfig(n_paths=60, seed=13)
    )
    assert analysis.max_survival is None
    assert analysis.conservative is None


# --- adapters and data quality -------------------------------------------


def _dense_curve(days: int = 400, start: str = "2022-01-03") -> pd.Series:
    idx = pd.bdate_range(start, periods=days)
    return pd.Series(np.linspace(10_000, 12_000, days), index=idx)


def test_sparse_daily_curve_is_refused_rather_than_simulated():
    """The measured failure mode of the archived curves. A series covering
    a third of sessions cannot support a daily-limit probability, so the
    adapter raises instead of returning a confident wrong number."""
    idx = pd.bdate_range("2022-01-03", periods=400)[::3]
    curve = pd.Series(np.linspace(10_000, 12_000, len(idx)), index=idx)
    with pytest.raises(pan.InsufficientPropData, match="sessions"):
        pan.daily_returns_from_equity(curve, "test")


def test_short_history_is_refused():
    with pytest.raises(pan.InsufficientPropData, match="daily observations"):
        pan.daily_returns_from_equity(_dense_curve(days=100), "test")


def test_dense_curve_is_accepted_with_provenance():
    series = pan.daily_returns_from_equity(_dense_curve(), "test")
    assert series.observations == 399
    assert series.session_coverage > 0.95
    assert series.source == "test"


def test_adapter_does_not_mutate_the_backtest_result():
    """The prop engine is an observer. If it mutated a result, the edge
    metrics computed from that same object would silently change."""
    curve = _dense_curve()
    before = curve.copy()

    class _Result:
        equity_curve = curve

    pan.daily_returns_from_result(_Result())
    pd.testing.assert_series_equal(curve, before)


def test_missing_data_raises_rather_than_returning_zeros():
    class _Empty:
        equity_curve = None
        per_symbol = {}

    with pytest.raises(pan.InsufficientPropData):
        pan.daily_returns_from_result(_Empty())


# --- risk metrics ---------------------------------------------------------


def test_risk_metrics_are_computed_on_a_known_series():
    rng = np.random.default_rng(3)
    returns = pd.Series(rng.normal(0.0005, 0.01, 1000))
    metrics = pan.compute_risk_metrics(returns)
    assert metrics.observations == 1000
    assert metrics.max_drawdown_pct < 0
    # The 95th-percentile drawdown is by construction shallower than the max.
    assert metrics.p95_drawdown_pct >= metrics.max_drawdown_pct
    assert metrics.worst_day_pct < 0
    assert metrics.return_over_max_dd is not None


def test_losing_streak_statistics():
    trades = pd.Series([1.0, -1.0, -1.0, -1.0, 2.0, -1.0, -1.0, 3.0])
    streaks = pan.losing_streaks(trades)
    assert streaks["max"] == 3
    assert streaks["count"] == 2


# --- verdict --------------------------------------------------------------


def test_verdict_is_insufficient_without_a_series():
    verdict = pan.prop_verdict(
        series=None, metrics=None, sizing=None, trade_count=None,
        validated=None, holdout_passed=None, mda_sufficient=None, account=_config(),
    )
    assert verdict.verdict == "insufficient"


def test_verdict_blocks_on_small_sample_despite_good_simulation():
    """Attractive economics must not promote an unproven edge -- the gate
    that keeps this framework from laundering weak evidence."""
    returns = pd.Series(np.random.default_rng(4).normal(0.0004, 0.004, 900))
    series = pan.daily_returns_from_equity(
        pd.Series(
            (1 + returns).cumprod().values * 10_000,
            index=pd.bdate_range("2022-01-03", periods=len(returns)),
        ),
        "test",
    )
    metrics = pan.compute_risk_metrics(series.returns)
    sizing = pa.sweep_sizing(
        series.returns, _config(), pa.PropSimulationConfig(n_paths=200, seed=6)
    )
    verdict = pan.prop_verdict(
        series=series, metrics=metrics, sizing=sizing, trade_count=12,
        validated=True, holdout_passed=True, mda_sufficient=True, account=_config(),
    )
    assert verdict.verdict == "poor"
    assert any("trades" in b for b in verdict.blockers)


def test_verdict_does_not_read_benchmark_gap():
    """Gap vs SPY is informational in prop analysis. The signature is the
    enforcement: there is nowhere to pass it."""
    import inspect

    assert "benchmark" not in inspect.signature(pan.prop_verdict).parameters


# --- normalization: the denominator bug this layer exists to prevent ------


class _Sleeve:
    """Minimal stand-in for SymbolBacktestResult: an equity curve and trades."""

    def __init__(self, equity, trades):
        self.equity_curve = pd.DataFrame({"Equity": equity})
        self.trades = trades


class _PerSymbolResult:
    def __init__(self, per_symbol):
        self.per_symbol = per_symbol


def _sleeve_with_one_position(days=400, cash=10_000.0, notional=2_000.0, drift=1.0):
    """One sleeve holding a single $2,000 position for the whole window,
    against $10,000 of engine cash -- 20% utilization, the shape that
    produced the original bug."""
    idx = pd.bdate_range("2022-01-03", periods=days)
    equity = pd.Series(cash + np.arange(days) * drift, index=idx)
    trades = pd.DataFrame({
        "Size": [10.0],
        "EntryPrice": [notional / 10.0],
        "ExitPrice": [notional / 10.0],
        "EntryTime": [idx[0]],
        "ExitTime": [idx[-1]],
        "PnL": [drift * days],
    })
    return _Sleeve(equity, trades)


def test_capital_base_is_position_notional_not_engine_cash():
    """THE regression test for the original defect. Ten sleeves of $10,000
    engine cash deploying $2,000 each must normalize against the $20,000
    actually at risk, never the $100,000 of cash the engine happened to
    allocate."""
    result = _PerSymbolResult({f"S{i}": _sleeve_with_one_position() for i in range(10)})
    series, profile = pan.normalized_daily_series(result)

    assert profile.basis == "peak_gross_notional"
    assert profile.peak_gross_notional == pytest.approx(20_000.0)
    # The engine's 10 x $10,000 = $100,000 must NOT be the denominator.
    assert profile.capital_base != pytest.approx(100_000.0)


def test_normalization_is_invariant_to_the_engine_cash_constant():
    """Doubling DEFAULT_CASH changes no economics -- same positions, same
    P&L -- so the normalized return series must be identical. Before the
    fix it would have halved."""
    lean = _PerSymbolResult({f"S{i}": _sleeve_with_one_position(cash=10_000.0) for i in range(5)})
    padded = _PerSymbolResult({f"S{i}": _sleeve_with_one_position(cash=50_000.0) for i in range(5)})

    lean_series, _ = pan.normalized_daily_series(lean)
    padded_series, _ = pan.normalized_daily_series(padded)

    pd.testing.assert_series_equal(
        lean_series.returns, padded_series.returns, check_exact=False, rtol=1e-12
    )


def test_normalization_is_invariant_to_idle_sleeve_count():
    """Adding sleeves that never trade adds engine cash and no exposure.
    That must not move the normalized series either -- the failure mode that
    made a 29-symbol strategy look 29x calmer than it is."""
    traded = {f"S{i}": _sleeve_with_one_position() for i in range(3)}
    idle_idx = pd.bdate_range("2022-01-03", periods=400)
    idle = {
        f"IDLE{i}": _Sleeve(pd.Series(10_000.0, index=idle_idx), pd.DataFrame())
        for i in range(20)
    }
    base_series, _ = pan.normalized_daily_series(_PerSymbolResult(dict(traded)))
    padded_series, profile = pan.normalized_daily_series(
        _PerSymbolResult({**traded, **idle})
    )

    assert profile.peak_gross_notional == pytest.approx(6_000.0)
    pd.testing.assert_series_equal(
        base_series.returns, padded_series.returns, check_exact=False, rtol=1e-12
    )


def test_portfolio_engine_keeps_its_native_return_interpretation():
    """A cross-sectional strategy has no discrete positions and is invested
    by construction. Forcing a notional reconstruction on it would be
    mathematically wrong, so its capital base stays its own equity and the
    normalized series equals the plain percentage change."""
    idx = pd.bdate_range("2022-01-03", periods=400)
    equity = pd.Series(np.linspace(10_000, 13_000, 400), index=idx)

    class _Portfolio:
        equity_curve = equity
        per_symbol = {}

    series, profile = pan.normalized_daily_series(_Portfolio())
    assert profile.basis == "portfolio_equity"
    pd.testing.assert_series_equal(
        series.returns, equity.pct_change().dropna(), check_names=False
    )


def test_exposure_profile_surfaces_a_near_zero_exposure_strategy():
    """The metric that makes the pathology visible instead of letting it
    masquerade as a high Sharpe."""
    idx = pd.bdate_range("2022-01-03", periods=400)
    equity = pd.Series(10_000.0 + np.arange(400) * 0.5, index=idx)
    # One 5-day position in a 400-day window: ~1% of sessions with exposure.
    trades = pd.DataFrame({
        "Size": [10.0], "EntryPrice": [100.0], "ExitPrice": [101.0],
        "EntryTime": [idx[10]], "ExitTime": [idx[14]], "PnL": [10.0],
    })
    result = _PerSymbolResult({"S": _Sleeve(equity, trades)})
    _, profile = pan.normalized_daily_series(result)

    assert profile.days_with_exposure_pct < 2.0
    assert profile.max_concurrent_positions == 1


def test_scaling_the_series_does_not_change_sharpe():
    """Guards against 'fixing' normalization by rescaling until the Sharpe
    looks reasonable. Sharpe is scale-invariant, so any change to it from a
    pure rescale would mean the transform was not a rescale."""
    rng = np.random.default_rng(17)
    returns = pd.Series(rng.normal(0.0004, 0.006, 800))
    base = pan.compute_risk_metrics(returns)
    scaled = pan.compute_risk_metrics(returns * 25.0)
    assert scaled.sharpe == pytest.approx(base.sharpe, rel=1e-9)


def test_active_exposure_metrics_separate_account_from_deployed_risk():
    idx = pd.bdate_range("2022-01-03", periods=400)
    equity = pd.Series(10_000.0 + np.arange(400) * 0.5, index=idx)
    trades = pd.DataFrame({
        "Size": [10.0], "EntryPrice": [100.0], "ExitPrice": [101.0],
        "EntryTime": [idx[10]], "ExitTime": [idx[14]], "PnL": [10.0],
    })
    result = _PerSymbolResult({"S": _Sleeve(equity, trades)})
    series, profile = pan.normalized_daily_series(result)
    metrics = pan.active_exposure_metrics(series.returns, profile)

    assert metrics["daysWithExposurePct"] < 2.0
    assert metrics["sharpeIsScaleInvariant"] is True


# --- search boundary ------------------------------------------------------


def test_sweep_extends_past_the_default_grid_until_failure_is_material():
    """A very calm strategy is safe at every default multiplier. The sweep
    must keep doubling rather than crowning 2.0x, which is the boundary and
    not an optimum."""
    returns = pd.Series([0.0002, -0.0001] * 400)
    analysis = pa.sweep_sizing(
        returns, _config(), pa.PropSimulationConfig(n_paths=150, seed=31)
    )
    assert max(r.risk_multiplier for r in analysis.rows) > 2.0


def test_boundary_is_reported_rather_than_called_optimal():
    """If even the hard ceiling never produces a material failure rate, the
    optimum is unresolved and must say so.

    Shaped like the real pathology: a tiny, essentially riskless drift, so
    payout rises monotonically with size and every named point lands on
    whatever multiplier the search happened to stop at."""
    returns = pd.Series([0.00001] * 400)
    analysis = pa.sweep_sizing(
        returns, _config(), pa.PropSimulationConfig(n_paths=60, seed=32)
    )
    assert analysis.boundary_reached
    assert analysis.unresolved_at_boundary
    assert "maxPayout" in analysis.unresolved_at_boundary


def test_a_resolved_optimum_is_not_flagged_as_a_boundary():
    rng = np.random.default_rng(19)
    returns = pd.Series(rng.normal(0.0003, 0.012, 900))
    analysis = pa.sweep_sizing(
        returns, _config(), pa.PropSimulationConfig(n_paths=300, seed=33)
    )
    assert not analysis.boundary_reached
    assert "maxPayout" not in analysis.unresolved_at_boundary


def test_fixed_base_series_is_aggregated_additively_not_compounded():
    """A fixed-base series is simple P&L over an unchanging denominator, so
    compounding it invents reinvestment that never happened. Measured on a
    flat 0.2%/day series: 50.4% annualized additively vs 65.4% compounded.
    """
    returns = pd.Series([0.002] * (pan.TRADING_DAYS_PER_YEAR * 5))
    additive = pan.compute_risk_metrics(returns, compounding=False)
    compounded = pan.compute_risk_metrics(returns, compounding=True)

    assert additive.annualized_return_pct == pytest.approx(50.4, abs=0.2)
    assert compounded.annualized_return_pct > additive.annualized_return_pct * 1.2


def test_normalized_per_symbol_series_reports_its_aggregation_basis():
    """The adapter must hand callers what they need to pick the right
    aggregation -- otherwise the compounding bug returns silently."""
    result = _PerSymbolResult({f"S{i}": _sleeve_with_one_position() for i in range(4)})
    _, profile = pan.normalized_daily_series(result)
    assert profile.basis == "peak_gross_notional"
    # Which is precisely the condition callers test to disable compounding.
    assert (profile.basis != "portfolio_equity") is True


# --- contamination guards -------------------------------------------------
#
# The measured failure these exist to prevent: Connors RSI2's pooled equity
# curve grew $59,419 while its trades produced $2,927. 95% of the apparent
# "edge" was risk-free interest on 29 x $10,000 of idle sleeve cash. Dividing
# that numerator by deployed capital reported a 37.74% capital-efficiency
# return for a strategy earning ~1.8%/yr. See LESSONS.md.


def _accruing_sleeve(days=400, cash=10_000.0, daily_rate=0.0001, trade_pnl=50.0):
    """A sleeve whose equity grows mostly from CASH INTEREST, with one small
    real trade -- the exact shape that produced the invalid result."""
    idx = pd.bdate_range("2022-01-03", periods=days)
    interest = cash * ((1 + daily_rate) ** np.arange(days) - 1)
    equity = pd.Series(cash + interest, index=idx)
    equity.iloc[200:] += trade_pnl
    trades = pd.DataFrame({
        "Size": [10.0], "EntryPrice": [100.0], "ExitPrice": [105.0],
        "EntryTime": [idx[195]], "ExitTime": [idx[200]], "PnL": [trade_pnl],
    })
    return _Sleeve(equity, trades)


def test_decomposition_detects_idle_cash_dominating_equity_growth():
    """The regression test for the invalid Connors/IBS result: equity growth
    that is overwhelmingly interest must be reported as non-trading, not
    passed through as edge."""
    result = _PerSymbolResult({f"S{i}": _accruing_sleeve() for i in range(10)})
    _, _, decomposition = pan.trading_pnl_daily(result)

    assert decomposition.non_trading_share_pct > 80.0
    assert decomposition.trading_pnl < decomposition.equity_change


def test_trading_pnl_reconciles_to_trade_level_pnl():
    """Independent reconstruction must agree with the trades it was built
    from -- the check that made the original contamination visible."""
    result = _PerSymbolResult({f"S{i}": _accruing_sleeve(trade_pnl=50.0) for i in range(4)})
    pnl, _, decomposition = pan.trading_pnl_daily(result)

    assert decomposition.reconciled
    # 4 sleeves x $50 of real trading P&L, and nothing else.
    assert float(pnl.sum()) == pytest.approx(200.0, abs=1.0)


def test_changing_default_cash_does_not_change_trading_pnl():
    """Doubling the engine's cash constant changes the interest earned and
    nothing economic. Trading P&L must be identical."""
    lean = _PerSymbolResult({f"S{i}": _accruing_sleeve(cash=10_000.0) for i in range(3)})
    padded = _PerSymbolResult({f"S{i}": _accruing_sleeve(cash=100_000.0) for i in range(3)})

    lean_pnl, _, lean_dec = pan.trading_pnl_daily(lean)
    padded_pnl, _, padded_dec = pan.trading_pnl_daily(padded)

    assert float(lean_pnl.sum()) == pytest.approx(float(padded_pnl.sum()), abs=1.0)
    # ...while the CONTAMINATION it would have caused scales with the cash.
    assert padded_dec.non_trading_pnl > lean_dec.non_trading_pnl * 5


def test_adding_idle_sleeves_does_not_change_trading_pnl():
    """Sleeves that only ever hold cash contribute interest and no trades."""
    traded = {f"S{i}": _accruing_sleeve() for i in range(2)}
    idx = pd.bdate_range("2022-01-03", periods=400)
    idle = {
        f"IDLE{i}": _Sleeve(pd.Series(10_000.0, index=idx), pd.DataFrame())
        for i in range(15)
    }
    base_pnl, _, _ = pan.trading_pnl_daily(_PerSymbolResult(dict(traded)))
    padded_pnl, _, _ = pan.trading_pnl_daily(_PerSymbolResult({**traded, **idle}))

    assert float(base_pnl.sum()) == pytest.approx(float(padded_pnl.sum()), abs=1.0)


def test_prop_series_uses_trading_pnl_not_total_equity_return():
    """The architectural rule: a per-symbol prop series is built from trade
    P&L over deployed capital, never from total equity growth."""
    result = _PerSymbolResult({f"S{i}": _accruing_sleeve() for i in range(6)})
    series, profile, decomposition = pan.prop_series_from_result(result)

    assert profile.basis == "marked_peak_gross_notional"
    assert series.source == "trading_pnl/marked_peak_gross"
    # Total return implied by the prop series must track TRADING P&L, not the
    # far larger equity change that includes interest.
    implied = float(series.returns.sum()) * profile.capital_base
    assert implied == pytest.approx(decomposition.trading_pnl, abs=5.0)
    assert implied < decomposition.equity_change / 2


def test_decomposition_is_reported_for_portfolio_engines_too():
    """A cross-sectional engine must not be exempt from decomposition just
    because it shows full exposure -- the assumption is labelled as an
    assumption rather than silently trusted."""
    idx = pd.bdate_range("2022-01-03", periods=400)

    class _Portfolio:
        equity_curve = pd.Series(np.linspace(10_000, 13_000, 400), index=idx)
        per_symbol = {}

    _, profile, decomposition = pan.prop_series_from_result(_Portfolio())
    assert profile.basis == "portfolio_equity"
    assert decomposition.non_trading_share_pct == 0.0
    assert "ASSUMPTION" in decomposition.note
