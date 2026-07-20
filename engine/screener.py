"""Live cross-sectional stock screener: valuation/quality/growth-momentum/
risk composite scores across engine/universe.py:RESEARCH_UNIVERSE, plus
analyst-consensus columns straight from engine/fundamentals.py.

This is a LIVE research tool, not a backtest input -- every score describes
today's state only (today's snapshot fundamentals + a trailing price
window), is never cached across days, and never feeds a strategy or a
backtest. See engine/fundamentals.py's own point-in-time/snapshot split for
why that boundary matters for backtesting specifically; it doesn't apply
here because nothing here is replayed against history.

Every score is 0-100 (a cross-sectional percentile rank among the other
symbols with a real value for that field -- a missing field excludes a
symbol from that one ranking rather than defaulting it to a misleading
middle score). The composite is a plain average of the four factor scores,
disclosed as such in SCORE_METHODOLOGY -- not a proprietary or validated
multi-factor model. Descriptive only: presented as "Score: NN/100", never
as a buy/sell signal -- see CLAUDE.md's non-goals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module
from engine import fundamentals
from engine.universe import RESEARCH_UNIVERSE

MOMENTUM_LOOKBACK_DAYS = 182  # ~6 months
VOLATILITY_LOOKBACK_DAYS = 90
TRADING_DAYS_PER_YEAR = 252

SCORE_METHODOLOGY = (
    "Composite is a plain average of the four factor scores below, each a "
    "cross-sectional percentile rank (0-100) among tracked symbols with "
    "data for that field -- not a validated multi-factor model. Descriptive "
    "statistics only: this does not tell you what to buy or sell."
)


def _price_history(symbol: str, lookback_days: int) -> pd.Series:
    end = date.today()
    start = end - timedelta(days=lookback_days + 30)  # pad for weekends/holidays
    bars = data_module.get_bars(symbol, "1d", start, end)
    if bars.empty:
        return pd.Series(dtype=float)
    return bars["Close"]


def _windowed(closes: pd.Series, lookback_days: int) -> pd.Series:
    if closes.empty:
        return closes
    cutoff = closes.index.max() - pd.Timedelta(days=lookback_days)
    return closes.loc[closes.index >= cutoff]


def _momentum_pct(closes: pd.Series, lookback_days: int) -> float | None:
    window = _windowed(closes, lookback_days)
    if len(window) < 2:
        return None
    first, last = float(window.iloc[0]), float(window.iloc[-1])
    return None if first <= 0 else (last / first - 1) * 100


def _annualized_volatility_pct(closes: pd.Series, lookback_days: int) -> float | None:
    window = _windowed(closes, lookback_days)
    if len(window) < 5:
        return None
    returns = window.pct_change().dropna()
    if returns.empty:
        return None
    return float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100)


def _max_drawdown_pct(closes: pd.Series, lookback_days: int) -> float | None:
    window = _windowed(closes, lookback_days)
    if window.empty:
        return None
    running_max = window.cummax()
    drawdown = (window / running_max - 1) * 100
    return float(drawdown.min())


@dataclass
class ScreenerRow:
    symbol: str
    price: float | None
    trailing_pe: float | None
    profit_margins_pct: float | None
    return_on_equity_pct: float | None
    debt_to_equity: float | None
    momentum_6m_pct: float | None
    eps_growth_yoy_pct: float | None
    revenue_growth_yoy_pct: float | None
    volatility_pct: float | None
    max_drawdown_pct: float | None
    analyst_rating: float | None
    analyst_target_price: float | None
    upside_pct: float | None
    market_cap: float | None


def _build_row(symbol: str) -> ScreenerRow:
    snap = fundamentals.snapshot(symbol)
    closes = _price_history(symbol, max(MOMENTUM_LOOKBACK_DAYS, VOLATILITY_LOOKBACK_DAYS))
    price = snap.current_price if snap.current_price is not None else (
        float(closes.iloc[-1]) if len(closes) else None
    )
    upside = None
    if snap.analyst_target_price is not None and price:
        upside = (snap.analyst_target_price / price - 1) * 100
    return ScreenerRow(
        symbol=symbol,
        price=price,
        trailing_pe=snap.trailing_pe,
        profit_margins_pct=snap.profit_margins_pct,
        return_on_equity_pct=snap.return_on_equity_pct,
        debt_to_equity=snap.debt_to_equity,
        momentum_6m_pct=_momentum_pct(closes, MOMENTUM_LOOKBACK_DAYS),
        eps_growth_yoy_pct=snap.eps_growth_yoy_pct,
        revenue_growth_yoy_pct=snap.revenue_growth_yoy_pct,
        volatility_pct=_annualized_volatility_pct(closes, VOLATILITY_LOOKBACK_DAYS),
        max_drawdown_pct=_max_drawdown_pct(closes, VOLATILITY_LOOKBACK_DAYS),
        analyst_rating=snap.analyst_rating,
        analyst_target_price=snap.analyst_target_price,
        upside_pct=upside,
        market_cap=snap.market_cap,
    )


def _percentile_rank(
    values: dict[str, float | None], higher_is_better: bool
) -> dict[str, float | None]:
    """0-100 percentile rank of each symbol's value among the others that
    have a real value (None/NaN stays None -- excluded from ranking rather
    than defaulted to a middling score that would misrepresent missing
    data)."""
    valid = {
        k: v for k, v in values.items()
        if v is not None and not (isinstance(v, float) and np.isnan(v))
    }
    if not valid:
        return {k: None for k in values}
    ranks = pd.Series(valid).rank(pct=True) * 100
    if not higher_is_better:
        ranks = 100 - ranks
    return {k: (float(ranks[k]) if k in ranks.index else None) for k in values}


def _avg(components: list[dict[str, float | None]], symbol: str) -> float | None:
    vals = [c[symbol] for c in components if c.get(symbol) is not None]
    return float(np.mean(vals)) if vals else None


def build_screener(symbols: list[str] | None = None) -> dict[str, Any]:
    """Screener rows + composite scores for `symbols` (defaults to the full
    RESEARCH_UNIVERSE). Every ranking is computed cross-sectionally within
    this exact call's symbol set, so a filtered subset (e.g. one sector) is
    ranked against itself, not silently against the whole universe."""
    tickers = symbols if symbols is not None else RESEARCH_UNIVERSE
    rows = {s: _build_row(s) for s in tickers}

    valuation_rank = _percentile_rank(
        {s: r.trailing_pe for s, r in rows.items()}, higher_is_better=False
    )
    quality_components = [
        _percentile_rank({s: r.profit_margins_pct for s, r in rows.items()}, higher_is_better=True),
        _percentile_rank({s: r.return_on_equity_pct for s, r in rows.items()}, higher_is_better=True),
        _percentile_rank({s: r.debt_to_equity for s, r in rows.items()}, higher_is_better=False),
    ]
    growth_components = [
        _percentile_rank({s: r.momentum_6m_pct for s, r in rows.items()}, higher_is_better=True),
        _percentile_rank({s: r.eps_growth_yoy_pct for s, r in rows.items()}, higher_is_better=True),
        _percentile_rank({s: r.revenue_growth_yoy_pct for s, r in rows.items()}, higher_is_better=True),
    ]
    # Risk: higher volatility AND bigger (more negative) drawdown both mean
    # MORE risk, so both rank toward a higher riskScore -- this is a risk
    # gauge, not a safety gauge; higher never reads as "good" in the UI.
    risk_components = [
        _percentile_rank({s: r.volatility_pct for s, r in rows.items()}, higher_is_better=True),
        _percentile_rank({s: r.max_drawdown_pct for s, r in rows.items()}, higher_is_better=False),
    ]

    out_rows = []
    for s, r in rows.items():
        quality = _avg(quality_components, s)
        growth = _avg(growth_components, s)
        risk = _avg(risk_components, s)
        valuation = valuation_rank.get(s)
        composite_parts = [v for v in [valuation, quality, growth, risk] if v is not None]
        composite = float(np.mean(composite_parts)) if composite_parts else None
        out_rows.append({
            "symbol": s,
            "price": r.price,
            "compositeScore": composite,
            "valuationScore": valuation,
            "qualityScore": quality,
            "growthMomentumScore": growth,
            "riskScore": risk,
            "trailingPe": r.trailing_pe,
            "profitMarginsPct": r.profit_margins_pct,
            "returnOnEquityPct": r.return_on_equity_pct,
            "debtToEquity": r.debt_to_equity,
            "momentum6mPct": r.momentum_6m_pct,
            "volatilityPct": r.volatility_pct,
            "maxDrawdownPct": r.max_drawdown_pct,
            "analystRating": r.analyst_rating,
            "analystTargetPrice": r.analyst_target_price,
            "upsidePct": r.upside_pct,
            "marketCap": r.market_cap,
        })

    out_rows.sort(key=lambda row: (row["compositeScore"] is None, -(row["compositeScore"] or 0)))
    return {
        "asOf": date.today().isoformat(),
        "methodology": SCORE_METHODOLOGY,
        "rows": out_rows,
    }
