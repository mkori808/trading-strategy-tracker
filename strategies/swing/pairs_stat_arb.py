"""Pairs / Stat Arb -- long one asset, short a cointegrated partner when
their price spread diverges from its historical mean, market-neutral by
construction.

Structurally different from every other strategy in this book in two ways,
neither of which fits strategies.base.Strategy: (1) it trades TWO legs as
one combined position, not one symbol in isolation, and (2) which pair to
trade is itself a discovered parameter (found by cointegration testing),
not a fixed rule ahead of time. See engine/pairs.py for the dedicated
engine this drives. Not wired into the webapp dashboard (see api/main.py)
-- converted to a dataclass here for consistency with every other strategy,
even though the UI can't run it yet.

The tracker's own notes flag this strategy's biggest real risk: "very prone
to great in-sample / broken live." Taken seriously here, not just noted:
engine/pairs.py selects the cointegrated pair using only the FIRST HALF of
the backtest window (a training period) and trades that pair over the
SECOND HALF only -- pair selection never sees the data it's graded on.

This is a plain dataclass, not a Strategy or CrossSectionalStrategy
subclass -- there is exactly one pairs strategy in this book so far, and
introducing a formal ABC for a single implementation would be an
abstraction with no second use yet to justify it.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategies.params import param_field


@dataclass
class PairsStatArb:
    name = "Pairs / Stat Arb"
    timeframe = "1d"

    entry_zscore: float = param_field(
        2.0, label="Entry z-score", minimum=1.0, maximum=4.0, step=0.25,
    )
    exit_zscore: float = param_field(
        0.5, label="Exit z-score", minimum=0.0, maximum=2.0, step=0.1,
    )
    # Cointegration-break stop: a genuinely mean-reverting spread shouldn't
    # wander this far from its rolling mean; if it does, treat the pair
    # relationship as broken rather than "even more overdue to revert."
    stop_zscore: float = param_field(
        4.0, label="Stop z-score (cointegration break)", minimum=2.5, maximum=8.0, step=0.5,
    )
    zscore_lookback: int = param_field(
        60, label="Z-score lookback (bars)", minimum=20, maximum=120, step=5,
    )
