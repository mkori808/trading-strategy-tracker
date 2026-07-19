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

    @abstractmethod
    def rebalance(
        self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp
    ) -> dict[str, float]:
        """Given each symbol's bars up to and including `as_of` (must not
        look past it), return target portfolio weights {symbol: weight} for
        the holding period until the next rebalance. Weights need not sum
        to 1.0 -- any remainder is held as cash. An empty dict means fully
        in cash for this period."""
