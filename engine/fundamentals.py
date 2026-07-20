"""Fundamental / dividend data feed, cached like OHLCV.

This module exists because the rest of the pipeline carries prices only. It
splits deliberately into two tiers, and every caller is expected to know
which tier a field came from:

POINT-IN-TIME (real history, safe to use on a historical scan date)
    Sourced from `yfinance.Ticker.dividends`, which is a genuine event
    series going back decades (VZ: 169 payments to 1984). Trailing yield,
    dividend growth YoY, 5-year dividend CAGR and dividend-CUT detection are
    all computed from it as of each date, using only prior payments.

SNAPSHOT (today's value, NOT point-in-time)
    Sourced from `yfinance.Ticker.info`: market cap, payout ratio, EPS growth
    YoY, revenue growth YoY, trailing P/E, analyst consensus rating, analyst
    mean price target. yfinance exposes no history for any of these. Applying
    them to a 2021 scan date asserts that a company's 2026 fundamentals were
    knowable in 2021, which is false.

Why the split matters more than a warning string: screening on snapshot
fields selects companies that are healthy TODAY, which systematically
excludes the dividend-cutters whose collapse is exactly what a "the dividend
is a floor, so no stop is needed" thesis needs to be tested against. See
engine/dividend_hybrid.py, which runs the screen both ways so the size of
that bias is measured rather than merely disclosed.

Prices: yield is a level-sensitive ratio, so it needs UNADJUSTED closes. The
project's main bar pipeline (engine/data.py) uses auto_adjust=True, which
back-adjusts historical closes downward for dividends -- dividing a real
dividend by a total-return-adjusted price overstates historical yield badly.
So this module keeps its own small unadjusted-close cache, used ONLY as the
yield denominator. Backtest fills still use the adjusted bars every other
strategy uses, so returns stay comparable across the book.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from engine.data import DATA_DIR
from engine.universe import TIMEZONE

TRADING_DAYS_PER_YEAR = 252

NOT_POINT_IN_TIME_WARNING = (
    "WARNING: Fundamental fields sourced from yfinance.info are not "
    "point-in-time -- treat results as indicative only until replaced with a "
    "historical fundamentals feed."
)

SURVIVORSHIP_WARNING = (
    "WARNING: Screening on snapshot fundamentals selects companies healthy "
    "TODAY, excluding the dividend-cutters that would refute a no-stop "
    "thesis. Compare against the point-in-time-only screen before believing "
    "any Version A result."
)

# Fields with real history, computed as of each scan date.
POINT_IN_TIME_FIELDS = (
    "trailing_dividend_yield_pct",
    "dividend_growth_yoy_pct",
    "dividend_cagr_5y_pct",
)

# Fields that are today's snapshot applied to every historical date.
SNAPSHOT_FIELDS = (
    "market_cap",
    "payout_ratio_pct",
    "eps_growth_yoy_pct",
    "revenue_growth_yoy_pct",
    "trailing_pe",
    "analyst_rating",
    "analyst_target_price",
    "profit_margins_pct",
    "return_on_equity_pct",
    "debt_to_equity",
)

# A TTM dividend decline bigger than this counts as a cut. Not zero: payment
# dates drift by a few days between years, so a strict "TTM went down at all"
# test fires on ordinary calendar noise (a quarter landing on either side of
# the 252-bar window) rather than on an actual reduction.
DIVIDEND_CUT_THRESHOLD = 0.10

# Consecutive bars a TTM decline must hold before it counts as a real cut,
# rather than a payment landing on the far side of the trailing window. One
# trading month; a genuine cut persists for a year.
CUT_PERSISTENCE_DAYS = 21


@dataclass
class FundamentalSnapshot:
    """Today's fundamentals for one symbol. NOT point-in-time -- see module
    docstring. `fetched_at` is recorded so a stale cache is visible rather
    than silently passed off as current."""

    symbol: str
    market_cap: float | None
    payout_ratio_pct: float | None
    eps_growth_yoy_pct: float | None
    revenue_growth_yoy_pct: float | None
    trailing_pe: float | None
    analyst_rating: float | None
    analyst_target_price: float | None
    current_price: float | None
    fetched_at: str
    # Appended after the fields above already shipped -- kept last with
    # defaults so a JSON cache file written before these existed still loads
    # via `FundamentalSnapshot(**json.loads(...))` (same reasoning as
    # PairsResult.symbols being appended last in engine/pairs.py).
    #
    # yfinance.Ticker.info shapes, confirmed empirically (AAPL): profitMargins
    # and returnOnEquity are fractions (0.27 = 27%, apply _pct()); debtToEquity
    # already arrives percentage-scale (79.5 means 79.5%, not 0.795) -- do not
    # scale it again.
    profit_margins_pct: float | None = None
    return_on_equity_pct: float | None = None
    debt_to_equity: float | None = None


def _snapshot_path(symbol: str) -> "object":
    return DATA_DIR / f"{symbol}_fundamentals.json"


def _pct(value: float | None) -> float | None:
    """yfinance returns growth/payout as fractions (0.043 = 4.3%)."""
    return None if value is None else float(value) * 100


def snapshot(symbol: str, force_refresh: bool = False) -> FundamentalSnapshot:
    """Today's fundamental snapshot for `symbol`, cached to disk.

    Returns a snapshot with None fields rather than raising when yfinance is
    unavailable -- callers must treat a missing field as a screen FAILURE,
    never as a pass (see engine/dividend_hybrid.py)."""
    path = _snapshot_path(symbol)
    if path.exists() and not force_refresh:
        raw = json.loads(path.read_text())
        # A cache file written before profit_margins_pct/return_on_equity_pct/
        # debt_to_equity existed would otherwise silently load as "no quality
        # data for this symbol" (dataclass default None) rather than "not
        # fetched yet" -- refetch once instead of masking a stale cache as a
        # real None result.
        if all(k in raw for k in ("profit_margins_pct", "return_on_equity_pct", "debt_to_equity")):
            return FundamentalSnapshot(**raw)

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:  # noqa: BLE001 -- a missing feed must not kill a run
        info = {}

    snap = FundamentalSnapshot(
        symbol=symbol,
        market_cap=_maybe_float(info.get("marketCap")),
        payout_ratio_pct=_pct(_maybe_float(info.get("payoutRatio"))),
        eps_growth_yoy_pct=_pct(_maybe_float(info.get("earningsGrowth"))),
        revenue_growth_yoy_pct=_pct(_maybe_float(info.get("revenueGrowth"))),
        trailing_pe=_maybe_float(info.get("trailingPE")),
        analyst_rating=_maybe_float(info.get("recommendationMean")),
        analyst_target_price=_maybe_float(info.get("targetMeanPrice")),
        current_price=_maybe_float(info.get("currentPrice")),
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        profit_margins_pct=_pct(_maybe_float(info.get("profitMargins"))),
        return_on_equity_pct=_pct(_maybe_float(info.get("returnOnEquity"))),
        debt_to_equity=_maybe_float(info.get("debtToEquity")),
    )
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(asdict(snap), indent=2))
    return snap


def _maybe_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def dividends(symbol: str, force_refresh: bool = False) -> pd.Series:
    """Full dividend payment history for `symbol`, cached.

    This is REAL point-in-time data -- each payment is stamped with the date
    it was actually paid. Empty series if unavailable."""
    path = DATA_DIR / f"{symbol}_dividends.parquet"
    if path.exists() and not force_refresh:
        return pd.read_parquet(path)["Dividends"]

    try:
        raw = yf.Ticker(symbol).dividends
    except Exception:  # noqa: BLE001
        return pd.Series(dtype=float)
    if raw is None or len(raw) == 0:
        return pd.Series(dtype=float)

    raw = raw.copy()
    raw.index = raw.index.tz_convert(TIMEZONE)
    raw = raw.sort_index()
    DATA_DIR.mkdir(exist_ok=True)
    raw.to_frame("Dividends").to_parquet(path)
    return raw


def unadjusted_close(symbol: str, start: date, end: date, force_refresh: bool = False) -> pd.Series:
    """Split-adjusted but NOT dividend-adjusted closes, cached.

    The yield denominator has to be the price actually quoted at the time.
    engine/data.py's auto_adjust=True closes are back-adjusted for dividends,
    so historical yield computed against them is inflated -- materially, over
    a 5-year window on a 5%-yielding name."""
    path = DATA_DIR / f"{symbol}_unadjusted.parquet"
    if path.exists() and not force_refresh:
        cached = pd.read_parquet(path)["Close"]
        if not cached.empty and cached.index.min().date() <= start and cached.index.max().date() >= end:
            return cached.loc[str(start):str(end)]

    try:
        raw = yf.download(
            symbol, start=start, end=end, interval="1d",
            auto_adjust=False, progress=False, multi_level_index=False,
        )
    except Exception:  # noqa: BLE001
        return pd.Series(dtype=float)
    if raw is None or raw.empty or "Close" not in raw:
        return pd.Series(dtype=float)

    close = raw["Close"].dropna()
    if close.index.tz is None:
        close.index = close.index.tz_localize("UTC").tz_convert(TIMEZONE)
    else:
        close.index = close.index.tz_convert(TIMEZONE)
    DATA_DIR.mkdir(exist_ok=True)
    close.to_frame("Close").to_parquet(path)
    return close.loc[str(start):str(end)]


def _window_sum(
    paid: pd.Series, index: pd.DatetimeIndex, days_from: int, days_to: int
) -> pd.Series:
    """Sum of dividend payments falling in (t - days_from, t - days_to] for
    each bar t in `index`.

    Calendar-window rather than bar-count matching, for two reasons:

    1. CORRECTNESS. engine/data.py localizes daily bars by tz_localize("UTC")
       then converting to America/New_York, which stamps each session at
       20:00 on the PREVIOUS calendar day. Dividend payments are stamped
       09:30 on the payment date. Matching payment dates to bar dates
       therefore misses almost every payment -- the first version of this
       function silently reported VZ (a serial dividend RAISER) as having cut
       its dividend on 379 bars, because TTM was summing a near-random subset
       of payments.
    2. NO WARMUP HOLE. Sums come from the full payment history, not from
       whatever slice of bars the backtest window happens to contain, so a
       trailing-year figure is correct on the window's very first bar.

    Look-ahead: the upper bound is (t - days_to) with days_to >= 0, and
    searchsorted uses side="right" on payment timestamps, so a bar only ever
    counts payments at or before its own timestamp. Because bars are stamped
    20:00 the prior evening, a dividend paid during the session itself lands
    just AFTER that bar and is counted from the next bar on -- one session
    late, deliberately, which errs away from look-ahead rather than toward it.
    """
    if paid.empty or len(index) == 0:
        return pd.Series(0.0, index=index)

    stamps = paid.index
    cumulative = paid.to_numpy().cumsum()

    def _cum_at(bounds: pd.DatetimeIndex):
        positions = stamps.searchsorted(bounds, side="right")
        return pd.Series(
            [0.0 if p == 0 else float(cumulative[p - 1]) for p in positions], index=index
        )

    upper = _cum_at(index - pd.Timedelta(days=days_to))
    lower = _cum_at(index - pd.Timedelta(days=days_from))
    return upper - lower


def trailing_dividends(symbol: str, index: pd.DatetimeIndex) -> pd.Series:
    """Trailing-twelve-month dividends per share, as of each bar in `index`."""
    return _window_sum(dividends(symbol), index, days_from=365, days_to=0)


def _prior_year_dividends(symbol: str, index: pd.DatetimeIndex) -> pd.Series:
    """TTM dividends as they stood one year before each bar."""
    return _window_sum(dividends(symbol), index, days_from=730, days_to=365)


def trailing_yield_pct(symbol: str, index: pd.DatetimeIndex) -> pd.Series:
    """Point-in-time trailing dividend yield (%), as of each bar in `index`.

    TTM dividends / unadjusted close on that date. Returns NaN where the
    unadjusted price is unavailable -- a missing price must fail the screen,
    not default it to zero (which would silently pass a "< X%" test)."""
    if len(index) == 0:
        return pd.Series(dtype=float)
    ttm = trailing_dividends(symbol, index)
    start, end = index.min().date(), index.max().date()
    price = unadjusted_close(symbol, start, end)
    if price.empty:
        return pd.Series(float("nan"), index=index)
    aligned_price = price.reindex(index, method="ffill")
    return (ttm / aligned_price.where(aligned_price > 0)) * 100


def dividend_growth_yoy_pct(symbol: str, index: pd.DatetimeIndex) -> pd.Series:
    """Point-in-time YoY growth in TTM dividends (%). NaN for a symbol that
    paid nothing a year ago -- which fails the screen rather than passing it."""
    ttm = trailing_dividends(symbol, index)
    prior = _prior_year_dividends(symbol, index)
    return (ttm / prior.where(prior > 0) - 1) * 100


def dividend_cagr_5y_pct(symbol: str, index: pd.DatetimeIndex) -> pd.Series:
    """Point-in-time 5-year CAGR of TTM dividends (%)."""
    ttm = trailing_dividends(symbol, index)
    prior = _window_sum(dividends(symbol), index, days_from=365 * 6, days_to=365 * 5)
    ratio = ttm / prior.where(prior > 0)
    return (ratio.pow(1 / 5) - 1) * 100


def dividend_cut_series(
    symbol: str, index: pd.DatetimeIndex, threshold: float = DIVIDEND_CUT_THRESHOLD
) -> pd.Series:
    """True on bars where TTM dividends have fallen more than `threshold`
    below their level a year earlier -- i.e. the dividend was cut.

    This is the one genuinely point-in-time test of the "dividend floor"
    thesis: it detects the floor giving way, from real payment history.

    A raw TTM-vs-prior-year comparison is not enough on its own. Quarterly
    payments sit ~91 days apart against a 365-day window, so the window
    intermittently catches 3 payments instead of 4 and shows a phantom ~25%
    "cut" that resolves within days -- measured at 24 such bars on AAPL, a
    company that has never cut. A genuine cut persists for a full year, so
    the decline must hold for CUT_PERSISTENCE_DAYS consecutive bars to count.
    That removes the calendar artifact without suppressing real cuts (INTC's
    2024 suspension, DOW's 2025 cut, MMM's post-spinoff cut all survive it).
    """
    ttm = trailing_dividends(symbol, index)
    prior = _prior_year_dividends(symbol, index)
    declining = (ttm < prior * (1 - threshold)) & (prior > 0)
    if len(declining) < CUT_PERSISTENCE_DAYS:
        return declining
    # Backward-looking: a bar is a cut only if the decline has already held
    # for the whole trailing window, so this never peeks at later bars.
    sustained = declining.rolling(CUT_PERSISTENCE_DAYS, min_periods=CUT_PERSISTENCE_DAYS).min()
    return (sustained == 1).fillna(False)


def fundamentals_frame(symbol: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    """All point-in-time fundamental series for one symbol, one row per bar."""
    return pd.DataFrame(
        {
            "trailing_dividend_yield_pct": trailing_yield_pct(symbol, index),
            "dividend_growth_yoy_pct": dividend_growth_yoy_pct(symbol, index),
            "dividend_cagr_5y_pct": dividend_cagr_5y_pct(symbol, index),
            "dividend_cut": dividend_cut_series(symbol, index),
        },
        index=index,
    )
