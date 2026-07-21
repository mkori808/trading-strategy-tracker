from datetime import date

import pandas as pd
import pytest

from engine.cross_sectional import _rebalance_dates, run_cross_sectional_backtest
from strategies.cross_sectional import CrossSectionalStrategy


class _FixedWeights(CrossSectionalStrategy):
    """Test double: hands back a pre-scripted weight dict per rebalance
    call, in order, regardless of the bars it's given."""

    name = "Fixed"
    timeframe = "1mo"

    def __init__(self, schedule: list[dict[str, float]]):
        self.schedule = list(schedule)
        self.calls = 0

    def rebalance(self, universe_bars, as_of):
        weights = self.schedule[min(self.calls, len(self.schedule) - 1)]
        self.calls += 1
        return weights


@pytest.fixture
def two_symbol_bars(daily_bars_factory):
    # ~3 months of daily bars so at least 2-3 monthly rebalances occur.
    a = daily_bars_factory(closes=[100 + i * 0.1 for i in range(65)], volumes=[1e6] * 65, start="2024-01-02")
    b = daily_bars_factory(closes=[50 - i * 0.05 for i in range(65)], volumes=[1e6] * 65, start="2024-01-02")
    return a, b


def test_fully_in_one_symbol_tracks_its_return(monkeypatch, two_symbol_bars):
    a, b = two_symbol_bars

    def fake_get_bars(symbol, interval, start, end, **kwargs):
        return {"A": a, "B": b}[symbol]

    monkeypatch.setattr("engine.cross_sectional.data_module.get_bars", fake_get_bars)

    strat = _FixedWeights([{"A": 1.0}])
    result = run_cross_sectional_backtest(
        "Fixed", strat, ["A", "B"], date(2024, 1, 1), date(2024, 4, 1), cash=10_000,
    )

    expected_return_pct = (a["Close"].iloc[-1] / a["Close"].iloc[0] - 1) * 100
    assert abs(result.return_pct - expected_return_pct) < 1.0  # within rounding of rebalance timing
    assert result.final_equity > 10_000  # A trends up


def test_switching_symbols_liquidates_the_dropped_one(monkeypatch, two_symbol_bars):
    a, b = two_symbol_bars

    def fake_get_bars(symbol, interval, start, end, **kwargs):
        return {"A": a, "B": b}[symbol]

    monkeypatch.setattr("engine.cross_sectional.data_module.get_bars", fake_get_bars)

    # Hold A first, then switch entirely to B -- if A weren't liquidated,
    # equity would keep drifting with A's price after the switch.
    strat = _FixedWeights([{"A": 1.0}, {"B": 1.0}, {"B": 1.0}])
    result = run_cross_sectional_backtest(
        "Fixed", strat, ["A", "B"], date(2024, 1, 1), date(2024, 4, 1), cash=10_000,
    )

    assert len(result.rebalances) >= 2
    last_holdings = result.rebalances.iloc[-1]["holdings"]
    assert last_holdings == {"B": 1.0}


def test_empty_weights_means_flat_cash_equity(monkeypatch, two_symbol_bars):
    a, b = two_symbol_bars

    def fake_get_bars(symbol, interval, start, end, **kwargs):
        return {"A": a, "B": b}[symbol]

    monkeypatch.setattr("engine.cross_sectional.data_module.get_bars", fake_get_bars)

    strat = _FixedWeights([{}])
    result = run_cross_sectional_backtest(
        "Fixed", strat, ["A", "B"], date(2024, 1, 1), date(2024, 4, 1), cash=10_000,
    )

    assert result.final_equity == 10_000
    assert result.return_pct == 0.0


def test_daily_rebalance_dates_is_every_trading_day():
    calendar = pd.date_range("2024-01-02", "2024-01-31", freq="B")
    assert _rebalance_dates(calendar, "daily") == set(calendar)


def test_semimonthly_rebalance_dates_is_two_per_month():
    calendar = pd.date_range("2024-01-01", "2024-03-31", freq="B")
    dates = sorted(_rebalance_dates(calendar, "semimonthly"))
    assert len(dates) == 6  # 2 per month x 3 months
    # One rebalance in each half of January: 1st-15th and 16th-31st.
    jan = [d for d in dates if d.month == 1]
    assert len(jan) == 2
    assert jan[0].day <= 15
    assert jan[1].day > 15


def test_quarterly_rebalance_dates_is_one_per_quarter():
    calendar = pd.date_range("2024-01-01", "2024-12-31", freq="B")
    dates = sorted(_rebalance_dates(calendar, "quarterly"))
    assert len(dates) == 4
    assert [d.month for d in dates] == [1, 4, 7, 10]


def test_daily_rebalance_trades_more_often_than_monthly(monkeypatch, two_symbol_bars):
    a, b = two_symbol_bars

    def fake_get_bars(symbol, interval, start, end, **kwargs):
        return {"A": a, "B": b}[symbol]

    monkeypatch.setattr("engine.cross_sectional.data_module.get_bars", fake_get_bars)

    # A strategy that alternates its target weight every call -- daily
    # rebalancing should log far more rebalance events than monthly over
    # the same ~3-month window.
    class _Alternating(CrossSectionalStrategy):
        name = "Alternating"
        timeframe = "1d"

        def __init__(self):
            self.calls = 0

        def rebalance(self, universe_bars, as_of):
            self.calls += 1
            return {"A": 1.0} if self.calls % 2 == 0 else {"B": 1.0}

    monthly = run_cross_sectional_backtest(
        "Alternating", _Alternating(), ["A", "B"], date(2024, 1, 1), date(2024, 4, 1),
        cash=10_000, rebalance_frequency="monthly",
    )
    daily = run_cross_sectional_backtest(
        "Alternating", _Alternating(), ["A", "B"], date(2024, 1, 1), date(2024, 4, 1),
        cash=10_000, rebalance_frequency="daily",
    )
    assert len(daily.rebalances) > len(monthly.rebalances)
    assert len(daily.rebalances) >= 60  # ~63 trading days in the window


def test_no_data_produces_flat_result(monkeypatch):
    class _NeverRebalance(CrossSectionalStrategy):
        name = "Never"
        timeframe = "1mo"

        def rebalance(self, universe_bars, as_of):
            return {}

    monkeypatch.setattr(
        "engine.cross_sectional.data_module.get_bars",
        lambda symbol, interval, start, end, **kwargs: pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"]
        ),
    )

    result = run_cross_sectional_backtest(
        "Never", _NeverRebalance(), ["NOPE"], date(2024, 1, 1), date(2024, 2, 1), cash=5_000,
    )
    assert result.final_equity == 5_000
    assert result.return_pct == 0.0
