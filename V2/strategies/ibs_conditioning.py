"""Independent IBS conditioning research with explicit integrity controls.

This module answers one question at a time: does the one-day IBS reversal
spread differ across a pre-registered conditioning variable?  It does not
combine conditioners and it does not modify any existing paper-trading track.

Signal timing
-------------
All signal-day fields use information observable by the signal-day close.
IBS is formed from that day's high, low, and close.  A position enters at the
next trading session's open and exits at that session's close.  This avoids the
unimplementable assumption that a fully formed close signal can trade at the
same close.  Entry and exit costs are charged separately using the signal
day's trailing, pre-event ADV.

Research integrity
------------------
The default development window ends in 2018.  The previously consumed
historical holdout is never described as fresh validation.  Running requires
an explicit acknowledgement flag, and every output manifest records that the
result is historical development evidence requiring prospective confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import statsmodels.api as sm


TRADING_DAYS = 252
BUCKETS = ("low", "mid", "high")
FACTOR_COLUMNS = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom")
TAXONOMY = ("SUPPORTED", "FALSIFIED", "UNRESOLVED / UNDERPOWERED")


@dataclass(frozen=True)
class ConditioningHypothesis:
    hypothesis_id: str
    feature: str
    favorable_bucket: str
    adverse_bucket: str
    buckets: tuple[str, ...]
    hypothesis: str


# This is a registry, not a composite recipe.  Every entry is evaluated in a
# separate bucket split and gets its own contrast, classification, and report.
HYPOTHESES: tuple[ConditioningHypothesis, ...] = (
    ConditioningHypothesis(
        "ibs_earnings_event", "earnings_event", "non_event", "event",
        ("non_event", "event"),
        "IBS reversal is stronger away from earnings filings than on filing dates.",
    ),
    ConditioningHypothesis(
        "ibs_overnight_gap_share", "overnight_gap_share", "low", "high", BUCKETS,
        "Moves with a smaller overnight component reverse more strongly than gap-dominated moves.",
    ),
    ConditioningHypothesis(
        "ibs_abnormal_volume", "abnormal_volume", "low", "high", BUCKETS,
        "Low-abnormal-volume moves reverse more strongly than high-volume information events.",
    ),
    ConditioningHypothesis(
        "ibs_turnover", "turnover", "low", "high", BUCKETS,
        "Low-turnover dislocations have a larger gross reversal spread than high-turnover moves.",
    ),
    ConditioningHypothesis(
        "ibs_liquidity", "adv20_pre", "low", "high", BUCKETS,
        "Less-liquid stocks have a larger gross IBS reversal spread than liquid stocks.",
    ),
    ConditioningHypothesis(
        "ibs_intraday_range", "intraday_range", "low", "high", BUCKETS,
        "Narrower-range moves reverse more strongly than wide-range information-like moves.",
    ),
    ConditioningHypothesis(
        "ibs_idiosyncratic_volatility", "idio_vol20_pre", "low", "high", BUCKETS,
        "Low-idiosyncratic-volatility moves have a more reliable IBS reversal spread.",
    ),
    ConditioningHypothesis(
        "ibs_market_cap", "marketcap_pre", "low", "high", BUCKETS,
        "Smaller-cap stocks have a larger IBS reversal spread than larger-cap stocks.",
    ),
    ConditioningHypothesis(
        "ibs_volatility_bucket", "total_vol20_pre", "low", "high", BUCKETS,
        "Low-total-volatility moves have a more reliable IBS reversal spread than high-volatility moves.",
    ),
    ConditioningHypothesis(
        "ibs_market_regime", "market_regime", "bull", "bear",
        ("bull", "sideways", "bear"),
        "IBS reversal is stronger in bull markets than in bear markets.",
    ),
)


@dataclass(frozen=True)
class ResearchSettings:
    start: str = "2001-01-01"
    end: str = "2018-12-31"
    min_price: float = 1.0
    min_adv: float = 500_000.0
    min_names: int = 100
    min_names_per_leg: int = 2
    bucket_count: int = 3
    participation_rate: float = 0.005
    familywise_t_threshold: float = 2.81
    minimum_contrast_days: int = 250

    def __post_init__(self) -> None:
        if pd.Timestamp(self.start) > pd.Timestamp(self.end):
            raise ValueError("start must be on or before end")
        if self.min_price < 0 or self.min_adv < 0:
            raise ValueError("price and ADV thresholds cannot be negative")
        if self.min_names < 5 or self.min_names_per_leg < 1:
            raise ValueError("minimum-name thresholds are too small")
        if self.bucket_count != 3:
            raise ValueError("the preregistered design uses exactly three continuous buckets")


def side_cost_rate(adv: pd.Series | np.ndarray) -> np.ndarray:
    """Per-side spread, fees, and slippage assumption, conditional on ADV."""
    values = np.asarray(adv, dtype=float)
    return np.select(
        [values < 5e6, values < 25e6, values < 100e6],
        [35e-4, 20e-4, 12.5e-4], default=7.5e-4,
    )


def _candidate_metadata(con: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    exchanges = ("NASDAQ", "NYSE", "NYSEMKT", "NYSEARCA", "BATS")
    placeholders = ",".join("?" * len(exchanges))
    query = f"""SELECT ticker,sector,firstpricedate,lastpricedate
                FROM tickers
                WHERE category='Domestic Common Stock'
                  AND exchange IN ({placeholders})
                  AND firstpricedate<=?
                  AND (lastpricedate IS NULL OR lastpricedate>=?)"""
    frame = pd.read_sql_query(
        query, con, params=[*exchanges, end, start],
        parse_dates=["firstpricedate", "lastpricedate"],
    )
    return frame[
        ~frame.sector.isin(["Financial Services", "Real Estate", "Healthcare"])
    ].drop_duplicates("ticker").reset_index(drop=True)


def load_research_window(
    con: sqlite3.Connection,
    metadata: pd.DataFrame,
    core_start: pd.Timestamp,
    core_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load one bounded annual research window plus PIT-safe warmup/forward rows."""
    lo = core_start - pd.Timedelta(days=500)
    hi = core_end + pd.Timedelta(days=10)
    live = metadata[
        (metadata.firstpricedate <= hi)
        & (metadata.lastpricedate.isna() | (metadata.lastpricedate >= lo))
    ]
    tickers = live.ticker.tolist()
    if not tickers:
        return pd.DataFrame(), pd.DataFrame(columns=["ticker", "date"])
    placeholders = ",".join("?" * len(tickers))
    query = f"""SELECT s.ticker,s.date,s.open,s.high,s.low,s.close,s.volume,
                       d.marketcap
                FROM stocks s LEFT JOIN daily d
                  ON d.ticker=s.ticker AND d.date=s.date
                WHERE s.ticker IN ({placeholders}) AND s.date>=? AND s.date<=?
                ORDER BY s.ticker,s.date"""
    chunks = pd.read_sql_query(
        query, con,
        params=[*tickers, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")],
        parse_dates=["date"], chunksize=400_000,
    )
    parts: list[pd.DataFrame] = []
    for chunk in chunks:
        for column in ("open", "high", "low", "close", "volume", "marketcap"):
            chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
        parts.append(chunk)
    prices = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if prices.empty:
        return prices, pd.DataFrame(columns=["ticker", "date"])
    prices = prices.merge(live, on="ticker", how="left", validate="many_to_one")

    # In this bundle, fundamentals.date is the filing/publication date.  ARQ
    # observations therefore proxy earnings-event dates without using the
    # earlier fiscal calendar date.  No +/- future-day matching is allowed.
    earnings = pd.read_sql_query(
        f"""SELECT DISTINCT ticker,date FROM fundamentals
             WHERE ticker IN ({placeholders}) AND dimension='ARQ'
               AND date>=? AND date<=?""",
        con, params=[*tickers, lo.strftime("%Y-%m-%d"), core_end.strftime("%Y-%m-%d")],
        parse_dates=["date"],
    )
    return prices, earnings


def _rolling_by_ticker(
    values: pd.Series,
    tickers: pd.Series,
    window: int,
    min_periods: int,
    statistic: str,
) -> pd.Series:
    grouped = values.groupby(tickers, sort=False)
    if statistic == "median":
        out = grouped.rolling(window, min_periods=min_periods).median()
    elif statistic == "std":
        out = grouped.rolling(window, min_periods=min_periods).std()
    elif statistic == "mean":
        out = grouped.rolling(window, min_periods=min_periods).mean()
    else:
        raise ValueError(f"unknown rolling statistic: {statistic}")
    return out.reset_index(level=0, drop=True).sort_index()


def build_features(
    prices: pd.DataFrame,
    earnings: pd.DataFrame,
    core_start: pd.Timestamp,
    core_end: pd.Timestamp,
    settings: ResearchSettings | None = None,
) -> pd.DataFrame:
    """Build close-known conditioners and next-session execution returns.

    Rolling risk/liquidity baselines are shifted one session, so the event
    being classified cannot alter its own baseline.  The explicit forward
    fields are used only as outcomes, never in eligibility or bucket labels.
    """
    settings = settings or ResearchSettings(
        start=core_start.strftime("%Y-%m-%d"), end=core_end.strftime("%Y-%m-%d")
    )
    p = prices.sort_values(["ticker", "date"]).copy().reset_index(drop=True)
    if p.empty:
        return p
    g = p.groupby("ticker", sort=False)
    p["history_n"] = g.cumcount() + 1
    p["prev_close"] = g.close.shift(1)
    p["ret1"] = p.close / p.prev_close - 1
    p["gap"] = p.open / p.prev_close - 1
    p["intraday_move"] = p.close / p.open - 1
    p["ibs"] = (p.close - p.low) / (p.high - p.low).replace(0, np.nan)
    p["ibs_score"] = 1 - p.ibs
    p["dollar_volume"] = p.close * p.volume

    ticker_key = p.ticker
    p["adv20_pre"] = _rolling_by_ticker(
        g.dollar_volume.shift(1), ticker_key, 20, 15, "median"
    )
    p["volume20_pre"] = _rolling_by_ticker(
        g.volume.shift(1), ticker_key, 20, 15, "median"
    )
    p["abnormal_volume"] = p.volume / p.volume20_pre.replace(0, np.nan)
    p["marketcap_pre"] = g.marketcap.shift(1)
    p["turnover"] = p.dollar_volume / p.marketcap_pre.replace(0, np.nan)
    p["intraday_range"] = (p.high - p.low) / p.prev_close.replace(0, np.nan)
    p["total_vol20_pre"] = _rolling_by_ticker(
        g.ret1.shift(1), ticker_key, 20, 15, "std"
    )

    abs_gap = p.gap.abs()
    abs_intraday = p.intraday_move.abs()
    p["overnight_gap_share"] = abs_gap / (abs_gap + abs_intraday).replace(0, np.nan)

    # PIT internal-market return: current stock return weighted by prior-day
    # market cap.  Beta and idiosyncratic volatility baselines use only
    # observations strictly before the signal date.
    valid_market = p.ret1.notna() & p.marketcap_pre.gt(0)
    numerator = (p.ret1.where(valid_market) * p.marketcap_pre.where(valid_market)).groupby(p.date).sum(min_count=1)
    denominator = p.marketcap_pre.where(valid_market).groupby(p.date).sum(min_count=1)
    market_return = numerator / denominator
    p["market_return"] = p.date.map(market_return)
    lag_stock = g.ret1.shift(1)
    lag_market = p.groupby("ticker", sort=False).market_return.shift(1)
    mean_x = _rolling_by_ticker(lag_stock, ticker_key, 60, 40, "mean")
    mean_y = _rolling_by_ticker(lag_market, ticker_key, 60, 40, "mean")
    mean_xy = _rolling_by_ticker(lag_stock * lag_market, ticker_key, 60, 40, "mean")
    mean_y2 = _rolling_by_ticker(lag_market * lag_market, ticker_key, 60, 40, "mean")
    denom = mean_y2 - mean_y * mean_y
    p["beta60_pre"] = (mean_xy - mean_x * mean_y) / denom.where(denom.abs() > 1e-12)
    p["idio_return"] = p.ret1 - p.beta60_pre * p.market_return
    p["idio_vol20_pre"] = _rolling_by_ticker(
        p.groupby("ticker", sort=False).idio_return.shift(1), ticker_key, 20, 15, "std"
    )

    p["entry_date"] = g.date.shift(-1)
    p["entry_open"] = g.open.shift(-1)
    p["exit_close"] = g.close.shift(-1)
    p["forward_return"] = p.exit_close / p.entry_open - 1
    for horizon in (2, 5):
        p[f"exit_close_h{horizon}"] = g.close.shift(-horizon)
        p[f"forward_return_h{horizon}"] = p[f"exit_close_h{horizon}"] / p.entry_open - 1
    sessions = pd.DatetimeIndex(sorted(p.date.dropna().unique()))
    next_session = pd.Series(sessions[1:], index=sessions[:-1])
    p["expected_entry_date"] = p.date.map(next_session)

    earn_index = pd.MultiIndex.from_frame(
        earnings[["ticker", "date"]].drop_duplicates()
    ) if not earnings.empty else pd.MultiIndex.from_arrays([[], []])
    row_index = pd.MultiIndex.from_frame(p[["ticker", "date"]])
    p["earnings_event"] = np.where(row_index.isin(earn_index), "event", "non_event")

    market_index = (1 + market_return.fillna(0)).cumprod()
    market_ma200 = market_index.rolling(200, min_periods=200).mean()
    regime = pd.Series("sideways", index=market_index.index, dtype="object")
    regime[market_index > market_ma200] = "bull"
    regime[market_index < market_ma200 * 0.95] = "bear"
    regime[market_ma200.isna()] = np.nan
    p["market_regime"] = p.date.map(regime)

    listing_ok = p.lastpricedate.isna() | (p.lastpricedate >= p.date)
    eligible = (
        p.date.between(core_start, core_end)
        & (p.firstpricedate <= p.date)
        & listing_ok
        & p.history_n.ge(252)
        & p.close.ge(settings.min_price)
        & p.adv20_pre.ge(settings.min_adv)
        & p.ibs_score.notna()
        & p.forward_return.between(-0.80, 3.0)
        & p.entry_date.eq(p.expected_entry_date)
        & p.entry_open.gt(0)
        & p.exit_close.gt(0)
    )
    return p[eligible].copy()


def assign_condition_bucket(day: pd.DataFrame, hypothesis: ConditioningHypothesis) -> pd.Series:
    """Assign only this hypothesis's bucket; no conditioner interactions."""
    values = day[hypothesis.feature]
    if hypothesis.feature in {"earnings_event", "market_regime"}:
        return values.where(values.isin(hypothesis.buckets))
    good = values.replace([np.inf, -np.inf], np.nan).dropna()
    out = pd.Series(index=day.index, dtype="object")
    if len(good) < 3:
        return out
    ranks = good.rank(method="first", pct=True)
    out.loc[good.index] = pd.cut(
        ranks, [0, 1 / 3, 2 / 3, 1], labels=BUCKETS, include_lowest=True
    ).astype("object")
    return out


def _spearman(x: pd.Series, y: pd.Series) -> float:
    pair = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
    return float(pair.x.corr(pair.y, method="spearman")) if len(pair) >= 5 else np.nan


def evaluate_day(
    day: pd.DataFrame,
    hypothesis: ConditioningHypothesis,
    min_names: int = 100,
    min_names_per_leg: int = 2,
) -> list[dict]:
    """Return independent bucket observations for one signal date."""
    d = day.dropna(subset=["ibs_score", "forward_return", "adv20_pre"]).copy()
    if len(d) < min_names:
        return []
    # Global thresholds preserve the meaning of "extreme IBS" across every
    # subgroup.  We then ask how those same extremes behave in each bucket.
    d["ibs_pct"] = d.ibs_score.rank(method="average", pct=True)
    # Conventional naming: Q1 is the most favorable (lowest raw IBS), Q5
    # is the least favorable.  ibs_pct itself remains increasing in
    # favorability for IC calculation.
    d["ibs_quintile"] = 6 - np.ceil(d.ibs_pct * 5).clip(1, 5).astype(int)
    d["condition_bucket"] = assign_condition_bucket(d, hypothesis)
    observations: list[dict] = []
    for bucket in hypothesis.buckets:
        group = d[d.condition_bucket == bucket]
        q1 = group[group.ibs_quintile == 1]
        q5 = group[group.ibs_quintile == 5]
        if len(q1) < min_names_per_leg or len(q5) < min_names_per_leg:
            continue
        q1_cost = side_cost_rate(q1.adv20_pre)
        q5_cost = side_cost_rate(q5.adv20_pre)
        q1_net = (1 + q1.forward_return.to_numpy(float)) * (1 - q1_cost) ** 2 - 1
        q5_net = (1 + q5.forward_return.to_numpy(float)) * (1 - q5_cost) ** 2 - 1
        quintile_returns = {
            f"q{i}_return": float(group.loc[group.ibs_quintile == i, "forward_return"].mean())
            for i in range(1, 6)
        }
        horizon_diagnostics = {}
        for horizon in (2, 5):
            column = f"forward_return_h{horizon}"
            horizon_diagnostics[f"spread_gross_h{horizon}"] = float(
                q1[column].mean() - q5[column].mean()
            )
            horizon_diagnostics[f"ic_h{horizon}"] = _spearman(group.ibs_score, group[column])
        observations.append({
            "signal_date": pd.Timestamp(group.date.iloc[0]),
            "return_date": pd.Timestamp(group.entry_date.iloc[0]),
            "hypothesis_id": hypothesis.hypothesis_id,
            "bucket": bucket,
            "n_bucket": len(group), "n_long": len(q1), "n_avoid": len(q5),
            "long_gross": float(q1.forward_return.mean()),
            "long_net": float(np.mean(q1_net)),
            "spread_gross": float(q1.forward_return.mean() - q5.forward_return.mean()),
            "spread_net_hypothetical": float(np.mean(q1_net) - np.mean(q5_net)),
            "ic": _spearman(group.ibs_score, group.forward_return),
            "average_side_cost": float(np.mean(q1_cost)),
            "capacity_usd_half_pct_adv": float(0.005 * q1.adv20_pre.sum()),
            "average_position_weight": float(1 / len(q1)),
            "market_regime": str(group.market_regime.iloc[0]),
            **quintile_returns, **horizon_diagnostics,
        })
    return observations


def _hac_mean_test(series: pd.Series, maxlags: int = 5) -> dict:
    values = series.dropna().astype(float)
    if len(values) < 30:
        return {"mean": np.nan, "tstat": np.nan, "pvalue": np.nan, "n": len(values)}
    model = sm.OLS(values.to_numpy(), np.ones((len(values), 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags}
    )
    return {
        "mean": float(model.params[0]), "tstat": float(model.tvalues[0]),
        "pvalue": float(model.pvalues[0]), "n": len(values),
    }


def _cagr(returns: pd.Series) -> float:
    values = returns.dropna()
    if values.empty or (1 + values).le(0).any():
        return np.nan
    return float(np.exp(np.log1p(values).sum() * TRADING_DAYS / len(values)) - 1)


def _max_drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns.fillna(0)).cumprod()
    return float((wealth / wealth.cummax() - 1).min()) if len(wealth) else np.nan


def _drawdown_duration(returns: pd.Series) -> int:
    wealth = (1 + returns.fillna(0)).cumprod()
    underwater = wealth < wealth.cummax()
    longest = current = 0
    for value in underwater:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def _factor_regression(returns: pd.Series, factors: pd.DataFrame) -> dict:
    columns = [c for c in FACTOR_COLUMNS if c in factors.columns]
    required = [*columns, "RF"]
    aligned = pd.concat([returns.rename("strategy"), factors[required]], axis=1).dropna()
    if len(aligned) < 30:
        return {"alpha_annual": np.nan, "alpha_tstat": np.nan, "r_squared": np.nan, "betas": {}}
    model = sm.OLS(
        aligned.strategy - aligned.RF,
        sm.add_constant(aligned[columns]),
    ).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    return {
        "alpha_annual": float((1 + model.params["const"]) ** TRADING_DAYS - 1),
        "alpha_tstat": float(model.tvalues["const"]),
        "r_squared": float(model.rsquared),
        "betas": {name: float(model.params[name]) for name in columns},
    }


def _annualized_mean(series: pd.Series) -> float:
    value = float(series.dropna().mean()) if series.notna().any() else np.nan
    return float((1 + value) ** TRADING_DAYS - 1) if np.isfinite(value) and value > -1 else np.nan


def summarize_bucket(
    observations: pd.DataFrame,
    factors: pd.DataFrame,
    spy_returns: pd.Series,
    calendar: pd.DatetimeIndex,
) -> dict:
    obs = observations.sort_values("return_date").drop_duplicates("return_date", keep="last")
    active = obs.set_index("return_date")
    net = active.long_net.reindex(calendar, fill_value=0.0)
    gross = active.long_gross.reindex(calendar, fill_value=0.0)
    spy = spy_returns.reindex(calendar).dropna()
    aligned_net = net.reindex(spy.index)
    regression = _factor_regression(net, factors.reindex(calendar))
    std = net.std(ddof=1)
    downside = net[net < 0]
    downside_dev = float(np.sqrt((downside ** 2).mean())) if len(downside) else np.nan
    ic_test = _hac_mean_test(active.ic)
    spread_test = _hac_mean_test(active.spread_gross)
    q_means = [float(active[f"q{i}_return"].mean()) for i in range(1, 6)]
    monotonic_corr = pd.Series(range(1, 6)).corr(pd.Series(q_means), method="spearman")
    regimes = {}
    for regime in ("bull", "sideways", "bear"):
        subset = active[active.market_regime == regime]
        regimes[regime] = {
            "spread_annualized": _annualized_mean(subset.spread_gross),
            "long_net_annualized_active_days": _annualized_mean(subset.long_net),
            "n": len(subset),
        }
    subperiods = {}
    signal_dates = pd.to_datetime(active.signal_date)
    for label, lo, hi in (
        ("2001_2009", "2001-01-01", "2009-12-31"),
        ("2010_2018", "2010-01-01", "2018-12-31"),
    ):
        subset = active[(signal_dates >= lo) & (signal_dates <= hi)]
        subperiods[label] = {
            "spread_annualized": _annualized_mean(subset.spread_gross),
            "long_net_annualized_active_days": _annualized_mean(subset.long_net),
            "n": len(subset),
        }
    betas = regression["betas"]
    return {
        "active_days": len(active),
        "average_bucket_names": float(active.n_bucket.mean()),
        "average_positions": float(active.n_long.mean()),
        "cagr_gross": _cagr(gross), "cagr_net": _cagr(net),
        "spy_cagr": _cagr(spy), "excess_cagr": _cagr(aligned_net) - _cagr(spy),
        "cumulative_return_net": float((1 + net).prod() - 1),
        "annualized_volatility": float(std * np.sqrt(TRADING_DAYS)) if pd.notna(std) else np.nan,
        "sharpe": float(np.sqrt(TRADING_DAYS) * net.mean() / std) if pd.notna(std) and std > 0 else np.nan,
        "sortino": float(np.sqrt(TRADING_DAYS) * net.mean() / downside_dev) if pd.notna(downside_dev) and downside_dev > 0 else np.nan,
        "max_drawdown": _max_drawdown(net),
        "max_drawdown_duration_days": _drawdown_duration(net),
        "alpha_annual": regression["alpha_annual"], "alpha_tstat": regression["alpha_tstat"],
        "r_squared": regression["r_squared"],
        "market_beta": betas.get("Mkt-RF", np.nan),
        "smb_beta": betas.get("SMB", np.nan), "hml_beta": betas.get("HML", np.nan),
        "rmw_beta": betas.get("RMW", np.nan), "cma_beta": betas.get("CMA", np.nan),
        "momentum_beta": betas.get("Mom", np.nan),
        "spy_correlation": float(net.corr(spy_returns.reindex(calendar))),
        "spread_annualized_gross": _annualized_mean(active.spread_gross),
        "spread_hac_tstat": spread_test["tstat"],
        "ic_mean": ic_test["mean"], "ic_hac_tstat": ic_test["tstat"],
        "spread_h2_annualized": _annualized_mean(active.spread_gross_h2),
        "spread_h2_hac_tstat": _hac_mean_test(active.spread_gross_h2, maxlags=2)["tstat"],
        "ic_h2_mean": float(active.ic_h2.mean()),
        "spread_h5_annualized": _annualized_mean(active.spread_gross_h5),
        "spread_h5_hac_tstat": _hac_mean_test(active.spread_gross_h5, maxlags=5)["tstat"],
        "ic_h5_mean": float(active.ic_h5.mean()),
        "quintile_monotonicity_spearman": float(monotonic_corr),
        **{f"q{i}_annualized_active_days": _annualized_mean(active[f"q{i}_return"]) for i in range(1, 6)},
        "annual_two_way_turnover": float(2 * active.shape[0] * TRADING_DAYS / len(calendar)),
        "estimated_cost_drag_annual": _cagr(gross) - _cagr(net),
        "average_side_cost_bps": float(active.average_side_cost.mean() * 10_000),
        "capacity_usd_half_pct_adv": float(active.capacity_usd_half_pct_adv.mean()),
        "average_position_weight": float(active.average_position_weight.mean()),
        "regime_behavior": regimes, "subperiod_behavior": subperiods,
    }


def classify_contrast(
    difference: pd.Series,
    threshold: float = 2.81,
    minimum_days: int = 250,
) -> tuple[str, dict]:
    test = _hac_mean_test(difference)
    if test["n"] < minimum_days or not np.isfinite(test["tstat"]):
        verdict = "UNRESOLVED / UNDERPOWERED"
    elif test["tstat"] >= threshold:
        verdict = "SUPPORTED"
    elif test["tstat"] <= -threshold:
        verdict = "FALSIFIED"
    else:
        verdict = "UNRESOLVED / UNDERPOWERED"
    return verdict, test


def _hac_group_difference(favorable: pd.Series, adverse: pd.Series, maxlags: int = 5) -> dict:
    """HAC difference in mean for mutually exclusive condition dates.

    Most bucket contrasts are paired: both bucket spreads exist on the same
    signal date.  Market regimes are necessarily mutually exclusive, so a
    paired subtraction would always have an empty intersection.  This helper
    estimates the bull-minus-bear mean directly with a regime indicator.
    """
    favored = favorable.dropna().astype(float)
    adverse = adverse.dropna().astype(float)
    if len(favored) + len(adverse) < 30:
        return {"mean": np.nan, "tstat": np.nan, "pvalue": np.nan, "n": len(favored) + len(adverse)}
    frame = pd.DataFrame({
        "spread": pd.concat([favored, adverse], ignore_index=True),
        "favorable": [1.0] * len(favored) + [0.0] * len(adverse),
    })
    model = sm.OLS(frame.spread, sm.add_constant(frame[["favorable"]])).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags}
    )
    return {
        "mean": float(model.params["favorable"]),
        "tstat": float(model.tvalues["favorable"]),
        "pvalue": float(model.pvalues["favorable"]),
        "n": len(frame),
    }


def load_factors(start: str, end: str, csv_path: str | Path | None = None) -> pd.DataFrame:
    """Load daily FF5 + momentum factors; an explicit CSV enables frozen inputs."""
    if csv_path:
        frame = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        missing = set(["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]) - set(frame.columns)
        if missing:
            raise ValueError(f"factor CSV is missing columns: {sorted(missing)}")
        return frame.sort_index()
    import pandas_datareader.data as web
    ff5 = web.DataReader("F-F_Research_Data_5_Factors_2x3_daily", "famafrench", start=start, end=end)[0] / 100
    mom = web.DataReader("F-F_Momentum_Factor_daily", "famafrench", start=start, end=end)[0] / 100
    ff5.index = ff5.index.to_timestamp() if isinstance(ff5.index, pd.PeriodIndex) else pd.to_datetime(ff5.index)
    mom.index = mom.index.to_timestamp() if isinstance(mom.index, pd.PeriodIndex) else pd.to_datetime(mom.index)
    momentum_column = next((c for c in mom.columns if c.lower().strip() in {"mom", "umd"}), mom.columns[0])
    return ff5.join(mom[[momentum_column]].rename(columns={momentum_column: "Mom"}), how="left").sort_index()


def load_spy_returns(start: str, end: str, csv_path: str | Path | None = None) -> pd.Series:
    """Load actual SPY total-return series (adjusted close), never a 10% proxy."""
    if csv_path:
        frame = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        if "return" in frame.columns:
            return frame["return"].astype(float).sort_index().rename("SPY")
        price_column = next((c for c in ("adjusted_close", "Adj Close", "Close") if c in frame.columns), None)
        if price_column is None:
            raise ValueError("SPY CSV needs a return or adjusted-close column")
        return frame[price_column].astype(float).pct_change(fill_method=None).rename("SPY")
    import yfinance as yf
    hist = yf.download(
        "SPY", start=start,
        end=(pd.Timestamp(end) + pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
        auto_adjust=True, progress=False,
    )
    if hist.empty:
        raise RuntimeError("SPY download returned no data")
    close = hist["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close.astype(float).pct_change(fill_method=None).rename("SPY")


def validate_preregistration(path: str | Path, settings: ResearchSettings) -> dict:
    """Fail closed if executable settings drift from the frozen specification."""
    specification = json.loads(Path(path).read_text(encoding="utf-8"))
    registered_ids = [item["hypothesis_id"] for item in specification.get("hypotheses", [])]
    executable_ids = [item.hypothesis_id for item in HYPOTHESES]
    if registered_ids != executable_ids:
        raise ValueError("preregistration hypothesis order/content does not match the executable registry")
    integrity = specification.get("integrity", {})
    if integrity.get("development_start") != settings.start or integrity.get("development_end") != settings.end:
        raise ValueError("requested dates do not match the preregistered development window")
    multiple = specification.get("multiple_testing", {})
    if float(multiple.get("absolute_t_threshold", np.nan)) != settings.familywise_t_threshold:
        raise ValueError("executable familywise threshold differs from preregistration")
    if int(multiple.get("minimum_paired_days", -1)) != settings.minimum_contrast_days:
        raise ValueError("executable minimum paired days differs from preregistration")
    return specification


def run_research(
    db_path: str | Path,
    settings: ResearchSettings,
    factors: pd.DataFrame,
    spy_returns: pd.Series,
    hypotheses: Iterable[ConditioningHypothesis] = HYPOTHESES,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Run the registered conditioners independently and summarize results."""
    hypotheses = tuple(hypotheses)
    start_ts, end_ts = pd.Timestamp(settings.start), pd.Timestamp(settings.end)
    observations: list[dict] = []
    execution_dates: set[pd.Timestamp] = set()
    con = sqlite3.connect(str(db_path))
    metadata = _candidate_metadata(con, settings.start, settings.end)
    try:
        for year in range(start_ts.year, end_ts.year + 1):
            lo = max(start_ts, pd.Timestamp(year=year, month=1, day=1))
            hi = min(end_ts, pd.Timestamp(year=year, month=12, day=31))
            prices, earnings = load_research_window(con, metadata, lo, hi)
            features = build_features(prices, earnings, lo, hi, settings=settings)
            for _, day in features.groupby("date", sort=True):
                if day.empty:
                    continue
                execution_dates.add(pd.Timestamp(day.entry_date.iloc[0]))
                for hypothesis in hypotheses:
                    observations.extend(evaluate_day(
                        day, hypothesis, settings.min_names, settings.min_names_per_leg
                    ))
    finally:
        con.close()

    obs = pd.DataFrame(observations)
    if obs.empty:
        raise RuntimeError("No valid observations; check data coverage and eligibility settings")
    calendar = pd.DatetimeIndex(sorted(execution_dates))
    bucket_rows: list[dict] = []
    contrast_rows: list[dict] = []
    results: dict = {"hypotheses": {}}
    for hypothesis in hypotheses:
        hobs = obs[obs.hypothesis_id == hypothesis.hypothesis_id]
        bucket_results = {}
        for bucket in hypothesis.buckets:
            bobs = hobs[hobs.bucket == bucket]
            if bobs.empty:
                continue
            metrics = summarize_bucket(bobs, factors, spy_returns, calendar)
            bucket_results[bucket] = metrics
            flat = {k: v for k, v in metrics.items() if k not in {"regime_behavior", "subperiod_behavior"}}
            for regime, values in metrics["regime_behavior"].items():
                for key, value in values.items():
                    flat[f"regime_{regime}_{key}"] = value
            for subperiod, values in metrics["subperiod_behavior"].items():
                for key, value in values.items():
                    flat[f"subperiod_{subperiod}_{key}"] = value
            bucket_rows.append({
                "hypothesis_id": hypothesis.hypothesis_id,
                "feature": hypothesis.feature, "bucket": bucket, **flat,
            })
        fav = hobs[hobs.bucket == hypothesis.favorable_bucket].set_index("signal_date").spread_gross
        adv = hobs[hobs.bucket == hypothesis.adverse_bucket].set_index("signal_date").spread_gross
        if hypothesis.feature == "market_regime":
            contrast_method = "HAC difference in mean across mutually exclusive regime dates"
            test = _hac_group_difference(fav, adv)
            if test["n"] < settings.minimum_contrast_days or not np.isfinite(test["tstat"]):
                verdict = "UNRESOLVED / UNDERPOWERED"
            elif test["tstat"] >= settings.familywise_t_threshold:
                verdict = "SUPPORTED"
            elif test["tstat"] <= -settings.familywise_t_threshold:
                verdict = "FALSIFIED"
            else:
                verdict = "UNRESOLVED / UNDERPOWERED"
        else:
            contrast_method = "paired same-signal-date spread difference with HAC mean test"
            difference = (fav - adv).dropna()
            verdict, test = classify_contrast(
                difference, settings.familywise_t_threshold, settings.minimum_contrast_days
            )
        contrast = {
            "hypothesis_id": hypothesis.hypothesis_id,
            "feature": hypothesis.feature,
            "favorable_bucket": hypothesis.favorable_bucket,
            "adverse_bucket": hypothesis.adverse_bucket,
            "classification": verdict,
            "contrast_method": contrast_method,
            "contrast_mean_daily": test["mean"],
            "contrast_annualized": (
                (1 + test["mean"]) ** TRADING_DAYS - 1
                if np.isfinite(test["mean"]) and test["mean"] > -1 else np.nan
            ),
            "contrast_hac_tstat": test["tstat"],
            "contrast_pvalue": test["pvalue"],
            "contrast_days": test["n"],
            "familywise_t_threshold": settings.familywise_t_threshold,
            "historical_evidence_stage": "DEVELOPMENT / NOT INDEPENDENT VALIDATION",
        }
        contrast_rows.append(contrast)
        results["hypotheses"][hypothesis.hypothesis_id] = {
            "specification": asdict(hypothesis), "classification": verdict,
            "contrast": contrast, "buckets": bucket_results,
        }
    results["taxonomy"] = list(TAXONOMY)
    results["evidence_stage"] = "historical development; prospective confirmation required"
    return pd.DataFrame(bucket_rows), pd.DataFrame(contrast_rows), obs, results


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _git_commit(repo_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return None


def write_outputs(
    bucket_results: pd.DataFrame,
    contrasts: pd.DataFrame,
    observations: pd.DataFrame,
    results: dict,
    factors: pd.DataFrame,
    spy_returns: pd.Series,
    settings: ResearchSettings,
    output_root: str | Path,
    preregistration_path: str | Path,
    db_path: str | Path,
) -> Path:
    preregistration_path = Path(preregistration_path)
    prereg_bytes = preregistration_path.read_bytes()
    prereg_sha = hashlib.sha256(prereg_bytes).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(contrasts.to_csv(index=False).encode()).hexdigest()[:10]
    out = Path(output_root) / f"{stamp}_{digest}"
    out.mkdir(parents=True, exist_ok=False)

    bucket_results.to_csv(out / "bucket_metrics.csv", index=False)
    contrasts.to_csv(out / "hypothesis_contrasts.csv", index=False)
    observations.to_csv(out / "daily_diagnostics.csv", index=False)
    factors.to_csv(out / "factor_returns.csv")
    spy_returns.to_csv(out / "spy_total_return_daily.csv")
    (out / "results.json").write_text(
        json.dumps(_json_safe(results), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (out / "preregistration_snapshot.json").write_bytes(prereg_bytes)

    repo_root = Path(__file__).resolve().parents[2]
    db = Path(db_path).resolve()
    manifest = {
        "created_utc": stamp,
        "evidence_stage": "historical development; NOT fresh validation",
        "prospective_confirmation_required": True,
        "holdout_budget_remaining": 0,
        "settings": asdict(settings),
        "hypotheses": [h.hypothesis_id for h in HYPOTHESES],
        "execution": "signal at close[t]; enter open[t+1]; exit close[t+1]",
        "cost_model": "ADV-tiered per-side costs charged on entry and exit",
        "preregistration_sha256": prereg_sha,
        "source_database": str(db),
        "source_database_size": db.stat().st_size if db.exists() else None,
        "source_database_mtime_utc": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).isoformat() if db.exists() else None,
        "git_commit": _git_commit(repo_root),
        "result_files_sha256": {},
    }
    for name in ("bucket_metrics.csv", "hypothesis_contrasts.csv", "daily_diagnostics.csv", "results.json"):
        manifest["result_files_sha256"][name] = hashlib.sha256((out / name).read_bytes()).hexdigest()
    (out / "run_manifest.json").write_text(
        json.dumps(_json_safe(manifest), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    def pct(value) -> str:
        return f"{value:.2%}" if pd.notna(value) else "N/A"

    def num(value) -> str:
        return f"{value:.2f}" if pd.notna(value) else "N/A"

    lines = [
        "# IBS Conditioning Research", "",
        "> Historical development evidence only. The old holdout is consumed; these results require prospective confirmation.", "",
        f"Requested development sample: {settings.start} through {settings.end}. ",
        "Signal is formed at close and traded next-session open to close. Every conditioner is tested independently.", "",
        "## Independent hypothesis results", "",
        "| Hypothesis | Contrast | Annualized difference | HAC t | Days | Classification |",
        "|---|---|---:|---:|---:|---|",
    ]
    for _, row in contrasts.iterrows():
        lines.append(
            f"| {row.hypothesis_id} | {row.favorable_bucket} - {row.adverse_bucket} | "
            f"{pct(row.contrast_annualized)} | {row.contrast_hac_tstat:.2f} | "
            f"{int(row.contrast_days)} | {row.classification} |"
        )
    lines.extend([
        "", "## Costed bucket portfolios", "",
        "| Hypothesis | Bucket | Net CAGR | SPY CAGR | Excess CAGR | Alpha | Alpha t | Sharpe | Max DD | Turnover | IC |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for _, row in bucket_results.iterrows():
        if row.bucket not in {
            next(h for h in HYPOTHESES if h.hypothesis_id == row.hypothesis_id).favorable_bucket,
            next(h for h in HYPOTHESES if h.hypothesis_id == row.hypothesis_id).adverse_bucket,
        }:
            continue
        lines.append(
            f"| {row.hypothesis_id} | {row.bucket} | {pct(row.cagr_net)} | {pct(row.spy_cagr)} | "
            f"{pct(row.excess_cagr)} | {pct(row.alpha_annual)} | {num(row.alpha_tstat)} | "
            f"{num(row.sharpe)} | {pct(row.max_drawdown)} | {num(row.annual_two_way_turnover)} | {num(row.ic_mean)} |"
        )
    lines.extend([
        "", "## Interpretation rules", "",
        f"The familywise threshold is |t| >= {settings.familywise_t_threshold:.2f} across the ten registered conditioning hypotheses. "
        "SUPPORTED means the preregistered contrast has the expected sign and clears that threshold; FALSIFIED means it clears the threshold in the opposite direction; all other outcomes are UNRESOLVED / UNDERPOWERED.", "",
        "A conditioning result is not a portfolio and is not assessed against the 20% finished-portfolio target. Full factor loadings, drawdown duration, costs, capacity, quintile behavior, 2/5-session horizon decay, subperiods, and regime behavior are in `bucket_metrics.csv` and `results.json`.", "",
        "## Provenance and limitations", "",
        "Earnings events use Sharadar ARQ filing/publication dates, with exact-date matching and no future-day tolerance. The bundle does not include announcement timestamps, historical bid/ask spreads, or historical borrow data. The combined spread/fees/slippage cost model is therefore an ADV-tier estimate, and before/after-market earnings timing cannot be separated. Market regime uses a PIT lagged-cap-weighted internal equity-market index; SPY is used as the total-return benchmark, not as a signal input. Daily close-to-close factors are the nearest available factor proxy for the open-to-close strategy return.", "",
        "No existing paper track or state file was read as research evidence or modified by this run.",
    ])
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="V2/data/sharadar.db")
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2018-12-31")
    parser.add_argument("--factors-csv")
    parser.add_argument("--spy-csv")
    parser.add_argument("--output-root", default="V2/reports/ibs_conditioning")
    parser.add_argument("--preregistration", default="research/ibs_conditioning_preregistration.json")
    parser.add_argument(
        "--acknowledge-used-historical-data", action="store_true",
        help="Required: confirms this is development/characterization, not fresh validation.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.acknowledge_used_historical_data:
        parser.error("--acknowledge-used-historical-data is required")
    settings = ResearchSettings(start=args.start, end=args.end)
    preregistration = Path(args.preregistration)
    if not preregistration.exists():
        parser.error(f"preregistration does not exist: {preregistration}")
    try:
        validate_preregistration(preregistration, settings)
    except (ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    data_end = (pd.Timestamp(settings.end) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    factors = load_factors(settings.start, data_end, args.factors_csv)
    spy = load_spy_returns(settings.start, data_end, args.spy_csv)
    bucket_results, contrasts, observations, results = run_research(
        args.db, settings, factors, spy
    )
    out = write_outputs(
        bucket_results, contrasts, observations, results, factors, spy,
        settings, args.output_root, preregistration, args.db,
    )
    print(out)


if __name__ == "__main__":
    main()
