"""Live entry-signal scanner for the paper-trading monitor.

One scan cycle (`scan_once`) fetches the latest fully-closed 5-min bars for
every day-trading strategy's own registered universe (via
engine/runner.py:run_config -- the same universe its canonical backtest
uses, not a hand-rolled list) and checks whether entry_signal fires. New
firings are logged to engine/signals_db.py.

Three things this deliberately does NOT do, each explained where it matters
more than a one-line comment can carry:

1. Doesn't write fetched bars to engine/data.py's parquet cache. That cache
   is what backtests read for reproducibility; injecting a partial live
   session into it would corrupt that guarantee. `_fetch_live_intraday`
   talks to Alpaca directly instead.
2. Doesn't gate alerts through engine/filters.py's FilteredStrategy. The
   canonical backtest doesn't either (FilteredStrategy is only exercised by
   engine/compare_filters.py's exploratory comparison) -- gating live alerts
   would make them inconsistent with what the Compare tab's numbers
   represent. Regime/trend-template state is attached to each alert as
   context (see engine/signals_db.py), never as a condition for logging it.
3. Doesn't place any order. Monitoring and detection only -- see
   engine/alpaca_trading.py's module docstring for why order placement is a
   separate, later effort.

Poll cadence: matched to the strategies' own 5-min bar timeframe, not to
some faster wall-clock interval. Alpaca's free-tier IEX feed never serves
the most recent ~16 minutes (engine/data.py:_ALPACA_RECENT_CUTOFF) --
polling faster than a bar's own timeframe just re-fetches the same
already-stale bar more often, buying no new information.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from engine import regime as regime_module
from engine import signals_db
from engine import trend_template
from engine.alpaca_client import market_data_client
from engine.alpaca_trading import trading_client
from engine.data import _ALPACA_RECENT_CUTOFF, _OHLCV_COLUMNS, _RTH_END, _RTH_START, _localize
from engine.runner import run_config
from engine.universe import SECTOR_BENCHMARK
from strategies.registry import DAY_TRADING_STRATEGIES

# Trading-day warmup margin for any strategy's rolling intraday indicators --
# generous relative to what any of the 5-min day strategies actually need,
# since each only reads the tail of `bars` (must not look past the last row).
SCAN_LOOKBACK_DAYS = 15
BAR_TIMEFRAME_MINUTES = 5

_filter_state_lock = threading.Lock()
_filter_state: dict[str, Any] = {"date": None, "regime_labels": None, "template_frames": {}}


def _refresh_filter_state(today: date, symbols: list[str]) -> None:
    """Recompute regime + trend-template state once per calendar date. This
    process runs for days at a time -- caching this once at import time
    would silently miss a mid-week regime flip."""
    with _filter_state_lock:
        if _filter_state["date"] == today:
            return
        start = today - timedelta(days=trend_template.TREND_WARMUP_DAYS)
        spy_bars = trend_template.load_bars_with_warmup(SECTOR_BENCHMARK, start, today)
        regime_labels = regime_module.regime_series(spy_bars)
        frames = {
            symbol: trend_template.trend_template_frame(
                trend_template.load_bars_with_warmup(symbol, start, today), spy_bars
            )
            for symbol in symbols
        }
        _filter_state.update(date=today, regime_labels=regime_labels, template_frames=frames)


def _filter_context(symbol: str) -> tuple[str | None, bool | None]:
    """Best-effort regime state + trend-template pass, recorded as context
    only -- see the module docstring's point 2. Never gates an alert."""
    regime_labels = _filter_state.get("regime_labels")
    regime_state = (
        str(regime_labels.iloc[-1]) if regime_labels is not None and not regime_labels.empty else None
    )
    frame = _filter_state.get("template_frames", {}).get(symbol)
    template_pass = bool(frame["passes"].iloc[-1]) if frame is not None and not frame.empty else None
    return regime_state, template_pass


def _fetch_live_intraday(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Batched 5-min bars for `symbols`, fetched directly from Alpaca -- one
    request for the whole universe, never written to engine/data.py's
    cache. Mirrors engine/data.py:_fetch_alpaca_intraday's transform
    (RTH filter, tz localization, adjusted OHLCV) without its cache write."""
    client, _ = market_data_client()
    if client is None:
        return {}

    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    end_dt = datetime.now(timezone.utc) - _ALPACA_RECENT_CUTOFF
    start_dt = end_dt - timedelta(days=SCAN_LOOKBACK_DAYS)
    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame(BAR_TIMEFRAME_MINUTES, TimeFrameUnit.Minute),
            start=start_dt,
            end=end_dt,
            feed=DataFeed.IEX,
            adjustment=Adjustment.ALL,
        )
        raw = client.get_stock_bars(req).df
    except Exception:  # noqa: BLE001 -- a bad response must not kill the scan cycle
        return {}
    if raw is None or raw.empty:
        return {}

    out: dict[str, pd.DataFrame] = {}
    available_symbols = set(raw.index.get_level_values("symbol"))
    for symbol in symbols:
        if symbol not in available_symbols:
            continue
        df = raw.xs(symbol, level="symbol")
        df = df.rename(columns=str.capitalize)[_OHLCV_COLUMNS].dropna()
        df = _localize(df)
        df = df.between_time(_RTH_START, _RTH_END, inclusive="left")
        if not df.empty:
            out[symbol] = df
    return out


def _bar_is_closed(bars: pd.DataFrame) -> bool:
    """Defensive check independent of the cutoff baked into the fetch
    request above: the last bar's own end time must already be in the past."""
    if bars.empty:
        return False
    bar_end = bars.index[-1] + timedelta(minutes=BAR_TIMEFRAME_MINUTES)
    return bar_end <= pd.Timestamp.now(tz=bars.index.tz)


def scan_once() -> list[dict[str, Any]]:
    """One scan cycle. No-ops (returns []) if Alpaca isn't configured or the
    market is closed -- checked via Alpaca's own trading calendar
    (TradingClient.get_clock), which already accounts for holidays and early
    closes rather than a hand-rolled weekday+hours check."""
    client, _ = trading_client()
    if client is None:
        return []
    try:
        clock = client.get_clock()
    except Exception:  # noqa: BLE001 -- network hiccup: skip this cycle, don't crash it
        return []
    if not clock.is_open:
        return []

    today = date.today()

    universes: dict[str, list[str]] = {}
    for strategy_name in DAY_TRADING_STRATEGIES:
        _, symbols, _, _ = run_config(strategy_name)
        universes[strategy_name] = symbols

    all_symbols = sorted({s for symbols in universes.values() for s in symbols})
    _refresh_filter_state(today, all_symbols)

    bars_by_symbol = _fetch_live_intraday(all_symbols)
    if not bars_by_symbol:
        return []

    new_alerts: list[dict[str, Any]] = []
    detected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for strategy_name, strategy in DAY_TRADING_STRATEGIES.items():
        for symbol in universes[strategy_name]:
            bars = bars_by_symbol.get(symbol)
            if bars is None or not _bar_is_closed(bars):
                continue
            try:
                fired = strategy.entry_signal(bars)
            except Exception:  # noqa: BLE001 -- one bad symbol must not kill the cycle
                continue
            if not fired:
                continue

            direction = (
                strategy.entry_direction(bars) if strategy.direction == "both" else strategy.direction
            )
            regime_state, template_pass = _filter_context(symbol)
            bar_ts = bars.index[-1]
            price = float(bars["Close"].iloc[-1])
            inserted = signals_db.log_signal(
                detected_at=detected_at,
                bar_timestamp=bar_ts.isoformat(),
                strategy_name=strategy_name,
                symbol=symbol,
                direction=direction,
                price=price,
                timeframe=strategy.timeframe,
                regime_state=regime_state,
                trend_template_pass=template_pass,
            )
            if inserted:
                new_alerts.append({
                    "strategyName": strategy_name,
                    "symbol": symbol,
                    "direction": direction,
                    "barTimestamp": bar_ts.isoformat(),
                    "price": price,
                })
    return new_alerts
