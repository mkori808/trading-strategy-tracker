"""Trending movers: today's gainers/losers and momentum streaks across
engine/universe.py:RESEARCH_UNIVERSE.

Reuses engine/quotes.py:symbol_metadata()'s already-computed day change-pct
(from cached daily bars) -- no new fetch logic, no live quote dependency, so
this works with no Alpaca keys just like the Symbols tab does. Live/
descriptive only, same framing as engine/screener.py: never a backtest
input.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from engine import data as data_module
from engine import quotes as quotes_module
from engine.universe import RESEARCH_UNIVERSE

STREAK_LOOKBACK_DAYS = 30
MIN_STREAK_DAYS = 2


def _streak(symbol: str) -> dict[str, Any]:
    """Consecutive up/down days ending on the latest cached close. Only a
    short trailing window is fetched -- the streak length itself is what's
    reported, not the whole price history."""
    end = date.today()
    start = end - timedelta(days=STREAK_LOOKBACK_DAYS)
    bars = data_module.get_bars(symbol, "1d", start, end)
    closes = bars["Close"] if not bars.empty else pd.Series(dtype=float)
    diffs = closes.diff().dropna()
    if diffs.empty or diffs.iloc[-1] == 0:
        return {"symbol": symbol, "direction": None, "days": 0}

    direction = "up" if diffs.iloc[-1] > 0 else "down"
    streak = 0
    for d in reversed(diffs.tolist()):
        if (direction == "up" and d > 0) or (direction == "down" and d < 0):
            streak += 1
        else:
            break
    return {"symbol": symbol, "direction": direction, "days": streak}


def build_movers(symbols: list[str] | None = None, top_n: int = 10) -> dict[str, Any]:
    tickers = symbols if symbols is not None else RESEARCH_UNIVERSE
    rows = [quotes_module.symbol_metadata(s) for s in tickers]
    ranked = [r for r in rows if r["changePct"] is not None]

    gainers = sorted(ranked, key=lambda r: r["changePct"], reverse=True)[:top_n]
    losers = sorted(ranked, key=lambda r: r["changePct"])[:top_n]

    streaks = [s for s in (_streak(t) for t in tickers) if s["days"] >= MIN_STREAK_DAYS]
    streaks.sort(key=lambda s: s["days"], reverse=True)

    return {
        "asOf": date.today().isoformat(),
        "gainers": gainers,
        "losers": losers,
        "streaks": streaks[:top_n],
    }
