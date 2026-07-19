import pandas as pd

from engine.metrics import (
    STATUS_NEGATIVE,
    STATUS_NOT_TESTED,
    STATUS_POSITIVE,
    STATUS_SAMPLE_TOO_SMALL,
    compute_metrics,
)


def _trades(rows):
    return pd.DataFrame(rows, columns=["EntryPrice", "SL", "Size", "PnL"])


def test_no_trades_is_not_tested():
    m = compute_metrics("S", "SPY", pd.DataFrame(columns=["EntryPrice", "SL", "Size", "PnL"]))
    assert m.trades_taken == 0
    assert m.status == STATUS_NOT_TESTED


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
    assert m.status == STATUS_POSITIVE


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
