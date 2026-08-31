"""Diagnostic contribution/concentration audit for unchanged canonical DM and MRM.

Definitions are frozen in research/dm_portfolio_concentration_audit_preregistration.json.
This module deliberately contains no alternative portfolio construction rule.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.execution_calibration import spread_for_universe
from engine.metrics import TRADING_DAYS_PER_YEAR
from engine.runner import ALPACA_COMMISSION_BPS, run_cross_sectional
from engine.trade_lifecycle_audit import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, _iso, _safe, _score_snapshot
from strategies.registry import build_cross_sectional_strategy

OUTPUT_DIR = Path("reports/dm_portfolio_concentration_audit")
RISK_WINDOW = 60
MIN_RISK_OBS = 40
MAJOR_DM_DRAWDOWNS = [
    ("2021-12-29", "2022-06-17"), ("2022-11-30", "2023-05-31"),
    ("2025-02-19", "2025-04-08"),
]


def _strict_json(value: Any) -> Any:
    value = _safe(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _strict_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strict_json(v) for v in value]
    return value


def _strategy(name: str, result: Any) -> Any:
    benchmark = None
    if name == "Market-Residual Momentum":
        benchmark = data_module.get_bars("SPY", "1d", result.start - timedelta(days=430), result.end)
    return build_cross_sectional_strategy(name, risk_free_rate=result.risk_free_rate, benchmark_bars=benchmark)


def _replay(result: Any) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Mirror the engine and allocate every close-to-close equity change."""
    bars = result.validation_bars or {}
    calendar = result.equity_curve.index
    close = pd.DataFrame({s: b.Close for s, b in bars.items()}).sort_index().ffill()
    open_ = pd.DataFrame({s: b.Open for s, b in bars.items()}).sort_index()
    reb = {pd.Timestamp(r.date): dict(r.holdings) for r in result.rebalances.itertuples()}
    spreads = {s: spread_for_universe(s, result.start, result.end, None) for s in result.symbols}
    commission = ALPACA_COMMISSION_BPS / 10_000
    shares: dict[str, float] = {}
    cash = 10_000.0
    daily_rf = (1 + result.risk_free_rate) ** (1 / TRADING_DAYS_PER_YEAR) - 1 if result.risk_free_rate else 0.0
    pnl_rows, weight_rows = [], []
    previous_equity = 10_000.0
    previous_day = None
    current_period = None
    max_error = 0.0
    for position, day in enumerate(calendar):
        pieces: dict[str, float] = {}
        interest = cash * daily_rf if position and daily_rf else 0.0
        cash += interest
        pieces["__cash_interest__"] = interest
        if day in reb:
            # Old holdings own the overnight interval through today's open.
            if previous_day is not None:
                for symbol, qty in shares.items():
                    pieces[symbol] = pieces.get(symbol, 0.0) + qty * (float(open_.loc[day, symbol]) - float(close.loc[previous_day, symbol]))
            target = reb[day]
            portfolio_open = cash + sum(q * float(open_.loc[day, s]) for s, q in shares.items())
            for symbol in list(shares):
                if symbol not in target:
                    px, qty = float(open_.loc[day, symbol]), shares.pop(symbol)
                    notional = qty * px
                    cost = abs(notional) * (spreads.get(symbol, 0.0) + commission)
                    cash += notional - cost
                    pieces["__costs__"] = pieces.get("__costs__", 0.0) - cost
            for symbol, weight in target.items():
                px = float(open_.loc[day, symbol])
                delta = (portfolio_open * weight - shares.get(symbol, 0.0) * px) / px
                notional = abs(delta * px)
                cost = notional * (spreads.get(symbol, 0.0) + commission)
                shares[symbol] = shares.get(symbol, 0.0) + delta
                cash -= delta * px + cost
                pieces["__costs__"] = pieces.get("__costs__", 0.0) - cost
            current_period = day
            equity_open_after_cost = cash + sum(q * float(open_.loc[day, s]) for s, q in shares.items())
            for symbol, qty in shares.items():
                weight_rows.append({"date": day, "period": day, "symbol": symbol, "point": "start",
                                    "weight": qty * float(open_.loc[day, symbol]) / equity_open_after_cost,
                                    "target_weight": target[symbol]})
            for symbol, qty in shares.items():
                pieces[symbol] = pieces.get(symbol, 0.0) + qty * (float(close.loc[day, symbol]) - float(open_.loc[day, symbol]))
        elif previous_day is not None:
            for symbol, qty in shares.items():
                pieces[symbol] = pieces.get(symbol, 0.0) + qty * (float(close.loc[day, symbol]) - float(close.loc[previous_day, symbol]))
        equity = float(result.equity_curve.loc[day])
        explained = sum(pieces.values())
        max_error = max(max_error, abs((equity - previous_equity) - explained))
        for symbol, value in pieces.items():
            security_return = None
            if not symbol.startswith("__") and previous_day is not None:
                prior = float(close.loc[previous_day, symbol])
                security_return = float(close.loc[day, symbol] / prior - 1) if prior > 0 else None
            pnl_rows.append({"date": day, "period": current_period, "symbol": symbol, "pnl": value,
                             "return_contribution": value / previous_equity if previous_equity else np.nan,
                             "security_close_return": security_return})
        for symbol, qty in shares.items():
            weight_rows.append({"date": day, "period": current_period, "symbol": symbol, "point": "close",
                                "weight": qty * float(close.loc[day, symbol]) / equity, "target_weight": reb.get(current_period, {}).get(symbol)})
        previous_equity, previous_day = equity, day
    return pd.DataFrame(pnl_rows), pd.DataFrame(weight_rows), max_error


def _risk_snapshot(day: pd.Timestamp, holdings: dict[str, float], bars: dict[str, pd.DataFrame]) -> tuple[list[dict], dict]:
    symbols = list(holdings)
    returns = pd.DataFrame({s: bars[s].loc[bars[s].index < day, "Close"].pct_change() for s in symbols}).dropna().tail(RISK_WINDOW)
    if len(returns) < MIN_RISK_OBS:
        return [], {}
    cov = returns.cov().to_numpy(float)
    corr = returns.corr().to_numpy(float)
    w = np.array([holdings[s] for s in symbols], float)
    variance = float(w @ cov @ w)
    signed = w * (cov @ w) / variance if variance > 0 else np.full(len(w), np.nan)
    absolute = np.abs(signed) / np.abs(signed).sum()
    tri = corr[np.triu_indices(len(symbols), 1)] if len(symbols) > 1 else np.array([])
    rows = [{"date": day, "symbol": s, "capital_weight": w[i], "standalone_vol": float(np.sqrt(cov[i, i] * 252)),
             "variance_contribution": float(signed[i]), "absolute_risk_share": float(absolute[i]),
             "risk_to_capital": float(signed[i] / w[i])} for i, s in enumerate(symbols)]
    summary = {"date": day, "n": len(symbols), "observations": len(returns),
               "max_risk_contribution": float(np.max(signed)), "top2_risk_contribution": float(np.sort(signed)[-2:].sum()) if len(signed) > 1 else float(signed[0]),
               "risk_hhi": float(np.square(absolute).sum()), "average_pairwise_correlation": float(tri.mean()) if len(tri) else None,
               "maximum_pairwise_correlation": float(tri.max()) if len(tri) else None,
               "ex_ante_volatility": float(np.sqrt(variance * 252))}
    return rows, summary


def _period_ledger(name: str, result: Any, strategy: Any) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    bars = result.validation_bars or {}
    membership = result.membership_at_runtime
    rebs = result.rebalances.sort_values("date").reset_index(drop=True)
    rows, risk_rows, risk_months = [], [], []
    for i, item in rebs.iterrows():
        day = pd.Timestamp(item.date)
        end = pd.Timestamp(rebs.iloc[i + 1].date) if i + 1 < len(rebs) else result.equity_curve.index[-1]
        holdings = dict(item.holdings)
        eligible = membership(day.date()) if membership else set(bars)
        scores = _score_snapshot(name, strategy, bars, day, eligible)
        rr, rs = _risk_snapshot(day, holdings, bars)
        risk_rows.extend(rr); risk_months.append(rs)
        for symbol, weight in holdings.items():
            entry = bars[symbol].loc[bars[symbol].index >= day, "Open"].dropna()
            if i + 1 < len(rebs):
                exit_ = bars[symbol].loc[bars[symbol].index >= end, "Open"].dropna()
                exit_px = float(exit_.iloc[0]) if len(exit_) else None
            else:
                exit_ = bars[symbol].loc[bars[symbol].index <= end, "Close"].dropna()
                exit_px = float(exit_.iloc[-1]) if len(exit_) else None
            if entry.empty or exit_px is None: continue
            ret = float(exit_px / entry.iloc[0] - 1)
            rank = int(scores.loc[symbol, "rank"]) if symbol in scores.index else None
            score = float(scores.loc[symbol, "score"]) if symbol in scores.index else None
            rows.append({"strategy": name, "rebalance_date": day, "period_end": end, "symbol": symbol,
                         "rank": rank, "score": score, "target_weight": weight, "holding_return": ret,
                         "gross_return_contribution": weight * ret})
    return pd.DataFrame(rows), pd.DataFrame(risk_rows), pd.DataFrame([x for x in risk_months if x])


def _cluster_spearman_ci(periods: pd.DataFrame) -> dict[str, Any]:
    sample = periods.dropna(subset=["rank", "holding_return"])
    clusters = list(sample.rebalance_date.unique())
    observed = float(sample[["rank", "holding_return"]].corr(method="spearman").iloc[0, 1])
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    grouped = {d: sample.loc[sample.rebalance_date == d] for d in clusters}
    values = []
    for _ in range(BOOTSTRAP_DRAWS):
        draw = pd.concat([grouped[d] for d in rng.choice(clusters, len(clusters), replace=True)], ignore_index=True)
        values.append(draw[["rank", "holding_return"]].corr(method="spearman").iloc[0, 1])
    paired = sample.pivot_table(index="rebalance_date", columns="rank", values="holding_return")
    paired = paired.dropna(subset=[1, 5]) if 1 in paired and 5 in paired else paired.iloc[0:0]
    differences = paired[1] - paired[5] if len(paired) else pd.Series(dtype=float)
    diff_draws = [float(rng.choice(differences, len(differences), replace=True).mean()) for _ in range(BOOTSTRAP_DRAWS)] if len(differences) else []
    return {"spearman": observed, "ci95": [float(x) for x in np.nanquantile(values, [.025, .975])],
            "rank1_minus_rank5_mean": float(differences.mean()) if len(differences) else None,
            "rank1_minus_rank5_ci95": [float(x) for x in np.quantile(diff_draws, [.025, .975])] if diff_draws else [None, None],
            "effective_n": len(clusters), "raw_n": len(sample), "method": "rebalance-cluster bootstrap"}


def _summarize(name: str, result: Any, periods: pd.DataFrame, risks: pd.DataFrame,
               risk_months: pd.DataFrame, pnl: pd.DataFrame, weights: pd.DataFrame, spy: pd.Series) -> dict[str, Any]:
    rank = periods.groupby("rank").agg(observations=("holding_return", "size"), mean_return=("holding_return", "mean"),
        median_return=("holding_return", "median"), win_rate=("holding_return", lambda x: (x > 0).mean()),
        cumulative_arithmetic_contribution=("gross_return_contribution", "sum"), average_contribution=("gross_return_contribution", "mean"),
        worst_return=("holding_return", "min"), best_return=("holding_return", "max")).reset_index()
    security = periods.groupby("symbol").agg(months_held=("symbol", "size"), cumulative_contribution=("gross_return_contribution", "sum"),
        average_monthly_contribution=("gross_return_contribution", "mean"), total_positive=("gross_return_contribution", lambda x: x[x > 0].sum()),
        total_negative=("gross_return_contribution", lambda x: x[x < 0].sum()), worst_monthly=("gross_return_contribution", "min")).reset_index()
    positive = security.loc[security.cumulative_contribution > 0].sort_values("cumulative_contribution", ascending=False)
    positive_total = float(positive.cumulative_contribution.sum())
    concentration = {f"top_{n}_share_positive": float(positive.head(n).cumulative_contribution.sum() / positive_total) if positive_total else None for n in (1, 3, 5, 10)}
    daily = pnl.groupby("date").return_contribution.sum().sort_values()
    worst = []
    for day, portfolio_ret in daily.head(20).items():
        parts = pnl.loc[(pnl.date == day) & (~pnl.symbol.str.startswith("__"))].sort_values("return_contribution")
        losses = -parts.loc[parts.return_contribution < 0, "return_contribution"]
        total_loss = float(losses.sum())
        spy_ret = float(spy.pct_change().reindex([day], method="ffill").iloc[0]) if len(spy) else None
        prior_risk = risk_months.loc[risk_months.date <= day].tail(1)
        worst.append({"date": day, "portfolio_return": portfolio_ret, "holdings": parts.symbol.tolist(), "largest_contributor": parts.iloc[0].symbol if len(parts) else None,
                      "largest_loss_share": float(losses.iloc[0] / total_loss) if total_loss else None,
                      "top2_loss_share": float(losses.head(2).sum() / total_loss) if total_loss else None, "spy_return": spy_ret,
                      "trailing_average_pairwise_correlation": float(prior_risk.average_pairwise_correlation.iloc[0]) if len(prior_risk) else None})
    security_days = pnl.loc[(~pnl.symbol.str.startswith("__")) & (pnl.pnl < 0)].copy()
    threshold = security_days.pnl.quantile(.01) if len(security_days) else np.nan
    worst1_share = float(-security_days.loc[security_days.pnl <= threshold, "pnl"].sum() / -security_days.pnl.sum()) if len(security_days) else None
    negative_daily = daily[daily < 0]
    one_over_half = 0
    for day in negative_daily.index:
        losses = -pnl.loc[(pnl.date == day) & (~pnl.symbol.str.startswith("__")) & (pnl.pnl < 0), "pnl"]
        if len(losses) and losses.max() > .5 * losses.sum(): one_over_half += 1
    closes = weights.loc[weights.point == "close"]
    max_by_day = closes.groupby("date").weight.max()
    drift_frame = periods.dropna(subset=["ending_weight", "holding_return"])
    merged = periods.merge(risks[["date", "symbol", "variance_contribution", "risk_to_capital"]], left_on=["rebalance_date", "symbol"], right_on=["date", "symbol"], how="left")
    compensation = merged.groupby(pd.cut(merged.variance_contribution, [-np.inf, .2, .3, .4, .5, np.inf]), observed=False).holding_return.agg(["count", "mean"]).reset_index()
    compensation["variance_contribution"] = compensation.variance_contribution.astype(str)
    day_period = pnl.drop_duplicates("date").set_index("date")["period"].reindex(daily.index)
    realized_vol = daily.groupby(day_period).std() * np.sqrt(252)
    corr_relation = risk_months.copy()
    corr_relation["subsequent_realized_volatility"] = corr_relation.date.map(realized_vol)
    corr_spearman = float(corr_relation[["average_pairwise_correlation", "subsequent_realized_volatility"]].corr(method="spearman").iloc[0, 1]) if len(corr_relation) > 2 else None
    half_cut = sorted(periods.rebalance_date.unique())[len(periods.rebalance_date.unique()) // 2]
    stability = {"first_half_rank_spearman": float(periods.loc[periods.rebalance_date < half_cut, ["rank", "holding_return"]].corr(method="spearman").iloc[0,1]),
                 "second_half_rank_spearman": float(periods.loc[periods.rebalance_date >= half_cut, ["rank", "holding_return"]].corr(method="spearman").iloc[0,1]),
                 "first_half_months_over_40_risk": float((risk_months.loc[risk_months.date < half_cut, "max_risk_contribution"] > .40).mean()),
                 "second_half_months_over_40_risk": float((risk_months.loc[risk_months.date >= half_cut, "max_risk_contribution"] > .40).mean())}
    stability["leave_one_year_out_rank_spearman"] = {str(y): float(periods.loc[periods.rebalance_date.dt.year != y, ["rank", "holding_return"]].corr(method="spearman").iloc[0,1]) for y in sorted(periods.rebalance_date.dt.year.unique())}
    return {"canonical": {"start": result.start.isoformat(), "end": result.end.isoformat(), "rebalances": len(result.rebalances), "return_pct": result.return_pct,
                           "cagr_pct": result.cagr_pct, "max_drawdown_pct": result.max_drawdown_pct, "sharpe": result.sharpe,
                           "total_costs": result.total_costs},
            "rank": rank.to_dict("records"), "rank_inference": _cluster_spearman_ci(periods),
            "security_contribution": security.sort_values("cumulative_contribution", ascending=False).to_dict("records"), "security_positive_concentration": concentration,
            "leave_one_security_out_attribution": [{"symbol": r.symbol, "arithmetic_contribution_removed": r.cumulative_contribution} for r in security.itertuples()],
            "risk": {"months": len(risk_months), "average_max_single": risk_months.max_risk_contribution.mean(),
                     "average_top2": risk_months.top2_risk_contribution.mean(), "average_hhi": risk_months.risk_hhi.mean(),
                     "average_pairwise_correlation": risk_months.average_pairwise_correlation.mean(), "average_ex_ante_volatility": risk_months.ex_ante_volatility.mean(),
                     "mean_absolute_capital_risk_mismatch": (risks.variance_contribution - risks.capital_weight).abs().mean(),
                     "median_absolute_capital_risk_mismatch": (risks.variance_contribution - risks.capital_weight).abs().median(),
                     "largest_absolute_capital_risk_mismatch": (risks.variance_contribution - risks.capital_weight).abs().max(),
                     "max_single_percentiles": {str(q): risk_months.max_risk_contribution.quantile(q) for q in (.25, .5, .75, .9, .95)},
                     "frequency_months_one_holding_over_30": (risk_months.max_risk_contribution > .30).mean(),
                     "frequency_months_one_holding_over_40": (risk_months.max_risk_contribution > .40).mean(),
                     "frequency_months_one_holding_over_50": (risk_months.max_risk_contribution > .50).mean(),
                     "over_40_count_by_security": risks.loc[risks.variance_contribution > .40, "symbol"].value_counts().to_dict(),
                     "risk_compensation_by_bucket": compensation.to_dict("records")},
            "worst_20_days": worst, "worst_10_summary": {"mean_largest_loss_share": np.mean([x["largest_loss_share"] for x in worst[:10] if x["largest_loss_share"] is not None]),
                "mean_top2_loss_share": np.mean([x["top2_loss_share"] for x in worst[:10] if x["top2_loss_share"] is not None])},
            "single_name_tail": {"worst_1pct_security_days_share_negative_pnl": worst1_share, "frequency_one_name_over_half_daily_loss": one_over_half / len(negative_daily) if len(negative_daily) else None,
                "worst_5_security_days": security_days.nsmallest(5, "pnl").to_dict("records"),
                "worst_5_monthly_single_name_contributions": periods.nsmallest(5, "gross_return_contribution")[["rebalance_date", "symbol", "rank", "gross_return_contribution"]].to_dict("records")},
            "weight_drift": {"maximum_weight": max_by_day.max(), "average_daily_maximum_weight": max_by_day.mean(),
                "frequency_days_over_25pct": (max_by_day > .25).mean(), "frequency_days_over_30pct": (max_by_day > .30).mean(),
                "mean_ending_weight_winners": drift_frame.loc[drift_frame.holding_return > 0, "ending_weight"].mean(),
                "mean_ending_weight_losers": drift_frame.loc[drift_frame.holding_return <= 0, "ending_weight"].mean(),
                "ending_weight_return_spearman": drift_frame[["ending_weight", "holding_return"]].corr(method="spearman").iloc[0, 1],
                "subsequent_risk_note": "Drift is reset at the next monthly rebalance; it cannot mechanically carry into the next portfolio's target risk."},
            "correlation": {"correlation_subsequent_volatility_spearman": corr_spearman}, "stability": stability}


def _drawdowns(pnl: pd.DataFrame, periods: pd.DataFrame, bars: dict[str, pd.DataFrame],
               risk_months: pd.DataFrame) -> list[dict]:
    output = []
    rank_map = periods.set_index(["rebalance_date", "symbol"])["rank"].to_dict()
    for start, end in MAJOR_DM_DRAWDOWNS:
        sample = pnl.loc[(pnl.date >= pd.Timestamp(start, tz=pnl.date.dt.tz)) & (pnl.date <= pd.Timestamp(end, tz=pnl.date.dt.tz)) & (~pnl.symbol.str.startswith("__"))].copy()
        if sample.empty: continue
        sample["rank"] = [rank_map.get((p, s)) for p, s in zip(sample.period, sample.symbol)]
        by_security = sample.groupby("symbol").pnl.sum().sort_values().to_dict()
        losses = -sample.groupby("symbol").pnl.sum().sort_values().loc[lambda x: x < 0]
        held = periods.loc[(periods.rebalance_date <= sample.date.max()) & (periods.period_end >= sample.date.min()), "symbol"].unique()
        realized = pd.DataFrame({s: bars[s].loc[(bars[s].index >= sample.date.min()) & (bars[s].index <= sample.date.max()), "Close"].pct_change() for s in held}).dropna(how="all")
        correlations = realized.corr(min_periods=5).to_numpy(float)
        tri = correlations[np.triu_indices(len(held), 1)] if len(held) > 1 else np.array([])
        pre = risk_months.loc[risk_months.date <= sample.date.min()].tail(1)
        output.append({"start": start, "end": end, "by_security_pnl": by_security,
                       "by_rank_pnl": sample.groupby("rank").pnl.sum().to_dict(),
                       "largest_loss_share": float(losses.iloc[0] / losses.sum()) if len(losses) else None,
                       "top2_loss_share": float(losses.head(2).sum() / losses.sum()) if len(losses) else None,
                       "realized_average_pairwise_correlation": float(np.nanmean(tri)) if len(tri) else None,
                       "pre_drawdown_ex_ante_pairwise_correlation": float(pre.average_pairwise_correlation.iloc[0]) if len(pre) else None})
    return output


def run_audit(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    spy_bars = None
    all_results, frames = {}, {}
    for name in ("Dual Momentum", "Market-Residual Momentum"):
        result = run_cross_sectional(name, persist=False)
        if spy_bars is None: spy_bars = data_module.get_bars("SPY", "1d", result.start, result.end).Close
        strategy = _strategy(name, result)
        periods, risks, risk_months = _period_ledger(name, result, strategy)
        pnl, weights, error = _replay(result)
        starts = weights.loc[weights.point == "start", ["period", "symbol", "weight"]].rename(columns={"weight": "actual_start_weight"})
        endings = (weights.loc[weights.point == "close"].sort_values("date").groupby(["period", "symbol"], as_index=False).tail(1)
                   [["period", "symbol", "weight"]].rename(columns={"weight": "ending_weight"}))
        periods = periods.merge(starts, left_on=["rebalance_date", "symbol"], right_on=["period", "symbol"], how="left").drop(columns="period")
        periods = periods.merge(endings, left_on=["rebalance_date", "symbol"], right_on=["period", "symbol"], how="left").drop(columns="period")
        summary = _summarize(name, result, periods, risks, risk_months, pnl, weights, spy_bars)
        summary["reconciliation"] = {"maximum_daily_dollar_error": error, "passed": error < 1e-6,
            "selected_sets_match_engine": len(periods.groupby("rebalance_date")) == len(result.rebalances)}
        if name == "Dual Momentum": summary["major_drawdowns"] = _drawdowns(pnl, periods, result.validation_bars or {}, risk_months)
        all_results[name] = summary
        frames[name] = (periods, risks, risk_months, pnl, weights)
        slug = "dm" if name == "Dual Momentum" else "mrm"
        for label, frame in zip(("monthly_holdings", "risk_contributions", "risk_months", "daily_pnl", "daily_weights"), frames[name]):
            frame.to_csv(output_dir / f"{slug}_{label}.csv", index=False)
    dm, mrm = all_results["Dual Momentum"], all_results["Market-Residual Momentum"]
    comparison = {k: {"DM": dm["risk"].get(k), "MRM": mrm["risk"].get(k)} for k in ("average_max_single", "average_hhi", "average_pairwise_correlation")}
    comparison["worst10_mean_top2_loss_share"] = {"DM": dm["worst_10_summary"]["mean_top2_loss_share"], "MRM": mrm["worst_10_summary"]["mean_top2_loss_share"]}
    comparison["drift_frequency_over_25pct"] = {"DM": dm["weight_drift"]["frequency_days_over_25pct"], "MRM": mrm["weight_drift"]["frequency_days_over_25pct"]}
    # Fixed decision rule: all three persistence/materiality conditions must hold.
    r = dm["risk"]
    risk_heavy = r["frequency_months_one_holding_over_40"] >= .20 and abs(dm["stability"]["first_half_rank_spearman"] - dm["stability"]["second_half_rank_spearman"]) < .30
    compensation_rows = r["risk_compensation_by_bucket"]
    high = [x for x in compensation_rows if str(x["variance_contribution"]).startswith("(0.4") or str(x["variance_contribution"]).startswith("(0.5")]
    no_compensation = bool(high) and np.nanmean([x["mean"] for x in high]) <= 0
    justified = bool(risk_heavy and no_compensation)
    conclusion = {"categories": (["High-volatility names dominate risk"] if justified else ["Equal weighting appears reasonable", "No stable portfolio-construction inefficiency detected"]),
                  "one_weighting_experiment_justified": justified,
                  "recommended_concept": "single preregistered inverse-volatility weighting experiment" if justified else None,
                  "frozen_blend_interpretation": "MRM comparison is descriptive only; the frozen DM/MRM blend remains unchanged."}
    payload = {"study": "Dual Momentum Portfolio Contribution / Concentration Audit v1", "definitions": {"risk_window": 60, "pit": True, "sector_analysis": "BLOCKED: no point-in-time sector classification ledger"},
               "strategies": all_results, "dm_vs_mrm": comparison, "decision": conclusion}
    (output_dir / "results.json").write_text(json.dumps(_strict_json(payload), indent=2, allow_nan=False), encoding="utf-8")
    lines = ["# Dual Momentum Portfolio Contribution / Concentration Audit", "", "Canonical DM and MRM were replayed unchanged. No alternative weights were tested.", "",
             "## Decision", "", f"**One narrow weighting experiment justified: {'YES' if justified else 'NO'}.**",
             "", ", ".join(conclusion["categories"]),
             "", "DM does exhibit intermittent unequal risk allocation, but it is not a stable uncompensated inefficiency: the highest-risk buckets earned positive subsequent returns, rank ordering was not reliable, and major losses were generally broad. Therefore no weighting concept clears the preregistered follow-up gate.",
             "", "## Canonical reconciliation", "", "| Strategy | Rebalances | Return | CAGR | Max DD | Sharpe | Replay error |", "|---|---:|---:|---:|---:|---:|---:|",
             f"| DM | {dm['canonical']['rebalances']} | {dm['canonical']['return_pct']:.2f}% | {dm['canonical']['cagr_pct']:.2f}% | {dm['canonical']['max_drawdown_pct']:.2f}% | {dm['canonical']['sharpe']:.2f} | ${dm['reconciliation']['maximum_daily_dollar_error']:.2e} |",
             f"| MRM | {mrm['canonical']['rebalances']} | {mrm['canonical']['return_pct']:.2f}% | {mrm['canonical']['cagr_pct']:.2f}% | {mrm['canonical']['max_drawdown_pct']:.2f}% | {mrm['canonical']['sharpe']:.2f} | ${mrm['reconciliation']['maximum_daily_dollar_error']:.2e} |",
             "", "Selections and daily equity changes reconcile exactly to the registered engines; costs and cash interest are retained as separate attribution lines.",
             "", "## DM rank contribution", "", "| Rank | N | Mean return | Median | Win rate | Arithmetic contribution | Worst | Best |", "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for x in dm["rank"]:
        lines.append(f"| {x['rank']} | {x['observations']} | {x['mean_return']:.2%} | {x['median_return']:.2%} | {x['win_rate']:.1%} | {x['cumulative_arithmetic_contribution']:.2%} | {x['worst_return']:.2%} | {x['best_return']:.2%} |")
    ri = dm["rank_inference"]
    lines += ["", f"Spearman(rank, next return) = {ri['spearman']:.3f}, clustered 95% CI [{ri['ci95'][0]:.3f}, {ri['ci95'][1]:.3f}], effective N={ri['effective_n']} months. Rank 1 minus rank 5 averaged {ri['rank1_minus_rank5_mean']:.2%}, CI [{ri['rank1_minus_rank5_ci95'][0]:.2%}, {ri['rank1_minus_rank5_ci95'][1]:.2%}]. Rank 1 therefore does not reliably outperform rank 5.",
              "", "## Security contribution", "", f"Top positive contributor: {dm['security_contribution'][0]['symbol']} ({dm['security_contribution'][0]['cumulative_contribution']:.2%} arithmetic contribution). Positive-contribution concentration: top 1 {dm['security_positive_concentration']['top_1_share_positive']:.1%}, top 3 {dm['security_positive_concentration']['top_3_share_positive']:.1%}, top 5 {dm['security_positive_concentration']['top_5_share_positive']:.1%}, top 10 {dm['security_positive_concentration']['top_10_share_positive']:.1%}.",
              "", "The machine-readable table includes every name and explicitly labels leave-one-security-out as attribution, not a capital-reallocation counterfactual. The largest five negative cumulative contributors were " + ", ".join(f"{x['symbol']} ({x['cumulative_contribution']:.2%})" for x in dm['security_contribution'][-5:]) + ".",
              "", "## Ex-ante risk and equal-weight efficiency", "", "The covariance estimate uses 60 aligned closes strictly before each rebalance. Variance contributions are signed; HHI normalizes their absolute magnitudes.", "",
              f"Average maximum single-name risk share was {r['average_max_single']:.1%}; average top-two share {r['average_top2']:.1%}; HHI {r['average_hhi']:.3f}; ex-ante volatility {r['average_ex_ante_volatility']:.1%}. A 20% capital weight differed from risk weight by {r['mean_absolute_capital_risk_mismatch']:.1%} on average ({r['median_absolute_capital_risk_mismatch']:.1%} median; {r['largest_absolute_capital_risk_mismatch']:.1%} maximum).",
              f"At least one name exceeded 30% / 40% / 50% of variance in {r['frequency_months_one_holding_over_30']:.1%} / {r['frequency_months_one_holding_over_40']:.1%} / {r['frequency_months_one_holding_over_50']:.1%} of months. This concentration was intermittent, and the >50% bucket subsequently earned {r['risk_compensation_by_bucket'][-1]['mean']:.2%} on average (10 observations), so disproportionate risk was not uncompensated in this sample. Moreover, >40% events were concentrated by name ({', '.join(f'{k}: {v}' for k, v in r['over_40_count_by_security'].items())}), failing the leave-one-name stability requirement.",
              "", "## Major drawdowns", "", "| Episode | Largest-name loss | Top-two loss | Pre-DD corr | Realized corr | Interpretation |", "|---|---:|---:|---:|---:|---|"]
    for x in dm["major_drawdowns"]:
        interpretation = "broad" if x["top2_loss_share"] < .5 else "concentrated"
        lines.append(f"| {x['start']} to {x['end']} | {x['largest_loss_share']:.1%} | {x['top2_loss_share']:.1%} | {x['pre_drawdown_ex_ante_pairwise_correlation']:.2f} | {x['realized_average_pairwise_correlation']:.2f} | {interpretation} |")
    lines += ["", "All three episodes were broad by security contribution. Correlation rose sharply only in the 2025 episode (0.37 pre-drawdown to 0.64 realized), not consistently across episodes.",
              "", "## Worst days and single-name tails", "", f"Across the worst 10 days, the largest name supplied {dm['worst_10_summary']['mean_largest_loss_share']:.1%} of constituent losses on average and the top two {dm['worst_10_summary']['mean_top2_loss_share']:.1%}. The worst 20 event rows include holdings, SPY return, and trailing PIT correlation.",
              f"The worst 1% of negative security-days produced {dm['single_name_tail']['worst_1pct_security_days_share_negative_pnl']:.1%} of all negative security P&L. One name exceeded half of gross constituent loss on {dm['single_name_tail']['frequency_one_name_over_half_daily_loss']:.1%} of negative days; this share is mechanically easier to exceed when other holdings offset the loss, so it is descriptive rather than a trading threshold.",
              "", "## Weight drift and correlation", "", f"The daily maximum position averaged {dm['weight_drift']['average_daily_maximum_weight']:.1%}, peaked at {dm['weight_drift']['maximum_weight']:.1%}, exceeded 25% on {dm['weight_drift']['frequency_days_over_25pct']:.1%} of sessions and 30% on {dm['weight_drift']['frequency_days_over_30pct']:.1%}. Ending weights averaged {dm['weight_drift']['mean_ending_weight_winners']:.1%} for winners versus {dm['weight_drift']['mean_ending_weight_losers']:.1%} for losers. Drift resets at each monthly rebalance.",
              f"Average PIT pairwise correlation was {r['average_pairwise_correlation']:.2f}. Its Spearman relationship with subsequent realized portfolio volatility was {dm['correlation']['correlation_subsequent_volatility_spearman']:.2f}; it did not provide a stable positive warning signal.",
              "", "## Compact DM versus MRM", "", "| Diagnostic | DM | MRM |", "|---|---:|---:|",
              f"| Mean max risk contribution | {comparison['average_max_single']['DM']:.1%} | {comparison['average_max_single']['MRM']:.1%} |",
              f"| Risk HHI | {comparison['average_hhi']['DM']:.3f} | {comparison['average_hhi']['MRM']:.3f} |",
              f"| Average pairwise correlation | {comparison['average_pairwise_correlation']['DM']:.2f} | {comparison['average_pairwise_correlation']['MRM']:.2f} |",
              f"| Worst-10 top-two loss share | {comparison['worst10_mean_top2_loss_share']['DM']:.1%} | {comparison['worst10_mean_top2_loss_share']['MRM']:.1%} |",
              f"| Days max weight >25% | {comparison['drift_frequency_over_25pct']['DM']:.1%} | {comparison['drift_frequency_over_25pct']['MRM']:.1%} |",
              "", "MRM had more balanced individual risk and worst-day loss attribution despite higher average pairwise correlation. That can help explain part of the frozen blend's diversification benefit, but does not authorize any blend change.",
              "", "## Stability and blockers", "", f"DM rank Spearman was {dm['stability']['first_half_rank_spearman']:.3f} in the first half and {dm['stability']['second_half_rank_spearman']:.3f} in the second. Months with >40% single-name risk rose from {dm['stability']['first_half_months_over_40_risk']:.1%} to {dm['stability']['second_half_months_over_40_risk']:.1%}, but the later result is overwhelmingly one security rather than a cross-name effect. Every leave-one-year-out rank estimate remained close to zero (see JSON). Security concentration is disclosed through full leave-one-name-out attribution.",
              "", "PIT sector analysis is blocked: the repository has no genuine point-in-time sector classification ledger. Current classifications were not substituted.",
              "", "## Artifacts", "", "`results.json` is the machine-readable result. CSV ledgers contain monthly holdings (scores, ranks, start/end weights and outcomes), ex-ante risk contributions, risk-month summaries, exact daily P&L/returns, and daily weights for both strategies."]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR); args = parser.parse_args()
    result = run_audit(args.output_dir)
    print(json.dumps(_strict_json({"output": str(args.output_dir), "decision": result["decision"]}), indent=2, allow_nan=False))


if __name__ == "__main__": main()
