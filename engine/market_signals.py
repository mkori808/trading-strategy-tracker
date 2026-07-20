"""Market Signals: a breadth-based sentiment score across
engine/universe.py:RESEARCH_UNIVERSE, extending the existing Market tab
(engine/market_overview.py's regime/sector-performance/trend-template).

This is a composite built from disclosed, computable inputs -- % of tracked
symbols above their 50/200-day SMA, net new-20-day-highs-vs-lows, and SPY's
own regime state (engine/regime.py, unchanged) -- not a claim to replicate
any licensed index (e.g. CNN's Fear & Greed). Every component is returned
alongside the score so "the composite moved" is never opaque; see
CLAUDE.md's "always log selectivity" principle applied to this composite.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from engine import data as data_module
from engine import regime as regime_module
from engine.universe import RESEARCH_UNIVERSE

SMA_SHORT = 50
SMA_LONG = 200
NEW_HIGH_LOW_LOOKBACK_DAYS = 20
# 400 calendar days to cover a 200-TRADING-day SMA, matching
# engine/regime.py:REGIME_WARMUP_DAYS's same calendar-vs-trading-day
# conversion (~5/7 ratio plus a holiday cushion) rather than a tighter
# number that silently starves the long SMA of enough bars.
WARMUP_DAYS = 400

REGIME_COMPONENT_SCORE = {"Bullish": 100.0, "Neutral": 50.0, "Bearish": 0.0}


def _symbol_breadth(symbol: str, end: date) -> dict[str, bool | None]:
    start = end - timedelta(days=WARMUP_DAYS)
    bars = data_module.get_bars(symbol, "1d", start, end)
    closes = bars["Close"] if not bars.empty else None
    if closes is None or len(closes) < SMA_SHORT:
        return {
            "above_sma50": None, "above_sma200": None,
            "new_high_20d": None, "new_low_20d": None,
        }

    last = float(closes.iloc[-1])
    above_sma50 = last > float(closes.tail(SMA_SHORT).mean())
    above_sma200 = (
        last > float(closes.tail(SMA_LONG).mean()) if len(closes) >= SMA_LONG else None
    )

    window = closes.tail(NEW_HIGH_LOW_LOOKBACK_DAYS)
    has_window = len(window) >= NEW_HIGH_LOW_LOOKBACK_DAYS
    return {
        "above_sma50": above_sma50,
        "above_sma200": above_sma200,
        "new_high_20d": bool(has_window and last >= window.max()),
        "new_low_20d": bool(has_window and last <= window.min()),
    }


def market_signals() -> dict[str, Any]:
    """Today's breadth score across RESEARCH_UNIVERSE + SPY regime, folded
    into /api/market's response by api/main.py."""
    end = date.today()
    per_symbol = {s: _symbol_breadth(s, end) for s in RESEARCH_UNIVERSE}

    above50 = [v["above_sma50"] for v in per_symbol.values() if v["above_sma50"] is not None]
    above200 = [v["above_sma200"] for v in per_symbol.values() if v["above_sma200"] is not None]
    highs = sum(1 for v in per_symbol.values() if v["new_high_20d"])
    lows = sum(1 for v in per_symbol.values() if v["new_low_20d"])
    total_hl = sum(1 for v in per_symbol.values() if v["new_high_20d"] is not None)

    pct_above_sma50 = (sum(above50) / len(above50) * 100) if above50 else None
    pct_above_sma200 = (sum(above200) / len(above200) * 100) if above200 else None
    net_high_low_pct = ((highs - lows) / total_hl * 100) if total_hl else None
    # Rescale [-100, 100] net breadth to 0-100, comparable to the other
    # components (50 == as many new highs as new lows).
    net_high_low_score = ((net_high_low_pct + 100) / 2) if net_high_low_pct is not None else None

    spy_regime = regime_module.classify(
        regime_module.load_spy_bars(end - timedelta(days=regime_module.REGIME_WARMUP_DAYS), end)
    )
    regime_score = REGIME_COMPONENT_SCORE.get(spy_regime)

    parts = [
        v for v in [pct_above_sma50, pct_above_sma200, net_high_low_score, regime_score]
        if v is not None
    ]
    breadth_score = (sum(parts) / len(parts)) if parts else None

    return {
        "asOf": end.isoformat(),
        "score": breadth_score,
        "methodology": (
            "Plain average of: % of tracked symbols above their 50-day SMA, "
            "% above their 200-day SMA, net new-20-day-highs-vs-lows "
            "(rescaled to 0-100), and SPY's own regime state (Bullish=100 / "
            "Neutral=50 / Bearish=0). A breadth-based composite built from "
            "these disclosed inputs, not a licensed index."
        ),
        "components": {
            "pctAboveSma50": pct_above_sma50,
            "pctAboveSma200": pct_above_sma200,
            "netNewHighsLowsPct": net_high_low_pct,
            "spyRegime": spy_regime,
            "spyRegimeScore": regime_score,
        },
        "symbolsTracked": len(RESEARCH_UNIVERSE),
        "newHighs20d": highs,
        "newLows20d": lows,
    }
