import itertools

import pandas as pd
import pytest

from engine.metrics import (
    STATUS_INCOMPLETE_WARMUP,
    STATUS_NEGATIVE,
    STATUS_NOT_TESTED,
    STATUS_POSITIVE,
    STATUS_UNVERIFIED,
    STATUS_SAMPLE_TOO_SMALL,
    UNRANKABLE_STATUSES,
    compute_metrics,
    implausible_metrics,
    portfolio_status,
)


def _trades(rows):
    return pd.DataFrame(rows, columns=["EntryPrice", "SL", "Size", "PnL"])


def test_no_trades_is_not_tested():
    m = compute_metrics(
        "S", "SPY", pd.DataFrame(columns=["EntryPrice", "SL", "Size", "PnL"]),
        sharpe=-11_725_571_887.534, sortino=-9_000_000_000.0,
    )
    assert m.trades_taken == 0
    assert m.status == STATUS_NOT_TESTED
    assert m.sharpe is None
    assert m.sortino is None


def test_finite_sharpe_values_are_never_rejected_by_plausibility_floor():
    assert implausible_metrics(sharpe=3.0001) == []
    assert implausible_metrics(sharpe=51_844.877) == []
    assert implausible_metrics(sharpe=-50.0001) == []


@pytest.mark.parametrize("sharpe", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_sharpe_remains_invalid_data(sharpe):
    assert "not finite" in implausible_metrics(sharpe=sharpe)[0]


def test_small_sample_flagged_even_if_profitable():
    # 3 winning trades, each risking $1/share, size 1, PnL=$2 (2R win)
    trades = _trades([[100, 99, 1, 2], [100, 99, 1, 2], [100, 99, 1, 2]])
    m = compute_metrics("S", "SPY", trades)
    assert m.trades_taken == 3
    assert m.win_rate == 1.0
    assert m.status == STATUS_SAMPLE_TOO_SMALL


def test_expectancy_and_profit_factor_match_tracker_definitions():
    # 20 wins of +2R, 10 losses of -1R -> known win rate / expectancy / PF
    rows = [[100, 99, 1, 2] for _ in range(20)] + [[100, 99, 1, -1] for _ in range(10)]
    trades = _trades(rows)
    m = compute_metrics("S", "SPY", trades)
    assert m.trades_taken == 30
    assert m.wins == 20
    assert m.losses == 10
    assert m.win_rate == 20 / 30
    assert abs(m.avg_win_r - 2.0) < 1e-9
    assert abs(m.avg_loss_r - 1.0) < 1e-9
    expected_expectancy = (20 / 30) * 2.0 - (10 / 30) * 1.0
    assert abs(m.expectancy_r - expected_expectancy) < 1e-9
    expected_pf = (20 * 2) / (10 * 1)
    assert abs(m.profit_factor - expected_pf) < 1e-9
    # Synthetic trades carry no Sharpe and no alpha, so the risk-adjusted gate
    # cannot be evaluated -- and an unevaluated gate must not award a tier. This
    # asserted STATUS_POSITIVE until 2026-08-11, which is exactly how Overnight
    # Hold (28,370 trades, Sharpe None, alpha None) became the board's only
    # shortlisted row and therefore promotable to paper execution.
    assert m.status == STATUS_UNVERIFIED


def test_negative_expectancy_flagged_to_drop():
    rows = [[100, 99, 1, 1] for _ in range(10)] + [[100, 99, 1, -3] for _ in range(20)]
    trades = _trades(rows)
    m = compute_metrics("S", "SPY", trades)
    assert m.expectancy_r < 0
    assert m.status == STATUS_NEGATIVE


def test_short_trade_risk_uses_absolute_distance():
    # short: entry 100, stop 101 (risk $1/share), PnL of -2 on size -1 is a 2R loss
    trades = _trades([[100, 101, -1, -2]] * 30)
    m = compute_metrics("S", "SPY", trades)
    assert abs(m.avg_loss_r - 2.0) < 1e-9


def test_status_incomplete_warmup_is_unrankable():
    assert STATUS_INCOMPLETE_WARMUP in UNRANKABLE_STATUSES


# Regression test on the PROPERTY, not one instance: warmup_ok=False must
# force STATUS_INCOMPLETE_WARMUP regardless of what the other numbers say --
# not just for the one real run (Dual Momentum x sp500_current) that exposed
# the gap. Two runs read "Positive return - shortlist" with a failed
# warmup_validity check sitting in the same validation report, because
# portfolio_status() only ever looked at return/Sharpe/benchmark/MDA and had
# no way to know the underlying data was incomplete. Sweeping a grid of
# return_pct/sharpe/benchmark/MDA/alpha combinations -- including values that
# would otherwise grade as strongly positive, strongly negative, or
# unverified -- asserts warmup_ok is checked FIRST and overrides every other
# gate, not just the specific combination that happened to trigger the bug.
_RETURN_PCTS = [-50.0, -0.01, 0.0, 1.0, 58.6, 92.27, 2477.8]
_SHARPES = [None, -1.0, 0.3, 0.5, 1.77, 3.0]
_BENCHMARKS = [None, -10.0, 0.0, 58.6]
_MDA_ALPHA = [(None, None), (17.7, 6.4), (2.0, 5.66)]


@pytest.mark.parametrize(
    "return_pct,sharpe,benchmark,mda_alpha",
    list(itertools.product(_RETURN_PCTS, _SHARPES, _BENCHMARKS, _MDA_ALPHA))[::7],
)
def test_failed_warmup_forces_incomplete_warmup_status_regardless_of_numbers(
    return_pct, sharpe, benchmark, mda_alpha,
):
    mda_pct, alpha_annual_pct = mda_alpha
    status = portfolio_status(
        return_pct, sharpe, benchmark,
        mda_pct=mda_pct, alpha_annual_pct=alpha_annual_pct,
        warmup_ok=False,
    )
    assert status == STATUS_INCOMPLETE_WARMUP
    assert status in UNRANKABLE_STATUSES


def test_passing_warmup_is_unaffected_by_the_new_parameter():
    # warmup_ok=True (the default) must reproduce the exact pre-existing
    # verdict -- this parameter is additive, not a rewrite of the other gates.
    without_param = portfolio_status(58.6, 1.77, 0.0)
    with_param_true = portfolio_status(58.6, 1.77, 0.0, warmup_ok=True)
    assert without_param == with_param_true
    assert without_param != STATUS_INCOMPLETE_WARMUP
