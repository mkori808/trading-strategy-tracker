"""Anchored VWAP (AVWAP) Breakout -- ride a breakout back above the AVWAP
running from a real institutional price event (an earnings gap, or a swing
low if earnings data isn't available -- see engine/run_avwap_breakout.py's
data-availability check and engine/avwap.py's module docstring). Long only
for this first implementation.

`anchor_dates` and `anchor_type` are structural, engine-injected fields
(like `positive_earnings` on PostEarningsDrift, `benchmark_bars` on
SectorRotationPlay) -- per-symbol anchor dates are computed once, vectorized
over the symbol's whole history, by engine.avwap's anchor-selection
functions, BEFORE this strategy is constructed. They are not `param_field()`
because they aren't a rule number a user picks; they're data the run wires
in. The regime + Trend Template gate this strategy also requires is NOT
implemented here -- it's the existing engine/filters.py:FilteredStrategy
wrapper, applied around this strategy specifically at construction time
(see engine/runner.py's AVWAP Breakout branch). This is the first strategy
in the book whose registered definition treats those filters as intrinsic
rather than an optional overlay -- worth remembering if it looks
inconsistent with every other swing strategy, which doesn't self-filter.

Entry rule interpretation, locked before any backtest ran (see LESSONS.md):
"closes above AVWAP for the first time since the anchor... or after being
below AVWAP for at least 5 consecutive bars" is read as ONE rule, not two --
a genuine cross (today's close > AVWAP, yesterday's close < AVWAP) that was
preceded by a run of at least `min_bars_below_avwap` consecutive bars
closed below AVWAP, counted only within the AVWAP window itself (bars
before the anchor don't count, so a fresh anchor mechanically can't have
enough runway for a qualifying cross in its first
`min_bars_below_avwap + 1` bars -- this is what "prevents entering on a
trivial first-bar cross" means in practice).

Exit-type attribution: the hard stop is a broker-level bracket order
(engine/backtest.py's adapter sets it via `stop_price`) invisible to this
class -- backtesting.py closes a stopped-out position without ever calling
`exit_signal` for that bar. So `exit_log` here only ever records
"signal_exit" (AVWAP cross-below) or "time_stop" entries; any real trade
with no matching `exit_log` row must have been the hard stop -- see
engine/run_avwap_breakout.py's post-hoc classification, which relies on
exactly that completeness argument rather than guessing from price
proximity to the stop.

`entry_bar`/`exit_bar` in the logs are positional bar indices (matching
backtesting.py trades' EntryBar/ExitBar columns exactly -- both are
`len(bars)` at the moment the deciding signal fired, since this engine
fills orders at the next bar -- see engine/excursion.py's docstring for the
same convention), not timestamps, so post-hoc joins to the real trades
frame are exact integer matches, not date-alignment guesses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from engine.avwap import compute_avwap
from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class AvwapBreakout(Strategy):
    name = "Anchored VWAP Breakout"
    timeframe = "1d"
    direction = "long"

    anchor_dates: list[date] = field(default_factory=list)
    anchor_type: str = "earnings_gap"

    min_bars_below_avwap: int = param_field(
        5, label="Min consecutive bars below AVWAP before a cross counts",
        minimum=1, maximum=20, step=1,
    )
    volume_confirmation_multiple: float = param_field(
        1.5, label="Entry volume vs. 20-day average", minimum=1.0, maximum=3.0, step=0.1,
    )
    volume_avg_lookback: int = param_field(
        20, label="Volume average lookback (days)", minimum=5, maximum=60, step=5,
    )
    hard_stop_pct: float = param_field(
        0.08, label="Hard stop (% below entry)", minimum=0.02, maximum=0.20, step=0.01,
    )
    time_stop_days: int = param_field(
        60, label="Time stop (calendar days)", minimum=10, maximum=180, step=5,
    )

    def __post_init__(self) -> None:
        # Stored as plain calendar dates, not Timestamps: anchor_dates comes
        # in tz-naive (injected as `date`), but bars.index is tz-aware, and
        # anchor selection only needs day-level granularity anyway. Resolved
        # back to the real (tz-aware) bar timestamp in _active_anchor_asof,
        # since compute_avwap needs an index value actually in `bars`.
        self._anchor_dates: list[date] = sorted(set(self.anchor_dates))
        self._pending_entry_bar: int | None = None
        self._pending_anchor: pd.Timestamp | None = None
        self._entry_time: pd.Timestamp | None = None
        self._entry_bar_index: int | None = None
        self._active_entry_anchor: pd.Timestamp | None = None
        self.entry_log: list[dict] = []
        self.exit_log: list[dict] = []

    def _active_anchor_asof(self, bars: pd.DataFrame) -> pd.Timestamp | None:
        as_of_date = bars.index[-1].date()
        candidates = [d for d in self._anchor_dates if d <= as_of_date]
        if not candidates:
            return None
        anchor_date = candidates[-1]
        matches = bars.index[bars.index.normalize() == pd.Timestamp(anchor_date, tz=bars.index.tz)]
        return matches[0] if len(matches) else None

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.min_bars_below_avwap + 2:
            return False

        anchor = self._active_anchor_asof(bars)
        if anchor is None:
            return False

        avwap_df = compute_avwap(bars, anchor)
        if len(avwap_df) < self.min_bars_below_avwap + 2:
            return False

        closes = bars.loc[avwap_df.index, "Close"]
        avwap = avwap_df["avwap"]
        below = closes < avwap

        # Genuine cross: today above, yesterday below -- not just "still above".
        if not (closes.iloc[-1] > avwap.iloc[-1] and below.iloc[-2]):
            return False

        run = 0
        for is_below in reversed(below.iloc[:-1].tolist()):
            if is_below:
                run += 1
            else:
                break
        if run < self.min_bars_below_avwap:
            return False

        avg_volume = bars["Volume"].shift(1).rolling(self.volume_avg_lookback).mean().iloc[-1]
        if pd.isna(avg_volume):
            return False
        if not (bars["Volume"].iloc[-1] > self.volume_confirmation_multiple * avg_volume):
            return False

        self._pending_entry_bar = len(bars)
        self._pending_anchor = anchor
        self.entry_log.append({
            "entry_bar": self._pending_entry_bar,
            "anchor": anchor,
            "avwap_at_signal": float(avwap.iloc[-1]),
        })
        return True

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price * (1 - self.hard_stop_pct)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if self._pending_entry_bar is not None and self._entry_time is None:
            # First exit_signal call since the real fill -- this bar IS the
            # fill bar (engine/backtest.py's adapter fills at the next bar
            # after the signal), so this is the accurate hold-duration start.
            self._entry_time = bars.index[-1]
            self._entry_bar_index = self._pending_entry_bar
            self._active_entry_anchor = self._pending_anchor
            self._pending_entry_bar = None
            self._pending_anchor = None

        if self._entry_time is None:
            return False  # defensive only -- see module docstring

        today = bars.index[-1]
        exit_bar = len(bars)
        held_calendar_days = (today - self._entry_time).days

        if held_calendar_days >= self.time_stop_days:
            self.exit_log.append({
                "entry_bar": self._entry_bar_index, "exit_bar": exit_bar, "reason": "time_stop",
            })
            self._reset_position_state()
            return True

        avwap_df = compute_avwap(bars, self._active_entry_anchor)
        if avwap_df.empty:
            return False
        if bars["Close"].iloc[-1] < avwap_df["avwap"].iloc[-1]:
            self.exit_log.append({
                "entry_bar": self._entry_bar_index, "exit_bar": exit_bar, "reason": "signal_exit",
            })
            self._reset_position_state()
            return True
        return False

    def _reset_position_state(self) -> None:
        self._entry_time = None
        self._entry_bar_index = None
        self._active_entry_anchor = None
