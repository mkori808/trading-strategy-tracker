"""Shared synthetic-data helpers for Conditional Edge Discovery's tests.

Every helper here is deterministic (fixed seed) and touches no network --
`monkeypatch_bars` replaces `data_module.get_bars` in whichever module needs
it, the same no-network-in-tests discipline the rest of this suite follows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NY = "America/New_York"


def synthetic_bars(n: int, *, start: str = "2015-01-02", seed: int = 0, drift: float = 0.0003, vol: float = 0.01) -> pd.DataFrame:
    """`n` business days of a synthetic geometric random walk, OHLCV shaped
    like `engine/data.py:get_bars` output (tz-aware NY index)."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(start=start, periods=n, tz=NY)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    open_ = open_ * (1 + rng.normal(0, 0.001, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


def monkeypatch_bars(monkeypatch, module, bars_by_symbol: dict[str, pd.DataFrame]) -> None:
    """Replace `module.data_module.get_bars` with a lookup into `bars_by_symbol`,
    sliced to [start, end] the way the real cache would be."""

    def _get_bars(symbol, interval, start, end, force_refresh=False):
        bars = bars_by_symbol.get(symbol)
        if bars is None or bars.empty:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        mask = (bars.index.date >= start) & (bars.index.date <= end)
        return bars.loc[mask]

    monkeypatch.setattr(module.data_module, "get_bars", _get_bars)


def make_trades(
    entries: list[tuple[pd.Timestamp, pd.Timestamp, float, float, float, float]],
) -> pd.DataFrame:
    """A backtesting.py-shaped trades frame from (entry, exit, entry_px, exit_px, sl, size) tuples."""
    rows = []
    for entry, exit_, entry_px, exit_px, sl, size in entries:
        rows.append({
            "EntryTime": entry, "ExitTime": exit_, "EntryPrice": entry_px, "ExitPrice": exit_px,
            "SL": sl, "TP": None, "Size": size,
            "PnL": (exit_px - entry_px) * size,
            "ReturnPct": (exit_px / entry_px - 1.0) * np.sign(size),
            "EntryBar": 0, "ExitBar": 1, "Tag": abs(entry_px - sl),
        })
    return pd.DataFrame(rows)
