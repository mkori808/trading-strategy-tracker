"""engine/runner.py:_universe_backtest_kwargs -- threading a registered
universe's contract multiplier and starting-cash override into
run_strategy_backtest(_seeded), the same way _fixed_universe_spread already
threads a universe's cost model. See universes/mnq_futures.json and
engine/backtest.py:run_symbol_backtest for why a futures universe needs both.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from engine import runner
from engine.metrics import compute_metrics
from strategies.swing.pullback_21ema import PullbackTo21Ema


def _empty_result(strategy_name, symbols, start, end):
    from engine.backtest import StrategyBacktestResult

    metrics = compute_metrics(strategy_name, "ALL", pd.DataFrame(), start, end)
    return StrategyBacktestResult(strategy_name, symbols, start, end, {}, metrics)


@pytest.fixture
def stub_engine(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(runner.data_module, "risk_free_rate", lambda start, end: 0.0)
    monkeypatch.setattr(
        runner.data_module, "get_bars",
        lambda symbol, interval, start, end, **k: pd.DataFrame(),
    )
    monkeypatch.setattr(
        runner, "_fixed_universe_spread", lambda request, symbols, start, end: None,
    )
    monkeypatch.setattr(runner, "mean_spread_bps", lambda *a, **k: None)

    def fake_strategy_backtest(name, strategy, symbols, interval, start, end, **kwargs):
        calls.append(kwargs)
        return _empty_result(name, symbols, start, end)

    monkeypatch.setattr(runner, "run_strategy_backtest", fake_strategy_backtest)
    monkeypatch.setattr(runner, "log_run", lambda *a, **k: 1)
    return calls


PULLBACK = "Pullback to 21 EMA"


def test_no_universe_passes_no_multiplier_or_cash_override(stub_engine):
    runner.run_backtest(PULLBACK, runner.RunRequest(symbols=["AAPL"]))
    assert "contract_multiplier" not in stub_engine[0]
    assert "cash" not in stub_engine[0]


def test_an_equity_universe_with_default_multiplier_passes_nothing_extra(stub_engine):
    # sp500_proxy: assetClass single-instrument, contractMultiplier
    # defaults to 1.0, no startingCash override -- must be indistinguishable
    # from no universe at all in what reaches run_strategy_backtest.
    runner.run_backtest(
        PULLBACK, runner.RunRequest(symbols=["SPY"], universe_id="sp500_proxy"),
    )
    assert "contract_multiplier" not in stub_engine[0]
    assert "cash" not in stub_engine[0]


def test_the_futures_universe_passes_its_multiplier_and_starting_cash(stub_engine):
    runner.run_backtest(
        PULLBACK, runner.RunRequest(symbols=["MNQ=F"], universe_id="mnq_futures"),
    )
    call = stub_engine[0]
    assert call["contract_multiplier"] == 2.0
    assert call["cash"] == 150_000.0


def test_universe_backtest_kwargs_helper_is_empty_with_no_request():
    assert runner._universe_backtest_kwargs(None) == {}


def test_universe_backtest_kwargs_helper_is_empty_with_no_universe_id():
    assert runner._universe_backtest_kwargs(runner.RunRequest(symbols=["AAPL"])) == {}
