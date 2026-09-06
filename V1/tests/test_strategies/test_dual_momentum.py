import pandas as pd

from strategies.swing.dual_momentum import DualMomentum

LOOKBACK_TRADING_DAYS = DualMomentum.lookback_trading_days
TOP_N = DualMomentum.top_n


def _series(closes: list[float], daily_bars_factory) -> pd.DataFrame:
    return daily_bars_factory(closes=closes, volumes=[1e6] * len(closes))


def test_no_holdings_with_too_little_history(daily_bars_factory):
    universe = {"A": _series([100.0] * 10, daily_bars_factory)}
    strat = DualMomentum()
    weights = strat.rebalance(universe, as_of=universe["A"].index[-1])
    assert weights == {}


def test_only_symbols_beating_risk_free_qualify(daily_bars_factory):
    n = LOOKBACK_TRADING_DAYS + 5
    winner = _series([100 * 1.001**i for i in range(n)], daily_bars_factory)  # up strongly
    loser = _series([100 * 0.999**i for i in range(n)], daily_bars_factory)  # down over the window
    universe = {"WIN": winner, "LOSE": loser}

    strat = DualMomentum(risk_free_rate=0.02)
    weights = strat.rebalance(universe, as_of=winner.index[-1])

    assert weights == {"WIN": 1.0}


def test_all_symbols_failing_absolute_filter_means_fully_cash(daily_bars_factory):
    n = LOOKBACK_TRADING_DAYS + 5
    flat = _series([100.0] * n, daily_bars_factory)  # zero trailing return
    universe = {"A": flat, "B": flat}

    strat = DualMomentum(risk_free_rate=0.05)  # cash beats a flat return
    weights = strat.rebalance(universe, as_of=flat.index[-1])

    assert weights == {}


def test_only_top_n_held_when_more_qualify(daily_bars_factory):
    n = LOOKBACK_TRADING_DAYS + 5
    universe = {}
    as_of = None
    for i in range(TOP_N + 3):
        # Distinct growth rates so ranking is unambiguous.
        bars = _series([100 * (1 + 0.0005 * (i + 1)) ** j for j in range(n)], daily_bars_factory)
        universe[f"S{i}"] = bars
        as_of = bars.index[-1]

    strat = DualMomentum(risk_free_rate=0.0)
    weights = strat.rebalance(universe, as_of=as_of)

    assert len(weights) == TOP_N
    # the highest-growth-rate symbols (largest i) should be the ones held
    held = set(weights)
    assert held == {f"S{i}" for i in range(3, TOP_N + 3)}
    assert all(abs(w - 1.0 / TOP_N) < 1e-9 for w in weights.values())


def test_rebalance_never_looks_past_as_of(daily_bars_factory):
    n = LOOKBACK_TRADING_DAYS + 5
    bars = _series([100.0] * n, daily_bars_factory)
    # Append a huge future spike the strategy must not be able to see.
    future = bars.copy()
    future.iloc[-1, future.columns.get_loc("Close")] = 100.0  # keep the as_of bar itself flat
    spike_index = pd.bdate_range(start=bars.index[-1], periods=2, tz=bars.index.tz)[1:2]
    spike = pd.DataFrame(
        {"Open": [1000.0], "High": [1000.0], "Low": [1000.0], "Close": [1000.0], "Volume": [1e6]},
        index=spike_index,
    )
    with_future = pd.concat([future, spike])

    strat = DualMomentum(risk_free_rate=0.0)
    weights = strat.rebalance({"A": with_future}, as_of=bars.index[-1])

    assert weights == {}  # flat trailing return as of `as_of`, future spike must not count
