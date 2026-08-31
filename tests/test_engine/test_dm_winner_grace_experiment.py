from __future__ import annotations

import pandas as pd

from engine.dm_winner_grace_experiment import OneRebalanceWinnerGrace, _verify_no_chained_grace


class ScriptedGrace(OneRebalanceWinnerGrace):
    def __init__(self, scripts):
        super().__init__(risk_free_rate=0.0, lookback_trading_days=2, top_n=5)
        self.scripts = scripts
        self.calls = 0

    def _scores(self, universe_bars, as_of):
        value = self.scripts[self.calls]
        self.calls += 1
        return value


def _history(entry_close: float = 105.0) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    prior_index = pd.date_range("2024-01-01", periods=3, freq="B")
    later_index = pd.date_range("2024-01-01", periods=4, freq="B")
    prior, later = {}, {}
    for symbol in "ABCDEF":
        prior[symbol] = pd.DataFrame({"Open": [90, 95, 98], "Close": [91, 96, 99]}, index=prior_index)
        close = entry_close if symbol == "E" else 101.0
        later[symbol] = pd.DataFrame({"Open": [90, 95, 98, 100], "Close": [91, 96, 99, close]}, index=later_index)
    return prior, later


def test_positive_outgoing_gets_exactly_one_grace_period() -> None:
    scripts = [
        {"A": .60, "B": .50, "C": .40, "D": .30, "E": .20, "F": .10},
        {"A": .60, "B": .50, "C": .40, "D": .30, "F": .25, "E": .20},
        {"A": .60, "B": .50, "C": .40, "D": .30, "F": .25, "E": .20},
    ]
    strategy = ScriptedGrace(scripts)
    prior, later = _history(entry_close=105.0)
    first = strategy.rebalance(prior, pd.Timestamp("2024-01-04"))
    second = strategy.rebalance(later, pd.Timestamp("2024-02-01"))
    third = strategy.rebalance(later, pd.Timestamp("2024-03-01"))
    assert set(first) == set("ABCDE")
    assert set(second) == set("ABCDE")
    assert set(third) == set("ABCDF")
    assert strategy.events[0].incumbent_symbol == "E"
    assert strategy.events[0].deferred_incoming_symbol == "F"
    assert _verify_no_chained_grace(strategy.decision_log)


def test_nonpositive_outgoing_exits_without_grace() -> None:
    scripts = [
        {"A": .60, "B": .50, "C": .40, "D": .30, "E": .20, "F": .10},
        {"A": .60, "B": .50, "C": .40, "D": .30, "F": .25, "E": .20},
    ]
    strategy = ScriptedGrace(scripts)
    prior, later = _history(entry_close=99.0)
    strategy.rebalance(prior, pd.Timestamp("2024-01-04"))
    target = strategy.rebalance(later, pd.Timestamp("2024-02-01"))
    assert set(target) == set("ABCDF")
    assert not strategy.events
    assert "E" in strategy.decision_log[-1]["losersExited"]


def test_multiple_grace_events_defer_worst_ranked_incoming_names() -> None:
    scripts = [
        {"A": .60, "B": .50, "C": .40, "D": .30, "E": .20, "F": .10, "G": .05},
        {"A": .60, "B": .50, "C": .40, "F": .35, "G": .34, "D": .20, "E": .19},
    ]
    strategy = ScriptedGrace(scripts)
    prior, later = _history(entry_close=105.0)
    # Add G to both views; it was not initially selected.
    prior["G"] = prior["F"].copy()
    later["G"] = later["F"].copy()
    strategy.rebalance(prior, pd.Timestamp("2024-01-04"))
    target = strategy.rebalance(later, pd.Timestamp("2024-02-01"))
    assert set(target) == set("ABCDE")
    assert [(e.incumbent_symbol, e.deferred_incoming_symbol) for e in strategy.events] == [("D", "F"), ("E", "G")]
