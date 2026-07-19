"""Historical OHLCV fetch + local cache.

No network calls happen inside the backtest engine itself (see CLAUDE.md) --
this module is the only place that talks to yfinance. Backtests read from
the local parquet cache this module maintains, so results are reproducible.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from engine.alpaca_client import market_data_client
from engine.universe import TIMEZONE

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# Intraday intervals we can source from Alpaca (amount, unit). Anything not
# here (i.e. "1d") stays on yfinance, whose daily bars carry full consolidated
# volume -- better than Alpaca's IEX-only volume for the swing book.
_ALPACA_TIMEFRAMES: dict[str, tuple[int, str]] = {
    "1m": (1, "Minute"),
    "5m": (5, "Minute"),
    "15m": (15, "Minute"),
    "30m": (30, "Minute"),
    "60m": (1, "Hour"),
    "1h": (1, "Hour"),
}

# yfinance caps intraday history at ~60 days. Only relevant as a fallback when
# Alpaca isn't configured; Alpaca itself goes back years (see universe.py).
_YF_INTRADAY_CAP_DAYS = 58

# Free-tier IEX data can't serve the most recent ~15 min; keep a margin.
_ALPACA_RECENT_CUTOFF = timedelta(minutes=16)

# Regular trading hours in America/New_York. yfinance intraday is RTH-only by
# default, and the strategies (ORB especially) assume a 9:30 session open, so
# we filter Alpaca bars to match rather than let pre/post-market bars in.
_RTH_START = "09:30"
_RTH_END = "16:00"


def _cache_path(symbol: str, interval: str) -> Path:
    return DATA_DIR / f"{symbol}_{interval}.parquet"


def _localize(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(TIMEZONE)
    else:
        df.index = df.index.tz_convert(TIMEZONE)
    return df


def _fetch_yfinance(symbol: str, interval: str, start: date, end: date) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=True,
        progress=False,
        multi_level_index=False,
    )
    if raw.empty:
        return raw
    raw = raw[_OHLCV_COLUMNS].dropna()
    return _localize(raw)


def _fetch_alpaca_intraday(
    client, symbol: str, interval: str, start: date, end: date
) -> pd.DataFrame:
    """Intraday OHLCV from Alpaca's IEX feed, split/dividend-adjusted to match
    yfinance's auto_adjust, filtered to regular trading hours. Volume is IEX
    only (a partial sample) -- prices for liquid names are representative.
    Falls back to yfinance on any error so a bad response never kills a run."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    amount, unit = _ALPACA_TIMEFRAMES[interval]
    timeframe = TimeFrame(amount, getattr(TimeFrameUnit, unit))
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    end_dt = min(end_dt, datetime.now(timezone.utc) - _ALPACA_RECENT_CUTOFF)
    if end_dt <= start_dt:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)

    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start_dt,
            end=end_dt,
            feed=DataFeed.IEX,
            adjustment=Adjustment.ALL,
        )
        df = client.get_stock_bars(req).df
    except Exception:  # noqa: BLE001 -- degrade to yfinance rather than fail the run
        return _fetch_yfinance(symbol, interval, start, end)

    if df is None or df.empty:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)

    # .df is a (symbol, timestamp) MultiIndex with lowercase columns.
    df = df.reset_index(level="symbol", drop=True)
    df = df.rename(columns=str.capitalize)[_OHLCV_COLUMNS].dropna()
    df = _localize(df)
    return df.between_time(_RTH_START, _RTH_END, inclusive="left")


def _fetch(symbol: str, interval: str, start: date, end: date) -> pd.DataFrame:
    """Dispatch a bar fetch to the right source: Alpaca for intraday intervals
    when it's configured, yfinance otherwise (and for all daily bars)."""
    if interval in _ALPACA_TIMEFRAMES:
        client, _ = market_data_client()
        if client is not None:
            return _fetch_alpaca_intraday(client, symbol, interval, start, end)
        # No Alpaca: yfinance can't serve intraday older than ~60 days, so clamp.
        start = max(start, date.today() - timedelta(days=_YF_INTRADAY_CAP_DAYS))
    return _fetch_yfinance(symbol, interval, start, end)


def get_bars(
    symbol: str,
    interval: str,
    start: date,
    end: date,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Return cached OHLCV bars for `symbol`, fetching from yfinance if the
    cache is missing or doesn't cover the requested range."""
    path = _cache_path(symbol, interval)
    cached = None
    if path.exists() and not force_refresh:
        cached = pd.read_parquet(path)
        covers_start = not cached.empty and cached.index.min().date() <= start
        covers_end = not cached.empty and cached.index.max().date() >= end
        if covers_start and covers_end:
            return cached.loc[str(start):str(end)]

    fresh = _fetch(symbol, interval, start, end)
    if fresh.empty and cached is not None:
        return cached.loc[str(start):str(end)]

    DATA_DIR.mkdir(exist_ok=True)
    fresh.to_parquet(path)
    return fresh.loc[str(start):str(end)]


RISK_FREE_PROXY = "^IRX"  # CBOE 13-week T-bill rate, quoted in percent (5.25 = 5.25%)


def risk_free_rate(start: date, end: date) -> float:
    """Mean annualized risk-free rate over [start, end], as a decimal
    (0.035 = 3.5%), sourced from the 13-week T-bill via the same cached
    pipeline as price data. Falls back to 0.0 if the series is unavailable
    -- matching backtesting.py's own default -- rather than guessing."""
    bars = get_bars(RISK_FREE_PROXY, "1d", start, end)
    if bars.empty:
        return 0.0
    return float(bars["Close"].mean()) / 100


# Liquidity-tiered spread estimate, in decimal (0.0002 = 2bps). Deliberately
# NOT sourced from yfinance's free-tier `Ticker.info` bid/ask -- those are
# noisy indicative quotes, not real NBBO (observed 45bps on AAPL and 106bps
# on MMM in a spot check, implausible for blue-chip liquidity, and trusting
# them would make the cost model worse than the flat 10bps it replaces).
# Average historical dollar volume is reliable data we already have cached,
# and is a standard, defensible proxy for typical spread: the busier a name
# trades, the tighter its real-world spread tends to be. These tier
# boundaries are a documented heuristic, not a fitted market-microstructure
# model -- see LESSONS.md.
_SPREAD_TIERS: list[tuple[float, float]] = [
    (5_000_000_000, 0.0001),  # >= $5B/day  -> 1 bp   (mega-cap: AAPL, MSFT, SPY-tier)
    (1_000_000_000, 0.0002),  # >= $1B/day  -> 2 bps  (large-cap, high liquidity)
    (300_000_000, 0.0003),    # >= $300M/day -> 3 bps
]
_SPREAD_FALLBACK = 0.0005  # < $300M/day, or no volume data at all -> 5 bps


def estimate_spread(symbol: str, start: date, end: date) -> float:
    """Estimate this symbol's typical round-trip-relevant spread from its
    own historical average dollar volume over [start, end]."""
    bars = get_bars(symbol, "1d", start, end)
    if bars.empty:
        return _SPREAD_FALLBACK
    avg_dollar_volume = float((bars["Close"] * bars["Volume"]).mean())
    for threshold, spread in _SPREAD_TIERS:
        if avg_dollar_volume >= threshold:
            return spread
    return _SPREAD_FALLBACK


def earnings_dates(symbol: str, force_refresh: bool = False) -> pd.DataFrame:
    """Historical earnings dates for `symbol` (EPS Estimate, Reported EPS,
    Surprise(%)), cached locally. yfinance sources this from the analyst
    calendar and gives ~50 quarters back to ~2014 for large caps. Returns an
    empty frame if unavailable -- callers must degrade, not assume coverage.

    This is the one earnings feed in the project; PEAD uses it to enter on
    real post-earnings sessions rather than a price/volume proxy."""
    path = DATA_DIR / f"{symbol}_earnings.parquet"
    if path.exists() and not force_refresh:
        return pd.read_parquet(path)
    try:
        raw = yf.Ticker(symbol).get_earnings_dates(limit=60)
    except Exception:  # noqa: BLE001 -- network/parse failure -> no earnings data
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    raw = raw.copy()
    raw.index = raw.index.tz_convert(TIMEZONE)
    raw = raw.sort_index()
    DATA_DIR.mkdir(exist_ok=True)
    raw.to_parquet(path)
    return raw


def positive_earnings_dates(symbol: str) -> list[date]:
    """Dates of positive earnings surprises (Reported EPS > EPS Estimate) --
    the events PEAD tries to ride the drift from. Falls back to a positive
    Surprise(%) when reported/estimate aren't both present."""
    df = earnings_dates(symbol)
    if df.empty:
        return []
    out: list[date] = []
    for ts, row in df.iterrows():
        reported = row.get("Reported EPS")
        estimate = row.get("EPS Estimate")
        surprise = row.get("Surprise(%)")
        beat = None
        if pd.notna(reported) and pd.notna(estimate):
            beat = reported > estimate
        elif pd.notna(surprise):
            beat = surprise > 0
        if beat:
            out.append(ts.date())
    return out
