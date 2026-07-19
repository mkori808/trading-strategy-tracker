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

from engine import data as data_module
from engine import quotes as quotes_module
from engine import regime as regime_module
from engine import trend_template
from engine.universe import EQUITY_UNIVERSE, SECTOR_BENCHMARK, SECTOR_UNIVERSE

# How far back the regime log/distribution look -- a rolling window for the
# dashboard, not a backtest range.
REGIME_LOG_DAYS = 90


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
        "trendTemplate": trend_template_scan(),
    }
