"""Append only new 15-minute Alpaca bars to the existing SQLite database."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo
from build_alpaca_db import RAW_SYMBOLS, _credentials, _init, _download

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "alpaca_intraday.db"
ET = ZoneInfo("America/New_York")

def main() -> None:
    from alpaca.data.historical import StockHistoricalDataClient
    conn = sqlite3.connect(DB_PATH); _init(conn); client = StockHistoricalDataClient(*_credentials())
    symbols = sorted(set(RAW_SYMBOLS)); attempted = skipped = total = 0
    for symbol in symbols:
        last = conn.execute("SELECT MAX(timestamp) FROM bars_15m WHERE ticker=?", (symbol,)).fetchone()[0]
        if not last:
            print(f"{symbol}: no existing bars; run build_alpaca_db.py first"); skipped += 1; continue
        start = datetime.fromisoformat(last) + timedelta(minutes=15); end = datetime.now(ET); attempted += 1
        if start >= end: print(f"{symbol}: up to date through {last}"); continue
        try:
            bars = _download(client, symbol, start, end); now = datetime.utcnow().isoformat() + "Z"
            bars = [b for b in bars if clock_time(9,30) <= b.timestamp.astimezone(ET).time() < clock_time(16,0)]
            rows = [(symbol,b.timestamp.astimezone(ET).isoformat(),b.open,b.high,b.low,b.close,int(b.volume or 0),b.trade_count,b.vwap,now) for b in bars]
            conn.executemany("INSERT OR IGNORE INTO bars_15m VALUES (?,?,?,?,?,?,?,?,?,?)", rows); conn.commit(); total += len(rows); print(f"{symbol}: appended {len(rows)} bars")
        except Exception as exc: print(f"{symbol}: FAILED {type(exc).__name__}: {exc}")
    print(f"Symbols checked: {attempted}; skipped without baseline: {skipped}; bars appended: {total}"); conn.close()

if __name__ == "__main__": main()
