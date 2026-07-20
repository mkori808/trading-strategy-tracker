"""Sanity tests for engine/ensemble.py and strategies/swing/ensemble_voting.py
-- pure-function unit tests for the weighting/sizing/regime primitives
(fast, deterministic, no network), plus one end-to-end rebalance() sanity
check per regime state using synthetic dummy pricing data, per CLAUDE.md's
"every strategy testable in isolation with a small synthetic OHLCV series."
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from engine.ensemble import (
    ACTIVE,
    DEFENSIVE,
    atr_as_of,
    boolean_strategy_position_series,
    boolean_strategy_score,
    composite_scores,
    dual_momentum_scores,
    dynamic_weights,
    inverse_atr_weights,
    macro_regime,
    sub_strategy_rolling_sharpe,
    top_n_by_score,
)
from strategies.base import Strategy
from strategies.swing.ensemble_voting import EnsembleWeightedVoting


# ---------------------------------------------------------------------------
# Macro regime filter
# ---------------------------------------------------------------------------

def test_macro_regime_active_when_close_above_sma(daily_bars_factory):
    bars = daily_bars_factory(closes=[100 + i * 0.5 for i in range(210)])  # steady uptrend
    assert macro_regime(bars, bars.index[-1], sma_period=200) == ACTIVE


def test_macro_regime_defensive_when_close_below_sma(daily_bars_factory):
    # Long steady run-up (so the 200-SMA sits high) then a sharp final drop
    # below it -- the state the master switch exists to catch.
    closes = [100 + i * 0.5 for i in range(200)] + [90, 80, 70]
    bars = daily_bars_factory(closes=closes)
    assert macro_regime(bars, bars.index[-1], sma_period=200) == DEFENSIVE


def test_macro_regime_defensive_when_sma_not_warm():
    # Fewer bars than the SMA period -- unknown regime must gate off, not on.
    bars = pd.DataFrame(
        {"Open": [10, 11], "High": [10, 11], "Low": [10, 11], "Close": [10, 11], "Volume": [1, 1]},
        index=pd.bdate_range("2024-01-02", periods=2),
    )
    assert macro_regime(bars, bars.index[-1], sma_period=200) == DEFENSIVE


def test_macro_regime_defensive_on_empty_bars():
    assert macro_regime(pd.DataFrame(), pd.Timestamp("2024-01-02")) == DEFENSIVE


# ---------------------------------------------------------------------------
# Dynamic ensemble weighting: W_i = max(0, Sharpe_i) / sum(max(0, Sharpe_k))
# ---------------------------------------------------------------------------

def test_dynamic_weights_normalize_to_one():
    sharpe = {"A": 1.5, "B": 0.5, "C": 2.0}
    weights = dynamic_weights(sharpe)
    assert weights == pytest.approx({"A": 1.5 / 4.0, "B": 0.5 / 4.0, "C": 2.0 / 4.0})
    assert sum(weights.values()) == pytest.approx(1.0)


def test_dynamic_weights_clips_negative_sharpe_to_zero():
    sharpe = {"A": 1.0, "B": -2.0, "C": -0.1}
    weights = dynamic_weights(sharpe)
    assert weights["A"] == pytest.approx(1.0)  # sole positive Sharpe takes the whole vote
    assert weights["B"] == 0.0
    assert weights["C"] == 0.0


def test_dynamic_weights_all_non_positive_returns_all_zero_not_a_crash():
    weights = dynamic_weights({"A": -1.0, "B": 0.0})
    assert weights == {"A": 0.0, "B": 0.0}


def test_composite_scores_ignores_zero_weighted_sub_strategies():
    sub_scores = {
        "Winner": {"AAPL": 1.0, "MSFT": 0.0},
        "Loser": {"AAPL": 1.0, "MSFT": 1.0},  # would matter if not zero-weighted
    }
    weights = {"Winner": 1.0, "Loser": 0.0}
    composite = composite_scores(sub_scores, weights)
    assert composite == {"AAPL": 1.0, "MSFT": 0.0}


def test_dual_momentum_scores_rescales_equal_weight_to_unit_scale():
    # DualMomentum.rebalance() with top_n=5 hands back weight 0.2 per held
    # symbol -- dual_momentum_scores should rescale a held symbol to 1.0.
    target_weights = {"AAPL": 0.2, "MSFT": 0.2}
    scores = dual_momentum_scores(target_weights, top_n=5)
    assert scores == {"AAPL": pytest.approx(1.0), "MSFT": pytest.approx(1.0)}


# ---------------------------------------------------------------------------
# Top-N filter
# ---------------------------------------------------------------------------

def test_top_n_by_score_excludes_zero_and_negative():
    scores = {"A": 0.8, "B": 0.0, "C": -0.3, "D": 0.5, "E": 0.9}
    assert top_n_by_score(scores, n=2) == ["E", "A"]
    assert top_n_by_score(scores, n=10) == ["E", "A", "D"]  # B, C excluded regardless of n


# ---------------------------------------------------------------------------
# Inverse-ATR risk-parity sizing
# ---------------------------------------------------------------------------

def test_inverse_atr_weights_sum_to_one_when_uncapped():
    atrs = {"A": 1.0, "B": 2.0, "C": 4.0}
    weights = inverse_atr_weights(atrs, ["A", "B", "C"], max_weight=1.0)
    assert sum(weights.values()) == pytest.approx(1.0)
    # Lower ATR (less volatile) gets more capital, monotonically.
    assert weights["A"] > weights["B"] > weights["C"]


def test_inverse_atr_weights_respects_cap_and_redistributes_to_others():
    # A's uncapped inverse-vol share would dominate -- confirm the cap holds
    # and the overflow lands on the other five names (100% is reachable with
    # 6 names at a 20% cap, unlike the 3-name/60%-max case covered below).
    atrs = {"A": 0.1, "B": 5.0, "C": 5.0, "D": 5.0, "E": 5.0, "F": 5.0}
    weights = inverse_atr_weights(atrs, list(atrs), max_weight=0.20)
    assert weights["A"] == pytest.approx(0.20)
    assert all(w <= 0.20 + 1e-9 for w in weights.values())
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
    # The 5 equal-ATR names split the remaining 80% evenly, each under cap.
    for s in "BCDEF":
        assert weights[s] == pytest.approx(0.16)


def test_inverse_atr_weights_all_capped_correctly_sums_below_one():
    # Only 3 names at a 20% cap: the mathematical maximum is 60%, not 100%
    # -- the unallocated 40% has nowhere to go without breaking the cap, and
    # is implicitly left as cash. Asserting sum==1.0 here would be wrong.
    atrs = {"A": 0.1, "B": 5.0, "C": 5.0}
    weights = inverse_atr_weights(atrs, ["A", "B", "C"], max_weight=0.20)
    assert weights == pytest.approx({"A": 0.20, "B": 0.20, "C": 0.20})
    assert sum(weights.values()) == pytest.approx(0.60)


def test_inverse_atr_weights_drops_zero_or_missing_atr():
    atrs = {"A": 1.0, "B": 0.0}
    weights = inverse_atr_weights(atrs, ["A", "B", "C"], max_weight=1.0)  # C missing entirely
    assert set(weights) == {"A"}
    assert weights["A"] == pytest.approx(1.0)


def test_inverse_atr_weights_empty_input_returns_empty():
    assert inverse_atr_weights({}, [], max_weight=0.2) == {}


def test_atr_as_of_none_when_not_enough_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102])  # far fewer than period+1
    assert atr_as_of(bars, bars.index[-1], period=14) is None


def test_atr_as_of_positive_once_warm(daily_bars_factory):
    bars = daily_bars_factory(closes=[100 + (i % 3) for i in range(30)])
    value = atr_as_of(bars, bars.index[-1], period=14)
    assert value is not None and value > 0


# ---------------------------------------------------------------------------
# Boolean-strategy -> continuous score walker
# ---------------------------------------------------------------------------

class _FixedSignal(Strategy):
    """Test double: enters on a chosen bar index, exits on another, so the
    walker's transitions are checked against a known-good position series
    instead of a real strategy's own (harder to hand-verify) rules."""

    name = "Fixed"
    timeframe = "1d"
    direction = "long"

    def __init__(self, entry_idx: int, exit_idx: int):
        self.entry_idx = entry_idx
        self.exit_idx = exit_idx
        self._i = -1

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        self._i = len(bars) - 1
        return self._i == self.entry_idx

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price * 0.5  # far away -- never touched in this test

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        return (len(bars) - 1) == self.exit_idx


def test_boolean_strategy_position_series_tracks_entry_to_exit(daily_bars_factory):
    bars = daily_bars_factory(closes=[100.0] * 10)
    strategy = _FixedSignal(entry_idx=3, exit_idx=7)
    series = boolean_strategy_position_series(strategy, bars)

    assert list(series.iloc[:3]) == [False, False, False]
    assert list(series.iloc[3:7]) == [True, True, True, True]  # held from entry through exit_idx-1
    assert list(series.iloc[7:]) == [False, False, False]


def test_boolean_strategy_score_is_binary_zero_or_one(daily_bars_factory):
    bars = daily_bars_factory(closes=[100.0] * 5)
    flat = _FixedSignal(entry_idx=99, exit_idx=99)  # never fires
    assert boolean_strategy_score(flat, bars) == 0.0

    held = _FixedSignal(entry_idx=2, exit_idx=99)  # fires and never exits
    assert boolean_strategy_score(held, bars) == 1.0


# ---------------------------------------------------------------------------
# Rolling sub-strategy Sharpe
# ---------------------------------------------------------------------------

def test_sub_strategy_rolling_sharpe_zero_when_nothing_held(daily_bars_factory):
    close_df = pd.DataFrame(
        {"A": [100 + i for i in range(70)]},
        index=pd.bdate_range("2024-01-02", periods=70),
    )
    sharpe = sub_strategy_rolling_sharpe({"A": 0.0}, close_df, close_df.index[-1])
    assert sharpe == 0.0


def test_sub_strategy_rolling_sharpe_positive_for_a_steady_uptrend(daily_bars_factory):
    close_df = pd.DataFrame(
        {"A": [100 * (1.001**i) for i in range(70)]},
        index=pd.bdate_range("2024-01-02", periods=70),
    )
    sharpe = sub_strategy_rolling_sharpe({"A": 1.0}, close_df, close_df.index[-1], window_days=63)
    assert sharpe > 0


# ---------------------------------------------------------------------------
# End-to-end sanity: EnsembleWeightedVoting.rebalance() with dummy pricing
# data, both regime states.
# ---------------------------------------------------------------------------

@pytest.fixture
def ensemble_universe(daily_bars_factory, monkeypatch):
    """~260 trading days (enough to warm IBS's 252-day trend EMA and a
    63-day Sharpe window) of dummy OHLCV for SPY plus three candidate
    symbols, with no earnings history (PEAD contributes an all-zero score,
    which is a valid, honestly-computed outcome, not a test gap -- it's
    covered directly in test_pead.py-equivalent per-strategy tests)."""
    n = 260
    spy_up = daily_bars_factory(closes=[400 + i * 0.3 for i in range(n)], volumes=[5e7] * n)

    # AAA: steady uptrend with a late sharp pullback near the close (bar
    # closing near its own low) -- gives IBS a real entry to find, and
    # Breakout from Consolidation something to range against.
    aaa_closes = [100 + i * 0.2 for i in range(n - 1)]
    aaa_closes.append(aaa_closes[-1] * 0.97)
    aaa = daily_bars_factory(closes=aaa_closes, volumes=[2e6] * n)

    bbb = daily_bars_factory(closes=[50 - i * 0.05 for i in range(n)], volumes=[1e6] * n)  # downtrend
    ccc = daily_bars_factory(closes=[75 + (i % 5) * 0.5 for i in range(n)], volumes=[1e6] * n)  # choppy/flat

    bars = {"SPY": spy_up, "AAA": aaa, "BBB": bbb, "CCC": ccc}
    monkeypatch.setattr(
        "strategies.swing.ensemble_voting.data_module.positive_earnings_dates",
        lambda symbol: [],
    )
    return bars


def test_rebalance_active_regime_respects_allocation_caps(ensemble_universe):
    bars = ensemble_universe
    as_of = bars["SPY"].index[-1]
    strategy = EnsembleWeightedVoting(risk_free_rate=0.0, top_n=6, max_position_weight=0.20)

    weights = strategy.rebalance(bars, as_of)

    # Allocation-sum and per-position-cap sanity, regardless of which
    # symbols specifically got picked (that depends on which sub-strategies
    # fired on this synthetic data, which isn't the point of this test).
    assert all(w <= 0.20 + 1e-9 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "SPY" not in weights  # regime benchmark is never a trade candidate


def test_rebalance_defensive_regime_is_fully_flat(ensemble_universe):
    bars = dict(ensemble_universe)
    n = len(bars["SPY"])
    # Overwrite SPY with a late sharp drop below its own 200-SMA.
    closes = [400 + i * 0.3 for i in range(n - 5)] + [350, 340, 330, 320, 310]
    import pandas as pd  # local import mirrors daily_bars_factory's own dependency

    bars["SPY"] = bars["SPY"].copy()
    bars["SPY"]["Close"] = closes
    bars["SPY"]["Open"] = closes
    bars["SPY"]["High"] = closes
    bars["SPY"]["Low"] = closes

    as_of = bars["SPY"].index[-1]
    strategy = EnsembleWeightedVoting()

    assert strategy.rebalance(bars, as_of) == {}


def test_rebalance_missing_spy_fails_defensive_not_a_crash(ensemble_universe):
    bars = {k: v for k, v in ensemble_universe.items() if k != "SPY"}
    strategy = EnsembleWeightedVoting()
    assert strategy.rebalance(bars, bars["AAA"].index[-1]) == {}
