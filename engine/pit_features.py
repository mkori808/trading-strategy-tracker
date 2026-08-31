"""Point-in-time market-state features for Conditional Edge Discovery.

This module answers one question and nothing else: **what was observably
true about a symbol, its market, and its universe at the moment a strategy's
signal became actionable?** It never generates signals, never scores a
strategy, and never sees a trade outcome -- that separation is what keeps
`engine/conditional_analysis.py` honest, because a feature computed with any
knowledge of how the trade turned out would make every conditional result
circular.

Three rules govern everything here.

**1. Every value at bar i depends only on bars <= i.** All windows are
right-aligned rolling or recursive operations. There is no ``center=True``,
no full-series ``.max()``/``.min()`` standing in for a 52-week extreme, no
backward fill, and no benchmark alignment that reaches forward. This is
asserted by recomputation, not by inspection: ``tests/test_engine/
test_pit_features.py`` appends a wildly different future to the input and
requires the row at bar i to be unchanged. That property is also what makes
the module-level cache safe -- a feature frame computed over a longer window
yields the same row for the same bar, so reading a cached frame can never
import future information.

**2. The feature bar is the DECISION bar, not the fill bar.** The standard
engine executes NEXT_OPEN (see engine/event_timing.py), so a trade whose
fill is bar ``EntryBar`` was decided on the close of bar ``EntryBar - 1``.
The cross-sectional engine ranks on ``bars.index < rebalance_day`` and fills
at that day's open, so its decision bar is the last session strictly before
the rebalance. ``engine/observations.py`` owns that mapping; this module
only ever receives a timestamp and returns the row at-or-before it.

**3. A feature that cannot be computed legitimately is UNAVAILABLE, not
approximated.** ``FEATURES`` carries the full metadata contract for every
feature -- source, lookback, PIT status, missing-value behaviour, engine
compatibility -- including the ones declared and deliberately not computable
here (``days_until_earnings``). Coverage is never bought by synthesizing
history from a present-day snapshot; see CLAUDE.md, "Fundamentals data", for
the measured cost of doing that.

Two feature families are computable but are NOT eligible to drive a strong
conditional verdict, and say so in their metadata
(``discovery_eligible=False``):

- **Calendar features** (day of week, month, month/quarter-end proximity):
  high-overfitting-risk explanatory variables. Available for exploration.
- **Sector-relative features.** The project has no point-in-time GICS
  membership dataset, so the symbol -> sector-ETF map below is a static
  present-day classification applied backwards. Its bias is far milder than
  a snapshot fundamental (a company's sector is not assigned by its returns,
  and no studied constituent changed sector in-window) but it is still not
  point-in-time, so it is gated rather than silently trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

from engine import data as data_module
from engine import regime as regime_module
from engine.indicators import atr, rsi, sma

FeatureKind = Literal["continuous", "categorical", "boolean"]
FeatureSource = Literal[
    "symbol_ohlcv", "benchmark_ohlcv", "universe_ohlcv", "sector_ohlcv",
    "calendar", "earnings",
]

#: Whether a feature's VALUE is shared by every security observed at the same
#: decision date (portfolio_regime), is specific to one security even when
#: several securities are observed on the same date (security), or is
#: partially shared (mixed -- e.g. a sector-ETF-derived value, identical for
#: two securities in the same sector but not across sectors).
#:
#: This classification is what tells the dependence-aware statistics layer
#: (engine/conditional_dependence.py) whether a cross-sectional observation
#: set's rows for one rebalance are FIVE INDEPENDENT-LOOKING draws or ONE
#: regime observation repeated five times -- see that module's docstring and
#: CLAUDE.md's Conditional Edge Discovery methodology-audit notes (2026-08-22,
#: "dependence-aware statistics"). Persisted here, not inferred at query time,
#: so a feature's classification is auditable and versioned the same way its
#: other metadata is.
FeatureLevel = Literal["portfolio_regime", "security", "mixed"]

#: Trading days of history prepended before a study window so every declared
#: lookback is warm on its first traded bar. The deepest chain is a 252-day
#: rolling percentile OF a 20-day statistic (272 bars), plus slack.
FEATURE_WARMUP_TRADING_DAYS = 300

#: Calendar-day conversion for the warmup above. 252 trading days per 365.25
#: calendar days -> 1.45, matching engine/cross_sectional.py's convention
#: rather than inventing a second one.
TRADING_TO_CALENDAR = 1.45

#: Sentinel for "no earnings report is known at or before this bar". A real
#: distance is always smaller; a sentinel rather than NaN keeps the feature
#: usable (never-reported is a real state) while quantile bucketing still
#: separates it into the top bucket.
NO_EARNINGS_SENTINEL = 9_999

BENCHMARK = regime_module.BENCHMARK

#: All eleven sector SPDRs, for the sector-rank feature.
SECTOR_ETFS: tuple[str, ...] = (
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLRE", "XLU", "XLC",
)

#: Static present-day sector classification for the equity universe. NOT
#: point-in-time -- see this module's docstring. Every feature derived from
#: it carries pit_safe=False and discovery_eligible=False.
SECTOR_ETF_BY_SYMBOL: dict[str, str] = {
    "AAPL": "XLK", "MSFT": "XLK", "IBM": "XLK", "INTC": "XLK", "CSCO": "XLK",
    "CRM": "XLK", "V": "XLK",
    "JPM": "XLF", "GS": "XLF", "AXP": "XLF", "TRV": "XLF",
    "CVX": "XLE",
    "UNH": "XLV", "JNJ": "XLV", "MRK": "XLV", "AMGN": "XLV",
    "HD": "XLY", "MCD": "XLY", "NKE": "XLY",
    "PG": "XLP", "KO": "XLP", "WMT": "XLP",
    "BA": "XLI", "CAT": "XLI", "HON": "XLI", "MMM": "XLI",
    "DOW": "XLB",
    "VZ": "XLC", "DIS": "XLC",
}


@dataclass(frozen=True)
class FeatureDefinition:
    """The full data-integrity contract for one feature.

    Every feature declares where it comes from, how much history it needs,
    whether it is point-in-time safe, what happens when it is missing, and
    which engines it applies to. Holding that as data rather than as prose
    is what lets ``availability_report()`` answer "why is this column empty"
    without anyone re-reading the code.
    """

    key: str
    label: str
    group: str
    kind: FeatureKind
    source: FeatureSource
    #: Trailing BARS the value needs before it is defined at all.
    lookback_bars: int
    description: str
    pit_safe: bool = True
    pit_note: str = "Right-aligned rolling window; bar i uses bars <= i only."
    #: What happens to an observation whose value is missing. Only one policy
    #: is implemented on purpose: dropping the observation from that single
    #: feature's analysis, never imputing a middle value that would read as a
    #: measured neutral reading.
    missing_policy: str = "drop the observation from this feature's analysis"
    #: False => usable for exploration, never allowed to carry a hypothesis.
    discovery_eligible: bool = True
    available: bool = True
    unavailable_reason: str | None = None
    engines: tuple[str, ...] = ("standard", "cross_sectional")
    #: Ordered category values for categorical features. Also fixes display
    #: order, so an interaction heatmap's axis is stable between runs.
    categories: tuple[str, ...] = ()
    #: See FeatureLevel above. Defaulted by group in `_f()` (via
    #: `_LEVEL_OVERRIDES`/`_GROUP_DEFAULT_LEVEL`) rather than passed at every
    #: one of the ~80 call sites below; only the exceptions to a group's
    #: default are listed explicitly.
    level: FeatureLevel = "security"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "group": self.group,
            "kind": self.kind, "source": self.source,
            "lookbackBars": self.lookback_bars, "description": self.description,
            "pitSafe": self.pit_safe, "pitNote": self.pit_note,
            "missingPolicy": self.missing_policy,
            "discoveryEligible": self.discovery_eligible,
            "available": self.available, "unavailableReason": self.unavailable_reason,
            "engines": list(self.engines), "categories": list(self.categories),
            "level": self.level,
        }


#: Conceptual grouping, used by the redundancy report so five ways of saying
#: "short-term mean reversion" are never counted as five pieces of evidence.
FEATURE_GROUPS: dict[str, str] = {
    "trend": "Price / trend",
    "mean_reversion": "Mean reversion / oscillator",
    "volatility": "Volatility",
    "volume": "Volume / liquidity",
    "market_regime": "Market regime",
    "breadth": "Breadth",
    "relative": "Relative / cross-sectional",
    "sector": "Sector-relative (not point-in-time)",
    "gap": "Gap / overnight",
    "calendar": "Calendar",
    "event": "Event state",
}


#: Default level for every feature in a group, UNLESS the key appears in
#: `_LEVEL_OVERRIDES` below. Reflects what each group's builder function
#: actually computes (see `_market_definitions`, `_breadth_definitions`,
#: etc.): "market_regime" and "breadth" read only the benchmark/universe
#: panel, so their value is identical for every security observed on the
#: same decision date; "calendar" reads only the date; everything else reads
#: `bars` for the specific symbol being scored and therefore varies security
#: to security even within one rebalance.
_GROUP_DEFAULT_LEVEL: dict[str, FeatureLevel] = {
    "trend": "security", "mean_reversion": "security", "volatility": "security",
    "volume": "security", "market_regime": "portfolio_regime", "breadth": "portfolio_regime",
    "relative": "security", "sector": "mixed", "gap": "security",
    "calendar": "portfolio_regime", "event": "security",
}

#: Explicit exceptions to the group default, keyed by feature key. Every one
#: of these is a case where the group's usual rule (see above) doesn't hold
#: for this specific feature's actual computation:
#:
#: - `xs_dispersion_20d` is filed under "relative" (security by group
#:   default) but is built in pit_features.py as a UNIVERSE-WIDE dispersion
#:   value broadcast identically to every symbol's column
#:   (`_cross_sectional_percentiles`) -- portfolio-level despite its group.
#: - `rs_vs_sector_60d` and `residual_ret_20d_sector` are filed under
#:   "sector" (mixed by group default, since most sector features are
#:   identical for two securities sharing a sector ETF) but both SUBTRACT
#:   the shared sector term from the security's own return/residual, so the
#:   RESULT is symbol-specific even though one input term is shared --
#:   security-level in effect.
_LEVEL_OVERRIDES: dict[str, FeatureLevel] = {
    "xs_dispersion_20d": "portfolio_regime",
    "rs_vs_sector_60d": "security",
    "residual_ret_20d_sector": "security",
}


def _level_for(group: str, key: str) -> FeatureLevel:
    return _LEVEL_OVERRIDES.get(key, _GROUP_DEFAULT_LEVEL[group])


def _f(**kwargs: Any) -> FeatureDefinition:
    kwargs.setdefault("level", _level_for(kwargs["group"], kwargs["key"]))
    return FeatureDefinition(**kwargs)


def _trend_definitions() -> list[FeatureDefinition]:
    out: list[FeatureDefinition] = []
    for n in (5, 10, 20, 60, 120, 189, 252):
        out.append(_f(
            key=f"ret_{n}d", label=f"{n}-day return (%)", group="trend", kind="continuous",
            source="symbol_ohlcv", lookback_bars=n + 1,
            description=f"Close-to-close return over the trailing {n} sessions.",
        ))
    for n in (20, 50, 100, 200):
        out.append(_f(
            key=f"dist_sma{n}", label=f"Distance from {n}-day SMA (%)", group="trend",
            kind="continuous", source="symbol_ohlcv", lookback_bars=n,
            description=f"(Close / SMA{n} - 1) * 100.",
        ))
    out += [
        _f(key="sma20_vs_sma50", label="SMA20 vs SMA50 (%)", group="trend",
           kind="continuous", source="symbol_ohlcv", lookback_bars=50,
           description="(SMA20 / SMA50 - 1) * 100; positive = short above medium."),
        _f(key="sma50_vs_sma200", label="SMA50 vs SMA200 (%)", group="trend",
           kind="continuous", source="symbol_ohlcv", lookback_bars=200,
           description="(SMA50 / SMA200 - 1) * 100; the classic long-trend read."),
        _f(key="dist_52w_high", label="Distance from 52-week high (%)", group="trend",
           kind="continuous", source="symbol_ohlcv", lookback_bars=252,
           description=(
               "(Close / trailing 252-session high - 1) * 100. Rolling window, never a "
               "full-series max."
           )),
        _f(key="dist_52w_low", label="Distance from 52-week low (%)", group="trend",
           kind="continuous", source="symbol_ohlcv", lookback_bars=252,
           description="(Close / trailing 252-session low - 1) * 100."),
        _f(key="drawdown_60d", label="Drawdown from 60-day peak (%)", group="trend",
           kind="continuous", source="symbol_ohlcv", lookback_bars=60,
           description="(Close / trailing 60-session close peak - 1) * 100; always <= 0."),
        _f(key="trend_slope_60d", label="60-day log-price slope (%/session)", group="trend",
           kind="continuous", source="symbol_ohlcv", lookback_bars=60,
           description=(
               "OLS slope of log(Close) over a trailing 60-session window, in percent per "
               "session. Trailing regression only."
           )),
    ]
    return out


def _mean_reversion_definitions() -> list[FeatureDefinition]:
    out = [
        _f(key=f"rsi{n}", label=f"RSI({n})", group="mean_reversion", kind="continuous",
           source="symbol_ohlcv", lookback_bars=n * 5,
           description=f"Wilder RSI over {n} sessions, from engine/indicators.py.")
        for n in (2, 5, 14)
    ]
    out += [
        _f(key="ibs", label="Internal Bar Strength", group="mean_reversion",
           kind="continuous", source="symbol_ohlcv", lookback_bars=1,
           description="(Close - Low) / (High - Low) on the decision bar; 0 = closed at the low."),
        _f(key="ret_zscore_20d", label="Return z-score (20d)", group="mean_reversion",
           kind="continuous", source="symbol_ohlcv", lookback_bars=21,
           description="Decision-bar return standardised by its own trailing 20-session mean and std."),
        _f(key="consecutive_days", label="Consecutive up/down days", group="mean_reversion",
           kind="continuous", source="symbol_ohlcv", lookback_bars=10,
           description="Signed run length of same-direction closes ending on the decision bar."),
        _f(key="cum_ret_3d", label="Cumulative 3-day return (%)", group="mean_reversion",
           kind="continuous", source="symbol_ohlcv", lookback_bars=4,
           description="Close-to-close return over the trailing 3 sessions."),
        _f(key="dist_sma5", label="Distance from 5-day SMA (%)", group="mean_reversion",
           kind="continuous", source="symbol_ohlcv", lookback_bars=5,
           description="(Close / SMA5 - 1) * 100."),
    ]
    return out


def _volatility_definitions() -> list[FeatureDefinition]:
    out = [
        _f(key="atr14", label="ATR(14)", group="volatility", kind="continuous",
           source="symbol_ohlcv", lookback_bars=15,
           description="Average true range over 14 sessions, in price units."),
        _f(key="atr_pct", label="ATR as % of price", group="volatility", kind="continuous",
           source="symbol_ohlcv", lookback_bars=15,
           description="ATR(14) / Close * 100 -- comparable across symbols, unlike raw ATR."),
    ]
    for n in (10, 20, 60):
        out.append(_f(
            key=f"realized_vol_{n}d", label=f"{n}-day realized vol (%/yr)", group="volatility",
            kind="continuous", source="symbol_ohlcv", lookback_bars=n + 1,
            description=f"Annualised standard deviation of the trailing {n} daily log returns.",
        ))
    out += [
        _f(key="vol_percentile_252", label="Vol percentile vs own 252d history",
           group="volatility", kind="continuous", source="symbol_ohlcv", lookback_bars=273,
           description=(
               "Rank of 20-day realized vol within its own trailing 252-session history "
               "(0-100). Trailing rolling percentile, never a whole-sample rank."
           )),
        _f(key="vol_expansion", label="Vol expansion (10d / 60d)", group="volatility",
           kind="continuous", source="symbol_ohlcv", lookback_bars=61,
           description="Ratio of 10-day to 60-day realized vol; above 1 = expanding."),
        _f(key="range_vs_atr", label="Decision-bar range / ATR", group="volatility",
           kind="continuous", source="symbol_ohlcv", lookback_bars=15,
           description="(High - Low) on the decision bar divided by ATR(14)."),
    ]
    return out


def _volume_definitions() -> list[FeatureDefinition]:
    return [
        _f(key="rel_volume_20", label="Relative volume (vs 20d avg)", group="volume",
           kind="continuous", source="symbol_ohlcv", lookback_bars=20,
           description="Decision-bar volume divided by its trailing 20-session average."),
        _f(key="volume_percentile_252", label="Volume percentile (252d)", group="volume",
           kind="continuous", source="symbol_ohlcv", lookback_bars=252,
           description="Rank of decision-bar volume within the trailing 252 sessions (0-100)."),
        _f(key="volume_shock_z", label="Volume shock (z vs 60d)", group="volume",
           kind="continuous", source="symbol_ohlcv", lookback_bars=61,
           description="Decision-bar volume standardised by its trailing 60-session mean and std."),
        _f(key="dollar_volume", label="Dollar volume ($)", group="volume",
           kind="continuous", source="symbol_ohlcv", lookback_bars=1,
           description="Close * Volume on the decision bar."),
        _f(key="dollar_volume_percentile_252", label="Dollar-volume percentile (252d)",
           group="volume", kind="continuous", source="symbol_ohlcv", lookback_bars=252,
           description="Rank of decision-bar dollar volume within the trailing 252 sessions (0-100)."),
        _f(key="volume_regime_change", label="Volume regime change (20d / 60d)", group="volume",
           kind="continuous", source="symbol_ohlcv", lookback_bars=60,
           description="20-session average volume divided by the 60-session average."),
    ]


def _market_definitions() -> list[FeatureDefinition]:
    out = [
        _f(key="mkt_above_sma50", label="Market above 50-day SMA", group="market_regime",
           kind="boolean", source="benchmark_ohlcv", lookback_bars=50,
           description=f"{BENCHMARK} close above its own 50-session SMA."),
        _f(key="mkt_above_sma200", label="Market above 200-day SMA", group="market_regime",
           kind="boolean", source="benchmark_ohlcv", lookback_bars=200,
           description=f"{BENCHMARK} close above its own 200-session SMA."),
    ]
    for n in (20, 60, 120, 252):
        out.append(_f(
            key=f"mkt_ret_{n}d", label=f"Market {n}-day return (%)", group="market_regime",
            kind="continuous", source="benchmark_ohlcv", lookback_bars=n + 1,
            description=f"{BENCHMARK} close-to-close return over the trailing {n} sessions.",
        ))
    out += [
        _f(key="mkt_vol_20d", label="Market 20-day realized vol (%/yr)", group="market_regime",
           kind="continuous", source="benchmark_ohlcv", lookback_bars=21,
           description=f"Annualised std of {BENCHMARK}'s trailing 20 daily log returns."),
        _f(key="mkt_vol_percentile_252", label="Market vol percentile (252d)",
           group="market_regime", kind="continuous", source="benchmark_ohlcv",
           lookback_bars=273,
           description="Rank of market 20-day vol within its own trailing 252-session history (0-100)."),
        _f(key="mkt_drawdown_252", label="Market drawdown from 252d peak (%)",
           group="market_regime", kind="continuous", source="benchmark_ohlcv",
           lookback_bars=252,
           description=f"({BENCHMARK} close / trailing 252-session peak - 1) * 100."),
        _f(key="mkt_regime", label="Market regime (SMA 50/200)", group="market_regime",
           kind="categorical", source="benchmark_ohlcv", lookback_bars=200,
           categories=(regime_module.BEARISH, regime_module.NEUTRAL, regime_module.BULLISH),
           description=(
               "engine/regime.py's contemporaneous label -- Bullish when close is above both "
               "SMAs and SMA50 > SMA200, Bearish when close is below SMA200 and SMA50 < "
               "SMA200, Neutral otherwise. Reused rather than redefined, so the discovery "
               "engine and the pre-trade filter cannot disagree about what 'bull market' means."
           )),
    ]
    return out


def _breadth_definitions() -> list[FeatureDefinition]:
    out = [
        _f(key=f"breadth_above_sma{n}", label=f"% of universe above {n}-day SMA",
           group="breadth", kind="continuous", source="universe_ohlcv", lookback_bars=n,
           description=f"Share of the study universe closing above its own {n}-session SMA.")
        for n in (20, 50, 200)
    ]
    out += [
        _f(key="breadth_pos_ret_20d", label="% of universe with positive 20d return",
           group="breadth", kind="continuous", source="universe_ohlcv", lookback_bars=21,
           description="Share of the study universe whose trailing 20-session return is positive."),
        _f(key="breadth_pos_ret_60d", label="% of universe with positive 60d return",
           group="breadth", kind="continuous", source="universe_ohlcv", lookback_bars=61,
           description="Share of the study universe whose trailing 60-session return is positive."),
        _f(key="breadth_advance_decline_10d", label="Advance/decline balance (10d)",
           group="breadth", kind="continuous", source="universe_ohlcv", lookback_bars=11,
           description="Mean daily (advancers - decliners) / members over the trailing 10 sessions."),
        _f(key="breadth_momentum", label="Breadth momentum (50d breadth vs own 20d mean)",
           group="breadth", kind="continuous", source="universe_ohlcv", lookback_bars=70,
           description="`breadth_above_sma50` minus its own trailing 20-session mean."),
    ]
    return out


def _relative_definitions() -> list[FeatureDefinition]:
    return [
        _f(key="rs_vs_market_60d", label="Relative strength vs market (60d, pp)",
           group="relative", kind="continuous", source="benchmark_ohlcv", lookback_bars=61,
           description="Symbol 60-day return minus the benchmark's, in percentage points."),
        _f(key="residual_ret_20d", label="Market-residual 20d return (pp)", group="relative",
           kind="continuous", source="benchmark_ohlcv", lookback_bars=273,
           description=(
               "Symbol 20-day return minus beta x benchmark 20-day return, with beta from a "
               "trailing 252-session regression of daily returns. Right-aligned: the beta used "
               "at bar i is estimated on bars <= i."
           )),
        _f(key="xs_momentum_percentile_120d", label="Cross-sectional 120d momentum percentile",
           group="relative", kind="continuous", source="universe_ohlcv", lookback_bars=121,
           description=(
               "Percentile rank of the symbol's 120-day return within the study universe on "
               "that date (0-100)."
           )),
        _f(key="xs_ret_percentile_20d", label="Cross-sectional 20d return percentile",
           group="relative", kind="continuous", source="universe_ohlcv", lookback_bars=21,
           description="Percentile rank of the symbol's 20-day return within the study universe (0-100)."),
        _f(key="xs_vol_percentile", label="Cross-sectional volatility percentile",
           group="relative", kind="continuous", source="universe_ohlcv", lookback_bars=21,
           description="Percentile rank of the symbol's 20-day realized vol within the study universe (0-100)."),
        _f(key="xs_dispersion_20d", label="Universe return dispersion (20d, pp)",
           group="relative", kind="continuous", source="universe_ohlcv", lookback_bars=21,
           description=(
               "Cross-sectional standard deviation of trailing 20-day returns across the study "
               "universe -- a market-wide value, identical for every symbol on a given date."
           )),
    ]


_SECTOR_NOTE = (
    "Derived from a STATIC present-day symbol -> sector-ETF map. The project has no "
    "point-in-time GICS membership dataset, so this classification is applied backwards. "
    "Milder bias than a snapshot fundamental (sector is not assigned by returns, and no "
    "studied constituent changed sector in-window) but it is not point-in-time, so these "
    "features are exploratory only and can never carry a hypothesis."
)


def _sector_definitions() -> list[FeatureDefinition]:
    common: dict[str, Any] = {
        "group": "sector", "kind": "continuous", "source": "sector_ohlcv",
        "pit_safe": False, "pit_note": _SECTOR_NOTE, "discovery_eligible": False,
    }
    return [
        _f(key="sector_ret_60d", label="Sector ETF 60-day return (%)", lookback_bars=61,
           description="Trailing 60-session return of the symbol's mapped sector ETF.", **common),
        _f(key="sector_rank_60d", label="Sector rank (60d, 1 = strongest)", lookback_bars=61,
           description="Rank of the mapped sector ETF's 60-day return among all 11 sector SPDRs.",
           **common),
        _f(key="rs_vs_sector_60d", label="Relative strength vs sector (60d, pp)",
           lookback_bars=61,
           description="Symbol 60-day return minus its mapped sector ETF's, in percentage points.",
           **common),
        _f(key="residual_ret_20d_sector", label="Market+sector residual 20d return (pp)",
           lookback_bars=273,
           description=(
               "Symbol 20-day return minus its trailing-252-session two-factor (benchmark, "
               "sector ETF) fitted value. Right-aligned betas."
           ), **common),
    ]


def _gap_definitions() -> list[FeatureDefinition]:
    return [
        _f(key="gap_pct", label="Overnight gap (%)", group="gap", kind="continuous",
           source="symbol_ohlcv", lookback_bars=2,
           description="Decision-bar open divided by the prior close, minus 1, in percent."),
        _f(key="gap_vs_atr", label="Gap / ATR(14)", group="gap", kind="continuous",
           source="symbol_ohlcv", lookback_bars=16,
           description=(
               "Decision-bar gap in price units divided by ATR(14) -- gap size scaled to the "
               "symbol's own volatility."
           )),
        _f(key="gap_percentile_252", label="Gap percentile (252d)", group="gap",
           kind="continuous", source="symbol_ohlcv", lookback_bars=253,
           description="Rank of the decision-bar gap within the trailing 252 sessions of gaps (0-100)."),
        _f(key="gap_alignment", label="Gap vs trend alignment", group="gap", kind="categorical",
           source="symbol_ohlcv", lookback_bars=21,
           categories=("opposed", "flat", "aligned"),
           description="Whether the decision-bar gap's sign matches the sign of the trailing 20-day return."),
    ]


def _calendar_definitions() -> list[FeatureDefinition]:
    common: dict[str, Any] = {
        "group": "calendar", "source": "calendar", "lookback_bars": 0,
        "discovery_eligible": False,
        "pit_note": "Known from the calendar alone; trivially point-in-time.",
    }
    return [
        _f(key="day_of_week", label="Day of week", kind="categorical",
           categories=("Mon", "Tue", "Wed", "Thu", "Fri"),
           description=(
               "Weekday of the decision bar. High overfitting risk -- exploratory only, and "
               "never permitted to carry a conditional-edge verdict."
           ), **common),
        _f(key="month", label="Month", kind="categorical",
           categories=("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
           description="Calendar month of the decision bar. Exploratory only.", **common),
        _f(key="days_to_month_end", label="Sessions to month end", kind="continuous",
           description="Trading sessions remaining in the decision bar's calendar month. Exploratory only.",
           **common),
        _f(key="days_to_quarter_end", label="Sessions to quarter end", kind="continuous",
           description="Trading sessions remaining in the decision bar's calendar quarter. Exploratory only.",
           **common),
    ]


def _event_definitions() -> list[FeatureDefinition]:
    return [
        _f(key="days_since_earnings", label="Days since last earnings", group="event",
           kind="continuous", source="earnings", lookback_bars=0,
           description=(
               "Calendar days since the most recent REPORTED earnings date at or before the "
               f"decision bar; {NO_EARNINGS_SENTINEL} when none is known. Sourced from "
               "engine/data.py:earnings_dates, the same real historical feed PEAD trades on."
           )),
        _f(key="last_earnings_gap_pct", label="Last earnings-reaction gap (%)", group="event",
           kind="continuous", source="earnings", lookback_bars=1,
           description=(
               "Overnight gap on the first session at or after the most recent past earnings "
               "report -- the market's reaction to it."
           )),
        _f(key="post_earnings_5d", label="Within 7 days of earnings", group="event",
           kind="boolean", source="earnings", lookback_bars=0,
           description=(
               "True when the most recent reported earnings date falls within 7 calendar days "
               "before the decision bar."
           )),
        _f(key="days_until_earnings", label="Sessions until next scheduled earnings",
           group="event", kind="continuous", source="earnings", lookback_bars=0,
           available=False, pit_safe=False,
           unavailable_reason=(
               "Requires knowing, at the decision bar, a report date that had been SCHEDULED "
               "but not yet reported. engine/data.py:earnings_dates supplies actual report "
               "dates only, with no record of when each was first announced -- so any value "
               "here would be tomorrow's calendar read today. Left unavailable rather than "
               "approximated."
           ),
           description="Unavailable: no point-in-time earnings-calendar announcement data."),
    ]


FEATURES: dict[str, FeatureDefinition] = {
    d.key: d
    for d in (
        _trend_definitions() + _mean_reversion_definitions() + _volatility_definitions()
        + _volume_definitions() + _market_definitions() + _breadth_definitions()
        + _relative_definitions() + _sector_definitions() + _gap_definitions()
        + _calendar_definitions() + _event_definitions()
    )
}

AVAILABLE_FEATURES: list[str] = [k for k, d in FEATURES.items() if d.available]
DISCOVERY_FEATURES: list[str] = [
    k for k, d in FEATURES.items() if d.available and d.discovery_eligible
]


def availability_report(engine: str = "standard") -> dict[str, Any]:
    """Every declared feature with its integrity metadata, grouped.

    Explicitly includes the unavailable ones: "we did not compute this and
    here is exactly why" is part of the result, not an omission.
    """
    rows = [d.to_dict() for d in FEATURES.values() if engine in d.engines]
    return {
        "engine": engine,
        "groups": FEATURE_GROUPS,
        "features": rows,
        "availableCount": sum(1 for r in rows if r["available"]),
        "unavailableCount": sum(1 for r in rows if not r["available"]),
        "discoveryEligibleCount": sum(
            1 for r in rows if r["available"] and r["discoveryEligible"]
        ),
        "warmupTradingDays": FEATURE_WARMUP_TRADING_DAYS,
    }


# ---------------------------------------------------------------------------
# Primitive right-aligned helpers.
#
# Every one of these is a rolling/recursive operation whose value at position
# i is a function of positions <= i only. Keeping them in one block makes the
# causality property auditable at a glance instead of scattered through the
# builders below.
# ---------------------------------------------------------------------------


def _pct_change(series: pd.Series, periods: int) -> pd.Series:
    return (series / series.shift(periods) - 1.0) * 100.0


def _log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1))


def _realized_vol(close: pd.Series, window: int) -> pd.Series:
    return _log_returns(close).rolling(window).std(ddof=1) * np.sqrt(252) * 100.0


def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Rank of the CURRENT value within its own trailing `window` values.

    `rank(pct=True)` on the whole series would be a whole-sample rank -- the
    classic look-ahead in a "percentile" feature. This is a trailing rank
    that includes the current observation and nothing after it.
    """
    return series.rolling(window).apply(
        lambda w: (w <= w[-1]).sum() / len(w) * 100.0, raw=True
    )


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    """OLS slope of `series` against 0..window-1 over a trailing window."""
    x = np.arange(window, dtype=float)
    x_centered = x - x.mean()
    denom = float((x_centered ** 2).sum())
    if denom == 0:
        return pd.Series(np.nan, index=series.index)
    return series.rolling(window).apply(
        lambda w: float(np.dot(w - w.mean(), x_centered) / denom), raw=True
    )


def _signed_run_length(close: pd.Series) -> pd.Series:
    """Signed length of the current same-direction close-to-close run."""
    direction = np.sign(close.diff().fillna(0.0)).to_numpy()
    out = np.zeros(len(direction), dtype=float)
    run = 0.0
    for i, d in enumerate(direction):
        if d == 0:
            run = 0.0
        elif run != 0 and np.sign(run) == d:
            run += d
        else:
            run = d
        out[i] = run
    return pd.Series(out, index=close.index)


def _rolling_beta(symbol_returns: pd.Series, market_returns: pd.Series, window: int) -> pd.Series:
    """Trailing-window beta of symbol on market. Right-aligned by construction."""
    cov = symbol_returns.rolling(window).cov(market_returns)
    var = market_returns.rolling(window).var()
    return cov / var.where(var != 0)


def _sessions_to_period_end(index: pd.DatetimeIndex, freq: str) -> pd.Series:
    """Trading sessions remaining in each bar's own month/quarter.

    Counted over the SESSIONS PRESENT IN THE INDEX, so it reflects the real
    trading calendar rather than calendar arithmetic. This deliberately uses
    the period the bar itself belongs to, which is known at the bar -- the
    number of remaining sessions in a month is a calendar fact, not a
    forecast, since exchange holiday schedules are published years ahead.
    """
    period = index.to_period(freq)
    frame = pd.DataFrame({"period": period}, index=index)
    frame["ordinal"] = np.arange(len(frame))
    last = frame.groupby("period")["ordinal"].transform("max")
    return (last - frame["ordinal"]).astype(float)


# ---------------------------------------------------------------------------
# Shared (market / universe / sector) context, computed once per study.
# ---------------------------------------------------------------------------


@dataclass
class MarketContext:
    """Everything that does not depend on WHICH symbol is being examined.

    Built once per discovery run and reused for every observation. The panel
    frames are aligned on a shared session index and forward-filled ONLY
    forward (a symbol with no bar today carries yesterday's value, never
    tomorrow's).
    """

    benchmark: pd.DataFrame
    market: pd.DataFrame
    breadth: pd.DataFrame
    universe_symbols: list[str]
    close_panel: pd.DataFrame
    sector_closes: pd.DataFrame
    sector_returns: pd.DataFrame
    sector_rank: pd.DataFrame
    xs_percentiles: dict[str, pd.DataFrame]
    warnings: list[str]


def _load_panel(
    symbols: Sequence[str], fetch_start: date, end: date
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Close and volume panels for `symbols`, plus any symbols that failed."""
    closes: dict[str, pd.Series] = {}
    volumes: dict[str, pd.Series] = {}
    missing: list[str] = []
    for symbol in symbols:
        try:
            bars = data_module.get_bars(symbol, "1d", fetch_start, end)
        except Exception:  # noqa: BLE001 -- a missing symbol withholds columns, not the run
            bars = pd.DataFrame()
        if bars.empty:
            missing.append(symbol)
            continue
        closes[symbol] = bars["Close"]
        volumes[symbol] = bars["Volume"]
    close_panel = pd.DataFrame(closes).sort_index() if closes else pd.DataFrame()
    volume_panel = pd.DataFrame(volumes).sort_index() if volumes else pd.DataFrame()
    # ffill only: a stale price is yesterday's information, a bfilled one is
    # tomorrow's. Never bfill here.
    return close_panel.ffill(), volume_panel.ffill(), missing


def _market_features(benchmark: pd.DataFrame) -> pd.DataFrame:
    close = benchmark["Close"]
    out = pd.DataFrame(index=benchmark.index)
    # Object dtype, not bool: an unwarmed SMA means the flag is UNKNOWN, and a
    # bool column has no way to say that -- it would silently read as False,
    # i.e. "market below its 200-day average", on every bar before the average
    # exists. Unknown must stay unknown so the missing-value policy drops the
    # observation instead of scoring it.
    for period, key in ((50, "mkt_above_sma50"), (200, "mkt_above_sma200")):
        average = sma(close, period)
        out[key] = (close > average).astype(object).where(average.notna(), other=None)
    for n in (20, 60, 120, 252):
        out[f"mkt_ret_{n}d"] = _pct_change(close, n)
    out["mkt_vol_20d"] = _realized_vol(close, 20)
    out["mkt_vol_percentile_252"] = _rolling_percentile(out["mkt_vol_20d"], 252)
    out["mkt_drawdown_252"] = (close / close.rolling(252).max() - 1.0) * 100.0
    out["mkt_regime"] = regime_module.regime_series(benchmark)
    return out


def _breadth_features(close_panel: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=close_panel.index)
    if close_panel.empty:
        return out
    valid = close_panel.notna()
    members = valid.sum(axis=1).replace(0, np.nan)
    for n in (20, 50, 200):
        above = close_panel > close_panel.rolling(n).mean()
        warm = close_panel.rolling(n).mean().notna()
        out[f"breadth_above_sma{n}"] = (above & warm).sum(axis=1) / warm.sum(axis=1).replace(0, np.nan) * 100.0
    for n in (20, 60):
        rets = close_panel / close_panel.shift(n) - 1.0
        out[f"breadth_pos_ret_{n}d"] = (rets > 0).sum(axis=1) / rets.notna().sum(axis=1).replace(0, np.nan) * 100.0
    daily = close_panel.pct_change()
    net = ((daily > 0).sum(axis=1) - (daily < 0).sum(axis=1)) / members
    out["breadth_advance_decline_10d"] = net.rolling(10).mean() * 100.0
    out["breadth_momentum"] = (
        out["breadth_above_sma50"] - out["breadth_above_sma50"].rolling(20).mean()
    )
    return out


def _cross_sectional_percentiles(close_panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Per-date percentile ranks across the universe.

    Ranked ACROSS COLUMNS on each row -- a cross-sectional rank on a single
    date uses no other date's data, so it is point-in-time by construction.
    """
    if close_panel.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    ret120 = close_panel / close_panel.shift(120) - 1.0
    ret20 = close_panel / close_panel.shift(20) - 1.0
    vol20 = np.log(close_panel / close_panel.shift(1)).rolling(20).std(ddof=1)
    out["xs_momentum_percentile_120d"] = ret120.rank(axis=1, pct=True) * 100.0
    out["xs_ret_percentile_20d"] = ret20.rank(axis=1, pct=True) * 100.0
    out["xs_vol_percentile"] = vol20.rank(axis=1, pct=True) * 100.0
    dispersion = ret20.std(axis=1, ddof=1) * 100.0
    out["xs_dispersion_20d"] = pd.DataFrame(
        {c: dispersion for c in close_panel.columns}, index=close_panel.index
    )
    return out


def build_market_context(
    universe: Sequence[str], start: date, end: date
) -> MarketContext:
    """Load and compute every shared series a discovery run needs.

    `start`/`end` bound the STUDY window; history is fetched from
    `FEATURE_WARMUP_TRADING_DAYS` sessions before `start` so a feature is
    warm on the window's first bar rather than NaN for its first year.
    """
    fetch_start = start - timedelta(
        days=int(FEATURE_WARMUP_TRADING_DAYS * TRADING_TO_CALENDAR) + 10
    )
    warnings: list[str] = []
    benchmark = data_module.get_bars(BENCHMARK, "1d", fetch_start, end)
    if benchmark.empty:
        raise ValueError(
            f"No {BENCHMARK} bars for {fetch_start}..{end}; market-regime, breadth and "
            "relative features cannot be computed without a benchmark."
        )
    close_panel, _volume_panel, missing = _load_panel(universe, fetch_start, end)
    if missing:
        warnings.append(
            f"{len(missing)} universe symbol(s) had no bars and are excluded from breadth "
            f"and cross-sectional features: {', '.join(sorted(missing))}"
        )
    sector_panel, _sv, sector_missing = _load_panel(SECTOR_ETFS, fetch_start, end)
    if sector_missing:
        warnings.append(
            f"{len(sector_missing)} sector ETF(s) unavailable; sector features degraded: "
            f"{', '.join(sorted(sector_missing))}"
        )
    sector_returns = (
        (sector_panel / sector_panel.shift(60) - 1.0) * 100.0
        if not sector_panel.empty else pd.DataFrame()
    )
    # rank(axis=1) is a per-date cross-sectional rank: no other date's data
    # enters it, so it stays point-in-time even though the CLASSIFICATION
    # that maps a symbol to one of these columns is not (see _SECTOR_NOTE).
    sector_rank = (
        sector_returns.rank(axis=1, ascending=False) if not sector_returns.empty
        else pd.DataFrame()
    )
    return MarketContext(
        benchmark=benchmark,
        market=_market_features(benchmark),
        breadth=_breadth_features(close_panel),
        universe_symbols=list(close_panel.columns),
        close_panel=close_panel,
        sector_closes=sector_panel,
        sector_returns=sector_returns,
        sector_rank=sector_rank,
        xs_percentiles=_cross_sectional_percentiles(close_panel),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Per-symbol feature frame.
# ---------------------------------------------------------------------------


def _symbol_price_features(bars: pd.DataFrame) -> pd.DataFrame:
    close, high, low, open_, volume = (
        bars["Close"], bars["High"], bars["Low"], bars["Open"], bars["Volume"]
    )
    out = pd.DataFrame(index=bars.index)

    for n in (5, 10, 20, 60, 120, 189, 252):
        out[f"ret_{n}d"] = _pct_change(close, n)
    for n in (20, 50, 100, 200):
        out[f"dist_sma{n}"] = (close / sma(close, n) - 1.0) * 100.0
    out["sma20_vs_sma50"] = (sma(close, 20) / sma(close, 50) - 1.0) * 100.0
    out["sma50_vs_sma200"] = (sma(close, 50) / sma(close, 200) - 1.0) * 100.0
    out["dist_52w_high"] = (close / close.rolling(252).max() - 1.0) * 100.0
    out["dist_52w_low"] = (close / close.rolling(252).min() - 1.0) * 100.0
    out["drawdown_60d"] = (close / close.rolling(60).max() - 1.0) * 100.0
    out["trend_slope_60d"] = _rolling_slope(np.log(close), 60) * 100.0

    for n in (2, 5, 14):
        out[f"rsi{n}"] = rsi(close, n)
    bar_range = (high - low).replace(0, np.nan)
    out["ibs"] = (close - low) / bar_range
    daily_ret = close.pct_change()
    rolling_std = daily_ret.rolling(20).std(ddof=1)
    out["ret_zscore_20d"] = (
        (daily_ret - daily_ret.rolling(20).mean()) / rolling_std.where(rolling_std > 0)
    )
    out["consecutive_days"] = _signed_run_length(close)
    out["cum_ret_3d"] = _pct_change(close, 3)
    out["dist_sma5"] = (close / sma(close, 5) - 1.0) * 100.0

    atr14 = atr(bars, 14)
    out["atr14"] = atr14
    out["atr_pct"] = atr14 / close.where(close > 0) * 100.0
    for n in (10, 20, 60):
        out[f"realized_vol_{n}d"] = _realized_vol(close, n)
    out["vol_percentile_252"] = _rolling_percentile(out["realized_vol_20d"], 252)
    out["vol_expansion"] = out["realized_vol_10d"] / out["realized_vol_60d"].where(
        out["realized_vol_60d"] > 0
    )
    out["range_vs_atr"] = (high - low) / atr14.where(atr14 > 0)

    vol_sma20 = volume.rolling(20).mean()
    out["rel_volume_20"] = volume / vol_sma20.where(vol_sma20 > 0)
    out["volume_percentile_252"] = _rolling_percentile(volume, 252)
    vol_std60 = volume.rolling(60).std(ddof=1)
    out["volume_shock_z"] = (
        (volume - volume.rolling(60).mean()) / vol_std60.where(vol_std60 > 0)
    )
    dollar_volume = close * volume
    out["dollar_volume"] = dollar_volume
    out["dollar_volume_percentile_252"] = _rolling_percentile(dollar_volume, 252)
    vol_sma60 = volume.rolling(60).mean()
    out["volume_regime_change"] = vol_sma20 / vol_sma60.where(vol_sma60 > 0)

    prev_close = close.shift(1)
    gap = (open_ / prev_close.where(prev_close > 0) - 1.0) * 100.0
    out["gap_pct"] = gap
    out["gap_vs_atr"] = (open_ - prev_close) / atr14.where(atr14 > 0)
    out["gap_percentile_252"] = _rolling_percentile(gap, 252)
    trend20 = out["ret_20d"]
    alignment = pd.Series("flat", index=bars.index, dtype=object)
    alignment[(gap > 0) & (trend20 > 0)] = "aligned"
    alignment[(gap < 0) & (trend20 < 0)] = "aligned"
    alignment[(gap > 0) & (trend20 < 0)] = "opposed"
    alignment[(gap < 0) & (trend20 > 0)] = "opposed"
    alignment[gap.isna() | trend20.isna()] = None
    out["gap_alignment"] = alignment

    out["day_of_week"] = pd.Series(
        pd.DatetimeIndex(bars.index).day_name().str[:3], index=bars.index
    )
    out["month"] = pd.Series(
        pd.DatetimeIndex(bars.index).month_name().str[:3], index=bars.index
    )
    out["days_to_month_end"] = _sessions_to_period_end(pd.DatetimeIndex(bars.index), "M")
    out["days_to_quarter_end"] = _sessions_to_period_end(pd.DatetimeIndex(bars.index), "Q")
    return out


def _earnings_features(symbol: str, bars: pd.DataFrame) -> pd.DataFrame:
    """Days since the last REPORTED earnings, and that report's gap.

    Only past reports are ever consulted (`searchsorted` on a sorted array of
    report timestamps, taking strictly-at-or-before). The forward-looking
    counterpart is declared unavailable -- see `days_until_earnings`.
    """
    out = pd.DataFrame(index=bars.index)
    out["days_since_earnings"] = float(NO_EARNINGS_SENTINEL)
    out["last_earnings_gap_pct"] = np.nan
    out["post_earnings_5d"] = False
    try:
        frame = data_module.earnings_dates(symbol)
    except Exception:  # noqa: BLE001 -- no earnings feed degrades the feature, not the run
        frame = pd.DataFrame()
    if frame is None or frame.empty:
        return out

    index = pd.DatetimeIndex(bars.index)
    events = pd.DatetimeIndex(sorted(pd.DatetimeIndex(frame.index)))
    if index.tz is not None and events.tz is None:
        events = events.tz_localize(index.tz)
    elif index.tz is None and events.tz is not None:
        events = events.tz_convert(None)
    elif index.tz is not None and events.tz is not None:
        events = events.tz_convert(index.tz)

    # side="right" -> strictly at-or-before the bar. A report stamped later
    # the same day would still be "the future" for a bar decided at the
    # close; the -1 index makes that impossible to select.
    positions = np.searchsorted(events.to_numpy(), index.to_numpy(), side="right") - 1
    prior = np.where(positions >= 0, positions, -1)

    gap = (bars["Open"] / bars["Close"].shift(1) - 1.0) * 100.0
    # Reaction gap for each event: the first bar at or after the event.
    event_positions = np.searchsorted(index.to_numpy(), events.to_numpy(), side="left")
    event_gaps = np.full(len(events), np.nan)
    inside = event_positions < len(index)
    event_gaps[inside] = gap.to_numpy()[event_positions[inside]]

    days_since = np.full(len(index), float(NO_EARNINGS_SENTINEL))
    last_gap = np.full(len(index), np.nan)
    has_prior = prior >= 0
    if has_prior.any():
        event_values = events.to_numpy()[prior[has_prior]]
        delta = (index.to_numpy()[has_prior] - event_values) / np.timedelta64(1, "D")
        days_since[has_prior] = delta.astype(float)
        last_gap[has_prior] = event_gaps[prior[has_prior]]
    out["days_since_earnings"] = days_since
    out["last_earnings_gap_pct"] = last_gap
    out["post_earnings_5d"] = days_since <= 7.0
    return out


def _relative_features(
    bars: pd.DataFrame, symbol: str, context: MarketContext
) -> pd.DataFrame:
    """Market- and sector-relative features, aligned onto the symbol's index.

    Alignment is `reindex(..., method="ffill")`: on a session the benchmark
    did not trade, the symbol carries the benchmark's LAST KNOWN value. Never
    `bfill`, which would carry a value from the future.
    """
    out = pd.DataFrame(index=bars.index)
    close = bars["Close"]
    bench_close = context.benchmark["Close"].reindex(bars.index, method="ffill")

    symbol_ret60 = _pct_change(close, 60)
    out["rs_vs_market_60d"] = symbol_ret60 - _pct_change(bench_close, 60)

    symbol_daily = close.pct_change()
    bench_daily = bench_close.pct_change()
    beta = _rolling_beta(symbol_daily, bench_daily, 252)
    out["residual_ret_20d"] = _pct_change(close, 20) - beta * _pct_change(bench_close, 20)

    for key, frame in context.xs_percentiles.items():
        if symbol in frame.columns:
            out[key] = frame[symbol].reindex(bars.index, method="ffill")
        else:
            out[key] = np.nan

    etf = SECTOR_ETF_BY_SYMBOL.get(symbol)
    if etf and etf in context.sector_closes.columns:
        sector_close = context.sector_closes[etf].reindex(bars.index, method="ffill")
        sector_ret60 = _pct_change(sector_close, 60)
        out["sector_ret_60d"] = sector_ret60
        out["sector_rank_60d"] = (
            context.sector_rank[etf].reindex(bars.index, method="ffill")
            if etf in context.sector_rank.columns else np.nan
        )
        out["rs_vs_sector_60d"] = symbol_ret60 - sector_ret60
        # Two-factor residual: subtract the benchmark leg and the sector leg,
        # each with its own trailing-252-session beta. Both betas are
        # right-aligned, so the value at bar i uses bars <= i only.
        sector_daily = sector_close.pct_change()
        sector_beta = _rolling_beta(symbol_daily, sector_daily, 252)
        out["residual_ret_20d_sector"] = (
            _pct_change(close, 20)
            - beta * _pct_change(bench_close, 20)
            - sector_beta * _pct_change(sector_close, 20)
        )
    else:
        for key in ("sector_ret_60d", "sector_rank_60d", "rs_vs_sector_60d",
                    "residual_ret_20d_sector"):
            out[key] = np.nan
    return out


#: Cache of computed feature frames. Safe because every value is
#: right-aligned: the row for bar i is identical whether the frame was
#: computed over a short window or a long one, so a cache hit can never
#: introduce information from beyond bar i. The key still carries the window
#: so a differently-warmed frame is never silently reused for a run whose
#: warmup requirement it does not meet.
_FEATURE_CACHE: dict[tuple, pd.DataFrame] = {}


def clear_cache() -> None:
    _FEATURE_CACHE.clear()


def symbol_features(
    symbol: str, context: MarketContext, start: date, end: date
) -> pd.DataFrame:
    """Full feature frame for `symbol`, indexed by bar timestamp.

    Includes the warmup prefix, so a lookup for the study window's first bar
    resolves to a warm value rather than NaN. Callers slice by timestamp,
    never by position.
    """
    key = (symbol, start, end, id(context))
    cached = _FEATURE_CACHE.get(key)
    if cached is not None:
        return cached
    fetch_start = start - timedelta(
        days=int(FEATURE_WARMUP_TRADING_DAYS * TRADING_TO_CALENDAR) + 10
    )
    bars = data_module.get_bars(symbol, "1d", fetch_start, end)
    frame = features_from_bars(bars, symbol, context)
    _FEATURE_CACHE[key] = frame
    return frame


def features_from_bars(
    bars: pd.DataFrame, symbol: str, context: MarketContext
) -> pd.DataFrame:
    """Compute every feature for `bars` -- the injectable core of
    `symbol_features`, so tests can drive it with synthetic data."""
    if bars.empty:
        return pd.DataFrame(columns=list(FEATURES))
    frame = _symbol_price_features(bars)
    frame = frame.join(_relative_features(bars, symbol, context))
    frame = frame.join(_earnings_features(symbol, bars))
    market = context.market.reindex(bars.index, method="ffill")
    frame = frame.join(market)
    breadth = context.breadth.reindex(bars.index, method="ffill")
    frame = frame.join(breadth)
    for key, definition in FEATURES.items():
        if not definition.available:
            continue
        if key not in frame.columns:
            frame[key] = np.nan
    return frame[[k for k in FEATURES if FEATURES[k].available and k in frame.columns]]


def snapshot_at(frame: pd.DataFrame, timestamp: pd.Timestamp) -> dict[str, Any]:
    """The feature row at or before `timestamp`.

    "At or before" rather than exact-match so a decision bar stamped
    slightly differently from the feature index (a holiday-shifted session,
    a tz-normalisation difference) resolves BACKWARDS. Resolving forwards
    would hand the observation a bar it could not have seen -- the single
    most likely place for leakage to enter this module, so it is asserted in
    tests rather than left to convention.
    """
    if frame.empty:
        return {}
    index = frame.index
    position = index.searchsorted(timestamp, side="right") - 1
    if position < 0:
        return {}
    row = frame.iloc[position]
    return {
        key: (None if _is_missing(value) else _native(value))
        for key, value in row.items()
    }


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _native(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return str(value)
