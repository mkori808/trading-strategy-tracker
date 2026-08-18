from __future__ import annotations

import pandas as pd
import pytest

from engine.matched_benchmark import annotate_trades, summarize_matches


def _spy(opens=(100.0, 105.0), closes=(101.0, 110.0)):
    index = pd.DatetimeIndex(["2024-01-02", "2024-01-05"])
    return pd.DataFrame({"Open": opens, "Close": closes}, index=index)


def _trade(entry="2024-01-02", exit_="2024-01-05", *, size=10, ret=.20, pnl=200.0):
    return pd.DataFrame([{
        "EntryTime": pd.Timestamp(entry), "ExitTime": pd.Timestamp(exit_),
        "EntryPrice": 100.0, "ExitPrice": 120.0, "Size": size,
        "ReturnPct": ret, "PnL": pnl,
    }])


def test_one_isolated_trade_uses_exact_entry_open_and_exit_close():
    matched = annotate_trades(_trade(), _spy())
    assert matched.loc[0, "TradeReturn"] == pytest.approx(.20)
    assert matched.loc[0, "MatchedSPYReturn"] == pytest.approx(.10)
    assert matched.loc[0, "ExcessVsSPY"] == pytest.approx(.10)
    assert matched.loc[0, "MatchedSPYEntryTime"] == pd.Timestamp("2024-01-02")
    assert matched.loc[0, "MatchedSPYExitTime"] == pd.Timestamp("2024-01-05")


def test_missing_benchmark_bar_is_not_forward_filled():
    matched = annotate_trades(_trade(exit_="2024-01-04"), _spy())
    assert pd.isna(matched.loc[0, "MatchedSPYReturn"])
    summary = summarize_matches(matched, capital_base=10_000)
    assert summary.matched_trades == 0
    assert summary.missing_trades == 1


def test_partial_investment_preserves_cash_period_in_matched_return():
    matched = annotate_trades(_trade(), _spy())
    summary = summarize_matches(matched, capital_base=10_000)
    # Only $1,000 of a $10,000 account was deployed: SPY contributes 1%,
    # strategy contributes 2%, and the other 90% remains cash.
    assert summary.matched_return_pct == pytest.approx(1.0)
    assert summary.matched_excess_pct == pytest.approx(1.0)


def test_overlapping_trades_use_each_deployed_notional_once():
    trades = pd.concat([_trade(), _trade(size=5, ret=.10, pnl=50.0)], ignore_index=True)
    matched = annotate_trades(trades, _spy())
    summary = summarize_matches(matched, capital_base=10_000)
    assert summary.matched_return_pct == pytest.approx(1.5)
    assert summary.matched_excess_pct == pytest.approx(1.0)


def test_short_trade_remains_separate_from_long_spy_opportunity_cost():
    trade = _trade(size=-10, ret=.10, pnl=100.0)
    matched = annotate_trades(trade, _spy())
    # A profitable 10% short and a 10% long-SPY alternative have zero excess;
    # the benchmark is not incorrectly sign-flipped with the position.
    assert matched.loc[0, "ExcessVsSPY"] == pytest.approx(0.0)


def test_net_strategy_pnl_includes_transaction_costs_in_excess():
    trade = _trade(ret=.10, pnl=90.0)  # $100 gross move less $10 costs
    matched = annotate_trades(trade, _spy(opens=(100, 100), closes=(100, 110)))
    summary = summarize_matches(matched, capital_base=1_000)
    assert summary.matched_return_pct == pytest.approx(10.0)
    assert summary.matched_excess_pct == pytest.approx(-1.0)


def test_close_to_open_execution_convention_is_supported():
    spy = _spy(opens=(99.0, 110.0), closes=(100.0, 111.0))
    matched = annotate_trades(
        _trade(), spy, entry_field="Close", exit_field="Open"
    )
    assert matched.loc[0, "MatchedSPYReturn"] == pytest.approx(.10)
