"""Append only newly available Sharadar rows to the existing database."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from build_sharadar_db import MinimalSharadarClient, TABLES, _load_api_key, _download_chunked

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "sharadar.db"

def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    client = MinimalSharadarClient(_load_api_key())
    for table in ("stocks", "daily", "actions", "fundamentals", "sp500"):
        try:
            maximum = conn.execute(f'SELECT MAX(date) FROM "{table}"').fetchone()[0]
            start = (date.fromisoformat(maximum) + timedelta(days=1)).isoformat() if maximum else "2000-01-01"
            end = date.today().isoformat()
            if start > end:
                print(f"[{table}] up to date through {maximum}"); continue
            print(f"[{table}] appending {start} through {end}", flush=True)
            result = _download_chunked(conn, client, table, primary_key=(
                ["ticker", "date"] if table in ("stocks", "daily", "sp500") else
                ["ticker", "date", "action"] if table == "actions" else
                ["ticker", "dimension", "calendardate", "date"]
            ), start=start, end=end)
            print(f"[{table}] inserted {result['rowsInserted']} rows; errors={len(result['errors'])}")
        except Exception as exc:
            print(f"[{table}] FAILED: {type(exc).__name__}: {exc}")
    print("[tickers] skipped: no date column; existing identities were not repulled")
    conn.close()

if __name__ == "__main__": main()
