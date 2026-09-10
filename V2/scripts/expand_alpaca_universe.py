"""Expand V2/data/alpaca_intraday.db to the PIT S&P 500 universe (2020-01-27 onward).

For tickers already in the DB, only backfills the missing earlier window
(2020-01-27 -> existing min timestamp) and tops up to now. For new tickers,
downloads the full 2020-01-27 -> now range. Existing rows are never deleted.
"""
from __future__ import annotations
import re, sqlite3, sys, time
from datetime import datetime, timedelta, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_alpaca_db import _credentials

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "alpaca_intraday.db"
ET = ZoneInfo("America/New_York")
# Alpaca's IEX feed has no 15-minute historical bars before 2020-07-27 (verified
# empirically: AAPL/MSFT/JPM/WMT all return their first bar at 2020-07-27 09:30 ET).
# The task's requested 2020-01-27 start is unavailable from this feed.
TARGET_START = datetime(2020, 7, 27, tzinfo=ET)

UNIVERSE_PATH = Path(__file__).resolve().parent / "pit_sp500_universe.txt"


def _load_universe() -> list[str]:
    symbols = []
    for line in UNIVERSE_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"[A-Z]{1,5}", s):
            symbols.append(s)
        else:
            print(f"SKIPPED {s}: invalid ticker format")
    return sorted(dict.fromkeys(symbols))


def _rth_bars(client, symbol, start, end):
    from alpaca.data.enums import DataFeed, Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    req = StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame(15, TimeFrameUnit.Minute),
        start=start, end=end, feed=DataFeed.IEX, adjustment=Adjustment.RAW,
    )
    bars = client.get_stock_bars(req).data.get(symbol, [])
    return [b for b in bars if clock_time(9, 30) <= b.timestamp.astimezone(ET).time() < clock_time(16, 0)]


def main():
    from alpaca.data.historical import StockHistoricalDataClient
    symbols = _load_universe()
    print(f"Universe: {len(symbols)} tickers")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS bars_15m (ticker TEXT,timestamp TEXT,open REAL,high REAL,low REAL,close REAL,volume INTEGER,trade_count INTEGER,vwap REAL,downloaded_at TEXT,PRIMARY KEY(ticker,timestamp))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bars_15m_ticker_timestamp ON bars_15m(ticker,timestamp)")
    conn.commit()

    client = StockHistoricalDataClient(*_credentials())
    now = datetime.now(ET)

    n_new, n_backfilled, n_topped_up, n_uptodate, n_failed = 0, 0, 0, 0, 0

    for i, sym in enumerate(symbols, 1):
        row = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM bars_15m WHERE ticker=?", (sym,)).fetchone()
        existing_min, existing_max = row if row else (None, None)
        windows = []
        if existing_min is None:
            windows.append(("full", TARGET_START, now))
        else:
            emin = datetime.fromisoformat(existing_min)
            emax = datetime.fromisoformat(existing_max)
            if emin > TARGET_START + timedelta(days=1):
                windows.append(("backfill", TARGET_START, emin - timedelta(minutes=15)))
            if emax < now - timedelta(days=1):
                windows.append(("topup", emax + timedelta(minutes=15), now))

        if not windows:
            print(f"[{i}/{len(symbols)}] {sym}: up to date")
            n_uptodate += 1
            continue

        try:
            total_inserted = 0
            for kind, wstart, wend in windows:
                if wstart >= wend:
                    continue
                bars = _rth_bars(client, sym, wstart, wend)
                nowiso = datetime.now(ET).isoformat()
                rows = [(sym, b.timestamp.astimezone(ET).isoformat(), b.open, b.high, b.low, b.close,
                         int(b.volume or 0), b.trade_count, b.vwap, nowiso) for b in bars]
                conn.executemany("INSERT OR IGNORE INTO bars_15m VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
                conn.commit()
                total_inserted += len(rows)
                if kind == "full":
                    n_new += 1
                elif kind == "backfill":
                    n_backfilled += 1
                elif kind == "topup":
                    n_topped_up += 1
            print(f"[{i}/{len(symbols)}] {sym}: +{total_inserted} bars ({', '.join(k for k, *_ in windows)})")
        except Exception as e:
            n_failed += 1
            print(f"[{i}/{len(symbols)}] {sym}: FAILED {type(e).__name__}: {e}")
        time.sleep(0.25)

    n, lo, hi, ntick = conn.execute(
        "SELECT COUNT(*), MIN(timestamp), MAX(timestamp), COUNT(DISTINCT ticker) FROM bars_15m"
    ).fetchone()
    conn.close()
    print("\n=== SUMMARY ===")
    print(f"Symbols processed: {len(symbols)}  new={n_new} backfilled={n_backfilled} topped_up={n_topped_up} up_to_date={n_uptodate} failed={n_failed}")
    print(f"DB now: {ntick} tickers, {n} total bars, {lo} -> {hi}")


if __name__ == "__main__":
    main()
