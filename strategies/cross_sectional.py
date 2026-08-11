"""Interface for strategies whose signal requires ranking the whole
universe against itself at a point in time (e.g. relative momentum), not
one symbol's own bars in isolation.

strategies.base.Strategy's entry_signal(bars) is called once per symbol,
independently, and never sees any other symbol's data -- there is no way
to express "hold the top 5 of 29 symbols by trailing return" through it.
This is a deliberately separate, smaller interface for that different
shape of problem; see engine/cross_sectional.py for the backtest loop that
drives it, and LESSONS.md for why this isn't just bolted onto Strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class CrossSectionalStrategy(ABC):
    name: str
    timeframe: str

    def required_history_days(self) -> int:
        """Trading days of history this strategy needs BEFORE the first bar it
        is asked to rank, so the engine can prepend warmup rather than making
        the strategy wait for the traded window to supply it.

        Defaults to 0: a strategy with no lookback needs no warmup. Any
        strategy that reads a trailing window must override this, or it will
        silently sit in cash for the opening stretch of every backtest.

        This exists because that failure was real and invisible.
        engine/cross_sectional.py fetched only [start, end], and
        strategies/swing/dual_momentum.py answered "not enough bars yet" with
        `continue` -- indistinguishable, in the output, from "nothing cleared
        the absolute-momentum filter today." Dual Momentum at a 273-day
        lookback was therefore 100% cash for the first 274 TRADING days
        (~13 months) of every window, while its CAGR, Sharpe and max drawdown
        were all computed over the full span as though it had been invested.
        It also silently confounded every lookback comparison: sweeping
        105/147/273 moved the dead period to ~5/~7/~13 months, so those runs
        covered materially different investment periods and the return
        difference could not be attributed to the lookback at all.

        engine/regime.py:REGIME_WARMUP_DAYS already solved exactly this for
        the filter layer, with a comment noting it exists so a comparison
        measures the filter rather than the warmup. The lesson simply never
        reached this engine.
        """
        return 0

    @abstractmethod
    def rebalance(
        self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp
    ) -> dict[str, float]:
        """Given each symbol's bars up to and including `as_of` (must not
        look past it), return target portfolio weights {symbol: weight} for
        the holding period until the next rebalance. Weights need not sum
        to 1.0 -- any remainder is held as cash. An empty dict means fully
        in cash for this period."""
