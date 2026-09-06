"""Fetch daily OHLCV for every sp500_pit_free historical ticker (commit
sp500_price_ingestion_v1).

Reads ``data/sp500_pit_free/metadata/symbol_map.csv`` (one row per ticker
tenure), computes each ticker's REQUIRED window (union of its tenures, plus
warmup), and ensures ``data/sp500_pit_free/prices/<TICKER>.parquet`` covers
it -- reusing this project's existing flat ``data/*.parquet`` cache where it
already covers the window (avoiding a redundant network fetch for names
other backtests have already pulled), and falling back to a fresh yfinance
fetch otherwise. A symbol that cannot be fetched at all is NEVER dropped
from the dataset: it is recorded with ``status: MISSING_DELISTED_PRICE_HISTORY``
in ``data/sp500_pit_free/audits/price_coverage.json`` (and the flat
``price_coverage.csv``), so downstream coverage-by-date accounting (the next
build step) can see exactly what's missing instead of silently shrinking
the universe -- the amateur survivorship-bias failure mode CLAUDE.md warns
about for this whole project.

``missing_sessions`` is estimated against ``pd.bdate_range`` (calendar
business days), the same dependency-free approximation
``engine/sanity.py:_reindex_business_days`` already uses elsewhere in this
project -- it overcounts expected sessions by roughly the ~9-10 US market
holidays per year (no real NYSE calendar dependency is added), so it is a
conservative (slightly pessimistic) coverage estimate, not an exact one.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd
import yfinance as yf

from engine import data as data_module


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "sp500_pit_free"
SYMBOL_MAP_PATH = DATA_DIR / "metadata" / "symbol_map.csv"
PRICES_DIR = DATA_DIR / "prices"
AUDIT_PATH = DATA_DIR / "audits" / "price_coverage.json"
COVERAGE_CSV_PATH = DATA_DIR / "audits" / "price_coverage.csv"

WARMUP_CALENDAR_DAYS = 300
EDGE_TOLERANCE_DAYS = 7
OHLCV = ["Open", "High", "Low", "Close", "Volume"]

# pd.bdate_range counts every weekday as an expected session, including the
# ~9-10 US market holidays per year it has no calendar knowledge of. Verified
# empirically against AAPL/MSFT/JNJ's actual (complete, uncontroversial)
# 1996-2025 cached history: real gap is ~3.5% (271/7664 business days), not
# noise near zero. The OK threshold must sit safely above that baseline, or
# every genuinely complete series misreports as PARTIAL_COVERAGE.
OK_MISSING_FRACTION_THRESHOLD = 0.06


def ticker_windows(symbol_map_path: Path = SYMBOL_MAP_PATH) -> dict[str, tuple[date, date]]:
    """One (requested_start, requested_end) per ticker: the union of every
    tenure it appears in. An empty end_date (still a member as of the
    membership ledger's own coverage end) resolves to today."""
    spans: dict[str, list[tuple[date, date]]] = defaultdict(list)
    with symbol_map_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            start = date.fromisoformat(row["start_date"])
            end = date.fromisoformat(row["end_date"]) if row["end_date"] else date.today()
            spans[row["vendor_symbol"]].append((start, end))
    return {
        ticker: (min(s for s, _ in tenures), max(e for _, e in tenures))
        for ticker, tenures in spans.items()
    }


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=OHLCV)
    try:
        frame = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - a corrupt cache is reported as unavailable
        return pd.DataFrame(columns=OHLCV)
    if frame.empty:
        return frame
    return data_module._normalize_daily_session_index(frame)


def _covers(frame: pd.DataFrame, required_start: date, required_end: date) -> bool:
    if frame.empty:
        return False
    actual_start, actual_end = frame.index.min().date(), frame.index.max().date()
    warm_start = required_start - timedelta(days=WARMUP_CALENDAR_DAYS)
    return (
        actual_start <= warm_start + timedelta(days=EDGE_TOLERANCE_DAYS)
        and actual_end >= required_end - timedelta(days=EDGE_TOLERANCE_DAYS)
    )


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
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


def reuse_existing_cache(windows: dict[str, tuple[date, date]]) -> list[str]:
    """Copy from the project's shared flat cache where it already covers the
    window, instead of re-fetching a name other backtests already pulled."""
    reused = []
    for ticker, (start, end) in windows.items():
        dest = PRICES_DIR / f"{ticker}.parquet"
        if _covers(_read_parquet(dest), start, end):
            continue
        shared = _read_parquet(data_module.DATA_DIR / f"{ticker}_1d.parquet")
        if _covers(shared, start, end):
            _write_parquet(dest, shared)
            reused.append(ticker)
    return reused


def fetch_missing(windows: dict[str, tuple[date, date]], *, batch_size: int = 40) -> list[str]:
    pending = [
        ticker for ticker, (start, end) in sorted(windows.items())
        if not _covers(_read_parquet(PRICES_DIR / f"{ticker}.parquet"), start, end)
    ]
    if not pending:
        return []
    yf.set_tz_cache_location(str(DATA_DIR / ".yfinance_cache"))
    fetched = []
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        common_start = min(windows[t][0] for t in batch) - timedelta(days=WARMUP_CALENDAR_DAYS)
        common_end = max(windows[t][1] for t in batch)
        raw = yf.download(
            batch, start=common_start, end=common_end + timedelta(days=1),
            interval="1d", auto_adjust=True, progress=False,
            group_by="ticker", threads=True,
        )
        for ticker in batch:
            try:
                frame = raw[ticker] if len(batch) > 1 else raw
            except KeyError:
                continue
            if frame.empty or not set(OHLCV).issubset(frame.columns):
                continue
            frame = frame[OHLCV].dropna(how="all").copy()
            if frame.empty:
                continue
            frame = data_module._normalize_daily_session_index(frame)
            existing = _read_parquet(PRICES_DIR / f"{ticker}.parquet")
            if not existing.empty:
                frame = pd.concat([existing, frame]).sort_index()
                frame = frame[~frame.index.duplicated(keep="last")]
            _write_parquet(PRICES_DIR / f"{ticker}.parquet", frame)
            fetched.append(ticker)
        print(f"FETCH {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)
    return fetched


def _missing_sessions(frame: pd.DataFrame, start: date, end: date) -> int:
    if end < start:
        return 0
    expected = pd.bdate_range(start, end)
    if frame.empty:
        return len(expected)
    present = set(frame.index.normalize().date)
    return sum(1 for stamp in expected if stamp.date() not in present)


def assess(windows: dict[str, tuple[date, date]]) -> dict[str, dict[str, Any]]:
    report: dict[str, dict[str, Any]] = {}
    for ticker, (start, end) in sorted(windows.items()):
        frame = _read_parquet(PRICES_DIR / f"{ticker}.parquet")
        clipped = frame.loc[(frame.index.date >= start) & (frame.index.date <= end)] if not frame.empty else frame
        missing = _missing_sessions(clipped, start, end)
        expected = len(pd.bdate_range(start, end)) or 1
        missing_fraction = missing / expected
        if clipped.empty:
            status = "MISSING_DELISTED_PRICE_HISTORY"
        elif missing_fraction <= OK_MISSING_FRACTION_THRESHOLD:
            status = "OK"
        else:
            status = "PARTIAL_COVERAGE"
        report[ticker] = {
            "ticker": ticker,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "available_start": clipped.index.min().date().isoformat() if not clipped.empty else "",
            "available_end": clipped.index.max().date().isoformat() if not clipped.empty else "",
            "missing_sessions": missing,
            "expected_sessions": expected,
            "status": status,
        }
    return report


def build(
    *,
    symbol_map_path: Path = SYMBOL_MAP_PATH,
    audit_path: Path = AUDIT_PATH,
    coverage_csv_path: Path = COVERAGE_CSV_PATH,
    fetch: bool = True,
    batch_size: int = 40,
) -> dict:
    windows = ticker_windows(symbol_map_path)
    reused = reuse_existing_cache(windows)
    fetched = fetch_missing(windows, batch_size=batch_size) if fetch else []
    report = assess(windows)

    status_counts: dict[str, int] = defaultdict(int)
    for row in report.values():
        status_counts[row["status"]] += 1

    audit = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "buildStep": "sp500_price_ingestion_v1",
        "tickersTotal": len(windows),
        "reusedFromSharedCache": len(reused),
        "fetchedFromNetwork": len(fetched),
        "statusCounts": dict(status_counts),
        "warmupCalendarDaysRequired": WARMUP_CALENDAR_DAYS,
        "sessionCalendarMethod": "pandas business-day approximation (no NYSE holiday calendar dependency)",
        "symbols": report,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")

    coverage_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with coverage_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "ticker", "requested_start", "requested_end", "available_start",
            "available_end", "missing_sessions", "status",
        ])
        writer.writeheader()
        for ticker in sorted(report):
            row = report[ticker]
            writer.writerow({key: row[key] for key in writer.fieldnames})

    return {
        "generatedAt": audit["generatedAt"],
        "tickersTotal": audit["tickersTotal"],
        "reusedFromSharedCache": audit["reusedFromSharedCache"],
        "fetchedFromNetwork": audit["fetchedFromNetwork"],
        "statusCounts": audit["statusCounts"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol-map", type=Path, default=SYMBOL_MAP_PATH)
    parser.add_argument("--no-fetch", action="store_true", help="assess cache only, no network calls")
    parser.add_argument("--batch-size", type=int, default=40)
    args = parser.parse_args()
    result = build(symbol_map_path=args.symbol_map, fetch=not args.no_fetch, batch_size=args.batch_size)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
