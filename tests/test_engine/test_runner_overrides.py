"""engine/runner.py's override plumbing: RunRequest, apply_params, and
run_backtest's dispatch to the standard/PEAD/Overnight paths.

The actual backtest execution (engine/backtest.py, engine/overnight.py) is
tested elsewhere -- these tests stub it out and assert on WHAT it was
called with, so they run instantly and without network/data access.
"""

from __future__ import annotations

from datetime import date

import pytest

from engine import runner
from engine.metrics import compute_metrics
from strategies.swing.pullback_21ema import PullbackTo21Ema


def _empty_result(strategy_name: str, symbols: list[str], start: date, end: date):
    import pandas as pd

    from engine.backtest import StrategyBacktestResult

    metrics = compute_metrics(strategy_name, "ALL", pd.DataFrame(), start, end)
    return StrategyBacktestResult(strategy_name, symbols, start, end, {}, metrics)


@pytest.fixture
def stub_engine(monkeypatch):
    """Replace the real backtest engines and network-touching helpers with
    recording stubs. Returns a dict of call logs the test can inspect."""
    calls: dict[str, list] = {"strategy_backtest": [], "seeded": [], "overnight": [], "log_run": []}

    monkeypatch.setattr(runner.data_module, "risk_free_rate", lambda start, end: 0.03)
    monkeypatch.setattr(
        runner.data_module, "get_bars",
        lambda symbol, interval, start, end, **k: __import__("pandas").DataFrame(),
    )
    monkeypatch.setattr(
        runner.data_module, "positive_earnings_dates", lambda symbol: [date(2023, 1, 1)]
    )

    def fake_strategy_backtest(name, strategy, symbols, interval, start, end, **kwargs):
        calls["strategy_backtest"].append(
            {"name": name, "strategy": strategy, "symbols": symbols,
             "interval": interval, "start": start, "end": end}
        )
        return _empty_result(name, symbols, start, end)

    def fake_seeded(name, factory, symbols, interval, start, end, **kwargs):
        instances = [factory(s) for s in symbols]
        calls["seeded"].append(
            {"name": name, "instances": instances, "symbols": symbols, "start": start, "end": end}
        )
        return _empty_result(name, symbols, start, end)

    def fake_overnight(name, config, symbols, start, end, **kwargs):
        calls["overnight"].append(
            {"name": name, "config": config, "symbols": symbols, "start": start, "end": end}
        )
        return _empty_result(name, symbols, start, end)

    def fake_log_run(metrics, symbols, params=None, is_canonical=True):
        calls["log_run"].append({"symbols": symbols, "params": params, "is_canonical": is_canonical})
        return 1

    monkeypatch.setattr(runner, "run_strategy_backtest", fake_strategy_backtest)
    monkeypatch.setattr(runner, "run_strategy_backtest_seeded", fake_seeded)
    monkeypatch.setattr(runner, "run_overnight_backtest", fake_overnight)
    monkeypatch.setattr(runner, "log_run", fake_log_run)
    return calls


PULLBACK = "Pullback to 21 EMA"


# --- RunRequest.is_default ---------------------------------------------------


def test_is_default_true_when_nothing_set():
    assert runner.RunRequest().is_default()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbols": ["AAPL"]},
        {"start": date(2020, 1, 1)},
        {"end": date(2020, 1, 1)},
        {"params": {"x": 1}},
    ],
)
def test_is_default_false_when_any_field_set(kwargs):
    assert not runner.RunRequest(**kwargs).is_default()


# --- apply_params -------------------------------------------------------------


def test_apply_params_returns_the_same_strategy_when_params_is_none():
    strategy = PullbackTo21Ema()
    assert runner.apply_params(strategy, None) is strategy


def test_apply_params_replaces_valid_fields():
    strategy = runner.apply_params(PullbackTo21Ema(), {"pullback_atr_tolerance": 0.9})
    assert strategy.pullback_atr_tolerance == 0.9
    assert strategy.trend_lookback == 10  # untouched


def test_apply_params_rejects_unknown_field():
    with pytest.raises(ValueError, match="not a tunable parameter"):
        runner.apply_params(PullbackTo21Ema(), {"not_a_real_field": 1})


def test_apply_params_rejects_out_of_bounds_value():
    with pytest.raises(ValueError, match="above its maximum"):
        runner.apply_params(PullbackTo21Ema(), {"pullback_atr_tolerance": 999})


def test_apply_params_rejects_wrong_type():
    with pytest.raises(ValueError, match="must be a number"):
        runner.apply_params(PullbackTo21Ema(), {"pullback_atr_tolerance": "wide"})


# --- run_backtest: standard per-symbol path ----------------------------------


def test_no_request_is_canonical_and_uses_registered_defaults(stub_engine):
    runner.run_backtest(PULLBACK)
    call = stub_engine["strategy_backtest"][0]
    assert call["symbols"] == runner.run_config(PULLBACK)[1]
    log = stub_engine["log_run"][0]
    assert log["is_canonical"] is True
    assert log["params"] is None


def test_symbol_override_is_applied_and_logged_as_experiment(stub_engine):
    request = runner.RunRequest(symbols=["AAPL", "MSFT"])
    runner.run_backtest(PULLBACK, request)
    assert stub_engine["strategy_backtest"][0]["symbols"] == ["AAPL", "MSFT"]
    assert stub_engine["log_run"][0]["is_canonical"] is False


def test_date_override_is_applied(stub_engine):
    request = runner.RunRequest(start=date(2019, 1, 1), end=date(2020, 1, 1))
    runner.run_backtest(PULLBACK, request)
    call = stub_engine["strategy_backtest"][0]
    assert call["start"] == date(2019, 1, 1)
    assert call["end"] == date(2020, 1, 1)


def test_param_override_reaches_the_strategy_instance(stub_engine):
    request = runner.RunRequest(params={"trend_lookback": 20})
    runner.run_backtest(PULLBACK, request)
    strategy = stub_engine["strategy_backtest"][0]["strategy"]
    assert strategy.trend_lookback == 20


def test_default_strategy_params_are_unaffected_by_a_request_with_no_params(stub_engine):
    runner.run_backtest(PULLBACK, runner.RunRequest(symbols=["AAPL"]))
    strategy = stub_engine["strategy_backtest"][0]["strategy"]
    assert strategy.pullback_atr_tolerance == 0.5  # untouched default


def test_bad_param_raises_before_any_run_or_log(stub_engine):
    with pytest.raises(ValueError):
        runner.run_backtest(PULLBACK, runner.RunRequest(params={"pullback_atr_tolerance": 999}))
    assert stub_engine["strategy_backtest"] == []
    assert stub_engine["log_run"] == []


# --- run_backtest: PEAD path (per-symbol factory) ----------------------------


def test_pead_symbol_override_and_params_apply_to_every_factory_instance(stub_engine):
    request = runner.RunRequest(symbols=["AAPL", "MSFT"], params={"ema_period": 30})
    runner.run_backtest(runner.PEAD_NAME, request)
    seeded_call = stub_engine["seeded"][0]
    assert seeded_call["symbols"] == ["AAPL", "MSFT"]
    assert all(instance.ema_period == 30 for instance in seeded_call["instances"])
    # real per-symbol earnings seeding still happens through the factory
    assert all(instance.positive_earnings == [date(2023, 1, 1)] for instance in seeded_call["instances"])


def test_pead_default_matches_run_config(stub_engine):
    runner.run_backtest(runner.PEAD_NAME)
    assert stub_engine["seeded"][0]["symbols"] == runner.run_config(runner.PEAD_NAME)[1]
    assert stub_engine["log_run"][0]["is_canonical"] is True


# --- run_backtest: Overnight Hold path ---------------------------------------


def test_overnight_params_and_symbols_override(stub_engine):
    request = runner.RunRequest(symbols=["SPY", "XLK"], params={"risk_pct": 0.02})
    runner.run_backtest(runner.OVERNIGHT_NAME, request)
    call = stub_engine["overnight"][0]
    assert call["symbols"] == ["SPY", "XLK"]
    assert call["config"].risk_pct == 0.02


def test_overnight_default_uses_etf_and_equity_universe(stub_engine):
    runner.run_backtest(runner.OVERNIGHT_NAME)
    from engine.universe import ETF_AND_EQUITY_UNIVERSE

    assert stub_engine["overnight"][0]["symbols"] == ETF_AND_EQUITY_UNIVERSE
    assert stub_engine["log_run"][0]["is_canonical"] is True


# --- strategy_class / run_config completeness --------------------------------


def test_run_config_gives_overnight_its_own_universe_not_the_generic_fallback():
    from engine.universe import ETF_AND_EQUITY_UNIVERSE

    _, symbols, _, _ = runner.run_config(runner.OVERNIGHT_NAME)
    assert symbols == ETF_AND_EQUITY_UNIVERSE


def test_strategy_class_resolves_every_dispatch_branch():
    from strategies.swing.overnight_hold import OvernightHold
    from strategies.swing.pead import PostEarningsDrift
    from strategies.swing.pullback_21ema import PullbackTo21Ema as Pullback
    from strategies.swing.sector_rotation import SectorRotationPlay

    assert runner.strategy_class(runner.PEAD_NAME) is PostEarningsDrift
    assert runner.strategy_class(runner.OVERNIGHT_NAME) is OvernightHold
    assert runner.strategy_class("Sector Rotation Play") is SectorRotationPlay
    assert runner.strategy_class(PULLBACK) is Pullback
