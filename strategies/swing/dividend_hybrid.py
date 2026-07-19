"""Dividend Hybrid -- a fundamental dividend screen front-loading an
intraday technical entry.

Thesis: screen for stocks you'd be willing to own as a long-term dividend
holding, then enter only on a specific technical setup. If the trade works
you exit as a swing trader; if it doesn't, the dividend is supposed to make
holding tolerable -- which is why the original strategy carries NO stop loss.
That claim is the thing engine/dividend_hybrid.py exists to test.

This is not a `strategies.base.Strategy`. Three of its rules fall outside
what that interface and the bracket engine can express:
  * position size is 10% of account equity, not risk-based
  * Version A has no stop at all and holds to the end of the window
  * the outputs it needs (max unrealized drawdown during hold, still-held
    count, dividend cuts during hold) have nowhere to live on a trade row
So it follows the precedent set by Overnight Hold: a config object plus a
dedicated engine that re-enters the shared result shape. See LESSONS.md.

TWO APPROXIMATIONS, both material, both disclosed in every run's output:

1. ENTRY TIMING. The real entry needs pre-market bars (gap detection) and
   5-minute bars (pullback to the intraday EMA20), neither of which this
   pipeline carries for a 5-year daily window. The daily proxy is defined in
   `entry_trigger` below.

2. SIGNAL-TO-FILL LAG -- a deliberate deviation from the literal spec. The
   spec approximates the pullback as "the day's close within 0.5% of SMA20"
   and says to fill at that same day's OPEN. That is look-ahead: the close
   is not knowable when the open prints, and CLAUDE.md rules it out
   explicitly ("a strategy can't use a bar's close to decide whether to
   enter during that same bar"). So the signal is evaluated on day T's
   completed bar and filled at day T+1's open, matching how every other
   strategy in this project is filled. This makes the proxy slightly
   pessimistic versus the real strategy (which enters intraday on T) and
   materially more honest than filling at a price that precedes the
   information used to trigger it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import sma

# --- screen thresholds (from the strategy spec) -----------------------------

MIN_DIVIDEND_YIELD_PCT = 4.0
MIN_MARKET_CAP = 1_000_000_000.0
MAX_ANALYST_RATING = 2.0          # 1 = strong buy .. 5 = strong sell
MIN_PAYOUT_RATIO_PCT = 0.0
MAX_PAYOUT_RATIO_PCT = 85.0
MIN_EPS_GROWTH_PCT = 0.0
MIN_DIVIDEND_GROWTH_PCT = 0.0
MIN_ADR_PCT = 2.0
ADR_LOOKBACK = 20

# --- entry trigger thresholds ----------------------------------------------

MIN_GAP_UP_PCT = 1.0
VOLUME_TOP_FRACTION = 0.30        # "top 30% of 20-day volume"
VOLUME_LOOKBACK = 20
SMA20_PROXIMITY_PCT = 0.5

# Two renderings of "price comes down near the 5-min EMA20 at open".
#
# TRIGGER_SPEC is the literal instruction: the day's close within 0.5% of the
# daily SMA20. Measured on this universe it fires with a gap-up on 0.64% of
# bars, and combined with the screen it produces ZERO trades over 5 years.
# That is not selectivity, it is a contradiction: a 5-minute EMA20 is a
# ~100-minute average that tracks price within a few tenths of a percent,
# while a 20-DAY average sits a median 3.55% away from price on gap-up days
# (p25 1.69%, p75 6.15%, measured across the Dow universe). Requiring a stock
# to gap up more than 1% AND sit within 0.5% of its 20-day average asks for
# two things that almost never co-occur -- the substitution of a daily
# average for an intraday one changed what the rule means, not just its
# precision.
#
# TRIGGER_INTRADAY_PROXY renders the same intent in terms daily bars can
# actually express: the stock gapped up, faded from the open during the
# session (a pullback happened), but held above the prior close (the gap did
# not fill). Fires with a gap-up on 2.54% of bars.
#
# Both are run and reported. The spec version is not silently replaced.
TRIGGER_SPEC = "spec"
TRIGGER_INTRADAY_PROXY = "intraday_proxy"

# --- exit rules -------------------------------------------------------------

VERSION_B_STOP_PCT = 8.0

# Version A has no stop, so it has no natural risk-per-share to normalize
# R-multiples against. Both versions use Version B's 8% stop distance as the
# nominal risk unit purely so avg win R / avg loss R / expectancy are directly
# comparable between them. For Version A this is a normalization convention,
# NOT a stop that exists -- same disclosed device Overnight Hold uses.
NOMINAL_RISK_PCT = VERSION_B_STOP_PCT

# Unrealized-loss levels tracked during a Version A hold. 40% is called out
# separately because the dividend-floor thesis is not merely strained there,
# it is broken: no plausible yield compensates a 40% capital loss.
DRAWDOWN_BUCKETS_PCT = (10.0, 20.0, 30.0, 40.0)
THESIS_BREAKDOWN_PCT = 40.0

# Qualitative gates from the original strategy that cannot be automated.
# Logged as manual checkpoints in every run's output, never silently treated
# as passed -- live trading would apply these as a final human gate.
MANUAL_CHECKPOINTS = (
    "Long-term chart review back to 2008 and 2020 -- confirm the company grew "
    "through both crashes rather than merely surviving them.",
    "P/E relative to sector peers -- a low absolute P/E means nothing without "
    "the sector comparison.",
)


@dataclass(frozen=True)
class DividendHybrid:
    """Rule parameters for one run. Frozen so a logged run's parameters
    can't drift from the run that produced it (CLAUDE.md: log the exact rule
    parameters, so '4% yield' and '5% yield' don't get conflated)."""

    name: str = "Dividend Hybrid"
    timeframe: str = "1d"
    direction: str = "long"

    min_dividend_yield_pct: float = MIN_DIVIDEND_YIELD_PCT
    min_market_cap: float = MIN_MARKET_CAP
    max_analyst_rating: float = MAX_ANALYST_RATING
    max_payout_ratio_pct: float = MAX_PAYOUT_RATIO_PCT
    min_eps_growth_pct: float = MIN_EPS_GROWTH_PCT
    min_dividend_growth_pct: float = MIN_DIVIDEND_GROWTH_PCT
    min_adr_pct: float = MIN_ADR_PCT
    min_gap_up_pct: float = MIN_GAP_UP_PCT
    sma20_proximity_pct: float = SMA20_PROXIMITY_PCT
    stop_pct: float = VERSION_B_STOP_PCT
    position_pct_of_equity: float = 10.0
    trigger_mode: str = TRIGGER_SPEC


def adr_pct(bars: pd.DataFrame, lookback: int = ADR_LOOKBACK) -> pd.Series:
    """Average Daily Range as a % of close, over a trailing window.

    Right-aligned rolling mean -- bar i sees only bars <= i."""
    daily_range = (bars["High"] - bars["Low"]) / bars["Close"].where(bars["Close"] > 0)
    return daily_range.rolling(lookback).mean() * 100


def technical_screen(bars: pd.DataFrame, config: DividendHybrid) -> pd.DataFrame:
    """SMA20 > SMA50 > SMA200 and ADR above threshold, per bar.

    Entirely point-in-time: both legs are trailing rolling windows on daily
    OHLCV the project already caches."""
    close = bars["Close"]
    sma20, sma50, sma200 = sma(close, 20), sma(close, 50), sma(close, 200)
    return pd.DataFrame(
        {
            "sma_stack": (sma20 > sma50) & (sma50 > sma200),
            "adr_ok": adr_pct(bars) > config.min_adr_pct,
        },
        index=bars.index,
    ).fillna(False)


def point_in_time_fundamental_screen(
    fundamentals: pd.DataFrame, config: DividendHybrid
) -> pd.DataFrame:
    """The fundamental criteria that have REAL history behind them.

    Both come from actual dividend payment records (engine/fundamentals.py),
    so they are safe to evaluate on a historical scan date. NaN fails."""
    return pd.DataFrame(
        {
            "yield_ok": fundamentals["trailing_dividend_yield_pct"]
            > config.min_dividend_yield_pct,
            "dividend_growth_ok": fundamentals["dividend_growth_yoy_pct"]
            > config.min_dividend_growth_pct,
        },
        index=fundamentals.index,
    ).fillna(False)


def snapshot_fundamental_screen(snapshot, price: pd.Series, config: DividendHybrid) -> pd.DataFrame:
    """The fundamental criteria that exist only as today's snapshot.

    Every column here applies a 2026 value to whatever scan date it is
    evaluated on. That is not a rounding error -- it selects for companies
    still healthy today, which is precisely the set that never cut its
    dividend, and therefore flatters any no-stop thesis. Kept separate from
    the point-in-time screen so the two can be run against each other and the
    bias measured (see engine/dividend_hybrid.py).

    A missing field fails its criterion. Never pass on absent data.
    """
    def _flat(value: bool) -> pd.Series:
        return pd.Series(value, index=price.index, dtype=bool)

    market_cap_ok = _flat(
        snapshot.market_cap is not None and snapshot.market_cap > config.min_market_cap
    )
    rating_ok = _flat(
        snapshot.analyst_rating is not None
        and snapshot.analyst_rating <= config.max_analyst_rating
    )
    payout_ok = _flat(
        snapshot.payout_ratio_pct is not None
        and MIN_PAYOUT_RATIO_PCT < snapshot.payout_ratio_pct < config.max_payout_ratio_pct
    )
    eps_ok = _flat(
        snapshot.eps_growth_yoy_pct is not None
        and snapshot.eps_growth_yoy_pct > config.min_eps_growth_pct
    )
    # The only snapshot criterion that varies per bar: today's analyst target
    # against the historical price. A hybrid of a 2026 target and a 2021
    # price -- disclosed, and one more reason this screen is the biased arm.
    if snapshot.analyst_target_price is None:
        below_target = _flat(False)
    else:
        below_target = price < snapshot.analyst_target_price

    return pd.DataFrame(
        {
            "market_cap_ok": market_cap_ok,
            "analyst_rating_ok": rating_ok,
            "payout_ratio_ok": payout_ok,
            "eps_growth_ok": eps_ok,
            "below_analyst_target": below_target,
        },
        index=price.index,
    ).fillna(False)


def entry_trigger(bars: pd.DataFrame, config: DividendHybrid) -> pd.DataFrame:
    """Daily-bar proxy for the real intraday entry.

    Real rule            -> daily approximation
    -------------------------------------------------------------------
    pre-market gap >1%   -> open > prior close * 1.01
    above intraday VWAP  -> open > SMA20
    volume confirmation  -> volume in the top 30% of the trailing 20 days
    pullback to 5m EMA20 -> close within 0.5% of SMA20

    "Top 30% of 20-day volume" is read as the top 30% of the trailing 20-day
    volume DISTRIBUTION (i.e. >= its 70th percentile), not 30% above the
    20-day mean. Stated because the phrasing admits both readings and they
    are materially different tests.

    Look-ahead: `prior_close` is shifted, and both rolling windows are
    right-aligned. The resulting signal is consumed by the engine on the
    FOLLOWING bar's open -- see this module's docstring.
    """
    close, open_, volume = bars["Close"], bars["Open"], bars["Volume"]
    sma20 = sma(close, 20)
    prior_close = close.shift(1)

    volume_threshold = volume.rolling(VOLUME_LOOKBACK).quantile(1 - VOLUME_TOP_FRACTION)

    if config.trigger_mode == TRIGGER_SPEC:
        pullback = (close - sma20).abs() / sma20.where(sma20 > 0) * 100 <= config.sma20_proximity_pct
    elif config.trigger_mode == TRIGGER_INTRADAY_PROXY:
        # Faded from the open (the pullback happened) but held above the prior
        # close (the gap did not fill).
        pullback = (close < open_) & (close > prior_close)
    else:
        raise ValueError(f"Unknown trigger_mode {config.trigger_mode!r}")

    return pd.DataFrame(
        {
            "gap_up": open_ > prior_close * (1 + config.min_gap_up_pct / 100),
            "open_above_sma20": open_ > sma20,
            "volume_ok": volume >= volume_threshold,
            "pullback": pullback,
        },
        index=bars.index,
    ).fillna(False)
