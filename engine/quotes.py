"""Live/delayed quote layer + symbol metadata for the dashboard's tracker view.

This is deliberately a SEPARATE data path from engine/data.py. That module
owns reproducible, cached historical bars that backtests run against; nothing
here ever feeds a backtest. Quotes are real-time-ish and non-reproducible by
nature, so keeping them out of the backtest pipeline preserves the "every
backtest number traces to fixed cached data" guarantee in CLAUDE.md.

Source is Alpaca's Market Data API (per CLAUDE.md, Alpaca is the chosen
broker/data source). The free tier serves the IEX feed, which is delayed and
covers only IEX-routed prints -- good enough for a watchlist, and clearly
labeled as such in the UI. It degrades gracefully: if alpaca-py isn't
installed or no keys are in .env, quote calls return a structured
"unavailable" payload with the reason instead of raising, so the rest of the
dashboard (which runs entirely off cached bars) keeps working.
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from typing import Any

from engine import data as data_module
from engine.alpaca_client import market_data_client
from engine.universe import (
    EQUITY_UNIVERSE,
    MIDCAP_UNIVERSE,
    SECTOR_BENCHMARK,
    SECTOR_UNIVERSE,
)

# Quotes go stale in seconds; this TTL just collapses a burst of dashboard
# requests (one table render = N symbols) into few upstream calls so we stay
# well under Alpaca's 200 req/min free-tier limit. Not a correctness cache.
_QUOTE_TTL_SECONDS = 15.0
_quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_quote_lock = threading.Lock()


# --- Symbol universe metadata (offline; from cached bars only) --------------

# Every symbol the dashboard knows about, with which pre-registered universe(s)
# it belongs to. A symbol can appear in more than one (none currently overlap,
# but the shape shouldn't assume that).
def _all_known_symbols() -> list[str]:
    seen: dict[str, None] = {}
    for sym in [*EQUITY_UNIVERSE, *MIDCAP_UNIVERSE, *SECTOR_UNIVERSE, SECTOR_BENCHMARK]:
        seen.setdefault(sym, None)
    return list(seen)


def _memberships(symbol: str) -> list[str]:
    tags: list[str] = []
    if symbol in EQUITY_UNIVERSE:
        tags.append("Dow")
    if symbol in MIDCAP_UNIVERSE:
        tags.append("Mid-cap")
    if symbol in SECTOR_UNIVERSE:
        tags.append("Sector ETF")
    if symbol == SECTOR_BENCHMARK:
        tags.append("Benchmark")
    return tags


# Liquidity tier from average dollar volume -- same boundaries the cost model
# uses in engine/data.py, surfaced here as a human label instead of a spread.
def _tier(avg_dollar_volume: float | None) -> str:
    if avg_dollar_volume is None:
        return "Unknown"
    if avg_dollar_volume >= 5_000_000_000:
        return "Mega-cap liquidity"
    if avg_dollar_volume >= 1_000_000_000:
        return "Large-cap liquidity"
    if avg_dollar_volume >= 300_000_000:
        return "Mid liquidity"
    return "Thin liquidity"


def _cached_daily(symbol: str):
    """Read this symbol's cached daily parquet directly, no network. Returns
    the DataFrame or None if nothing is cached yet."""
    path = data_module._cache_path(symbol, "1d")
    if not path.exists():
        return None
    import pandas as pd

    df = pd.read_parquet(path)
    return df if not df.empty else None


def symbol_metadata(symbol: str) -> dict[str, Any]:
    """Offline metadata + last cached daily close/change for one symbol.
    Never hits the network -- reads only what engine/data.py already cached,
    so the symbols table renders instantly and works with no Alpaca keys."""
    df = _cached_daily(symbol)
    last_close = prev_close = change_pct = avg_dollar_volume = None
    as_of = None
    if df is not None:
        closes = df["Close"]
        last_close = float(closes.iloc[-1])
        as_of = closes.index[-1].date().isoformat()
        if len(closes) >= 2:
            prev_close = float(closes.iloc[-2])
            if prev_close:
                change_pct = (last_close - prev_close) / prev_close * 100.0
        window = df.tail(60)
        avg_dollar_volume = float((window["Close"] * window["Volume"]).mean())

    return {
        "symbol": symbol,
        "universes": _memberships(symbol),
        "lastClose": last_close,
        "prevClose": prev_close,
        "changePct": change_pct,
        "closeAsOf": as_of,
        "avgDollarVolume": avg_dollar_volume,
        "liquidityTier": _tier(avg_dollar_volume),
        "hasCache": df is not None,
    }


def all_symbol_metadata() -> list[dict[str, Any]]:
    return [symbol_metadata(sym) for sym in _all_known_symbols()]


def daily_history(symbol: str, days: int = 365) -> list[dict[str, Any]]:
    """Daily OHLCV for the chart in the detail view. Uses the normal cached
    pipeline (fetches + caches on a miss). Kept to daily/`get_bars` so it
    never clobbers intraday caches and stays reproducible."""
    end = date.today()
    start = end - timedelta(days=days)
    bars = data_module.get_bars(symbol, "1d", start, end)
    if bars.empty:
        return []
    return [
        {
            "time": ts.date().isoformat(),
            "open": float(row.Open),
            "high": float(row.High),
            "low": float(row.Low),
            "close": float(row.Close),
            "volume": float(row.Volume),
        }
        for ts, row in bars.iterrows()
    ]


# --- Live/delayed quotes (Alpaca; optional) ---------------------------------

def quotes_available() -> tuple[bool, str]:
    client, reason = market_data_client()
    return client is not None, reason


def _fetch_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    client, reason = market_data_client()
    if client is None:
        return {s: {"symbol": s, "source": "unavailable", "reason": reason} for s in symbols}

    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockLatestTradeRequest

    try:
        req = StockLatestTradeRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
        latest = client.get_stock_latest_trade(req)
    except Exception as exc:  # noqa: BLE001 -- e.g. bad symbol, rate limit, network
        return {
            s: {"symbol": s, "source": "unavailable", "reason": f"Alpaca error: {exc}"}
            for s in symbols
        }

    out: dict[str, dict[str, Any]] = {}
    for s in symbols:
        trade = latest.get(s)
        if trade is None:
            out[s] = {"symbol": s, "source": "unavailable", "reason": "No IEX print returned."}
        else:
            out[s] = {
                "symbol": s,
                "price": float(trade.price),
                "asOf": trade.timestamp.isoformat(),
                "source": "alpaca-iex",  # delayed/IEX-only on the free tier
            }
    return out


def get_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Latest delayed trade price per symbol, TTL-cached. Missing/erroring
    symbols come back with source == 'unavailable' and a reason rather than
    being dropped, so the UI can show why."""
    now = time.monotonic()
    result: dict[str, dict[str, Any]] = {}
    to_fetch: list[str] = []

    with _quote_lock:
        for s in symbols:
            hit = _quote_cache.get(s)
            if hit is not None and (now - hit[0]) < _QUOTE_TTL_SECONDS:
                result[s] = hit[1]
            else:
                to_fetch.append(s)

    if to_fetch:
        fetched = _fetch_quotes(to_fetch)
        stamp = time.monotonic()
        with _quote_lock:
            for s, q in fetched.items():
                # Don't cache transient failures -- let the next call retry.
                if q.get("source") != "unavailable":
                    _quote_cache[s] = (stamp, q)
                result[s] = q

    return result
