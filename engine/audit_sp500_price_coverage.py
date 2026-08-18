"""Fetchability and tenure-coverage audit for every S&P 500 historical ticker.

Membership truth is not price truth.  This script checks the complete set of
symbols present in the generated S&P 500 ledger, including deleted names, and
records whether the local adjusted-price series spans every date-effective
membership tenure.  Tickers with disjoint tenures are flagged as identity
ambiguous even when Yahoo returns data, because a reused symbol can represent
different legal issuers.

The script applies ``engine.sanity`` window floors to every stored frame.  It
does not run strategies or change any strategy configuration.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd
import yfinance as yf

from engine import data as data_module
from engine.sanity import check_window
from engine.universe_ledger import LEDGER_PATH, load_ledger


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "data" / "sources" / "sp500" / "fetchability.json"
UNIVERSE_KEY = "sp500"
WARMUP_CALENDAR_DAYS = 300
EDGE_TOLERANCE_DAYS = 7
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def membership_tenures(records: list[dict]) -> dict[str, list[tuple[date, date]]]:
    raw: dict[str, list[tuple[date, date]]] = {}
    for row in records:
        start = date.fromisoformat(row["effectiveStart"])
        end = date.fromisoformat(row["effectiveEnd"])
        for symbol in row.get("symbols", []):
            raw.setdefault(str(symbol), []).append((start, end))
    merged: dict[str, list[tuple[date, date]]] = {}
    for symbol, intervals in raw.items():
        out: list[tuple[date, date]] = []
        for start, end in sorted(intervals):
            if out and start <= out[-1][1] + timedelta(days=1):
                out[-1] = (out[-1][0], max(out[-1][1], end))
            else:
                out.append((start, end))
        merged[symbol] = out
    return merged


def _cached(symbol: str) -> pd.DataFrame:
    path = data_module.DATA_DIR / f"{symbol}_1d.parquet"
    if not path.exists():
        return pd.DataFrame(columns=OHLCV)
    try:
        frame = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - a corrupt cache is reported as unavailable
        return pd.DataFrame(columns=OHLCV)
    if frame.empty:
        return frame
    return data_module._normalize_daily_session_index(frame)


def _batch_frame(raw: pd.DataFrame, symbol: str, batch_size: int) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=OHLCV)
    try:
        frame = raw[symbol] if batch_size > 1 else raw
    except KeyError:
        return pd.DataFrame(columns=OHLCV)
    if frame.empty or not set(OHLCV).issubset(frame.columns):
        return pd.DataFrame(columns=OHLCV)
    frame = frame[OHLCV].dropna().copy()
    return data_module._normalize_daily_session_index(frame) if not frame.empty else frame


def _store(symbol: str, frame: pd.DataFrame, start: date, end: date) -> None:
    check_window(frame, start, end, label=f"S&P 500 fetchability {symbol}")
    previous = _cached(symbol)
    if not previous.empty:
        frame = pd.concat([previous, frame]).sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
    path = data_module.DATA_DIR / f"{symbol}_1d.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary)
        data_module._replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def download_missing(
    tenures: dict[str, list[tuple[date, date]]], *, batch_size: int = 40,
) -> None:
    # A non-empty cache is not enough: current-roster downloads commonly start
    # in 2019 even when the security was an index member years earlier.
    pending = [
        symbol for symbol, intervals in sorted(tenures.items())
        if not assess_symbol(symbol, intervals)["coversEveryTenure"]
    ]
    if not pending:
        return
    yf.set_tz_cache_location(str(data_module.DATA_DIR / ".yfinance_cache"))
    common_start = min(start for intervals in tenures.values() for start, _ in intervals) - timedelta(days=WARMUP_CALENDAR_DAYS)
    common_end = max(end for intervals in tenures.values() for _, end in intervals)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        raw = yf.download(
            batch,
            start=common_start,
            end=common_end + timedelta(days=1),
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        for symbol in batch:
            frame = _batch_frame(raw, symbol, len(batch))
            if not frame.empty:
                _store(symbol, frame, common_start, common_end)
        print(f"FETCH {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)


def assess_symbol(symbol: str, tenures: list[tuple[date, date]]) -> dict[str, Any]:
    frame = _cached(symbol)
    if frame.empty:
        return {
            "fetchable": False,
            "coversEveryTenure": False,
            "identityAmbiguous": len(tenures) > 1,
            "rows": 0,
            "coverageStart": None,
            "coverageEnd": None,
            "tenures": [[start.isoformat(), end.isoformat()] for start, end in tenures],
            "uncoveredTenures": [[start.isoformat(), end.isoformat()] for start, end in tenures],
        }
    actual_start = frame.index.min().date()
    actual_end = frame.index.max().date()
    uncovered = []
    for tenure_start, tenure_end in tenures:
        required_start = tenure_start - timedelta(days=WARMUP_CALENDAR_DAYS)
        start_ok = actual_start <= required_start + timedelta(days=EDGE_TOLERANCE_DAYS)
        end_ok = actual_end >= tenure_end - timedelta(days=EDGE_TOLERANCE_DAYS)
        if not (start_ok and end_ok):
            uncovered.append([tenure_start.isoformat(), tenure_end.isoformat()])
    return {
        "fetchable": True,
        "coversEveryTenure": not uncovered,
        "identityAmbiguous": len(tenures) > 1,
        "rows": int(len(frame)),
        "coverageStart": actual_start.isoformat(),
        "coverageEnd": actual_end.isoformat(),
        "tenures": [[start.isoformat(), end.isoformat()] for start, end in tenures],
        "uncoveredTenures": uncovered,
    }


def build_report(*, ledger_path: Path = LEDGER_PATH) -> dict[str, Any]:
    ledger = load_ledger(ledger_path)
    records = (ledger.get("universes") or {}).get(UNIVERSE_KEY, [])
    if not records:
        raise ValueError("S&P 500 ledger has not been built")
    tenures = membership_tenures(records)
    symbols = {symbol: assess_symbol(symbol, intervals) for symbol, intervals in sorted(tenures.items())}
    unfetchable = [symbol for symbol, item in symbols.items() if not item["fetchable"]]
    incomplete = [symbol for symbol, item in symbols.items() if not item["coversEveryTenure"]]
    ambiguous = [symbol for symbol, item in symbols.items() if item["identityAmbiguous"]]
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universeKey": UNIVERSE_KEY,
        "distinctHistoricalTickers": len(symbols),
        "fetchableTickers": len(symbols) - len(unfetchable),
        "unfetchableTickers": unfetchable,
        "incompleteTenureCoverageTickers": incomplete,
        "identityAmbiguousReusedTickers": ambiguous,
        "priceCoverageComplete": not incomplete and not ambiguous,
        "warmupCalendarDaysRequired": WARMUP_CALENDAR_DAYS,
        "symbols": symbols,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--batch-size", type=int, default=40)
    args = parser.parse_args()
    records = (load_ledger(args.ledger).get("universes") or {}).get(UNIVERSE_KEY, [])
    tenures = membership_tenures(records)
    if args.download_missing:
        download_missing(tenures, batch_size=args.batch_size)
    report = build_report(ledger_path=args.ledger)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {
        "generatedAt": report["generatedAt"],
        "universeKey": report["universeKey"],
        "distinctHistoricalTickers": report["distinctHistoricalTickers"],
        "fetchableTickers": report["fetchableTickers"],
        "unfetchableTickerCount": len(report["unfetchableTickers"]),
        "incompleteTenureCoverageTickerCount": len(report["incompleteTenureCoverageTickers"]),
        "identityAmbiguousReusedTickerCount": len(report["identityAmbiguousReusedTickers"]),
        "priceCoverageComplete": report["priceCoverageComplete"],
        "reportPath": str(args.report),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["priceCoverageComplete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
