import pandas as pd

from engine.backtest import StrategyBacktestResult, SymbolBacktestResult
from engine.stability import split_half_metrics


def _trades(rows):
    """rows: list of (entry_time, exit_time, entry_price, sl, size, pnl)"""
    return pd.DataFrame(
        rows,
        columns=["EntryTime", "ExitTime", "EntryPrice", "SL", "Size", "PnL"],
    )


def _result(trades_by_symbol: dict[str, pd.DataFrame]) -> StrategyBacktestResult:
    per_symbol = {
        symbol: SymbolBacktestResult(symbol, stats=None, trades=trades, equity_curve=None)
        for symbol, trades in trades_by_symbol.items()
    }
    return StrategyBacktestResult("S", list(per_symbol), None, None, per_symbol, metrics=None)


def test_splits_pooled_trades_at_midpoint_by_count():
    # 4 winning trades early (2R each), 4 losing trades late (1R each) --
    # pooled expectancy is positive overall but the split should show the
    # edge concentrated entirely in the first half.
    early = _trades(
        [
            [pd.Timestamp(f"2024-01-0{i}"), pd.Timestamp(f"2024-01-0{i}"), 100, 99, 1, 2]
            for i in range(1, 5)
        ]
    )
    late = _trades(
        [
            [pd.Timestamp(f"2024-02-0{i}"), pd.Timestamp(f"2024-02-0{i}"), 100, 99, 1, -1]
            for i in range(1, 5)
        ]
    )
    result = _result({"SPY": pd.concat([early, late], ignore_index=True)})

    split = split_half_metrics(result)

    assert split.first_half.trades_taken == 4
    assert split.first_half.expectancy_r > 0
    assert split.second_half.trades_taken == 4
    assert split.second_half.expectancy_r < 0


def test_pools_across_symbols_before_splitting():
    spy = _trades([[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-01"), 100, 99, 1, 2]])
    qqq = _trades([[pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-02"), 100, 99, 1, 2]])
    result = _result({"SPY": spy, "QQQ": qqq})

    split = split_half_metrics(result)

    assert split.first_half.trades_taken + split.second_half.trades_taken == 2


def test_empty_trades_produces_not_tested_halves():
    result = _result({"SPY": pd.DataFrame(columns=["EntryTime", "ExitTime", "EntryPrice", "SL", "Size", "PnL"])})

    split = split_half_metrics(result)

    assert split.first_half.trades_taken == 0
    assert split.second_half.trades_taken == 0
