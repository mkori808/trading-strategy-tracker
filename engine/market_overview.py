"""Market-wide dashboard data: SPY regime, sector performance, and Minervini
Trend Template pass-rate across the equity universe.

This module composes existing functions only -- no new signal-detection or
filter logic lives here. `engine/regime.py` and `engine/trend_template.py`
both already expose an "as of today" entry point (`classify`,
`passes_trend_template`); this module is just the dashboard-shaped wrapper
around them, following the exact wiring `engine/filters.py` already uses
(same warmup loaders, same functions) so there's no second, inconsistent
code path computing the same thing differently.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from engine import data as data_module
from engine import market_signals as market_signals_module
from engine import quotes as quotes_module
from engine import regime as regime_module
from engine import trend_template
from engine.universe import EQUITY_UNIVERSE, SECTOR_BENCHMARK, SECTOR_UNIVERSE

# How far back the regime log/distribution look -- a rolling window for the
# dashboard, not a backtest range.
REGIME_LOG_DAYS = 90

# ~21 trading days, calendar-day approximation matching the convention
# engine/screener.py and engine/market_signals.py already use for lookback
# windows rather than exact trading-day counts.
RS_LOOKBACK_DAYS = 30
# ~5 trading days back, for the rising/falling arrow (is this sector's RS
# improving or deteriorating vs. a week ago) -- a real comparison, not a
# cosmetic indicator.
RS_TREND_OFFSET_DAYS = 7


def _refresh_daily_cache(symbols: list[str], end: date) -> None:
    """Warm today's daily bar into engine/data.py's cache for `symbols` if
    it's missing, so /api/market doesn't depend on some other tab's traffic
    having already refreshed it. get_bars no-ops once the cache covers the
    range, so this is cheap on every call after the first each day."""
    start = end - timedelta(days=regime_module.REGIME_WARMUP_DAYS)
    for symbol in symbols:
        data_module.get_bars(symbol, "1d", start, end)


def current_regime() -> dict[str, Any]:
    """SPY's regime as of today, plus a recent log so a mid-week flip is
    visible rather than just the current label."""
    today = date.today()
    _refresh_daily_cache([regime_module.BENCHMARK], today)
    spy_bars = regime_module.load_spy_bars(today - timedelta(days=REGIME_LOG_DAYS), today)
    labels = regime_module.regime_series(spy_bars)
    log = regime_module.regime_log(labels).tail(REGIME_LOG_DAYS)
    return {
        "current": regime_module.classify(spy_bars),
        "asOf": labels.index[-1].date().isoformat() if not labels.empty else None,
        "distribution": regime_module.regime_distribution(labels),
        "recentLog": [
            {
                "date": row["date"].date().isoformat(),
                "regime": row["regime"],
                "changed": bool(row["changed"]),
            }
            for _, row in log.iterrows()
        ],
    }


def sector_performance() -> list[dict[str, Any]]:
    """Day change for every sector SPDR + the SPY benchmark, from cached
    daily bars (engine/quotes.py:symbol_metadata) -- no new fetch logic.
    Sorted best day-change first; symbols with no cached change sink last."""
    symbols = [*SECTOR_UNIVERSE, SECTOR_BENCHMARK]
    _refresh_daily_cache(symbols, date.today())
    rows = [quotes_module.symbol_metadata(sym) for sym in symbols]
    rows.sort(key=lambda r: (r["changePct"] is None, -(r["changePct"] or 0.0)))
    return rows


def _return_pct(symbol: str, end: date, lookback_days: int) -> float | None:
    """Simple return (%) over the trailing `lookback_days` calendar days
    ending at `end`, from cached daily bars."""
    start = end - timedelta(days=lookback_days + 10)
    bars = data_module.get_bars(symbol, "1d", start, end)
    closes = bars["Close"] if not bars.empty else None
    if closes is None or closes.empty:
        return None
    cutoff = closes.index.max() - pd.Timedelta(days=lookback_days)
    window = closes.loc[closes.index >= cutoff]
    if len(window) < 2:
        return None
    first, last = float(window.iloc[0]), float(window.iloc[-1])
    return None if first <= 0 else (last / first - 1) * 100


def _relative_strength(symbol: str, end: date, lookback_days: int) -> float | None:
    """Sector return / SPY return over the same trailing window, both as
    growth factors (1 + return). RS > 1 means the sector outperformed SPY
    over that window; RS < 1 means it lagged. None if either leg is
    unavailable -- never defaulted to a "neutral" 1.0 that would misrepresent
    missing data as parity."""
    sector_ret = _return_pct(symbol, end, lookback_days)
    spy_ret = _return_pct(SECTOR_BENCHMARK, end, lookback_days)
    if sector_ret is None or spy_ret is None:
        return None
    spy_factor = 1 + spy_ret / 100
    if spy_factor == 0:
        return None
    return (1 + sector_ret / 100) / spy_factor


def sector_rotation() -> dict[str, Any]:
    """Each sector SPDR's relative strength vs SPY (trailing ~21 trading
    days), ranked, with a rising/falling flag comparing today's RS to RS
    ~1 week ago -- reuses the same cached bars sector_performance() already
    warms. A real, computed ratio (not a cosmetic indicator)."""
    today = date.today()
    _refresh_daily_cache([*SECTOR_UNIVERSE, SECTOR_BENCHMARK], today)

    rows = []
    for symbol in SECTOR_UNIVERSE:
        rs_now = _relative_strength(symbol, today, RS_LOOKBACK_DAYS)
        rs_prior = _relative_strength(
            symbol, today - timedelta(days=RS_TREND_OFFSET_DAYS), RS_LOOKBACK_DAYS
        )
        rising = None if rs_now is None or rs_prior is None else rs_now > rs_prior
        rows.append({"symbol": symbol, "relativeStrength": rs_now, "rising": rising})

    rows.sort(key=lambda r: (r["relativeStrength"] is None, -(r["relativeStrength"] or 0.0)))
    return {"asOf": today.isoformat(), "lookbackDays": RS_LOOKBACK_DAYS, "rows": rows}


def trend_template_scan() -> dict[str, Any]:
    """Which symbols in the equity universe pass the 8-point trend template
    today, and why the rest don't -- the drill-down CLAUDE.md's "always log
    selectivity" rule asks for, computed once per symbol (not recomputed
    twice for the same frame)."""
    today = date.today()
    start = today - timedelta(days=REGIME_LOG_DAYS)
    benchmark_bars = trend_template.load_bars_with_warmup(SECTOR_BENCHMARK, start, today)

    rows = []
    passed = 0
    for symbol in EQUITY_UNIVERSE:
        bars = trend_template.load_bars_with_warmup(symbol, start, today)
        frame = trend_template.trend_template_frame(bars, benchmark_bars)
        if frame.empty:
            rows.append({"symbol": symbol, "passes": False, "failedCriteria": list(trend_template.CRITERIA)})
            continue
        last = frame.iloc[-1]
        passes = bool(last["passes"])
        if passes:
            passed += 1
        failed = [] if passes else [c for c in trend_template.CRITERIA if not bool(last[c])]
        rows.append({"symbol": symbol, "passes": passes, "failedCriteria": failed})

    total = len(EQUITY_UNIVERSE)
    return {
        "asOf": today.isoformat(),
        "passCount": passed,
        "failCount": total - passed,
        "passRate": passed / total if total else 0.0,
        "symbols": rows,
    }


def market_overview() -> dict[str, Any]:
    return {
        "regime": current_regime(),
        "sectorPerformance": sector_performance(),
        "sectorRotation": sector_rotation(),
        "trendTemplate": trend_template_scan(),
        "marketSignals": market_signals_module.market_signals(),
    }
