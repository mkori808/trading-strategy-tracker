"""The gating wrapper: regime + trend template in front of an entry rule.

Uses a stub inner strategy whose entry_signal is always True, so anything
that comes back False came from the gate and nothing else.
"""

from __future__ import annotations

import pandas as pd
import pytest

from engine.filters import FilteredStrategy, FilterDiagnostics
from engine.regime import BEARISH, BULLISH, NEUTRAL
from strategies.base import Strategy

NY = "America/New_York"


class AlwaysEnters(Strategy):
    name = "Always Enters"
    timeframe = "1d"
    direction = "long"

    def __init__(self) -> None:
        self.exit_calls = 0

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        return True

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price * 0.95

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        return entry_price * 1.10

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        self.exit_calls += 1
        return True


class AlwaysEntersShort(AlwaysEnters):
    name = "Always Enters Short"
    direction = "short"


class AlwaysEntersBoth(AlwaysEnters):
    name = "Always Enters Both"
    direction = "both"

    def __init__(self, side: str) -> None:
        super().__init__()
        self._side = side

    def entry_direction(self, bars: pd.DataFrame):
        return self._side


DATES = pd.date_range("2024-03-01", periods=5, freq="D", tz=NY)


def _daily_bars(n=5):
    idx = DATES[:n]
    return pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6}, index=idx
    )


def _labels(states):
    return pd.Series(states, index=DATES[: len(states)], dtype=object)


def _passes(values):
    return pd.Series(values, index=DATES[: len(values)], dtype=bool)


def _wrap(inner, regime_states, template_values, diagnostics=None):
    return FilteredStrategy(
        inner, _labels(regime_states), _passes(template_values),
        diagnostics or FilterDiagnostics(),
    )


ALL_BULL = [BULLISH] * 5
ALL_PASS = [True] * 5


# --- the gate ---------------------------------------------------------------


def test_entry_allowed_when_regime_bullish_and_template_passes():
    filtered = _wrap(AlwaysEnters(), ALL_BULL, ALL_PASS)
    assert filtered.entry_signal(_daily_bars())


@pytest.mark.parametrize("state", [NEUTRAL, BEARISH])
def test_entry_blocked_when_regime_is_not_bullish(state):
    filtered = _wrap(AlwaysEnters(), [state] * 5, ALL_PASS)
    assert not filtered.entry_signal(_daily_bars())


def test_entry_blocked_when_trend_template_fails():
    filtered = _wrap(AlwaysEnters(), ALL_BULL, [False] * 5)
    assert not filtered.entry_signal(_daily_bars())


def test_gate_follows_the_regime_bar_by_bar():
    states = [BULLISH, BEARISH, NEUTRAL, BULLISH, BEARISH]
    filtered = _wrap(AlwaysEnters(), states, ALL_PASS)
    allowed = [filtered.entry_signal(_daily_bars(n)) for n in range(1, 6)]
    assert allowed == [True, False, False, True, False]


def test_inner_entry_signal_is_not_even_consulted_when_the_gate_is_shut():
    class Exploding(AlwaysEnters):
        def entry_signal(self, bars):
            raise AssertionError("entry_signal must not run behind a shut gate")

    filtered = _wrap(Exploding(), [BEARISH] * 5, ALL_PASS)
    assert not filtered.entry_signal(_daily_bars())


# --- what the gate must NOT touch ------------------------------------------


def test_open_positions_are_never_force_closed_by_regime():
    """A regime flip stops new entries; it does not exit a live position.
    exit_signal must keep delegating to the strategy's own rule."""
    inner = AlwaysEnters()
    filtered = _wrap(inner, [BEARISH] * 5, [False] * 5)
    assert filtered.exit_signal(_daily_bars()) is True
    assert inner.exit_calls == 1


def test_bearish_bars_with_open_exposure_are_counted_not_acted_on():
    diagnostics = FilterDiagnostics()
    filtered = _wrap(AlwaysEnters(), [BEARISH] * 5, ALL_PASS, diagnostics)
    for n in range(1, 6):
        filtered.exit_signal(_daily_bars(n))
    assert diagnostics.bearish_bars_with_open_position == 5


def test_stop_and_target_pass_through_unchanged():
    filtered = _wrap(AlwaysEnters(), [BEARISH] * 5, [False] * 5)
    assert filtered.stop_price(_daily_bars(), 100.0) == pytest.approx(95.0)
    assert filtered.target_price(_daily_bars(), 100.0) == pytest.approx(110.0)


def test_short_entries_are_not_gated():
    """The filters gate LONG entries. A short strategy is untouched."""
    filtered = _wrap(AlwaysEntersShort(), [BEARISH] * 5, [False] * 5)
    assert filtered.entry_signal(_daily_bars())


def test_both_sided_strategy_is_gated_only_on_its_long_side():
    shut = ([BEARISH] * 5, [False] * 5)
    assert _wrap(AlwaysEntersBoth("short"), *shut).entry_signal(_daily_bars())
    assert not _wrap(AlwaysEntersBoth("long"), *shut).entry_signal(_daily_bars())


def test_wrapper_preserves_strategy_identity():
    inner = AlwaysEnters()
    filtered = _wrap(inner, ALL_BULL, ALL_PASS)
    assert (filtered.name, filtered.timeframe, filtered.direction) == (
        inner.name, inner.timeframe, inner.direction
    )


# --- look-ahead at the lookup boundary -------------------------------------


def _intraday_bars(day: str, time: str = "10:00"):
    idx = pd.DatetimeIndex([pd.Timestamp(f"{day} {time}", tz=NY)])
    return pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6}, index=idx
    )


class IntradayAlwaysEnters(AlwaysEnters):
    timeframe = "5m"


def test_intraday_uses_the_prior_session_not_todays_unfinished_daily_bar():
    """At 10:00 the current session's daily close does not exist yet. The
    lookup must resolve to the PREVIOUS daily bar.

    Yesterday says go, today says stop. A leaky implementation reads today's
    (future) value and blocks; the correct one reads yesterday and allows.
    """
    inner = IntradayAlwaysEnters()
    filtered = FilteredStrategy(
        inner,
        _labels([NEUTRAL, BULLISH, NEUTRAL, NEUTRAL, NEUTRAL]),  # 03-02 bullish, 03-03 not
        _passes([False, True, False, False, False]),
        FilterDiagnostics(),
    )
    assert filtered.entry_signal(_intraday_bars("2024-03-03"))


def test_intraday_gate_shuts_on_yesterdays_state_even_if_today_would_pass():
    inner = IntradayAlwaysEnters()
    filtered = FilteredStrategy(
        inner,
        _labels([NEUTRAL, BEARISH, BULLISH, BULLISH, BULLISH]),  # 03-02 bearish
        _passes([True] * 5),
        FilterDiagnostics(),
    )
    assert not filtered.entry_signal(_intraday_bars("2024-03-03"))


def test_daily_strategy_uses_its_own_bar():
    """Daily strategies DO read the current bar's value -- consistent with
    the engine's signal-on-close, fill-at-next-open convention."""
    filtered = _wrap(AlwaysEnters(), [NEUTRAL, BULLISH], [False, True])
    assert filtered.entry_signal(_daily_bars(2))


def test_bars_before_any_filter_history_are_blocked_not_allowed():
    """No filter data yet must fail closed."""
    filtered = FilteredStrategy(
        AlwaysEnters(), pd.Series(dtype=object), pd.Series(dtype=bool), FilterDiagnostics()
    )
    assert not filtered.entry_signal(_daily_bars())


# --- diagnostics ------------------------------------------------------------


def test_diagnostics_attribute_each_block_to_the_right_filter():
    diagnostics = FilterDiagnostics()
    inner = AlwaysEnters()
    regime_blocked = _wrap(inner, [NEUTRAL] * 5, ALL_PASS, diagnostics)
    template_blocked = _wrap(inner, ALL_BULL, [False] * 5, diagnostics)
    allowed = _wrap(inner, ALL_BULL, ALL_PASS, diagnostics)

    regime_blocked.entry_signal(_daily_bars())
    template_blocked.entry_signal(_daily_bars())
    template_blocked.entry_signal(_daily_bars())
    allowed.entry_signal(_daily_bars())

    assert diagnostics.blocked_by_regime == 1
    assert diagnostics.blocked_by_template == 2
    assert diagnostics.passed_filters == 1
    assert "1 blocked by regime" in diagnostics.summary()


def test_diagnostics_summary_of_an_unused_run_is_not_a_divide_by_zero():
    assert "no entry opportunities" in FilterDiagnostics().summary()


# --- factory wiring (no network) -------------------------------------------


def test_build_filter_factory_wires_regime_and_template_per_symbol(
    monkeypatch, daily_bars_factory
):
    """The factory must hand each symbol ITS OWN template series while all
    symbols share one regime series. Two symbols in the same (bullish) market,
    one in a Minervini uptrend and one in a downtrend, must gate differently.
    """
    import numpy as np

    from engine import filters as filters_module
    from engine import trend_template as tt

    bars_by_symbol = {
        "WIN": daily_bars_factory(list(np.linspace(100.0, 300.0, 500))),
        "LOSE": daily_bars_factory(list(np.linspace(300.0, 100.0, 500))),
        # SPY: a strong uptrend -> Bullish regime, but a weak enough 12-month
        # return that WIN still beats it on relative strength.
        "SPY": daily_bars_factory(list(np.linspace(100.0, 130.0, 500))),
    }

    # One patched loader: SPY is fetched through the same path as any symbol,
    # serving as both the regime input and the RS benchmark.
    monkeypatch.setattr(
        tt, "load_bars_with_warmup", lambda symbol, *a, **k: bars_by_symbol[symbol]
    )

    index = bars_by_symbol["WIN"].index
    start, end = index[0].date(), index[-1].date()
    strategy_for, diagnostics = filters_module.build_filter_factory(
        AlwaysEnters(), ["WIN", "LOSE"], start, end
    )

    last_bar = bars_by_symbol["WIN"]
    assert strategy_for("WIN").entry_signal(last_bar)
    assert not strategy_for("LOSE").entry_signal(last_bar)

    assert diagnostics.regime_distribution[BULLISH] > 0
    assert not diagnostics.scan_summary.empty
    # The template is doing selective work: not everything passes, not nothing.
    assert 0 < diagnostics.scan_summary["pass_rate"].mean() < 1


def test_factory_accepts_a_per_symbol_strategy_factory(monkeypatch, daily_bars_factory):
    """Strategies built per symbol (PEAD's real earnings dates) must be
    filterable the same way a shared instance is."""
    import numpy as np

    from engine import filters as filters_module
    from engine import trend_template as tt

    uptrend = daily_bars_factory(list(np.linspace(100.0, 300.0, 500)))
    flat = daily_bars_factory([100.0] * 500)
    monkeypatch.setattr(tt, "load_bars_with_warmup", lambda symbol, *a, **k: (
        flat if symbol == "SPY" else uptrend
    ))

    built: list[str] = []

    def factory(symbol: str) -> Strategy:
        built.append(symbol)
        return AlwaysEnters()

    index = uptrend.index
    strategy_for, _ = filters_module.build_filter_factory(
        factory, ["AAA", "BBB"], index[0].date(), index[-1].date()
    )
    strategy_for("AAA")
    strategy_for("BBB")
    assert built == ["AAA", "BBB"]
