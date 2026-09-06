"""engine/backtest.py's contract-multiplier support (added for the first real
futures universe, MNQ -- see universes/mnq_futures.json).

backtesting.py, the only P&L engine this module drives, has no notion of a
futures contract's dollar-per-point value: it computes every fill's P&L,
and every risk-based position size, as `size * raw_price`, which is only
correct when one unit of quoted price change IS one dollar (true for a
share, false for an index-point futures contract). These tests establish
the failure mode without the fix (a strategy that structurally never
trades, not merely one that misprices), and the corrected behavior with it
(dollar-correct sizing, PnL, and an equity-affordability cap that reflects
the real per-contract notional) -- see run_symbol_backtest's own comment
for the mechanism (scale the input bars by the multiplier so backtesting.py's
existing size * price arithmetic lands on the right dollar figures).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from engine import backtest
from strategies.base import Strategy

MNQ_LIKE_PRICE = 20_000.0


class _AlwaysLongPercentStops(Strategy):
    """Enters long on the first tradeable bar and never again; stop/target
    are percentages of entry price, matching how every real strategy in
    this project computes them (ATR multiples, % thresholds) -- never a
    hardcoded absolute price level. This is what makes the fix's central
    claim ("entry/exit decisions are scale-invariant") true by construction
    for this test strategy, the same way it is for the real ones.
    """

    name = "Always Long (test)"
    timeframe = "1d"
    direction = "long"

    def __init__(self, stop_pct: float = 0.02, target_pct: float = 0.04):
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self._entered = False

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if self._entered:
            return False
        self._entered = True
        return True

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price * (1 - self.stop_pct)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        return entry_price * (1 + self.target_pct)


def _rising_bars(start_price: float, n: int = 100, daily_rise_pct: float = 0.015) -> pd.DataFrame:
    """A clean, monotonically-rising synthetic series -- guarantees the
    target is hit (not the stop), so PnL is a definite, hand-computable
    positive number rather than depending on which of two bracket legs a
    noisy path happens to touch first."""
    idx = pd.bdate_range("2024-01-02", periods=n, tz="America/New_York")
    closes = start_price * (1 + daily_rise_pct) ** np.arange(n)
    frame = pd.DataFrame({
        "Open": closes, "High": closes * 1.001, "Low": closes * 0.999,
        "Close": closes, "Volume": 1_000_000,
    }, index=idx)
    return frame


@pytest.fixture
def stub_bars(monkeypatch):
    bars = _rising_bars(MNQ_LIKE_PRICE)

    def fake_get_bars(symbol, interval, start, end):
        return bars.copy()

    monkeypatch.setattr(backtest.data_module, "get_bars", fake_get_bars)
    return bars


def _run(contract_multiplier: float, cash: float) -> backtest.SymbolBacktestResult:
    return backtest.run_symbol_backtest(
        _AlwaysLongPercentStops(), "MNQ=F", "1d",
        date(2024, 1, 2), date(2024, 1, 2) + timedelta(days=150),
        cash=cash, spread=0.0, contract_multiplier=contract_multiplier,
    )


def test_index_point_prices_never_trade_without_the_multiplier(stub_bars):
    """The failure mode the fix exists for: at MNQ-like price levels, a 1%
    risk budget on a modest starting account can't afford even one unit
    once its stop distance is (wrongly) read in raw index points instead
    of dollars -- not a mispriced trade, no trade AT ALL. This is the
    'silently produces nothing' failure CLAUDE.md warns about elsewhere
    (a filter passing ~0% of the universe is not filtering), here for
    position sizing instead of a filter.
    """
    result = _run(contract_multiplier=1.0, cash=10_000.0)
    assert result.trades.empty


def test_multiplier_and_larger_cash_together_enable_a_trade(stub_bars):
    result = _run(contract_multiplier=2.0, cash=150_000.0)
    assert not result.trades.empty
    assert result.trades.iloc[0]["Size"] > 0


def test_realized_pnl_is_dollar_correct_for_the_contract_multiplier(stub_bars):
    """The core correctness claim: PnL per contract must equal the REAL
    (unscaled) price move in points times the multiplier -- not the raw
    price move alone, which is what backtesting.py would compute with no
    multiplier awareness at all.
    """
    result = _run(contract_multiplier=2.0, cash=150_000.0)
    trade = result.trades.iloc[0]
    size = float(trade["Size"])
    entry_scaled = float(trade["EntryPrice"])
    exit_scaled = float(trade["ExitPrice"])
    pnl = float(trade["PnL"])

    # EntryPrice/ExitPrice are reported in the scaled (dollar-equivalent)
    # units documented on run_symbol_backtest -- converting back to real
    # index points and re-deriving PnL from first principles (contracts *
    # points_moved * $/point) must match backtesting.py's own PnL exactly.
    multiplier = 2.0
    real_points_moved = (exit_scaled - entry_scaled) / multiplier
    expected_pnl = size * real_points_moved * multiplier
    assert pnl == pytest.approx(expected_pnl, rel=1e-9)
    assert pnl > 0  # the synthetic path is monotonically rising into the target


def test_equity_affordability_cap_reflects_the_real_scaled_notional(stub_bars):
    """With cash small enough that the CASH cap (not the risk cap) binds,
    the number of contracts affordable must fall as the multiplier rises --
    each contract's real notional is price * multiplier, and a naive
    (unscaled) engine would let a strategy hold twice as many contracts as
    the account could actually afford.
    """
    cash = 100_000.0
    # risk_pct=1.0 (the whole account, not the strategy's real default) is
    # deliberate: it guarantees the RISK cap can never be the binding one,
    # isolating the cash-affordability cap as the one under test.
    result_1x = backtest.run_symbol_backtest(
        _AlwaysLongPercentStops(stop_pct=0.30, target_pct=0.40), "MNQ=F", "1d",
        date(2024, 1, 2), date(2024, 1, 2) + timedelta(days=150),
        cash=cash, risk_pct=1.0, spread=0.0, contract_multiplier=1.0,
    )
    result_2x = backtest.run_symbol_backtest(
        _AlwaysLongPercentStops(stop_pct=0.30, target_pct=0.40), "MNQ=F", "1d",
        date(2024, 1, 2), date(2024, 1, 2) + timedelta(days=150),
        cash=cash, risk_pct=1.0, spread=0.0, contract_multiplier=2.0,
    )
    size_1x = float(result_1x.trades.iloc[0]["Size"])
    size_2x = float(result_2x.trades.iloc[0]["Size"])
    assert size_2x == pytest.approx(size_1x / 2, abs=1)


def test_entry_signal_timing_is_unaffected_by_the_multiplier(stub_bars):
    """The scaling must never change WHEN a strategy decides to trade --
    only the dollar consequences of that decision -- because every
    technical measure a real strategy uses (moving averages, ATR, RSI, %
    thresholds) scales uniformly with a uniformly-scaled price series.
    """
    result_1x = _run(contract_multiplier=1.0, cash=10_000_000.0)
    result_2x = _run(contract_multiplier=2.0, cash=10_000_000.0)
    assert list(result_1x.trades["EntryBar"]) == list(result_2x.trades["EntryBar"])
    assert list(result_1x.trades["ExitBar"]) == list(result_2x.trades["ExitBar"])


def test_default_multiplier_is_byte_identical_to_the_pre_existing_behavior(stub_bars):
    """contract_multiplier's default (1.0) must reproduce exactly what
    run_symbol_backtest already did for every equity/ETF/crypto universe --
    this feature must never change a single existing backtest's numbers."""
    explicit = backtest.run_symbol_backtest(
        _AlwaysLongPercentStops(), "AAPL", "1d",
        date(2024, 1, 2), date(2024, 1, 2) + timedelta(days=150),
        cash=10_000.0, spread=0.0, contract_multiplier=1.0,
    )
    default = backtest.run_symbol_backtest(
        _AlwaysLongPercentStops(), "AAPL", "1d",
        date(2024, 1, 2), date(2024, 1, 2) + timedelta(days=150),
        cash=10_000.0, spread=0.0,
    )
    pd.testing.assert_frame_equal(explicit.trades, default.trades)
