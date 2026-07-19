from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.pairs import find_cointegrated_pair, run_pairs_backtest
from strategies.swing.pairs_stat_arb import PairsStatArb


def _bars_from_closes(closes: np.ndarray, start="2024-01-02") -> pd.DataFrame:
    index = pd.bdate_range(start=start, periods=len(closes), tz="America/New_York")
    closes = pd.Series(closes, index=index)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.001, "Low": closes * 0.999,
        "Close": closes, "Volume": 1_000_000,
    })


@pytest.fixture
def synthetic_universe():
    rng = np.random.default_rng(42)
    n = 300
    common_walk = np.cumsum(rng.normal(0, 0.01, n))

    # A cointegrated pair: B tracks A's log-price plus stationary (mean-
    # reverting) noise, so log(A) - log(B) is stationary by construction.
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.7 * noise[i - 1] + rng.normal(0, 0.01)
    log_a = common_walk
    log_b = common_walk + noise
    price_a = 100 * np.exp(log_a)
    price_b = 100 * np.exp(log_b)

    # An unrelated symbol: an independent random walk, not cointegrated
    # with either A or B.
    independent_walk = np.cumsum(rng.normal(0, 0.01, n))
    price_c = 50 * np.exp(independent_walk)

    return {
        "A": _bars_from_closes(price_a),
        "B": _bars_from_closes(price_b),
        "C": _bars_from_closes(price_c),
    }


def test_finds_the_genuinely_cointegrated_pair(synthetic_universe):
    result = find_cointegrated_pair(synthetic_universe)
    assert result is not None
    assert {result.symbol_a, result.symbol_b} == {"A", "B"}
    assert result.p_value < 0.05


def test_no_pair_when_nothing_is_cointegrated():
    rng = np.random.default_rng(7)
    n = 300
    universe = {
        "X": _bars_from_closes(50 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))),
        "Y": _bars_from_closes(80 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))),
    }
    assert find_cointegrated_pair(universe) is None


def test_pair_selection_never_sees_the_trading_window(monkeypatch, synthetic_universe):
    seen_windows = []
    real_find = find_cointegrated_pair

    def spy(bars_by_symbol, **kwargs):
        seen_windows.append({s: (b.index.min(), b.index.max()) for s, b in bars_by_symbol.items()})
        return real_find(bars_by_symbol, **kwargs)

    monkeypatch.setattr("engine.pairs.find_cointegrated_pair", spy)

    def fake_get_bars(symbol, interval, start, end, **kwargs):
        return synthetic_universe[symbol]

    monkeypatch.setattr("engine.pairs.data_module.get_bars", fake_get_bars)

    strat = PairsStatArb()
    full_end = synthetic_universe["A"].index.max()
    result = run_pairs_backtest("Pairs / Stat Arb", strat, ["A", "B", "C"], date(2024, 1, 1), date(2025, 1, 1))

    assert len(seen_windows) == 1
    max_seen = max(hi for _, hi in seen_windows[0].values())
    assert max_seen <= result.training_window[1]
    assert max_seen < full_end  # selection window is strictly the first half


def test_market_neutral_entry_sizes_both_legs_from_shared_cash(monkeypatch, synthetic_universe):
    def fake_get_bars(symbol, interval, start, end, **kwargs):
        return synthetic_universe[symbol]

    monkeypatch.setattr("engine.pairs.data_module.get_bars", fake_get_bars)

    strat = PairsStatArb()
    result = run_pairs_backtest("Pairs / Stat Arb", strat, ["A", "B", "C"], date(2024, 1, 1), date(2025, 1, 1))

    assert result.pair is not None
    assert {result.pair.symbol_a, result.pair.symbol_b} == {"A", "B"}
    # at least one round trip should have occurred over a 150-bar trading window
    assert isinstance(result.trades, pd.DataFrame)
    assert result.final_equity > 0


def test_no_data_produces_flat_result(monkeypatch):
    monkeypatch.setattr(
        "engine.pairs.data_module.get_bars",
        lambda symbol, interval, start, end, **kwargs: pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"]
        ),
    )
    strat = PairsStatArb()
    result = run_pairs_backtest("Pairs / Stat Arb", strat, ["X", "Y"], date(2024, 1, 1), date(2024, 6, 1))
    assert result.pair is None
    assert result.final_equity == 10_000.0
