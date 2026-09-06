"""Adapters and scoring that sit between the backtest engine and
`engine/prop_account.py`'s rule simulator.

Split from `prop_account.py` on purpose: that module knows only about
return series and firm rules and has no idea what a Strategy is, which is
what lets it consume ANY existing or future strategy with no
strategy-specific code. This module is the only place that knows about
`StrategyBacktestResult`.

**Where the daily series comes from, and why not from the archive.**
`logging_db.research_equity_curves` looks like the obvious source and is
the wrong one. Measured 2026-08-22: its stored curves are stale and sparse
-- Internal Bar Strength held 94 daily points spanning a year (37% of
sessions, gaps to 15 days) and Connors RSI2 held 615 over four years (~59%,
gaps to 24 days), because what got archived were older, narrower validation
runs. Re-running the SAME strategies through the existing engine produced
1,255 contiguous daily points each, 96.2% of weekdays (the rest being
market holidays).

That gap matters more than it looks. A daily loss limit is evaluated every
session, so a series that silently skips sessions both understates how many
days are at risk and compresses multi-day moves into single observations --
a 24-day gap read as one "day" would wildly overstate daily tail risk. So
the adapter below takes a live result and refuses sparse input rather than
quietly simulating on it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from engine.backtest import portfolio_equity_curve
from engine.prop_account import TRADING_DAYS_PER_YEAR

#: Below this fraction of covered sessions the series is not a daily account
#: curve and a daily-limit probability computed from it would be fiction.
MIN_SESSION_COVERAGE = 0.80

#: Fewer daily observations than this and the bootstrap is resampling too
#: little history for a 12-month failure estimate to mean anything.
MIN_DAILY_OBSERVATIONS = 252


class InsufficientPropData(Exception):
    """Raised instead of returning a number that cannot be justified."""


@dataclass(frozen=True)
class DailySeries:
    """A validated daily return series plus the provenance to defend it."""

    returns: pd.Series
    start: pd.Timestamp
    end: pd.Timestamp
    observations: int
    session_coverage: float
    source: str

    def to_dict(self) -> dict:
        return {
            "start": self.start.date().isoformat(),
            "end": self.end.date().isoformat(),
            "observations": self.observations,
            "sessionCoverage": self.session_coverage,
            "source": self.source,
        }


def daily_returns_from_equity(equity: pd.Series, source: str) -> DailySeries:
    """Daily fractional returns from an equity curve, with coverage checks.

    Resamples to one point per calendar day (last value) before differencing
    so an intraday curve becomes a genuine DAILY series rather than a series
    of intraday steps -- otherwise "worst day" would really be "worst bar".
    """
    if equity is None or len(equity) < 2:
        raise InsufficientPropData("No equity curve available.")
    series = pd.to_numeric(equity, errors="coerce").dropna().sort_index()
    if series.empty or (series <= 0).any():
        raise InsufficientPropData("Equity curve is empty or non-positive.")

    daily = series.groupby(series.index.normalize()).last().dropna()
    if len(daily) < 2:
        raise InsufficientPropData("Fewer than two daily observations.")

    idx = pd.DatetimeIndex(daily.index)
    weekdays = len(pd.bdate_range(idx[0], idx[-1]))
    coverage = len(daily) / weekdays if weekdays else 0.0
    if coverage < MIN_SESSION_COVERAGE:
        raise InsufficientPropData(
            f"Daily curve covers only {coverage:.0%} of sessions between "
            f"{idx[0].date()} and {idx[-1].date()}; a daily loss limit cannot "
            "be evaluated honestly on a series that skips sessions."
        )
    if len(daily) < MIN_DAILY_OBSERVATIONS:
        raise InsufficientPropData(
            f"Only {len(daily)} daily observations (need "
            f"{MIN_DAILY_OBSERVATIONS} for a 12-month estimate)."
        )

    returns = daily.pct_change().dropna()
    return DailySeries(
        returns=returns,
        start=idx[0],
        end=idx[-1],
        observations=len(returns),
        session_coverage=coverage,
        source=source,
    )


def daily_returns_from_result(result: Any) -> DailySeries:
    """Extract a daily return series from any existing backtest result.

    Handles both engine shapes without strategy-specific code: the
    per-symbol engine's `StrategyBacktestResult` (pooled into one
    equal-weight portfolio curve by the existing
    `backtest.portfolio_equity_curve`, so the prop view matches how the rest
    of the app already computes portfolio Sharpe) and the cross-sectional /
    pairs engines' `equity_curve` attribute.

    Never mutates the result it is handed.
    """
    equity = getattr(result, "equity_curve", None)
    if equity is not None and not isinstance(equity, pd.Series):
        equity = None
    if equity is None:
        per_symbol = getattr(result, "per_symbol", None)
        if not per_symbol:
            raise InsufficientPropData(
                "Result exposes neither an equity_curve nor per-symbol results."
            )
        equity = portfolio_equity_curve(per_symbol)
        source = "portfolio_equity_curve(per_symbol)"
    else:
        source = "result.equity_curve"
    return daily_returns_from_equity(equity, source)


@dataclass
class ExposureProfile:
    """How much capital a strategy actually deploys, and how often.

    Exists because the account-level denominator is otherwise an engine
    accounting artifact. The per-symbol engine gives each of N sleeves
    `DEFAULT_CASH`, so a 29-symbol strategy is measured against $290,000 of
    notional cash whether it deploys $2,000 or $200,000 -- and a strategy
    sitting in cash most of the time then posts a tiny volatility and a
    spectacular Sharpe that says nothing about the idle remainder.
    `engine/exposure_diagnostic.py` documents that failure mode for the edge
    metrics; these fields are what let the prop layer avoid inheriting it.
    """

    peak_gross_notional: float
    avg_gross_notional: float
    median_gross_notional: float
    avg_net_notional: float
    max_concurrent_positions: int
    avg_concurrent_positions: float
    days_with_exposure_pct: float
    avg_utilization_while_active_pct: float
    annual_turnover_pct: float
    capital_base: float
    basis: str
    #: Appended (never inserted): both construction sites below are
    #: positional, so a mid-dataclass insert would silently shift fields.
    p95_gross_notional: float = 0.0
    avg_gross_while_active: float = 0.0
    median_gross_while_active: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _trade_frame(result: Any) -> pd.DataFrame | None:
    """Pool every sleeve's trades. None for engines with no discrete trades
    (cross-sectional / pairs), which is a meaningful answer rather than a
    failure -- see `normalized_daily_series`."""
    per_symbol = getattr(result, "per_symbol", None)
    if not per_symbol:
        return None
    frames = [
        r.trades for r in per_symbol.values()
        if getattr(r, "trades", None) is not None and not r.trades.empty
    ]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def compute_exposure_profile(
    result: Any, daily_index: pd.DatetimeIndex, equity: pd.Series
) -> ExposureProfile:
    """Reconstruct daily gross/net notional from the actual positions.

    Gross notional per day sums |Size| x EntryPrice over positions OPEN that
    day -- entry notional, the same quantity `engine/backtest.py` already
    uses for its net-exposure direction calculation. That makes the capital
    base a property of the strategy's real positions rather than of the
    engine's cash constant.
    """
    trades = _trade_frame(result)
    index = pd.DatetimeIndex(daily_index)
    if index.tz is not None:
        index = index.tz_localize(None)
    index = index.normalize()

    if trades is None or trades.empty:
        # A portfolio-allocation engine: capital IS the equity curve and the
        # strategy is invested by construction. Forcing a notional
        # reconstruction here would be mathematically wrong, not merely
        # inconvenient, so the genuine portfolio interpretation is kept.
        base = float(pd.to_numeric(equity, errors="coerce").dropna().iloc[0])
        return ExposureProfile(
            peak_gross_notional=base, avg_gross_notional=base,
            median_gross_notional=base, avg_net_notional=base,
            max_concurrent_positions=0, avg_concurrent_positions=0.0,
            days_with_exposure_pct=100.0,
            avg_utilization_while_active_pct=100.0,
            annual_turnover_pct=float("nan"), capital_base=base,
            basis="portfolio_equity",
            p95_gross_notional=base, avg_gross_while_active=base,
            median_gross_while_active=base,
        )

    gross = pd.Series(0.0, index=index)
    net = pd.Series(0.0, index=index)
    count = pd.Series(0, index=index, dtype=int)
    for _, trade in trades.iterrows():
        size = float(trade["Size"])
        notional = abs(size) * abs(float(trade["EntryPrice"]))
        entry = pd.Timestamp(trade["EntryTime"])
        entry = (entry.tz_localize(None) if entry.tz is not None else entry).normalize()
        raw_exit = trade.get("ExitTime")
        if pd.isna(raw_exit):
            exit_ = index[-1]
        else:
            exit_ = pd.Timestamp(raw_exit)
            exit_ = (exit_.tz_localize(None) if exit_.tz is not None else exit_).normalize()
        mask = (index >= entry) & (index <= exit_)
        gross[mask] += notional
        net[mask] += notional * np.sign(size)
        count[mask] += 1

    active = gross > 0
    years = max(len(index) / TRADING_DAYS_PER_YEAR, 1e-9)
    peak = float(gross.max())
    turnover = (
        float((trades["Size"].abs() * trades["EntryPrice"].abs()).sum() / peak / years * 100.0)
        if peak > 0 else float("nan")
    )
    return ExposureProfile(
        peak_gross_notional=peak,
        avg_gross_notional=float(gross.mean()),
        median_gross_notional=float(gross.median()),
        avg_net_notional=float(net.mean()),
        max_concurrent_positions=int(count.max()),
        avg_concurrent_positions=float(count.mean()),
        days_with_exposure_pct=float(active.mean()) * 100.0,
        avg_utilization_while_active_pct=(
            float(gross[active].mean() / peak) * 100.0
            if active.any() and peak > 0 else 0.0
        ),
        annual_turnover_pct=turnover,
        capital_base=peak,
        basis="peak_gross_notional",
        p95_gross_notional=float(np.percentile(gross, 95)),
        avg_gross_while_active=float(gross[active].mean()) if active.any() else 0.0,
        median_gross_while_active=float(gross[active].median()) if active.any() else 0.0,
    )


def normalized_daily_series(result: Any) -> tuple[DailySeries, ExposureProfile]:
    """Account-level daily returns on an economically meaningful denominator.

    **The sizing convention, and why this one.** 1.0x means the strategy is
    run at the size where its historical PEAK GROSS NOTIONAL equals the prop
    account's nominal size -- the account fully utilized at the strategy's
    own busiest moment. Peak gross notional is the deployable capital the
    strategy genuinely required; the engine's cash constant is not.

    One meaning of 1.0x across both engine types, without distorting either:

    - Per-symbol engines: daily dollar P&L (mark-to-market, from the pooled
      equity curve) divided by peak gross notional. The idle-cash sleeves
      drop out of the denominator entirely, which is the fix.
    - Portfolio-allocation engines: no discrete positions exist and the
      strategy is invested by construction, so the capital base IS its
      equity and this reduces to the native portfolio return -- unchanged,
      and correctly so.

    The risk multiplier then scales a properly-normalized base. It is not
    permitted to compensate for a wrong denominator, which is what it was
    silently doing before.
    """
    equity = getattr(result, "equity_curve", None)
    if equity is None or not isinstance(equity, pd.Series):
        per_symbol = getattr(result, "per_symbol", None)
        if not per_symbol:
            raise InsufficientPropData(
                "Result exposes neither an equity_curve nor per-symbol results."
            )
        equity = portfolio_equity_curve(per_symbol)
    if equity is None or len(equity) < 2:
        raise InsufficientPropData("No equity curve available.")

    series = pd.to_numeric(equity, errors="coerce").dropna().sort_index()
    daily = series.groupby(series.index.normalize()).last().dropna()
    if len(daily) < 2:
        raise InsufficientPropData("Fewer than two daily observations.")

    idx = pd.DatetimeIndex(daily.index)
    weekdays = len(pd.bdate_range(idx[0], idx[-1]))
    coverage = len(daily) / weekdays if weekdays else 0.0
    if coverage < MIN_SESSION_COVERAGE:
        raise InsufficientPropData(
            f"Daily curve covers only {coverage:.0%} of sessions between "
            f"{idx[0].date()} and {idx[-1].date()}; a daily loss limit cannot "
            "be evaluated honestly on a series that skips sessions."
        )
    if len(daily) < MIN_DAILY_OBSERVATIONS:
        raise InsufficientPropData(
            f"Only {len(daily)} daily observations (need {MIN_DAILY_OBSERVATIONS})."
        )

    profile = compute_exposure_profile(result, idx, daily)
    if profile.basis == "portfolio_equity":
        returns = daily.pct_change().dropna()
    else:
        if profile.capital_base <= 0:
            raise InsufficientPropData(
                "Strategy never held a position; no capital base to normalize on."
            )
        returns = (daily.diff() / profile.capital_base).dropna()

    return (
        DailySeries(
            returns=returns, start=idx[0], end=idx[-1], observations=len(returns),
            session_coverage=coverage, source=f"normalized:{profile.basis}",
        ),
        profile,
    )


def active_exposure_metrics(
    returns: pd.Series, profile: ExposureProfile
) -> dict[str, Any]:
    """Whole-account vs deployed-capital risk, reported side by side.

    A strategy in cash most of the time posts a small account-level
    volatility and therefore a large account-level Sharpe. That ratio
    describes the days it traded and is structurally silent about the rest,
    so low exposure must never read as an extraordinary edge on its own.

    Note `sharpeIsScaleInvariant`: rescaling the whole series does NOT move
    Sharpe. Normalization fixes the ECONOMIC MEANING of the return series --
    what a dollar of account risk buys -- and deliberately does not
    manufacture a different Sharpe. Anyone expecting normalization to have
    "fixed" a suspicious Sharpe is expecting the wrong thing; the honest
    reading of a high account-level Sharpe on low exposure is in the
    exposure columns, not the ratio.
    """
    values = returns.dropna()
    vol = float(values.std(ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)
    active = values[values != 0]
    utilization = (
        profile.avg_gross_notional / profile.peak_gross_notional
        if profile.peak_gross_notional else 0.0
    )
    return {
        "accountVolPct": vol * 100.0,
        "activeDayVolPct": (
            float(active.std(ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
            if len(active) > 1 else None
        ),
        "avgUtilizationPct": utilization * 100.0,
        "daysWithExposurePct": profile.days_with_exposure_pct,
        "sharpeIsScaleInvariant": True,
    }


@dataclass
class PnlDecomposition:
    """Where an equity curve's growth actually came from.

    Exists because treating all equity growth as trading edge produced a
    measured, catastrophic error: for Connors RSI2 the pooled curve grew
    $59,419 while its trades produced $2,927 -- **95% of the "edge" was
    risk-free interest** on the per-symbol engine's idle sleeve cash
    (29 x DEFAULT_CASH = $290,000 mostly uninvested for five years).
    Normalizing that numerator against deployed capital reported a 37.74%
    "capital-efficiency return" for a strategy that actually earned ~1.8%/yr
    on required capital. See LESSONS.md.

    `trading_pnl` is the only component the prop engine may use as the
    numerator. The rest are carry, and carry on a prop firm's nominal
    balance is not the trader's edge and generally not the trader's money.
    """

    trading_pnl: float
    equity_change: float
    #: equity_change - trading_pnl. For the per-symbol engine this is
    #: dominated by idle-cash accrual (engine/backtest.py:accrue_idle_cash).
    non_trading_pnl: float
    non_trading_share_pct: float
    reconciled: bool
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


#: Above this share of equity growth coming from non-trading sources, an
#: equity-derived prop series is contaminated and must not be used.
MAX_NON_TRADING_SHARE = 0.05


def trading_pnl_daily(result: Any) -> tuple[pd.Series, pd.Series, PnlDecomposition]:
    """Daily TRADING P&L and marked gross notional, rebuilt from positions.

    Independent of `portfolio_equity_curve` on purpose: it walks each
    sleeve's trades and marks open positions against that session's close,
    so it cannot inherit idle-cash accrual, and agreement with trade-level
    P&L is evidence rather than tautology. Verified 2026-08-22 to match the
    sum of trade P&L to the cent on both Connors RSI2 and IBS.

    Returns `(daily_trading_pnl, daily_marked_gross_notional, decomposition)`.
    """
    per_symbol = getattr(result, "per_symbol", None)
    if not per_symbol:
        raise InsufficientPropData(
            "Trade-level P&L requires a per-symbol result with trades."
        )

    from engine import data as data_module

    def _naive(ts):
        ts = pd.Timestamp(ts)
        return (ts.tz_localize(None) if ts.tz is not None else ts).normalize()

    all_days: set = set()
    for r in per_symbol.values():
        if r.equity_curve is not None and len(r.equity_curve):
            all_days |= {_naive(t) for t in pd.DatetimeIndex(r.equity_curve.index)}
    if not all_days:
        raise InsufficientPropData("No sleeve produced an equity curve.")
    days = pd.DatetimeIndex(sorted(all_days))

    pnl = pd.Series(0.0, index=days)
    gross = pd.Series(0.0, index=days)
    trade_total = 0.0

    for sym, r in per_symbol.items():
        trades = getattr(r, "trades", None)
        if trades is None or trades.empty:
            continue
        try:
            bars = data_module.get_bars(sym, "1d", days[0].date(), days[-1].date())
            close = bars["Close"]
            close.index = [_naive(t) for t in close.index]
            close = close.groupby(level=0).last()
        except Exception:  # noqa: BLE001 -- fall back to realized-at-exit below
            close = None

        for _, trade in trades.iterrows():
            size = float(trade["Size"])
            entry_px = float(trade["EntryPrice"])
            exit_px = (
                float(trade["ExitPrice"]) if not pd.isna(trade.get("ExitPrice")) else np.nan
            )
            trade_total += float(trade["PnL"])
            entry_day = _naive(trade["EntryTime"])
            exit_day = (
                days[-1] if pd.isna(trade.get("ExitTime")) else _naive(trade["ExitTime"])
            )
            window = days[(days >= entry_day) & (days <= exit_day)]
            if len(window) == 0:
                continue
            if close is None:
                if not np.isnan(exit_px):
                    pnl[window[-1]] += float(trade["PnL"])
                gross[window] += abs(size) * abs(entry_px)
                continue
            marks = close.reindex(window).ffill().fillna(entry_px)
            gross[window] += (marks.abs() * abs(size)).values
            prev = marks.shift(1)
            prev.iloc[0] = entry_px
            step = (marks - prev) * size
            if not np.isnan(exit_px):
                step.iloc[-1] = (exit_px - prev.iloc[-1]) * size
            pnl[window] += step.values

    pooled = portfolio_equity_curve(per_symbol)
    equity_change = 0.0
    if pooled is not None and len(pooled) > 1:
        daily_equity = pooled.groupby(pooled.index.normalize()).last()
        equity_change = float(daily_equity.iloc[-1] - daily_equity.iloc[0])

    trading_total = float(pnl.sum())
    non_trading = equity_change - trading_total
    share = abs(non_trading) / abs(equity_change) if equity_change else 0.0
    reconciled = abs(trading_total - trade_total) <= max(1.0, abs(trade_total) * 0.01)

    return pnl, gross, PnlDecomposition(
        trading_pnl=trading_total,
        equity_change=equity_change,
        non_trading_pnl=non_trading,
        non_trading_share_pct=share * 100.0,
        reconciled=reconciled,
        note=(
            "Reconstructed daily trading P&L matches trade-level P&L."
            if reconciled
            else f"Reconstruction {trading_total:,.2f} vs trade sum {trade_total:,.2f}."
        ),
    )


def prop_series_from_result(result: Any) -> tuple[DailySeries, ExposureProfile, PnlDecomposition]:
    """The prop numerator: TRADING P&L only, over a legitimate capital base.

    Per-symbol engines get the reconstructed trade-only path divided by peak
    MARKED gross notional. Portfolio-allocation engines have no discrete
    positions and their equity is genuinely at risk, so they keep the native
    portfolio return -- but the decomposition is still reported so a
    non-trading component cannot hide there either.
    """
    per_symbol = getattr(result, "per_symbol", None)
    if per_symbol:
        pnl, gross, decomposition = trading_pnl_daily(result)
        peak = float(gross.max())
        if peak <= 0:
            raise InsufficientPropData("Strategy never held a position.")
        idx = pd.DatetimeIndex(pnl.index)
        weekdays = len(pd.bdate_range(idx[0], idx[-1]))
        coverage = len(idx) / weekdays if weekdays else 0.0
        if coverage < MIN_SESSION_COVERAGE:
            raise InsufficientPropData(
                f"Trading-P&L series covers only {coverage:.0%} of sessions."
            )
        if len(idx) < MIN_DAILY_OBSERVATIONS:
            raise InsufficientPropData(f"Only {len(idx)} daily observations.")
        active = gross > 0
        profile = ExposureProfile(
            peak_gross_notional=peak,
            avg_gross_notional=float(gross.mean()),
            median_gross_notional=float(gross.median()),
            avg_net_notional=float("nan"),
            max_concurrent_positions=0,
            avg_concurrent_positions=0.0,
            days_with_exposure_pct=float(active.mean()) * 100.0,
            avg_utilization_while_active_pct=(
                float(gross[active].mean() / peak) * 100.0 if active.any() else 0.0
            ),
            annual_turnover_pct=float("nan"),
            capital_base=peak,
            basis="marked_peak_gross_notional",
            p95_gross_notional=float(np.percentile(gross, 95)),
            avg_gross_while_active=float(gross[active].mean()) if active.any() else 0.0,
            median_gross_while_active=float(gross[active].median()) if active.any() else 0.0,
        )
        series = DailySeries(
            returns=(pnl / peak).iloc[1:],
            start=idx[0], end=idx[-1], observations=len(idx) - 1,
            session_coverage=coverage, source="trading_pnl/marked_peak_gross",
        )
        return series, profile, decomposition

    series, profile = normalized_daily_series(result)
    equity = result.equity_curve
    change = float(equity.iloc[-1] - equity.iloc[0])
    decomposition = PnlDecomposition(
        trading_pnl=change, equity_change=change, non_trading_pnl=0.0,
        non_trading_share_pct=0.0, reconciled=True,
        note=(
            "Portfolio-allocation engine: no discrete positions and no idle "
            "sleeve cash, so equity change is treated as trading P&L. This is "
            "an ASSUMPTION about the engine, not a measurement of its carry."
        ),
    )
    return series, profile, decomposition


@dataclass
class PropRiskMetrics:
    """Risk statistics a prop desk actually reads, from a daily series.

    Deliberately includes BOTH return/max-drawdown and return/95th-
    percentile-drawdown. The second is emphasized because a single
    historical maximum is one observation -- it is the least stable number
    in the set, and sizing a real account off it is sizing off noise.
    Neither ratio is statistically definitive; both are screening metrics.
    """

    annualized_return_pct: float
    annualized_vol_pct: float
    sharpe: float | None
    sortino: float | None
    max_drawdown_pct: float
    max_drawdown_days: int
    p95_drawdown_pct: float
    p99_drawdown_pct: float
    worst_day_pct: float
    worst_5d_pct: float
    worst_20d_pct: float
    return_over_max_dd: float | None
    return_over_p95_dd: float | None
    positive_days_pct: float
    observations: int
    years: float

    def to_dict(self) -> dict:
        return asdict(self)


def _drawdown_series(returns: pd.Series, compounding: bool = True) -> pd.Series:
    """Drawdown on the equity path the returns actually describe.

    A series normalized against a FIXED capital base is a simple-return
    series: each value is P&L over an unchanging denominator, so the equity
    path is additive. Running cumprod on it compounds gains that were never
    reinvested and overstates both return and drawdown -- measured, a
    0.2%/day fixed-base series annualizes at 50.4% additively and 65.4%
    compounded. Portfolio-equity series really do compound and keep
    cumprod.
    """
    equity = (1.0 + returns).cumprod() if compounding else 1.0 + returns.cumsum()
    return equity / equity.cummax() - 1.0


def compute_risk_metrics(
    returns: pd.Series, risk_free_rate: float = 0.0, compounding: bool = True
) -> PropRiskMetrics:
    """Prop-relevant risk metrics from a daily fractional return series.

    Pass `compounding=False` for a series normalized against a fixed capital
    base (see `_drawdown_series`): its returns are simple P&L over an
    unchanging denominator and must be aggregated additively. Callers get
    the right flag from `normalized_daily_series` --
    `profile.basis != "portfolio_equity"`.
    """
    values = returns.dropna()
    if len(values) < 2:
        raise InsufficientPropData("Need at least two daily returns.")

    years = len(values) / TRADING_DAYS_PER_YEAR
    if compounding:
        total = float((1.0 + values).prod())
        annualized = (total ** (1.0 / years) - 1.0) * 100.0 if years > 0 and total > 0 else 0.0
    else:
        annualized = float(values.sum()) / years * 100.0 if years > 0 else 0.0
    vol = float(values.std(ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)
    excess = values - risk_free_rate / TRADING_DAYS_PER_YEAR
    sharpe = (
        float(excess.mean() / values.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        if values.std(ddof=1) > 0
        else None
    )
    downside = values[values < 0]
    sortino = (
        float(excess.mean() / downside.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        if len(downside) > 1 and downside.std(ddof=1) > 0
        else None
    )

    dd = _drawdown_series(values, compounding)
    max_dd = float(dd.min()) * 100.0
    # Percentiles over the drawdown PATH, not over separate episodes: the
    # question is "how deep is the account underwater on a bad day", which
    # is what a prop floor reacts to.
    p95_dd = float(np.percentile(dd, 5)) * 100.0
    p99_dd = float(np.percentile(dd, 1)) * 100.0

    underwater, longest, current = dd < 0, 0, 0
    for flag in underwater:
        current = current + 1 if flag else 0
        longest = max(longest, current)

    def _worst_roll(window: int) -> float:
        if len(values) < window:
            return float("nan")
        if compounding:
            rolled = (1.0 + values).rolling(window).apply(np.prod, raw=True) - 1.0
        else:
            rolled = values.rolling(window).sum()
        return float(rolled.min()) * 100.0

    return PropRiskMetrics(
        annualized_return_pct=annualized,
        annualized_vol_pct=vol * 100.0,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd,
        max_drawdown_days=longest,
        p95_drawdown_pct=p95_dd,
        p99_drawdown_pct=p99_dd,
        worst_day_pct=float(values.min()) * 100.0,
        worst_5d_pct=_worst_roll(5),
        worst_20d_pct=_worst_roll(20),
        return_over_max_dd=(annualized / abs(max_dd) if max_dd < 0 else None),
        return_over_p95_dd=(annualized / abs(p95_dd) if p95_dd < 0 else None),
        positive_days_pct=float((values > 0).mean()) * 100.0,
        observations=len(values),
        years=years,
    )


def losing_streaks(trade_returns: pd.Series) -> dict[str, float]:
    """Consecutive-loss statistics. A prop account dies to a STREAK, not to
    an average, so the distribution matters more than the mean."""
    values = pd.to_numeric(trade_returns, errors="coerce").dropna()
    streaks, current = [], 0
    for value in values:
        if value < 0:
            current += 1
        elif current:
            streaks.append(current)
            current = 0
    if current:
        streaks.append(current)
    if not streaks:
        return {"max": 0, "mean": 0.0, "p95": 0.0, "count": 0}
    return {
        "max": int(max(streaks)),
        "mean": float(np.mean(streaks)),
        "p95": float(np.percentile(streaks, 95)),
        "count": len(streaks),
    }


PropVerdict = str  # "strong" | "promising" | "borderline" | "poor" | "insufficient"


@dataclass
class PropSuitabilityResult:
    verdict: PropVerdict
    headline: str
    reasons: list[str]
    blockers: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def prop_verdict(
    *,
    series: DailySeries | None,
    metrics: PropRiskMetrics | None,
    sizing: Any,
    trade_count: int | None,
    validated: bool | None,
    holdout_passed: bool | None,
    mda_sufficient: bool | None,
    account: Any,
) -> PropSuitabilityResult:
    """A prop verdict SEPARATE from the existing edge verdict.

    Deliberately does not read benchmark gap. A strategy that trails SPY can
    be an excellent prop strategy and one that beats it can be a terrible
    one, so scoring the gap here would import the wrong question.

    Attractive simulated economics are necessary but never sufficient: the
    evidence gates below come first, because a bootstrap of an unproven edge
    reproduces the unproven edge with more decimal places. A strategy being
    prop-COMPATIBLE does not make its edge PROVEN, and this function will
    not promote one on simulation alone.
    """
    blockers: list[str] = []
    reasons: list[str] = []

    if series is None or metrics is None:
        return PropSuitabilityResult(
            "insufficient", "Insufficient evidence",
            [], ["No usable daily return series."],
        )
    if trade_count is not None and trade_count < 30:
        blockers.append(f"Only {trade_count} trades (sample too small).")
    if metrics.years < 1.0:
        blockers.append(f"Only {metrics.years:.1f} years of history.")
    if validated is False:
        blockers.append("Strategy has not passed the validation suite.")
    if mda_sufficient is False:
        blockers.append("MDA indicates insufficient statistical power.")

    best = getattr(sizing, "max_survival", None) if sizing else None
    conservative = getattr(sizing, "conservative", None) if sizing else None
    payout_point = getattr(sizing, "max_payout", None) if sizing else None

    if best is None:
        blockers.append(
            "No sizing achieves the failure-probability threshold; the "
            "strategy's normal drawdowns exceed the firm's loss budget."
        )

    if blockers:
        return PropSuitabilityResult(
            "insufficient" if series is None else "poor",
            "Insufficient evidence" if series is None else "Poor prop candidate",
            reasons, blockers,
        )

    net = getattr(payout_point, "expected_net_payout", 0.0) if payout_point else 0.0
    if net <= 0:
        return PropSuitabilityResult(
            "poor", "Poor prop candidate", reasons,
            ["Expected net payout after fees is not positive at any sizing."],
        )

    reasons.append(
        f"Max-survival sizing {best.risk_multiplier:.2f}x with "
        f"{best.failure_prob:.1%} 12-month failure probability."
    )
    reasons.append(f"Expected net annual payout ${net:,.0f} at best sizing.")

    strong = (
        holdout_passed is True
        and validated is True
        and conservative is not None
        and net > 0
        and abs(metrics.worst_day_pct) < account.daily_loss_limit_pct * 100.0 * 0.5
    )
    if strong:
        return PropSuitabilityResult(
            "strong", "Strong prop candidate", reasons, []
        )
    if conservative is not None and net > 0:
        return PropSuitabilityResult(
            "promising", "Promising prop candidate", reasons, []
        )
    return PropSuitabilityResult("borderline", "Borderline", reasons, [])
