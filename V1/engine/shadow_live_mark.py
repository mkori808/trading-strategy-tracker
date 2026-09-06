"""Live intraday equity marks for shadow (non-broker) forward strategies.

Every shadow strategy in this project settles its NAV once per finalized
session -- there is nothing wrong with that as the durable record, but it
means the dashboard shows a frozen number all day while the real paper
account (polled live from Alpaca every 30s, see LiveMonitorView.tsx)
visibly ticks. This module reconstructs a "right now" mark for a shadow
from ingredients every shadow already has: its last FINALIZED session's
equity and per-symbol closing prices (the baseline), its current frozen
holdings (already computed and persisted -- this module never re-runs a
backtest or changes what a strategy holds), and a live/delayed quote per
held symbol (`engine/quotes.py`, itself TTL-cached and rate-limit-safe).

This mirrors api/main.py:execution_daily's `equity - lastEquity` today row
for the real paper account, adapted for a strategy with no broker account
to poll: `live_equity = baseline_equity * (1 + sum(weight_i * (live_i /
baseline_i - 1)))`. Same shape, same "inProgress" semantics, so the
frontend can treat every shadow's today row identically to the paper
account's.

Never persists anything. A live mark is recomputed fresh on every call and
is never written into a NAV ledger, an amendments file, or any other
append-only record -- it is a display-only reconstruction, exactly as
disposable as a live quote itself.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from engine import data as data_module
from engine import quotes as quotes_module

NY = ZoneInfo("America/New_York")


def _unavailable(key: str, reason: str) -> dict[str, Any]:
    return {
        "key": key, "available": False, "reason": reason, "inProgress": True,
        "asOf": datetime.now(NY).isoformat(timespec="seconds"),
    }


def _baseline_close(symbol: str, as_of: date) -> float | None:
    """The symbol's own closing price on (or the last session at/before)
    `as_of` -- the exact price the baseline equity was struck against, so
    the live return computed against it is an apples-to-apples move since
    that close, not since some other arbitrary lookback."""
    bars = data_module.get_bars(symbol, "1d", as_of - timedelta(days=10), as_of)
    if bars is None or bars.empty:
        return None
    window = bars.loc[:str(as_of)]
    if window.empty:
        return None
    return float(window["Close"].iloc[-1])


def compute_live_mark(
    key: str,
    holdings: dict[str, float],
    baseline_equity: float | None,
    baseline_date: date | None,
    baseline_prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Mark `holdings` (symbol -> fraction of equity, any scale, shorts
    negative) to live quotes since a baseline.

    By default the baseline price per symbol is that symbol's own daily
    close on `baseline_date` (correct whenever the baseline genuinely IS a
    finalized session close, true of every shadow except the hourly one).
    Pass `baseline_prices` explicitly when the baseline was struck
    intraday -- e.g. the hourly shadow marks at a 30-minute bar close, not
    the session close, and same-day daily bars don't exist until the
    market closes, so falling back to the daily lookup would silently
    anchor to the WRONG (prior day's) close and overstate the live move.

    Returns the standard live-mark shape regardless of outcome so callers
    and the frontend never need to special-case a missing ingredient:
    `available=False` with a `reason` for "nothing to mark" or "no quotes
    for anything held"; `available=True` with `missingSymbols` listing any
    individual holdings a quote couldn't be found for (contributing zero to
    the mark rather than silently dropping the whole strategy).
    """
    if not holdings:
        return _unavailable(key, "No frozen holdings are recorded to mark.")
    if baseline_equity is None or baseline_date is None:
        return _unavailable(key, "No finalized baseline session is recorded yet.")

    symbols = sorted(holdings)
    quotes = quotes_module.get_quotes(symbols)
    weighted_return = 0.0
    missing: list[str] = []
    for symbol, weight in holdings.items():
        weight = float(weight)
        if not weight:
            continue
        baseline_price = (
            baseline_prices.get(symbol) if baseline_prices is not None
            else _baseline_close(symbol, baseline_date)
        )
        quote = quotes.get(symbol) or {}
        live_price = quote.get("price")
        if baseline_price is None or not baseline_price or live_price is None:
            missing.append(symbol)
            continue
        weighted_return += weight * (float(live_price) / baseline_price - 1.0)

    held_symbols = [s for s, w in holdings.items() if float(w)]
    if held_symbols and len(missing) == len(held_symbols):
        return _unavailable(key, f"No live quote available for any held symbol: {', '.join(sorted(missing))}.")

    equity = baseline_equity * (1 + weighted_return)
    return {
        "key": key,
        "available": True,
        "reason": None,
        "asOf": datetime.now(NY).isoformat(timespec="seconds"),
        "baselineDate": baseline_date.isoformat(),
        "baselineEquity": baseline_equity,
        "equity": equity,
        "profitLoss": equity - baseline_equity,
        "profitLossPct": (equity / baseline_equity - 1.0) * 100 if baseline_equity else None,
        "missingSymbols": sorted(missing),
        "inProgress": True,
    }


def compute_live_mark_from_broker(
    key: str,
    baseline_series_equity: float | None,
    account_equity: float | None,
    account_last_equity: float | None,
) -> dict[str, Any]:
    """The broker-backed alternative to `compute_live_mark`, for the one
    shadow series (Canonical DM) that IS the real Alpaca paper account
    under the hood. Reuses the account's own live equity growth factor
    since its last settled close (the same `equity / lastEquity` Alpaca
    already computes, api/main.py:execution_daily's `today` block uses
    directly) rather than reconstructing it from quotes on weights this
    module would otherwise have to re-derive -- the broker number is
    already more accurate than a synthetic reconstruction."""
    if baseline_series_equity is None:
        return _unavailable(key, "No finalized baseline session is recorded yet.")
    if account_equity is None or not account_last_equity:
        return _unavailable(key, "Live paper-account equity is unavailable.")
    growth = account_equity / account_last_equity
    equity = baseline_series_equity * growth
    return {
        "key": key,
        "available": True,
        "reason": None,
        "asOf": datetime.now(NY).isoformat(timespec="seconds"),
        "baselineDate": None,
        "baselineEquity": baseline_series_equity,
        "equity": equity,
        "profitLoss": equity - baseline_series_equity,
        "profitLossPct": (growth - 1.0) * 100,
        "missingSymbols": [],
        "inProgress": True,
        "source": "alpaca_paper_account",
    }
