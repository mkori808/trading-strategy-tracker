"""Fail-closed market-data preflight used before a validation run.

The audit separates structural errors (which make a backtest uninterpretable)
from warnings (which must remain visible but can occur legitimately around an
IPO or exchange holiday).  It never repairs prices in place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module

REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

# Auto-adjusting OHLC columns multiplies each value by a corporate-action
# factor. IEEE-754 rounding can then leave High one representable float below
# Close (or Low one float above it) even though the unadjusted bar was valid.
# Keep this at machine scale: at a $1,000 price the allowance is $0.000000001,
# far below a tick and far too small to conceal a genuinely malformed bar.
OHLC_RELATIVE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class DataQualityReport:
    passed: bool
    critical_issues: list[str]
    warnings: list[str]
    symbols: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "passed": payload["passed"],
            "criticalIssues": payload["critical_issues"],
            "warnings": payload["warnings"],
            "symbols": payload["symbols"],
        }


def audit_frame(symbol: str, bars: pd.DataFrame, interval: str) -> tuple[list[str], list[str], dict[str, Any]]:
    critical: list[str] = []
    warnings: list[str] = []
    if bars is None or bars.empty:
        return [f"{symbol}: no price data"], warnings, {"symbol": symbol, "rows": 0}
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in bars.columns]
    if missing_columns:
        critical.append(f"{symbol}: missing columns {', '.join(missing_columns)}")
        return critical, warnings, {"symbol": symbol, "rows": len(bars), "missingColumns": missing_columns}
    if not isinstance(bars.index, pd.DatetimeIndex):
        critical.append(f"{symbol}: index is not datetime-valued")
    duplicate_count = int(bars.index.duplicated().sum())
    if duplicate_count:
        critical.append(f"{symbol}: {duplicate_count} duplicate timestamps")
    if not bars.index.is_monotonic_increasing:
        critical.append(f"{symbol}: timestamps are not increasing")
    numeric = bars.loc[:, REQUIRED_COLUMNS].apply(pd.to_numeric, errors="coerce")
    nonfinite = int((~np.isfinite(numeric.to_numpy(dtype=float))).sum())
    if nonfinite:
        critical.append(f"{symbol}: {nonfinite} non-finite OHLCV values")
    price = numeric[["Open", "High", "Low", "Close"]]
    nonpositive = int((price <= 0).any(axis=1).sum())
    if nonpositive:
        critical.append(f"{symbol}: {nonpositive} rows have non-positive prices")
    ohlc_tolerance = price.abs().max(axis=1).clip(lower=1.0) * OHLC_RELATIVE_TOLERANCE
    invalid_ohlc = int((
        (numeric["High"] + ohlc_tolerance < price[["Open", "Close", "Low"]].max(axis=1))
        | (numeric["Low"] - ohlc_tolerance > price[["Open", "Close", "High"]].min(axis=1))
    ).sum())
    if invalid_ohlc:
        critical.append(f"{symbol}: {invalid_ohlc} rows violate OHLC bounds")
    negative_volume = int((numeric["Volume"] < 0).sum())
    if negative_volume:
        critical.append(f"{symbol}: {negative_volume} rows have negative volume")
    returns = numeric["Close"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    extreme = int((returns.abs() > 0.50).sum())
    if extreme:
        warnings.append(f"{symbol}: {extreme} one-bar moves exceed 50%; verify corporate actions")
    expected_timezone = "America/New_York"
    timezone = str(bars.index.tz) if getattr(bars.index, "tz", None) is not None else None
    if timezone is None:
        warnings.append(f"{symbol}: timestamps are timezone-naive")
    elif timezone != expected_timezone:
        warnings.append(f"{symbol}: timezone is {timezone}, expected {expected_timezone}")
    if interval == "1d" and len(bars) >= 2:
        gaps = pd.Series(bars.index.normalize()).diff().dt.days.dropna()
        long_gaps = int((gaps > 10).sum())
        if long_gaps:
            warnings.append(f"{symbol}: {long_gaps} gaps exceed 10 calendar days")
    details = {
        "symbol": symbol,
        "rows": int(len(bars)),
        "firstTimestamp": bars.index[0].isoformat(),
        "lastTimestamp": bars.index[-1].isoformat(),
        "timezone": timezone,
        "duplicateTimestamps": duplicate_count,
        "nonfiniteValues": nonfinite,
        "invalidOhlcRows": invalid_ohlc,
        "extremeReturnRows": extreme,
    }
    return critical, warnings, details


def audit_universe(symbols: list[str], interval: str, start: date, end: date) -> DataQualityReport:
    critical: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        bars = data_module.get_bars(symbol, interval, start, end)
        symbol_critical, symbol_warnings, details = audit_frame(symbol, bars, interval)
        critical.extend(symbol_critical)
        warnings.extend(symbol_warnings)
        rows.append(details)
    return DataQualityReport(not critical, critical, warnings, rows)
