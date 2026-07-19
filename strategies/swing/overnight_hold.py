"""Overnight Hold -- capture the "overnight risk premium": buy at the close,
sell at the next open, staying flat all day. Historically much of the equity
market's drift has accrued overnight rather than intraday.

This is a config object, not a strategies.base.Strategy, because its trade
can't be expressed in the per-symbol bracket engine: that engine fills entries
at the *next bar's open* and exits on bar closes, so a close->open hold is
unrepresentable there. engine/overnight.py holds the actual close->open logic
(mirroring how pairs_stat_arb.py pairs with engine/pairs.py).

There is no protective stop -- the overnight gap itself is the risk. A
nominal ATR risk unit is used only to size positions and to express results
as R-multiples comparable to the rest of the book; it is NOT a stop order.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategies.params import param_field


@dataclass
class OvernightHold:
    name = "Overnight Hold"
    timeframe = "1d"
    direction = "long"

    # Only hold overnight when the trend is up, to avoid catching falling
    # knives with an unstopped position.
    trend_sma_period: int = param_field(
        200, label="Trend filter SMA period", minimum=50, maximum=250, step=10,
    )
    risk_pct: float = param_field(
        0.01, label="Risk per trade (fraction of equity)", minimum=0.0025, maximum=0.05, step=0.0025,
        help="Nominal-ATR risk unit used for position sizing only -- not a real stop.",
    )
