"""Long-horizon evidence summaries for the all-stocks PIT backtest."""

from __future__ import annotations

from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from engine.cross_sectional import _rebalance_dates


TRADING_DAYS = 252
REGIMES = (
    ("1996–1999", 1996, 1999),
    ("2000–2002", 2000, 2002),
    ("2003–2007", 2003, 2007),
    ("2008–2009", 2008, 2009),
    ("2010–2019", 2010, 2019),
    ("2020", 2020, 2020),
    ("2021–2022", 2021, 2022),
    ("2023–present", 2023, 9999),
)


def _return_pct(values: pd.Series) -> float | None:
    clean = values.dropna()
    if len(clean) < 2 or float(clean.iloc[0]) <= 0:
        return None
    return float((clean.iloc[-1] / clean.iloc[0] - 1.0) * 100.0)


def _cagr_pct(values: pd.Series) -> float | None:
    clean = values.dropna()
    if len(clean) < 2 or float(clean.iloc[0]) <= 0:
        return None
    years = (clean.index[-1] - clean.index[0]).days / 365.25
    if years <= 0:
        return None
    return float(((clean.iloc[-1] / clean.iloc[0]) ** (1.0 / years) - 1.0) * 100.0)


def normalized_benchmark(close: pd.Series, strategy_index: pd.DatetimeIndex, initial: float) -> pd.Series:
    series = close.copy()
    if getattr(series.index, "tz", None) is not None and getattr(strategy_index, "tz", None) is None:
        series.index = series.index.tz_localize(None)
    elif getattr(series.index, "tz", None) is None and getattr(strategy_index, "tz", None) is not None:
        series.index = series.index.tz_localize(strategy_index.tz)
    series = series.reindex(strategy_index, method="ffill").dropna()
    if series.empty:
        return series
    return series / float(series.iloc[0]) * initial


def dynamic_random_benchmarks(
    bars_by_security: dict[str, pd.DataFrame],
    membership_at,
    strategy_index: pd.DatetimeIndex,
    *,
    rebalance_frequency: str,
    top_n: int,
    initial_equity: float,
    simulations: int = 400,
    seed: int = 1729,
) -> tuple[pd.Series, dict[str, Any]]:
    """Dynamic PIT equal weight plus concentration-matched random portfolios.

    Membership is evaluated before each execution date. Each random path draws
    exactly ``top_n`` eligible permanent IDs without replacement at every
    rebalance and holds equal weights until the next rebalance. This is a
    random-allocation null, not a buy-and-hold current-roster null.
    """
    close = pd.DataFrame({key: value["Close"] for key, value in bars_by_security.items()})
    close = close.reindex(strategy_index).ffill()
    rebalance_dates = _rebalance_dates(strategy_index, rebalance_frequency)
    rebalance_positions = [i for i, stamp in enumerate(strategy_index) if stamp in rebalance_dates]
    if not rebalance_positions:
        return pd.Series(dtype=float), {"simulations": 0, "reason": "no rebalance dates"}
    boundaries = rebalance_positions + [len(strategy_index) - 1]
    ew_value = initial_equity
    ew_points = pd.Series(index=strategy_index, dtype=float)
    rng = np.random.default_rng(seed)
    random_values = np.full(simulations, initial_equity, dtype=float)
    for segment_number, start_pos in enumerate(rebalance_positions):
        end_pos = boundaries[segment_number + 1]
        if end_pos <= start_pos:
            continue
        stamp = strategy_index[start_pos]
        eligible = sorted(
            security_id for security_id in membership_at(stamp.date())
            if security_id in close.columns and pd.notna(close.iloc[start_pos][security_id])
        )
        if not eligible:
            ew_points.iloc[start_pos:end_pos + 1] = ew_value
            continue
        segment = close.loc[strategy_index[start_pos:end_pos], eligible]
        relatives = segment.divide(segment.iloc[0]).replace([np.inf, -np.inf], np.nan)
        ew_path = relatives.mean(axis=1, skipna=True).fillna(1.0)
        ew_points.loc[ew_path.index] = ew_value * ew_path
        ew_value *= float(ew_path.iloc[-1])
        pick_count = min(top_n, len(eligible))
        ending_relatives = relatives.iloc[-1].fillna(1.0).to_numpy(dtype=float)
        for simulation in range(simulations):
            chosen = rng.choice(len(eligible), size=pick_count, replace=False)
            random_values[simulation] *= float(ending_relatives[chosen].mean())
    ew_points = ew_points.ffill().fillna(initial_equity)
    random_returns = (random_values / initial_equity - 1.0) * 100.0
    return ew_points, {
        "simulations": simulations,
        "seed": seed,
        "nullDefinition": (
            "At every rebalance, draw top_n permanent IDs uniformly without replacement "
            "from that date's PIT-eligible universe; equal weight and hold to next rebalance"
        ),
        "meanReturnPct": float(np.mean(random_returns)),
        "medianReturnPct": float(np.median(random_returns)),
        "p05ReturnPct": float(np.quantile(random_returns, 0.05)),
        "p95ReturnPct": float(np.quantile(random_returns, 0.95)),
        "maximumReturnPct": float(np.max(random_returns)),
        "returnsPct": random_returns.tolist(),
    }


def hac_mda(
    strategy_equity: pd.Series,
    benchmark_equity: pd.Series,
    *,
    confidence: float = 0.95,
    power: float = 0.80,
    actionable_alpha_pct: float = 2.0,
) -> dict[str, Any]:
    aligned = pd.concat(
        [strategy_equity.pct_change(), benchmark_equity.pct_change()], axis=1, join="inner",
    ).dropna()
    aligned.columns = ["strategy", "benchmark"]
    excess = (aligned["strategy"] - aligned["benchmark"]).to_numpy(dtype=float)
    n = len(excess)
    if n < 30:
        return {
            "mdaPct": None, "actionableAlphaPct": actionable_alpha_pct,
            "confidence": confidence, "power": power, "effectiveSampleSize": None,
            "methodology": "Newey-West/HAC long-run variance of daily benchmark-relative returns",
            "reason": "fewer than 30 aligned daily excess-return observations",
        }
    centered = excess - excess.mean()
    lag = max(1, int(np.floor(4 * (n / 100) ** (2 / 9))))
    gamma0 = float(np.dot(centered, centered) / n)
    long_run_variance = gamma0
    for offset in range(1, lag + 1):
        covariance = float(np.dot(centered[offset:], centered[:-offset]) / n)
        long_run_variance += 2.0 * (1.0 - offset / (lag + 1.0)) * covariance
    long_run_variance = max(long_run_variance, 0.0)
    annual_mean_se = np.sqrt(long_run_variance / n) * TRADING_DAYS
    normal = NormalDist()
    critical = normal.inv_cdf(1 - (1 - confidence) / 2) + normal.inv_cdf(power)
    mda_pct = float(critical * annual_mean_se * 100.0)
    effective_n = float(min(n, n * gamma0 / long_run_variance)) if long_run_variance > 0 else float(n)
    return {
        "mdaPct": mda_pct,
        "actionableAlphaPct": actionable_alpha_pct,
        "confidence": confidence,
        "power": power,
        "effectiveSampleSize": effective_n,
        "observations": n,
        "hacLags": lag,
        "annualizedExcessVolatilityPct": float(np.sqrt(long_run_variance * TRADING_DAYS) * 100.0),
        "methodology": (
            "Two-sided normal-approximation MDA using Newey-West/HAC long-run variance "
            "of the actual daily strategy-minus-SPY return series"
        ),
        "explanation": (
            "MDA estimates the smallest annual benchmark-relative edge this historical design "
            "could reliably detect. More securities do not automatically reduce MDA; additional "
            "independent market history and/or lower benchmark-relative variance do."
        ),
    }


def _rolling_excess(strategy: pd.Series, benchmark: pd.Series, days: int) -> pd.Series:
    aligned = pd.concat([strategy, benchmark], axis=1, join="inner").dropna()
    aligned.columns = ["strategy", "benchmark"]
    return (
        aligned["strategy"].pct_change(days)
        - aligned["benchmark"].pct_change(days)
    ).dropna() * 100.0


def analyze_pit_result(
    strategy_equity: pd.Series,
    spy_equity: pd.Series,
    *,
    total_costs: float,
    pit_diagnostics: dict[str, Any],
    actionable_alpha_pct: float = 2.0,
) -> dict[str, Any]:
    aligned = pd.concat([strategy_equity, spy_equity], axis=1, join="inner").dropna()
    aligned.columns = ["strategy", "spy"]
    strategy = aligned["strategy"]
    spy = aligned["spy"]
    strategy_return = _return_pct(strategy)
    spy_return = _return_pct(spy)
    strategy_cagr = _cagr_pct(strategy)
    spy_cagr = _cagr_pct(spy)
    daily = strategy.pct_change().dropna()
    annual_vol = float(daily.std(ddof=1) * np.sqrt(TRADING_DAYS) * 100.0) if len(daily) > 1 else None
    drawdown = strategy / strategy.cummax() - 1.0
    max_drawdown = abs(float(drawdown.min() * 100.0)) if not drawdown.empty else None
    calmar = (
        float(strategy_cagr / max_drawdown)
        if strategy_cagr is not None and max_drawdown not in (None, 0.0) else None
    )

    annual_returns = []
    for year in sorted(set(strategy.index.year) & set(spy.index.year)):
        sr = _return_pct(strategy[strategy.index.year == year])
        br = _return_pct(spy[spy.index.year == year])
        if sr is not None and br is not None:
            annual_returns.append({
                "year": int(year), "strategyPct": sr, "spyPct": br, "excessPct": sr - br,
            })
    regimes = []
    for label, first, last in REGIMES:
        mask = (strategy.index.year >= first) & (strategy.index.year <= last)
        sr = _return_pct(strategy[mask])
        br = _return_pct(spy[mask])
        if sr is not None and br is not None:
            regimes.append({"label": label, "strategyPct": sr, "spyPct": br, "excessPct": sr - br})

    rolling: dict[str, Any] = {}
    for years, days in ((1, 252), (3, 756), (5, 1260)):
        values = _rolling_excess(strategy, spy, days)
        rolling[f"{years}Year"] = {
            "observations": int(len(values)),
            "fractionBeatingSpy": float((values > 0).mean()) if len(values) else None,
            "medianExcessPct": float(values.median()) if len(values) else None,
            "worstExcessPct": float(values.min()) if len(values) else None,
            "bestExcessPct": float(values.max()) if len(values) else None,
        }

    split = max(1, min(len(strategy) - 1, int(len(strategy) * 0.80)))
    development_strategy, holdout_strategy = strategy.iloc[:split], strategy.iloc[split - 1:]
    development_spy, holdout_spy = spy.iloc[:split], spy.iloc[split - 1:]
    holdout_sr, holdout_br = _return_pct(holdout_strategy), _return_pct(holdout_spy)
    initial = float(strategy.iloc[0])
    base_return = strategy_return or 0.0
    base_cost_pct = total_costs / initial * 100.0 if initial > 0 else 0.0
    cost_stress = {
        f"{multiple}x": base_return - (multiple - 1) * base_cost_pct
        for multiple in (1, 2, 3)
    }
    return {
        "strategyReturnPct": strategy_return,
        "strategyCagrPct": strategy_cagr,
        "spyReturnPct": spy_return,
        "spyCagrPct": spy_cagr,
        "cumulativeGapPct": None if strategy_return is None or spy_return is None else strategy_return - spy_return,
        "annualizedBenchmarkRelativeReturnPct": (
            None if strategy_cagr is None or spy_cagr is None else strategy_cagr - spy_cagr
        ),
        "annualizedVolatilityPct": annual_vol,
        "maxDrawdownPct": max_drawdown,
        "calmarRatio": calmar,
        "mda": hac_mda(strategy, spy, actionable_alpha_pct=actionable_alpha_pct),
        "annualReturns": annual_returns,
        "regimes": regimes,
        "rollingExcess": rolling,
        "holdout": {
            "developmentStart": development_strategy.index[0].isoformat(),
            "developmentEnd": development_strategy.index[-1].isoformat(),
            "developmentStrategyPct": _return_pct(development_strategy),
            "developmentSpyPct": _return_pct(development_spy),
            "holdoutStart": holdout_strategy.index[0].isoformat(),
            "holdoutEnd": holdout_strategy.index[-1].isoformat(),
            "holdoutStrategyPct": holdout_sr,
            "holdoutSpyPct": holdout_br,
            "holdoutExcessPct": None if holdout_sr is None or holdout_br is None else holdout_sr - holdout_br,
            "splitPolicy": "Chronological first 80% development / final 20% untouched holdout",
        },
        "costStressReturnPct": cost_stress,
        "pitIntegrity": pit_diagnostics,
    }
