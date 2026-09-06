"""Build V2/data/sharadar.db: a single SQLite database holding full-history
Sharadar bundle data (tickers, stocks, actions, fundamentals, daily, sp500),
queryable from Jupyter notebooks.

Standalone by design -- does not import anything from V1. Reuses the
RATE-LIMITING AND RETRY PATTERNS validated extensively in V1's
engine/sharadar_client.py this session (5 req/sec here per this task's
spec, vs. V1's 4/sec default), and the CHUNK-BY-YEAR download strategy
V1 discovered was necessary: a single wide-date-range query against a
ticker-less (market-wide) Sharadar endpoint silently truncates below the
requested page size with no error (confirmed empirically in V1 this
session on the `actions` table: a 1998-2026 request for
`action=bankruptcyliquidation` returned only 1,359 rows, stopping dead at
2007-12-26, while 948 more real rows existed from 2008 onward). Every
market-wide, multi-year pull here chunks by calendar year to route around
that defect -- this script does not re-litigate it, just avoids it.

API key: SHARADAR_API_KEY from .env, loaded once, never printed, logged,
or included in any error message or cache path.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "sharadar.db"
ENV_PATH = ROOT / ".env"

BASE_URL = "https://api.sharadar.com/v1.0"
REQUESTS_PER_SECOND = 5.0
MAX_RETRIES = 5
TIMEOUT_SECONDS = 30
PAGE_SIZE = 10_000
HISTORY_START = "2000-01-01"


def _load_api_key() -> str:
    """Read SHARADAR_API_KEY from .env without ever printing it."""
    key = os.environ.get("SHARADAR_API_KEY")
    if key:
        return key.strip()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("SHARADAR_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("SHARADAR_API_KEY not found in environment or .env -- set it before running this script.")


class _RateLimiter:
    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


class SharadarError(RuntimeError):
    pass


class MinimalSharadarClient:
    """Standalone client: rate-limited to 5 req/sec, retries 429/5xx with
    exponential backoff. The API key is held only in memory, never logged."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._limiter = _RateLimiter(REQUESTS_PER_SECOND)

    def query_page(self, table_name: str, **params: Any) -> dict[str, Any]:
        query_params = {k: v for k, v in params.items() if v is not None}
        query_params["format"] = "json"
        query_params["api_key"] = self._api_key
        query = urllib.parse.urlencode(query_params, safe=",")
        url = f"{BASE_URL}/data/{table_name}?{query}"
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "sharadar-db-builder/1.0"}
        )
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._limiter.wait()
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    import json
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise SharadarError(f"{table_name}: unexpected JSON shape")
                return payload
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in (429,) and not (500 <= exc.code < 600):
                    # Never include the URL (it contains the API key) in a raised message.
                    raise SharadarError(f"{table_name}: HTTP {exc.code} (non-retryable)") from exc
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.0 + attempt * 2
                time.sleep(delay + random.uniform(0, 0.3))
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1) + random.uniform(0, 0.3))
        raise SharadarError(f"{table_name}: failed after {MAX_RETRIES} attempts ({type(last_error).__name__})") from last_error

    def query_all_paginated(self, table_name: str, **params: Any) -> Iterator[list[dict[str, Any]]]:
        """Yields one page (list of rows) at a time -- callers write
        incrementally rather than holding the whole table in memory."""
        skip = 0
        while True:
            payload = self.query_page(table_name, limit=PAGE_SIZE, skip=skip, **params)
            rows = payload["data"]
            yield rows
            if len(rows) < PAGE_SIZE:
                return
            skip += PAGE_SIZE


def _year_chunks(start: str, end: str) -> list[tuple[str, str]]:
    start_year = date.fromisoformat(start).year
    end_year = date.fromisoformat(end).year
    chunks = []
    for year in range(start_year, end_year + 1):
        chunk_start = f"{year}-01-01" if year > start_year else start
        chunk_end = f"{year}-12-31" if year < end_year else end
        chunks.append((chunk_start, chunk_end))
    return chunks


def _sqlite_columns_for(sample_rows: list[dict[str, Any]]) -> list[str]:
    """Union of keys across a sample of rows, order-preserving from first
    sight -- 'keep all vendor columns exactly as returned' means the
    schema is derived from the API's own response, never hand-typed."""
    seen: dict[str, None] = {}
    for row in sample_rows:
        for key in row.keys():
            seen[key] = None
    return list(seen.keys())


def _ensure_table(
    conn: sqlite3.Connection, table: str, columns: list[str], primary_key: list[str], has_date: bool
) -> None:
    extra = ["downloaded_at", "sharadar_table"]
    all_columns = list(columns) + [c for c in extra if c not in columns]
    col_defs = ", ".join(f'"{c}"' for c in all_columns)
    pk_defs = ", ".join(f'"{c}"' for c in primary_key)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs}, PRIMARY KEY ({pk_defs}))')
    if has_date and "ticker" in all_columns and "date" in all_columns:
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_ticker_date" ON "{table}"("ticker", "date")')
    conn.commit()


_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1


def _sqlite_safe(value: Any) -> Any:
    """SQLite's INTEGER storage class is a native 64-bit signed int; Python's
    int is arbitrary-precision. A vendor field occasionally exceeds that
    range (observed in fundamentals this session). Preserve the exact value
    as text rather than crash or silently truncate -- 'keep all vendor
    columns exactly as returned' includes columns with an unusually large
    value, not just the common case."""
    if isinstance(value, int) and not (_SQLITE_INT_MIN <= value <= _SQLITE_INT_MAX):
        return str(value)
    return value


def _insert_rows(conn: sqlite3.Connection, table: str, columns: list[str], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    all_columns = list(columns) + ["downloaded_at", "sharadar_table"]
    placeholders = ", ".join("?" for _ in all_columns)
    col_names = ", ".join(f'"{c}"' for c in all_columns)
    values = [
        tuple(_sqlite_safe(row.get(c)) for c in columns) + (now, table)
        for row in rows
    ]
    conn.executemany(f'INSERT OR REPLACE INTO "{table}" ({col_names}) VALUES ({placeholders})', values)
    conn.commit()
    return len(rows)


def _table_row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _download_range_recursive(
    conn: sqlite3.Connection,
    client: MinimalSharadarClient,
    table: str,
    primary_key: list[str],
    extra_params: dict[str, Any] | None,
    start: str,
    end: str,
    columns_holder: dict[str, list[str] | None],
) -> tuple[int, list[str]]:
    """Download [start, end] for a date-keyed table, bisecting on the API's
    hard skip=500000 pagination ceiling (confirmed empirically: skip=500000
    succeeds, skip=500001 returns HTTP 400) instead of silently truncating.
    `stocks` alone exceeds 510,000 rows in every single calendar year
    (2000-2026), so year-chunking was not fine enough on its own -- every
    year was getting cut to just its first ~510,000 rows in date order.
    On hitting the cap mid-range, split the date range in half and recurse;
    INSERT OR REPLACE makes any re-fetched overlap harmless."""
    total_rows = 0
    try:
        for page in client.query_all_paginated(
            table, **{"from": start, "to": end}, **(extra_params or {}), sort="date.asc"
        ):
            if not page:
                continue
            if columns_holder["cols"] is None:
                columns_holder["cols"] = _sqlite_columns_for(page)
                _ensure_table(conn, table, columns_holder["cols"], primary_key, has_date=True)
            total_rows += _insert_rows(conn, table, columns_holder["cols"], page)
        return total_rows, []
    except Exception as exc:  # noqa: BLE001 -- one bad sub-range must not abort the table or the run
        hit_skip_limit = isinstance(exc, SharadarError) and "HTTP 400" in str(exc)
        if hit_skip_limit and start != end:
            start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
            mid = (start_d + (end_d - start_d) // 2).isoformat()
            rows1, err1 = _download_range_recursive(
                conn, client, table, primary_key, extra_params, start, mid, columns_holder
            )
            rows2, err2 = _download_range_recursive(
                conn, client, table, primary_key, extra_params, mid, end, columns_holder
            )
            return total_rows + rows1 + rows2, err1 + err2
        return total_rows, [f"{start}..{end}: {type(exc).__name__}: {exc}"]


def _download_chunked(
    conn: sqlite3.Connection,
    client: MinimalSharadarClient,
    table: str,
    primary_key: list[str],
    *,
    start: str = HISTORY_START,
    end: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    end = end or date.today().isoformat()
    chunks = _year_chunks(start, end)
    total_rows = 0
    columns_holder: dict[str, list[str] | None] = {"cols": None}
    errors: list[str] = []
    for chunk_start, chunk_end in chunks:
        rows, chunk_errors = _download_range_recursive(
            conn, client, table, primary_key, extra_params, chunk_start, chunk_end, columns_holder
        )
        total_rows += rows
        errors.extend(chunk_errors)
        print(f"  [{table}] {chunk_start}..{chunk_end}: {total_rows} rows so far", flush=True)
    return {"table": table, "rowsInserted": total_rows, "errors": errors}


def download_tickers(conn: sqlite3.Connection, client: MinimalSharadarClient) -> dict[str, Any]:
    total_rows = 0
    columns: list[str] | None = None
    errors: list[str] = []
    try:
        for page in client.query_all_paginated("tickers", table="stocks"):
            if not page:
                continue
            if columns is None:
                columns = _sqlite_columns_for(page)
                _ensure_table(conn, "tickers", columns, primary_key=["ticker"], has_date=False)
            total_rows += _insert_rows(conn, "tickers", columns, page)
            print(f"  [tickers] {total_rows} rows so far", flush=True)
    except Exception as exc:  # noqa: BLE001 -- a failed table must not abort the others
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"table": "tickers", "rowsInserted": total_rows, "errors": errors}


def download_sp500(conn: sqlite3.Connection, client: MinimalSharadarClient) -> dict[str, Any]:
    return _download_chunked(conn, client, "sp500", primary_key=["ticker", "date"])


def download_actions(conn: sqlite3.Connection, client: MinimalSharadarClient) -> dict[str, Any]:
    return _download_chunked(conn, client, "actions", primary_key=["ticker", "date", "action"])


def download_stocks(conn: sqlite3.Connection, client: MinimalSharadarClient) -> dict[str, Any]:
    return _download_chunked(conn, client, "stocks", primary_key=["ticker", "date"])


def download_daily(conn: sqlite3.Connection, client: MinimalSharadarClient) -> dict[str, Any]:
    return _download_chunked(conn, client, "daily", primary_key=["ticker", "date"])


def download_fundamentals(conn: sqlite3.Connection, client: MinimalSharadarClient) -> dict[str, Any]:
    # Task spec asked for a primary key of (ticker, dimension, calendardate,
    # datekey) -- verified against the live API before writing this: no
    # `datekey` field exists in the real fundamentals/SF1 response. The
    # actual filing/publication-date field is called `date`. Using the
    # real column name here rather than inventing one that doesn't exist,
    # per this project's "keep all vendor columns exactly as returned" rule.
    return _download_chunked(
        conn, client, "fundamentals", primary_key=["ticker", "dimension", "calendardate", "date"]
    )


TABLES: dict[str, Any] = {
    "tickers": download_tickers,
    "stocks": download_stocks,
    "actions": download_actions,
    "fundamentals": download_fundamentals,
    "daily": download_daily,
    "sp500": download_sp500,
}


def build(*, refresh: bool = False, only: list[str] | None = None) -> list[dict[str, Any]]:
    api_key = _load_api_key()
    client = MinimalSharadarClient(api_key)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    results = []
    tables_to_run = only or list(TABLES.keys())
    for table in tables_to_run:
        downloader = TABLES[table]
        existing = _table_row_count(conn, table)
        if existing > 0 and not refresh:
            print(f"[{table}] already has {existing} rows -- skipping (use --refresh to redo)", flush=True)
            results.append({"table": table, "rowsInserted": 0, "skipped": True, "existingRows": existing})
            continue
        if refresh and existing > 0:
            conn.execute(f'DELETE FROM "{table}"')
            conn.commit()
        print(f"[{table}] downloading full history from {HISTORY_START}...", flush=True)
        try:
            result = downloader(conn, client)
        except Exception as exc:  # noqa: BLE001 -- one table crashing must not abort the others
            print(f"[{table}] FAILED: {type(exc).__name__}: {exc}", flush=True)
            results.append({"table": table, "rowsInserted": _table_row_count(conn, table), "errors": [str(exc)]})
            continue
        print(f"[{table}] DONE: {result['rowsInserted']} rows, {len(result['errors'])} chunk errors", flush=True)
        results.append(result)
    conn.close()
    return results


def validate() -> dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    summary = {}
    flags = []
    for table in TABLES:
        try:
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.OperationalError:
            row_count = 0
        entry: dict[str, Any] = {"rows": row_count}
        if row_count == 0:
            flags.append(f"{table}: ZERO ROWS")
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        if "date" in cols:
            date_range = conn.execute(f'SELECT MIN(date), MAX(date) FROM "{table}"').fetchone()
            entry["dateRange"] = date_range
            if row_count > 0 and date_range[0] and date_range[0] > "2000-06-01":
                flags.append(f"{table}: date range does not reach 2000 (starts {date_range[0]})")
        if "ticker" in cols:
            ticker_count = conn.execute(f'SELECT COUNT(DISTINCT ticker) FROM "{table}"').fetchone()[0]
            entry["tickers"] = ticker_count
            if table == "stocks" and ticker_count < 5000:
                flags.append(f"stocks: only {ticker_count} distinct tickers (< 5000)")
        summary[table] = entry
    conn.close()
    return {"summary": summary, "flags": flags}


def print_validation_report(report: dict[str, Any]) -> None:
    print()
    print(f"{'Table':<14} {'Rows':>10}  {'Date range':<25} {'Tickers':>10}")
    for table, entry in report["summary"].items():
        rows = entry["rows"]
        date_range = entry.get("dateRange")
        date_str = f"{date_range[0]} to {date_range[1]}" if date_range and date_range[0] else "-"
        tickers = entry.get("tickers", "-")
        print(f"{table:<14} {rows:>10}  {date_str:<25} {tickers!s:>10}")
    print()
    if report["flags"]:
        print("FLAGGED:")
        for flag in report["flags"]:
            print(f"  - {flag}")
    else:
        print("No flags -- all tables populated with expected coverage.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Redownload all tables even if already populated")
    parser.add_argument("--only", nargs="*", choices=list(TABLES.keys()), help="Only (re)download these tables")
    args = parser.parse_args()
    build(refresh=args.refresh, only=args.only)
    report = validate()
    print_validation_report(report)


if __name__ == "__main__":
    main()
