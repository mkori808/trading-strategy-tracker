"""Recover/extend daily OHLCV caches through Yahoo's public chart endpoint.

This is a maintenance fallback for when yfinance's cookie/crumb client is
rate-limited while the underlying chart endpoint remains available.  Fresh
rows are adjusted with Yahoo's adjusted-close ratio, audited, then merged with
the existing cache so recovery can never truncate older history.

Usage:
    python -m engine.recover_daily_cache
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import tempfile
from urllib.parse import quote

import pandas as pd
import requests

from engine import data as data_module
from engine.data_quality import audit_frame
from engine.universe import (
    EQUITY_UNIVERSE,
    MIDCAP_UNIVERSE,
    SECTOR_UNIVERSE,
    SMALL_CAP_UNIVERSE,
    TIMEZONE,
)


RECOVERY_SYMBOLS = list(dict.fromkeys([
    *EQUITY_UNIVERSE,
    *MIDCAP_UNIVERSE,
    *SMALL_CAP_UNIVERSE,
    *SECTOR_UNIVERSE,
    "SPY", "IWM", "IWD", "IWF", "MTUM",
]))


def fetch_chart(symbol: str, start: date, end: date) -> pd.DataFrame:
    period1 = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp())
    # Yahoo's period2 is exclusive, matching yfinance's start/end contract.
    # Excluding today's still-forming candle also prevents transient OHLC
    # inconsistencies (observed High below Open on MMM and DIS intraday).
    period2 = int(datetime.combine(end, time.min, tzinfo=timezone.utc).timestamp())
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}",
        params={
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div,splits",
        },
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 TradingStrategyLab/1.0"},
    )
    response.raise_for_status()
    payload = response.json()["chart"]
    if payload.get("error") or not payload.get("result"):
        raise RuntimeError(json.dumps(payload.get("error") or "empty chart response"))
    result = payload["result"][0]
    timestamps = result.get("timestamp") or []
    quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adjusted = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    if not timestamps:
        return pd.DataFrame(columns=data_module._OHLCV_COLUMNS)

    raw_close = quote_rows.get("close") or []
    records = []
    trading_dates = []
    for index, stamp in enumerate(timestamps):
        values = {
            key: (quote_rows.get(key.lower()) or [None] * len(timestamps))[index]
            for key in data_module._OHLCV_COLUMNS
        }
        if any(values[key] is None for key in data_module._OHLCV_COLUMNS):
            continue
        close = raw_close[index]
        adj_close = adjusted[index] if index < len(adjusted) else close
        factor = float(adj_close) / float(close) if close else 1.0
        for key in ("Open", "High", "Low", "Close"):
            values[key] = float(values[key]) * factor
        values["Volume"] = float(values["Volume"])
        trading_dates.append(pd.Timestamp(stamp, unit="s", tz="UTC").tz_convert(TIMEZONE).date())
        records.append(values)

    # Match engine.data._fetch_yfinance's index convention exactly: its naive
    # daily dates are localized to UTC, then converted to New York.
    frame = pd.DataFrame(records, index=pd.DatetimeIndex(trading_dates))
    return data_module._localize(frame).sort_index()


def merge_cache(symbol: str, fresh: pd.DataFrame, end_exclusive: date) -> int:
    path = data_module._cache_path(symbol, "1d")
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    merged = pd.concat([cached, fresh]).sort_index() if not cached.empty else fresh.sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    # Daily cache stamps are UTC-midnight dates converted to New York (the
    # preceding evening). Compare in UTC to recover the actual trading date.
    utc_dates = merged.index.tz_convert("UTC").date
    merged = merged[utc_dates < end_exclusive]
    data_module.DATA_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=data_module.DATA_DIR, prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        merged.to_parquet(tmp_path)
        data_module._replace_with_retry(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return len(merged)


def main() -> int:
    start = date.today() - timedelta(days=7 * 365)
    end = date.today()
    failures = []
    for symbol in RECOVERY_SYMBOLS:
        try:
            fresh = fetch_chart(symbol, start, end)
            critical, warnings, details = audit_frame(symbol, fresh, "1d")
            if critical:
                raise RuntimeError("; ".join(critical))
            rows = merge_cache(symbol, fresh, end)
            print(
                f"{symbol}: recovered {len(fresh)} rows; cache {rows} rows; "
                f"{details.get('firstTimestamp')} -> {details.get('lastTimestamp')}"
                + (f"; warnings: {'; '.join(warnings)}" if warnings else ""),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - report every ticker
            failures.append((symbol, str(exc)))
            print(f"{symbol}: FAILED {type(exc).__name__}: {exc}", flush=True)
    if failures:
        print("FAILURES=" + json.dumps(failures), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
