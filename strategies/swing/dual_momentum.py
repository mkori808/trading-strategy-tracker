"""Dual Momentum -- rank the universe by trailing relative momentum, hold
the top N, but only if each holding also clears an absolute filter (its
own trailing return beats the risk-free rate) -- a slot that fails the
absolute filter goes to cash instead of the next-best relative pick.
Cross-sectional / rotational; rebalances monthly.

Structurally different from every other strategy in this book: it needs
every symbol's trailing return at once, ranked against each other, which
strategies.base.Strategy's one-symbol-at-a-time interface can't express --
see strategies/cross_sectional.py, engine/cross_sectional.py, and
LESSONS.md. Not wired into the webapp dashboard (see api/main.py) --
converted to a dataclass here for consistency with every other strategy and
so its rule parameters are logged/inspectable the same way, even though the
UI can't run it yet.

Canonical Dual Momentum (Antonacci) uses a 12-month lookback; the
tracker's "6-12mo" range's upper bound, used here as the single documented
choice rather than blending both.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.cross_sectional import CrossSectionalStrategy
from strategies.params import param_field


@dataclass
class DualMomentum(CrossSectionalStrategy):
    name = "Dual Momentum"
    timeframe = "1mo"

    risk_free_rate: float = 0.0  # structural: the run window's real rate, not a rule parameter

    lookback_trading_days: int = param_field(
        252, label="Momentum lookback (trading days)", minimum=63, maximum=378, step=21,
    )
    top_n: int = param_field(
        5, label="Positions held", minimum=1, maximum=15, step=1,
    )

    def rebalance(
        self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp
    ) -> dict[str, float]:
        trailing_returns: dict[str, float] = {}
        for symbol, bars in universe_bars.items():
            hist = bars.loc[:as_of]
            if len(hist) < self.lookback_trading_days + 1:
                continue
            past = hist["Close"].iloc[-self.lookback_trading_days - 1]
            now = hist["Close"].iloc[-1]
            if past <= 0:
                continue
            trailing_returns[symbol] = now / past - 1

        # Absolute filter: a symbol only qualifies if it beat cash over the
        # same lookback -- otherwise it's excluded outright, not just
        # ranked lower (the "dual" in Dual Momentum).
        qualifying = {s: r for s, r in trailing_returns.items() if r > self.risk_free_rate}
        top = sorted(qualifying, key=qualifying.get, reverse=True)[: self.top_n]
        if not top:
            return {}  # nothing cleared the absolute filter -- fully in cash
        weight = 1.0 / len(top)
        return {symbol: weight for symbol in top}
