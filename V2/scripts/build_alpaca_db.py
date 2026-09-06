"""Download Alpaca 15-minute bars into V2/data/alpaca_intraday.db."""
from __future__ import annotations
import argparse, os, re, sqlite3, time
from datetime import datetime, timedelta, timezone, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "alpaca_intraday.db"
ET = ZoneInfo("America/New_York")
# The supplied list is intentionally kept as data, not inferred from the DB.
RAW_SYMBOLS = """LQDT DAKT LPX KMT KMX CHRW KO CI CIEN LOW CXW CXT KOP KOPN CINF CWT KR CWCO LOGI CVX CVSA CVS CL CVLT CLB CVCO CVBF CLF LNT LNN CLH CUZ LNG CLMT CULP KSS LMT CTSH CTS CTRN CLX CMC LLY CMCO CME CMG LKQ CMI CTBI CTAS CSX CMP CMPR L CSCO LHX CRVL CRUS CRL CROX CRM COO COP COST LDOS NUE HALO HAS GWW AAPL AMD AME ACN ADI ADM ADP ADBE AMAT AMGN AMZN AON APA APD APH APX AXP BA CAT GS HD HON IBM INTC JNJ JPM KO MCD MRK MSFT NKE PG TRV UNH V VZ WMT XOM NVDA""".split()

def _credentials():
    vals = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1); vals[k.strip()] = v.strip().strip('"').strip("'")
    key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_PAPER_API_KEY") or os.getenv("APCA_API_KEY_ID") or vals.get("ALPACA_API_KEY") or vals.get("ALPACA_PAPER_API_KEY") or vals.get("APCA_API_KEY_ID")
    secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_PAPER_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY") or vals.get("ALPACA_SECRET_KEY") or vals.get("ALPACA_PAPER_SECRET_KEY") or vals.get("APCA_API_SECRET_KEY")
    if not key or not secret: raise RuntimeError("Alpaca credentials were not found in environment or .env")
    return key, secret

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--refresh", action="store_true"); args = ap.parse_args()
    symbols = []
    for s in dict.fromkeys(RAW_SYMBOLS):
        if re.fullmatch(r"[A-Z]{1,5}", s): symbols.append(s)
        else: print(f"SKIPPED {s}: invalid ticker")
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.enums import DataFeed, Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    try:
        from tqdm import tqdm
    except ImportError:
        def tqdm(items, **kwargs):
            return items
    DB_PATH.parent.mkdir(parents=True, exist_ok=True); c = sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS bars_15m (ticker TEXT,timestamp TEXT,open REAL,high REAL,low REAL,close REAL,volume INTEGER,trade_count INTEGER,vwap REAL,downloaded_at TEXT,PRIMARY KEY(ticker,timestamp))")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bars_15m_ticker_timestamp ON bars_15m(ticker,timestamp)"); c.commit()
    client = StockHistoricalDataClient(*_credentials()); start = datetime.now(ET)-timedelta(days=365*5); end = datetime.now(ET); ok=fail=total=0
    for s in tqdm(symbols, desc="Downloading 15m bars", unit="symbol"):
        if not args.refresh and c.execute("SELECT 1 FROM bars_15m WHERE ticker=? LIMIT 1", (s,)).fetchone(): print(f"{s}: already present"); ok += 1; continue
        try:
            req = StockBarsRequest(symbol_or_symbols=s,timeframe=TimeFrame(15,TimeFrameUnit.Minute),start=start,end=end,feed=DataFeed.IEX,adjustment=Adjustment.RAW)
            bars = client.get_stock_bars(req).data.get(s, []); now=datetime.now(timezone.utc).isoformat()
            bars = [b for b in bars if clock_time(9, 30) <= b.timestamp.astimezone(ET).time() < clock_time(16, 0)]
            if args.refresh: c.execute("DELETE FROM bars_15m WHERE ticker=?", (s,))
            c.executemany("INSERT OR REPLACE INTO bars_15m VALUES (?,?,?,?,?,?,?,?,?,?)", [(s,b.timestamp.astimezone(ET).isoformat(),b.open,b.high,b.low,b.close,int(b.volume or 0),b.trade_count,b.vwap,now) for b in bars]); c.commit(); ok += 1; total += len(bars); print(f"{s}: {len(bars)} bars")
        except Exception as e: fail += 1; print(f"{s}: FAILED {type(e).__name__}: {e}")
        time.sleep(.25)
    n, lo, hi = c.execute("SELECT COUNT(*),MIN(timestamp),MAX(timestamp) FROM bars_15m").fetchone(); suspect=c.execute("SELECT ticker,COUNT(*) FROM bars_15m GROUP BY ticker HAVING COUNT(*)<100").fetchall(); c.close(); print(f"Symbols attempted / succeeded / failed: {len(symbols)} / {ok} / {fail}"); print(f"Total bars: {n}"); print(f"Date range: {lo} / {hi}"); print(f"Suspect (<100 bars): {suspect}")
if __name__ == "__main__": main()
