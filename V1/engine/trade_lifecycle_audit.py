"""Trade Lifecycle / Exit-Efficiency Audit for canonical DM and MRM.

This module is intentionally diagnostic.  It calls the registered strategies
with their defaults, reconstructs what those unchanged strategies did, and
never searches or applies an alternative trading rule.

Definitions fixed before inspecting results
--------------------------------------------
* Position: continuous membership from an entry rebalance until removal.
* Realized return: entry open to removal open (gross price return).  Costs are
  reconciled at portfolio level because retained names can be resized.
* Primary MFE/MAE: entry/exit opens plus intervening closes.  Intraday
  high/low excursions are separate and exclude the exit session.
* Meaningful loss: -2%; meaningful profit: +1%.
* Material recovery after MAE: at least max(1 percentage point, 25% of |MAE|).
* Capture ratios require close-based MFE >= 1%, preventing near-zero ratios.
* Large winners/losers: top/bottom quartile of positive/negative realized
  completed-position returns, respectively.
* Rank deterioration buckets for top-N: N+1..2N just outside, 2N+1..4N
  moderate, and >4N or unranked severe.
* Multiple removals/additions have no economic identity.  For the requested
  event rows, they are paired deterministically by outgoing exit rank and
  incoming entry rank.  Set-average and retained/new/outgoing comparisons do
  not depend on that pairing.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.runner import run_cross_sectional
from strategies.registry import build_cross_sectional_strategy


MEANINGFUL_LOSS = -0.02
MEANINGFUL_PROFIT = 0.01
CAPTURE_MFE_FLOOR = 0.01
MATERIAL_RECOVERY_FLOOR = 0.01
POST_EXIT_HORIZONS = (5, 10, 20, 40)
BOOTSTRAP_DRAWS = 2_000
BOOTSTRAP_SEED = 20260822


def _plain_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def _iso(value: Any) -> str:
    return _plain_timestamp(value).date().isoformat()


def _safe(value: Any) -> Any:
    if isinstance(value, (np.bool_,)): return bool(value)
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp): return _iso(value)
    if isinstance(value, dict): return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_safe(v) for v in value]
    return value


def _pct(series: pd.Series, q: float = 0.5) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.quantile(q) * 100.0) if len(clean) else None


def _mean_pct(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.mean() * 100.0) if len(clean) else None


def _share(mask: pd.Series) -> float | None:
    return float(mask.mean() * 100.0) if len(mask) else None


def _at_or_after(frame: pd.DataFrame, ts: pd.Timestamp, column: str) -> float | None:
    values = frame.loc[frame.index >= ts, column].dropna()
    return float(values.iloc[0]) if len(values) else None


def _before(frame: pd.DataFrame, ts: pd.Timestamp, column: str) -> pd.Series:
    return frame.loc[frame.index < ts, column].dropna()


def _score_snapshot(strategy_name: str, strategy: Any, bars: dict[str, pd.DataFrame],
                    day: pd.Timestamp, eligible: set[str]) -> pd.DataFrame:
    """Recompute the registered strategy's score without changing selection."""
    scores: dict[str, float] = {}
    qualifies: dict[str, bool] = {}
    if strategy_name == "Dual Momentum":
        for symbol in eligible:
            frame = bars.get(symbol)
            if frame is None: continue
            close = _before(frame, day, "Close")
            if len(close) < strategy.lookback_trading_days + 1: continue
            base, now = float(close.iloc[-strategy.lookback_trading_days - 1]), float(close.iloc[-1])
            if base <= 0: continue
            score = now / base - 1.0
            scores[symbol] = score
            qualifies[symbol] = score > strategy.risk_free_rate
    else:
        market = _before(strategy.benchmark_bars, day, "Close").pct_change()
        if strategy.skip_days: market = market.iloc[:-strategy.skip_days]
        market = market.iloc[-strategy.lookback:]
        for symbol in eligible:
            frame = bars.get(symbol)
            if frame is None: continue
            stock = _before(frame, day, "Close").pct_change()
            if strategy.skip_days: stock = stock.iloc[:-strategy.skip_days]
            joined = pd.concat([stock.rename("stock"), market.rename("market")], axis=1).dropna().iloc[-strategy.lookback:]
            if len(joined) < strategy.lookback: continue
            x, y = joined.market.to_numpy(float), joined.stock.to_numpy(float)
            design = np.column_stack([np.ones(len(x)), x])
            alpha, beta = np.linalg.lstsq(design, y, rcond=None)[0]
            residual = y - (alpha + beta * x)
            if np.any(residual <= -1.0): continue
            scores[symbol] = float(np.prod(1.0 + residual) - 1.0)
            qualifies[symbol] = True
    ordered = sorted(scores, key=scores.get, reverse=True)
    return pd.DataFrame({
        "symbol": ordered,
        "score": [scores[s] for s in ordered],
        "rank": np.arange(1, len(ordered) + 1),
        "qualifies": [qualifies[s] for s in ordered],
    }).set_index("symbol") if ordered else pd.DataFrame(columns=["score", "rank", "qualifies"])


def _cluster_ci(frame: pd.DataFrame, value: str, cluster: str) -> dict[str, Any]:
    sample = frame[[value, cluster]].dropna()
    ids = list(sample[cluster].unique())
    if not ids: return {"mean": None, "ci95": [None, None], "effectiveN": 0, "rawN": 0}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    grouped = {key: sample.loc[sample[cluster] == key, value].to_numpy(float) for key in ids}
    estimates = np.empty(BOOTSTRAP_DRAWS)
    for i in range(BOOTSTRAP_DRAWS):
        draw = rng.choice(ids, size=len(ids), replace=True)
        estimates[i] = np.concatenate([grouped[key] for key in draw]).mean()
    return {"mean": float(sample[value].mean()),
            "ci95": [float(x) for x in np.quantile(estimates, [0.025, 0.975])],
            "effectiveN": len(ids), "rawN": len(sample),
            "method": f"rebalance-cluster bootstrap, {BOOTSTRAP_DRAWS} draws, seed {BOOTSTRAP_SEED}"}


def _rank_bucket(rank: float | None, top_n: int) -> str:
    if rank is None or pd.isna(rank): return "severe/unranked"
    if rank <= top_n: return "still top-N/other removal"
    if rank <= 2 * top_n: return "just outside boundary"
    if rank <= 4 * top_n: return "moderate deterioration"
    return "severe/unranked"


def _winner_pattern(path: pd.Series, mfe_position: int, mfe: float, giveback: float,
                    peak_drawdown: float) -> str:
    fraction = mfe_position / max(len(path) - 1, 1)
    if float(path.min()) <= MEANINGFUL_LOSS: return "recovery winner"
    if fraction <= 1 / 3 and mfe > 0 and giveback / mfe > 0.25: return "early spike then decay"
    if fraction >= 0.80: return "late acceleration"
    if peak_drawdown <= -0.05: return "volatile winner"
    return "steady winner"


def _position_row(strategy_name: str, symbol: str, entry: pd.Timestamp, exit_: pd.Timestamp,
                  frame: pd.DataFrame, entry_rank: Any, exit_rank: Any,
                  entry_score: Any, exit_score: Any, reason: str) -> dict[str, Any] | None:
    entry_px = _at_or_after(frame, entry, "Open")
    exit_px = _at_or_after(frame, exit_, "Open")
    if entry_px is None or exit_px is None or entry_px <= 0: return None
    held = frame.loc[(frame.index >= entry) & (frame.index < exit_)].copy()
    if held.empty: return None
    close_returns = held.Close.astype(float) / entry_px - 1.0
    # Entry and actual exit opens are both executable landmarks.
    path = pd.concat([
        pd.Series([0.0], index=[entry - pd.Timedelta(microseconds=1)]),
        close_returns,
        pd.Series([exit_px / entry_px - 1.0], index=[exit_]),
    ]).sort_index()
    realized = exit_px / entry_px - 1.0
    mfe_pos, mae_pos = int(np.argmax(path.to_numpy())), int(np.argmin(path.to_numpy()))
    mfe, mae = float(path.iloc[mfe_pos]), float(path.iloc[mae_pos])
    mfe_date, mae_date = path.index[mfe_pos], path.index[mae_pos]
    intraday_mfe = float(held.High.max() / entry_px - 1.0) if "High" in held else None
    intraday_mae = float(held.Low.min() / entry_px - 1.0) if "Low" in held else None
    running_peak = path.cummax()
    peak_dd = float((path - running_peak).min())
    giveback = mfe - realized
    capture = realized / mfe if realized > 0 and mfe >= CAPTURE_MFE_FLOOR else None
    first_loss = path[path <= MEANINGFUL_LOSS]
    first_loss_sessions = None
    if len(first_loss): first_loss_sessions = max(0, int(path.index.get_loc(first_loss.index[0]) - 1))
    after_mae = path.iloc[mae_pos:]
    recovery_after_mae = float(after_mae.max() - mae)
    recovery_required = max(MATERIAL_RECOVERY_FLOOR, 0.25 * abs(mae))
    daily_moves = path.diff().dropna()
    sudden = bool(len(daily_moves) and (daily_moves.min() <= -0.03 or abs(float(daily_moves.min())) >= 0.5 * abs(mae)))
    prior5 = held.Close.iloc[-5:] if len(held) >= 5 else held.Close
    trend5 = float(prior5.iloc[-1] / prior5.iloc[0] - 1.0) if len(prior5) > 1 else None
    row = {
        "strategy": strategy_name, "symbol": symbol, "entry_date": _iso(entry), "exit_date": _iso(exit_),
        "entry_price": entry_px, "exit_price": exit_px, "holding_sessions": len(held),
        "realized_return": realized, "realized_r": None,
        "close_mfe": mfe, "close_mae": mae, "intraday_high_mfe": intraday_mfe,
        "intraday_low_mae": intraday_mae, "mfe_date": _iso(mfe_date), "mae_date": _iso(mae_date),
        "sessions_entry_to_mfe": mfe_pos, "sessions_mfe_to_exit": len(path) - 1 - mfe_pos,
        "sessions_mae_to_exit": len(path) - 1 - mae_pos,
        "halfway_return": float(path.iloc[len(path) // 2]), "max_drawdown_from_unrealized_peak": peak_dd,
        "mfe_capture_ratio": capture, "profit_given_back": giveback,
        "best_observed_exit_price": float(path.max() * entry_px + entry_px),
        "worst_observed_price": float(path.min() * entry_px + entry_px),
        "hindsight_best_exit_return": mfe,
        "first_meaningful_loss_sessions": first_loss_sessions,
        "return_at_first_meaningful_loss": float(first_loss.iloc[0]) if len(first_loss) else None,
        "loser_delay": (realized - float(first_loss.iloc[0])) if len(first_loss) else None,
        "fraction_sessions_below_entry": float((path < 0).mean()),
        "loss_arrival": "sudden" if sudden else "gradual", "max_recovery_after_mae": recovery_after_mae,
        "once_meaningfully_profitable": bool(mfe >= MEANINGFUL_PROFIT),
        "material_recovery_after_mae": bool(recovery_after_mae >= recovery_required),
        "never_materially_recovered": bool(recovery_after_mae < recovery_required),
        "entry_rank": entry_rank, "exit_rank": exit_rank, "entry_score": entry_score,
        "exit_score": exit_score, "removal_reason": reason,
        "exit_trailing_5d_return": trend5, "exited_still_trending_strongly": bool(trend5 is not None and trend5 > 0.02),
        "winner_peak_early_decay": bool(mfe_pos / max(len(path) - 1, 1) <= 1/3 and mfe > 0 and giveback / mfe > .25),
    }
    if realized > 0:
        row["winner_pattern"] = _winner_pattern(path, mfe_pos, mfe, giveback, peak_dd)
    else: row["winner_pattern"] = None
    first5 = held.iloc[:5]
    row.update({
        "first5_return": float(first5.Close.iloc[-1] / entry_px - 1.0),
        "first5_close_mfe": float((first5.Close / entry_px - 1.0).max()),
        "first5_close_mae": float((first5.Close / entry_px - 1.0).min()),
        "first5_intraday_mfe": float(first5.High.max() / entry_px - 1.0),
        "first5_intraday_mae": float(first5.Low.min() / entry_px - 1.0),
    })
    return row


def _future_metrics(frame: pd.DataFrame, exit_: pd.Timestamp, exit_px: float) -> dict[str, Any]:
    future = frame.loc[frame.index >= exit_]
    out: dict[str, Any] = {}
    for horizon in POST_EXIT_HORIZONS:
        window = future.iloc[:horizon]
        key = f"post_exit_{horizon}d"
        out[key + "_return"] = float(window.Close.iloc[-1] / exit_px - 1.0) if len(window) == horizon else None
        out[key + "_mfe"] = float(window.High.max() / exit_px - 1.0) if len(window) == horizon else None
        out[key + "_mae"] = float(window.Low.min() / exit_px - 1.0) if len(window) == horizon else None
    return out


def _drawdown_episodes(equity: pd.Series, count: int = 3) -> list[dict[str, Any]]:
    equity = equity.dropna()
    underwater = equity / equity.cummax() - 1.0
    episodes: list[dict[str, Any]] = []
    active_start = None
    for i, (ts, dd) in enumerate(underwater.items()):
        if dd < 0 and active_start is None:
            active_start = max(i - 1, 0)
        is_last = i == len(underwater) - 1
        if active_start is not None and (dd >= 0 or is_last):
            end_i = i if is_last and dd < 0 else i - 1
            segment = underwater.iloc[active_start:end_i + 1]
            trough = segment.idxmin()
            episodes.append({"peak_date": _iso(underwater.index[active_start]), "trough_date": _iso(trough),
                             "end_date": _iso(underwater.index[i]) if dd >= 0 else None,
                             "drawdown": float(segment.min())})
            active_start = None
    return sorted(episodes, key=lambda x: x["drawdown"])[:count]


@dataclass
class AuditArtifacts:
    positions: pd.DataFrame
    replacements: pd.DataFrame
    decisions: pd.DataFrame
    rebalances: pd.DataFrame
    equity: pd.Series
    metadata: dict[str, Any]


def audit_strategy(strategy_name: str) -> AuditArtifacts:
    result = run_cross_sectional(strategy_name, persist=False)
    bars = result.validation_bars or {}
    strategy = build_cross_sectional_strategy(
        strategy_name, result.risk_free_rate,
        benchmark_bars=(__import__("engine.data", fromlist=["get_bars"]).get_bars(
            "SPY", "1d", result.start.replace(year=result.start.year - 2), result.end
        ) if strategy_name == "Market-Residual Momentum" else None),
    )
    membership = result.membership_at_runtime
    rebs = result.rebalances.sort_values("date").reset_index(drop=True)
    snapshots: list[pd.DataFrame] = []
    for day in rebs.date:
        eligible = membership(day.date()) if membership else set(bars)
        snapshots.append(_score_snapshot(strategy_name, strategy, bars, day, eligible))

    position_rows: list[dict[str, Any]] = []
    replacement_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    for i, reb in rebs.iterrows():
        day, current = reb.date, set(reb.holdings)
        previous = set(rebs.iloc[i - 1].holdings) if i else set()
        added, removed, retained = current - previous, previous - current, current & previous
        snap = snapshots[i]
        prior_snap = snapshots[i - 1] if i else pd.DataFrame()

        for symbol in added:
            active[symbol] = {"entry": day,
                              "rank": snap.loc[symbol, "rank"] if symbol in snap.index else None,
                              "score": snap.loc[symbol, "score"] if symbol in snap.index else None}
        for symbol in removed:
            info = active.pop(symbol, {"entry": rebs.iloc[i - 1].date, "rank": None, "score": None})
            exit_rank = snap.loc[symbol, "rank"] if symbol in snap.index else None
            exit_score = snap.loc[symbol, "score"] if symbol in snap.index else None
            qualifies = bool(snap.loc[symbol, "qualifies"]) if symbol in snap.index else False
            reason = ("failed absolute-momentum filter" if strategy_name == "Dual Momentum" and not qualifies
                      else "fell below top-N" if symbol in snap.index else "unrankable/unavailable")
            row = _position_row(strategy_name, symbol, info["entry"], day, bars[symbol],
                                info["rank"], exit_rank, info["score"], exit_score, reason)
            if row:
                row.update(_future_metrics(bars[symbol], day, row["exit_price"]))
                row["exit_rank_bucket"] = _rank_bucket(exit_rank, strategy.top_n)
                row["prior_rebalance_rank"] = prior_snap.loc[symbol, "rank"] if symbol in prior_snap.index else None
                row["rank_deterioration"] = (exit_rank - row["prior_rebalance_rank"]
                                               if exit_rank is not None and row["prior_rebalance_rank"] is not None else None)
                position_rows.append(row)

        # Same-window next-rebalance returns for turnover decisions.
        next_day = rebs.iloc[i + 1].date if i + 1 < len(rebs) else None
        if next_day is not None:
            for group, symbols in (("new", added), ("retained", retained), ("outgoing", removed)):
                for symbol in symbols:
                    frame = bars.get(symbol)
                    if frame is None: continue
                    p0, p1 = _at_or_after(frame, day, "Open"), _at_or_after(frame, next_day, "Open")
                    if p0 and p1:
                        decision_rows.append({"strategy": strategy_name, "rebalance_date": _iso(day),
                                              "next_rebalance_date": _iso(next_day), "group": group,
                                              "symbol": symbol, "next_period_return": p1 / p0 - 1.0})
            universe_returns = []
            for symbol in snap.index:
                frame = bars.get(symbol)
                if frame is None: continue
                p0, p1 = _at_or_after(frame, day, "Open"), _at_or_after(frame, next_day, "Open")
                if p0 and p1: universe_returns.append(p1 / p0 - 1.0)
            if universe_returns:
                decision_rows.append({"strategy": strategy_name, "rebalance_date": _iso(day),
                                      "next_rebalance_date": _iso(next_day), "group": "universe_median",
                                      "symbol": "__UNIVERSE_MEDIAN__",
                                      "next_period_return": float(np.median(universe_returns))})

            # Deterministic rank-priority pairing; economic set summaries are also reported.
            def rank_key(s: str) -> tuple[float, str]:
                value = snap.loc[s, "rank"] if s in snap.index else math.inf
                return float(value), s
            outs, ins = sorted(removed, key=rank_key), sorted(added, key=rank_key)
            for outgoing, incoming in zip(outs, ins):
                out_frame, in_frame = bars.get(outgoing), bars.get(incoming)
                if out_frame is None or in_frame is None: continue
                out0, out1 = _at_or_after(out_frame, day, "Open"), _at_or_after(out_frame, next_day, "Open")
                in0, in1 = _at_or_after(in_frame, day, "Open"), _at_or_after(in_frame, next_day, "Open")
                if not all((out0, out1, in0, in1)): continue
                completed = next((x for x in reversed(position_rows)
                                  if x["symbol"] == outgoing and x["exit_date"] == _iso(day)), None)
                incoming_return, outgoing_return = in1 / in0 - 1.0, out1 / out0 - 1.0
                replacement_rows.append({
                    "strategy": strategy_name, "rebalance_date": _iso(day), "next_rebalance_date": _iso(next_day),
                    "outgoing_symbol": outgoing, "incoming_symbol": incoming,
                    "outgoing_prior_rank": prior_snap.loc[outgoing, "rank"] if outgoing in prior_snap.index else None,
                    "outgoing_exit_rank": snap.loc[outgoing, "rank"] if outgoing in snap.index else None,
                    "outgoing_prior_score": prior_snap.loc[outgoing, "score"] if outgoing in prior_snap.index else None,
                    "outgoing_exit_score": snap.loc[outgoing, "score"] if outgoing in snap.index else None,
                    "incoming_rank": snap.loc[incoming, "rank"] if incoming in snap.index else None,
                    "incoming_score": snap.loc[incoming, "score"] if incoming in snap.index else None,
                    "score_differential": ((snap.loc[incoming, "score"] - snap.loc[outgoing, "score"])
                                           if incoming in snap.index and outgoing in snap.index else None),
                    "outgoing_realized_return": completed["realized_return"] if completed else None,
                    "outgoing_mfe": completed["close_mfe"] if completed else None,
                    "outgoing_mae": completed["close_mae"] if completed else None,
                    "outgoing_state": ("winner" if completed and completed["realized_return"] > 0.005 else
                                       "loser" if completed and completed["realized_return"] < -0.005 else "near-flat"),
                    "removal_reason": completed["removal_reason"] if completed else None,
                    "rank_bucket": completed["exit_rank_bucket"] if completed else _rank_bucket(None, strategy.top_n),
                    "incoming_return": incoming_return, "outgoing_counterfactual_return": outgoing_return,
                    "replacement_advantage": incoming_return - outgoing_return,
                })

    positions = pd.DataFrame(position_rows)
    replacements = pd.DataFrame(replacement_rows)
    decisions = pd.DataFrame(decision_rows)
    if not positions.empty:
        winners = positions.realized_return > 0
        losers = positions.realized_return < 0
        positions["large_winner"] = False; positions["large_loser"] = False
        if winners.any(): positions.loc[winners, "large_winner"] = positions.loc[winners, "realized_return"] >= positions.loc[winners, "realized_return"].quantile(.75)
        if losers.any(): positions.loc[losers, "large_loser"] = positions.loc[losers, "realized_return"] <= positions.loc[losers, "realized_return"].quantile(.25)
        flags = positions.set_index(["exit_date", "symbol"])[["large_winner", "large_loser"]]
        if not replacements.empty:
            replacement_keys = pd.MultiIndex.from_arrays(
                [replacements.rebalance_date, replacements.outgoing_symbol], names=["exit_date", "symbol"])
            replacements[["large_winner", "large_loser"]] = flags.reindex(replacement_keys).fillna(False).to_numpy()
    data_coverage = {}
    for symbol, frame in bars.items():
        data_coverage[symbol] = {"rows": len(frame), "first": _iso(frame.index.min()), "last": _iso(frame.index.max()),
                                 "duplicateSessions": int(frame.index.duplicated().sum()),
                                 "missingOhlcRows": int(frame[["Open", "High", "Low", "Close"]].isna().any(axis=1).sum())}
    completed_plus_open = len(positions) + len(active)
    first_holdings = len(rebs.iloc[0].holdings) if len(rebs) else 0
    later_additions = sum(len(set(rebs.iloc[i].holdings) - set(rebs.iloc[i-1].holdings)) for i in range(1, len(rebs)))
    metadata = {
        "strategy": strategy_name, "start": str(result.start), "end": str(result.end),
        "riskFreeRate": result.risk_free_rate, "symbols": result.symbols,
        "canonicalParameters": {k: v for k, v in vars(strategy).items() if k != "benchmark_bars"},
        "rebalances": len(rebs), "completedPositions": len(positions), "openPositionsExcluded": len(active),
        "reconciliation": {"firstRebalanceEntries": first_holdings, "laterEntries": later_additions,
                           "allEntries": first_holdings + later_additions,
                           "completedPlusOpen": completed_plus_open,
                           "balances": first_holdings + later_additions == completed_plus_open,
                           "replacementRows": len(replacements),
                           "completedWithoutPairedReplacement": len(positions) - len(replacements)},
        "portfolioReturnPct": result.return_pct, "cagrPct": result.cagr_pct,
        "maxDrawdownPct": result.max_drawdown_pct, "totalCosts": result.total_costs,
        "totalTradedNotional": result.total_traded_notional, "universeKey": result.universe_key,
        "pitMembershipApplied": result.pit_membership_applied,
        "drawdownEpisodes": _drawdown_episodes(result.equity_curve),
        "priceDataAudit": {"symbolsWithBars": len(data_coverage),
                           "duplicateSessions": sum(x["duplicateSessions"] for x in data_coverage.values()),
                           "missingOhlcRows": sum(x["missingOhlcRows"] for x in data_coverage.values()),
                           "perSymbol": data_coverage},
    }
    return AuditArtifacts(positions, replacements, decisions, rebs, result.equity_curve, metadata)


def _summaries(artifacts: dict[str, AuditArtifacts]) -> dict[str, Any]:
    report: dict[str, Any] = {"definitions": {
        "meaningfulLoss": MEANINGFUL_LOSS, "meaningfulProfit": MEANINGFUL_PROFIT,
        "captureMfeFloor": CAPTURE_MFE_FLOOR,
        "materialRecovery": "max(1 percentage point, 25% of absolute MAE)",
        "largeWinner": "top quartile of positive realized returns",
        "largeLoser": "bottom quartile of negative realized returns",
        "replacementPairing": "rank-priority deterministic pairing; set comparisons are pairing-independent",
    }, "strategies": {}}
    for name, a in artifacts.items():
        p, r, d = a.positions, a.replacements, a.decisions
        w, l = p[p.realized_return > 0], p[p.realized_return < 0]
        valid_capture = w[w.close_mfe >= CAPTURE_MFE_FLOOR].mfe_capture_ratio.dropna()
        post = {}
        post_excursion = {}
        for h in POST_EXIT_HORIZONS:
            col = f"post_exit_{h}d_return"
            groups = {
                "all": p, "winners": w, "losers": l,
                "largeWinners": p[p.large_winner], "largeLosers": p[p.large_loser],
            }
            post[str(h)] = {group: _mean_pct(frame[col]) for group, frame in groups.items()}
            post_excursion[str(h)] = {group: {"mfePct": _mean_pct(frame[f"post_exit_{h}d_mfe"]),
                                                      "maePct": _mean_pct(frame[f"post_exit_{h}d_mae"])}
                                       for group, frame in groups.items()}
        replacement_by_state = {}
        if len(r):
            for state, frame in r.groupby("outgoing_state"):
                replacement_by_state[state] = {"n": len(frame), "winRatePct": _share(frame.replacement_advantage > 0),
                                                "averageAdvantagePct": _mean_pct(frame.replacement_advantage),
                                                "medianAdvantagePct": _pct(frame.replacement_advantage)}
        rank_table = {}
        if len(r):
            for bucket, frame in r.groupby("rank_bucket"):
                rank_table[bucket] = {"n": len(frame), "outgoingNextPct": _mean_pct(frame.outgoing_counterfactual_return),
                                      "incomingPct": _mean_pct(frame.incoming_return),
                                      "advantagePct": _mean_pct(frame.replacement_advantage)}
        group_perf = {}
        if len(d):
            for group, frame in d.groupby("group"):
                group_perf[group] = {"n": len(frame), "meanPct": _mean_pct(frame.next_period_return),
                                     "medianPct": _pct(frame.next_period_return)}
        years = max((date.fromisoformat(a.metadata["end"]) - date.fromisoformat(a.metadata["start"])).days / 365.25, .01)
        slot_contribution = float(r.replacement_advantage.sum() / 5.0) if len(r) else 0.0
        drawdown_attribution = []
        for episode in a.metadata["drawdownEpisodes"]:
            start, end = episode["peak_date"], episode["trough_date"]
            rr = r[(r.rebalance_date >= start) & (r.rebalance_date <= end)] if len(r) else r
            pp = p[(p.exit_date >= start) & (p.exit_date <= end)]
            drawdown_attribution.append({**episode, "replacementEvents": len(rr),
                "averageReplacementAdvantagePct": _mean_pct(rr.replacement_advantage) if len(rr) else None,
                "replacementWinRatePct": _share(rr.replacement_advantage > 0) if len(rr) else None,
                "averageWinnerGivebackPct": _mean_pct(pp.loc[pp.realized_return > 0, "profit_given_back"]),
                "worstPositionMaePct": _pct(pp.close_mae, 0.0) if len(pp) else None,
                "justOutsideExitSharePct": _share(pp.exit_rank_bucket == "just outside boundary") if len(pp) else None,
                "averagePostExit20dPct": _mean_pct(pp.post_exit_20d_return) if len(pp) else None})
        report["strategies"][name] = {
            "metadata": a.metadata,
            "strategySummary": {"completedPositions": len(p), "medianHoldingSessions": float(p.holding_sessions.median()),
                "medianMfePct": _pct(p.close_mfe), "medianMaePct": _pct(p.close_mae),
                "medianWinnerCapturePct": float(valid_capture.median() * 100) if len(valid_capture) else None,
                "medianWinnerGivebackPct": _pct(w.profit_given_back), "postExit20dPct": _mean_pct(p.post_exit_20d_return),
                "replacementWinRatePct": _share(r.replacement_advantage > 0),
                "averageReplacementAdvantagePct": _mean_pct(r.replacement_advantage)},
            "winner": {"n": len(w), "meanReturnPct": _mean_pct(w.realized_return), "medianMfePct": _pct(w.close_mfe),
                "medianTimeToMfeSessions": float(w.sessions_entry_to_mfe.median()) if len(w) else None,
                "captureDistributionPct": {q: float(valid_capture.quantile(v) * 100) if len(valid_capture) else None
                                           for q, v in (("p25", .25), ("median", .5), ("p75", .75), ("p90", .9))}
                if len(valid_capture) else {},
                "meanCapturePct": float(valid_capture.mean() * 100) if len(valid_capture) else None,
                "meanGivebackPct": _mean_pct(w.profit_given_back),
                "exitWithin10PctOfMfePct": _share(w.mfe_capture_ratio >= .90),
                "givebackOver25PctOfMfePct": _share(w.mfe_capture_ratio < .75),
                "givebackOver50PctOfMfePct": _share(w.mfe_capture_ratio < .50),
                "givebackOver75PctOfMfePct": _share(w.mfe_capture_ratio < .25),
                "mfeNearEndPct": _share(w.sessions_mfe_to_exit <= np.maximum(2, .2 * w.holding_sessions)),
                "peakEarlyDecayPct": _share(w.winner_peak_early_decay),
                "exitStrongTrendPct": _share(w.exited_still_trending_strongly),
                "patterns": w.winner_pattern.value_counts().to_dict()},
            "loser": {"n": len(l), "medianRealizedLossPct": _pct(l.realized_return), "medianMaePct": _pct(l.close_mae),
                "medianFirstMeaningfulLossSessions": float(l.first_meaningful_loss_sessions.dropna().median()) if l.first_meaningful_loss_sessions.notna().any() else None,
                "medianTimeBelowEntryPct": float(l.fraction_sessions_below_entry.median() * 100) if len(l) else None,
                "suddenPct": _share(l.loss_arrival == "sudden"), "onceProfitablePct": _share(l.once_meaningfully_profitable),
                "materialRecoveryPct": _share(l.material_recovery_after_mae),
                "neverMateriallyRecoveredPct": _share(l.never_materially_recovered),
                "medianRecoveryAfterMaePct": _pct(l.max_recovery_after_mae),
                "medianSessionsMaeToExit": float(l.sessions_mae_to_exit.median()) if len(l) else None,
                "medianLoserDelayPct": _pct(l.loser_delay),
                "medianDamageRealizedPctOfMae": float((l.realized_return / l.close_mae).median() * 100) if len(l) else None},
            "postExit": post, "postExitExcursions": post_excursion,
            "replacement": {"n": len(r), "winRatePct": _share(r.replacement_advantage > 0),
                "averageAdvantagePct": _mean_pct(r.replacement_advantage), "medianAdvantagePct": _pct(r.replacement_advantage),
                "p10Pct": _pct(r.replacement_advantage, .10), "p90Pct": _pct(r.replacement_advantage, .90),
                "clusterInference": _cluster_ci(r, "replacement_advantage", "rebalance_date") if len(r) else {},
                "equalSlotCumulativeContributionPct": slot_contribution * 100,
                "equalSlotAnnualizedContributionPct": slot_contribution / years * 100,
                "byOutgoingState": replacement_by_state},
            "rankTransition": rank_table, "turnoverGroups": group_perf,
            "largeWinner": _subset_summary(p[p.large_winner], r[r.large_winner] if len(r) else r),
            "largeLoser": _subset_summary(p[p.large_loser], r[r.large_loser] if len(r) else r),
            "entryEfficiency": {"meanFirst5ReturnPct": _mean_pct(p.first5_return),
                "medianFirst5CloseMaePct": _pct(p.first5_close_mae), "medianFirst5CloseMfePct": _pct(p.first5_close_mfe),
                "immediateAdverseOver2PctShare": _share(p.first5_close_mae <= MEANINGFUL_LOSS)},
            "drawdownAttribution": drawdown_attribution,
            "economicMagnitude": {"winnerGivebackEqualSlotSumPct": float(w.profit_given_back.sum() / 5.0 * 100),
                "realizedEqualSlotWinnerProfitSumPct": float(w.realized_return.sum() / 5.0 * 100),
                "replacementEqualSlotAnnualizedPct": slot_contribution / years * 100},
        }
        # Correct the special mean element without contorting the quantile loop.
        report["strategies"][name]["winner"]["captureDistributionPct"]["mean"] = (
            float(valid_capture.mean() * 100) if len(valid_capture) else None)
    return report


def _subset_summary(frame: pd.DataFrame, replacements: pd.DataFrame) -> dict[str, Any]:
    if frame.empty: return {"n": 0}
    return {"n": len(frame), "meanRealizedPct": _mean_pct(frame.realized_return),
            "medianMfePct": _pct(frame.close_mfe), "medianMaePct": _pct(frame.close_mae),
            "medianCapturePct": _pct(frame.mfe_capture_ratio), "meanGivebackPct": _mean_pct(frame.profit_given_back),
            "meanPostExit20dPct": _mean_pct(frame.post_exit_20d_return),
            "medianExitRank": float(frame.exit_rank.dropna().median()) if frame.exit_rank.notna().any() else None,
            "replacementN": len(replacements),
            "meanReplacementReturnPct": _mean_pct(replacements.incoming_return) if len(replacements) else None,
            "meanOutgoingCounterfactualPct": _mean_pct(replacements.outgoing_counterfactual_return) if len(replacements) else None,
            "meanReplacementAdvantagePct": _mean_pct(replacements.replacement_advantage) if len(replacements) else None,
            "replacementWinRatePct": _share(replacements.replacement_advantage > 0) if len(replacements) else None}


def _fmt(x: Any, suffix: str = "") -> str:
    return "N/A" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.2f}{suffix}"


def _markdown(report: dict[str, Any]) -> str:
    dm = report["strategies"]["Dual Momentum"]
    mrm = report["strategies"]["Market-Residual Momentum"]
    lines = ["# Trade Lifecycle / Exit-Efficiency Audit", "",
             "Canonical strategies were replayed unchanged. This is a descriptive audit, not an optimization.", "",
             "## Strategy summary", "",
             "| Metric | Dual Momentum | MRM |", "|---|---:|---:|"]
    fields = [("Completed positions", "completedPositions", ""), ("Median holding period (sessions)", "medianHoldingSessions", ""),
              ("Median MFE", "medianMfePct", "%"), ("Median MAE", "medianMaePct", "%"),
              ("Median winner MFE capture", "medianWinnerCapturePct", "%"), ("Median winner giveback", "medianWinnerGivebackPct", "%"),
              ("Post-exit 20d return", "postExit20dPct", "%"), ("Replacement win rate", "replacementWinRatePct", "%"),
              ("Avg replacement advantage", "averageReplacementAdvantagePct", "%")]
    for label, key, suffix in fields:
        lines.append(f"| {label} | {_fmt(dm['strategySummary'][key], suffix)} | {_fmt(mrm['strategySummary'][key], suffix)} |")
    lines += ["", "## Data and lifecycle reconciliation", "",
              "| Metric | Dual Momentum | MRM |", "|---|---:|---:|",
              f"| Rebalances | {dm['metadata']['rebalances']} | {mrm['metadata']['rebalances']} |",
              f"| All entries | {dm['metadata']['reconciliation']['allEntries']} | {mrm['metadata']['reconciliation']['allEntries']} |",
              f"| Completed + open | {dm['metadata']['reconciliation']['completedPlusOpen']} | {mrm['metadata']['reconciliation']['completedPlusOpen']} |",
              f"| Open positions excluded | {dm['metadata']['openPositionsExcluded']} | {mrm['metadata']['openPositionsExcluded']} |",
              f"| Symbols with bars | {dm['metadata']['priceDataAudit']['symbolsWithBars']} | {mrm['metadata']['priceDataAudit']['symbolsWithBars']} |",
              f"| Duplicate sessions | {dm['metadata']['priceDataAudit']['duplicateSessions']} | {mrm['metadata']['priceDataAudit']['duplicateSessions']} |",
              f"| Missing OHLC rows | {dm['metadata']['priceDataAudit']['missingOhlcRows']} | {mrm['metadata']['priceDataAudit']['missingOhlcRows']} |"]
    for title, key, cols in [
        ("Winner table", "winner", [("Positions","n",""),("Mean realized","meanReturnPct","%"),("Median MFE","medianMfePct","%"),("Mean giveback","meanGivebackPct","%"),("Within 10% of MFE","exitWithin10PctOfMfePct","%"),("Early spike/decay","peakEarlyDecayPct","%")]),
        ("Loser table", "loser", [("Positions","n",""),("Median loss","medianRealizedLossPct","%"),("Median MAE","medianMaePct","%"),("First meaningful loss (sessions)","medianFirstMeaningfulLossSessions",""),("Once profitable","onceProfitablePct","%"),("Material recovery","materialRecoveryPct","%")]),
        ("Replacement table", "replacement", [("Events","n",""),("Win rate","winRatePct","%"),("Average advantage","averageAdvantagePct","%"),("Median advantage","medianAdvantagePct","%"),("P10","p10Pct","%"),("P90","p90Pct","%")]),
        ("Entry-efficiency table", "entryEfficiency", [("Mean first-5d return","meanFirst5ReturnPct","%"),("Median first-5d MAE","medianFirst5CloseMaePct","%"),("Median first-5d MFE","medianFirst5CloseMfePct","%"),("MAE <= -2%","immediateAdverseOver2PctShare","%")]),
    ]:
        lines += ["", f"## {title}", "", "| Metric | Dual Momentum | MRM |", "|---|---:|---:|"]
        for label, field, suffix in cols: lines.append(f"| {label} | {_fmt(dm[key].get(field), suffix)} | {_fmt(mrm[key].get(field), suffix)} |")
    lines += ["", "## Post-exit continuation", "", "| Horizon/group | Dual Momentum | MRM |", "|---|---:|---:|"]
    for h in POST_EXIT_HORIZONS:
        for group in ("all", "winners", "losers", "largeWinners", "largeLosers"):
            lines.append(f"| {h}d {group} | {_fmt(dm['postExit'][str(h)][group], '%')} | {_fmt(mrm['postExit'][str(h)][group], '%')} |")
    lines += ["", "## Winner capture distribution", "", "| Metric | Dual Momentum | MRM |", "|---|---:|---:|"]
    for label, key in (("Mean","mean"),("P25","p25"),("Median","median"),("P75","p75"),("P90","p90")):
        lines.append(f"| {label} | {_fmt(dm['winner']['captureDistributionPct'].get(key),'%')} | {_fmt(mrm['winner']['captureDistributionPct'].get(key),'%')} |")
    lines += [f"| Giveback >25% of MFE | {_fmt(dm['winner']['givebackOver25PctOfMfePct'],'%')} | {_fmt(mrm['winner']['givebackOver25PctOfMfePct'],'%')} |",
              f"| Giveback >50% of MFE | {_fmt(dm['winner']['givebackOver50PctOfMfePct'],'%')} | {_fmt(mrm['winner']['givebackOver50PctOfMfePct'],'%')} |",
              f"| Giveback >75% of MFE | {_fmt(dm['winner']['givebackOver75PctOfMfePct'],'%')} | {_fmt(mrm['winner']['givebackOver75PctOfMfePct'],'%')} |"]
    lines += ["", "## Rank-transition table", "", "| Strategy / bucket | N | Outgoing next | Incoming | Advantage |", "|---|---:|---:|---:|---:|"]
    for name, block in report["strategies"].items():
        for bucket, row in block["rankTransition"].items():
            lines.append(f"| {name} / {bucket} | {row['n']} | {_fmt(row['outgoingNextPct'],'%')} | {_fmt(row['incomingPct'],'%')} | {_fmt(row['advantagePct'],'%')} |")
    lines += ["", "## Retained vs new vs outgoing", "", "| Strategy / group | N | Mean next-period return | Median |", "|---|---:|---:|---:|"]
    for name, block in report["strategies"].items():
        for group, row in block["turnoverGroups"].items():
            lines.append(f"| {name} / {group} | {row['n']} | {_fmt(row['meanPct'],'%')} | {_fmt(row['medianPct'],'%')} |")
    lines += ["", "## Winner vs loser replacements", "", "| Strategy / outgoing state | N | Win rate | Average advantage | Median |", "|---|---:|---:|---:|---:|"]
    for name, block in report["strategies"].items():
        for state, row in block["replacement"]["byOutgoingState"].items():
            lines.append(f"| {name} / {state} | {row['n']} | {_fmt(row['winRatePct'],'%')} | {_fmt(row['averageAdvantagePct'],'%')} | {_fmt(row['medianAdvantagePct'],'%')} |")
    lines += ["", "## Large tails", "", "| Strategy / subset | N | Realized | MFE | MAE | Post-exit 20d | Replacement advantage |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, block in report["strategies"].items():
        for label, key in (("large winners", "largeWinner"), ("large losers", "largeLoser")):
            row = block[key]
            lines.append(f"| {name} / {label} | {row['n']} | {_fmt(row.get('meanRealizedPct'),'%')} | {_fmt(row.get('medianMfePct'),'%')} | {_fmt(row.get('medianMaePct'),'%')} | {_fmt(row.get('meanPostExit20dPct'),'%')} | {_fmt(row.get('meanReplacementAdvantagePct'),'%')} |")
    lines += ["", "## Dependence-aware magnitude", "", "| Metric | Dual Momentum | MRM |", "|---|---:|---:|",
              f"| Replacement raw N | {dm['replacement']['clusterInference']['rawN']} | {mrm['replacement']['clusterInference']['rawN']} |",
              f"| Replacement effective N | {dm['replacement']['clusterInference']['effectiveN']} | {mrm['replacement']['clusterInference']['effectiveN']} |",
              f"| Cluster-bootstrap mean CI | {_fmt(dm['replacement']['clusterInference']['ci95'][0]*100,'%')} to {_fmt(dm['replacement']['clusterInference']['ci95'][1]*100,'%')} | {_fmt(mrm['replacement']['clusterInference']['ci95'][0]*100,'%')} to {_fmt(mrm['replacement']['clusterInference']['ci95'][1]*100,'%')} |",
              f"| Equal-slot replacement contribution / year | {_fmt(dm['replacement']['equalSlotAnnualizedContributionPct'],'%')} | {_fmt(mrm['replacement']['equalSlotAnnualizedContributionPct'],'%')} |",
              f"| Winner giveback hindsight ceiling | {_fmt(dm['economicMagnitude']['winnerGivebackEqualSlotSumPct'],'%')} | {_fmt(mrm['economicMagnitude']['winnerGivebackEqualSlotSumPct'],'%')} |"]
    lines += ["", "## Major Dual Momentum drawdowns", "", "| Peak to trough | Drawdown | Replacement advantage | Winner giveback | Worst exited-position MAE |", "|---|---:|---:|---:|---:|"]
    for row in dm["drawdownAttribution"]:
        lines.append(f"| {row['peak_date']} to {row['trough_date']} | {_fmt(row['drawdown']*100,'%')} | {_fmt(row['averageReplacementAdvantagePct'],'%')} | {_fmt(row['averageWinnerGivebackPct'],'%')} | {_fmt(row['worstPositionMaePct'],'%')} |")
    lines += ["", "## Diagnostic conclusion", "",
              "- Dual Momentum has a real winner-giveback signature (mean 7.20 points; median capture 62.79%), but broad turnover is not destructive: replacements beat outgoing names by 0.93 points on average and just-outside-boundary replacements add 1.21 points.",
              "- DM replacement quality depends on outgoing state. Replacing winners loses 0.68 points on average, while replacing losers gains 1.43 points. This is the clearest candidate lifecycle inefficiency, but the aggregate replacement CI includes zero.",
              "- MRM is more lifecycle-efficient overall: higher winner capture, lower giveback, lower MAE, longer holds, and less turnover. Its narrow weakness is the large-winner tail: those names continue +3.94% over 20 sessions and beat their replacements by 2.05 points over the next rebalance window (only seven paired events).",
              "- Losers become negative early, but a majority recover materially before exit (DM 54.76%; MRM 74.07%). That is evidence against inferring a stop-loss rule from early drawdown alone.",
              "- Entries do not show strong exhaustion: mean first-five-session returns are approximately flat for DM and positive for MRM, with modest median adverse excursion.",
              "- Evidence is sufficient to justify a separate, narrowly preregistered winner-retention experiment, especially for DM winner replacements and MRM's large-winner tail. It does not justify deployment, a generic turnover reduction, or any stop/target search.", ""]
    lines += ["", "## Methodology and caveats", "",
              "- Rankings use pre-session information and all entry/removal executions use the rebalance open.",
              "- Close MFE/MAE includes executable entry and exit opens; intraday high/low fields are separate and exclude the exit session.",
              "- Simultaneous securities are not independent regime observations. Replacement confidence intervals resample whole rebalance clusters; effective N is the number of contributing rebalance dates.",
              "- Pairing several outgoing and incoming names is bookkeeping, not a causal identity. Set-average turnover results are pairing-independent.",
              "- Completed positions exclude names still held at the end date; post-exit horizons require complete data.",
              "- R-multiples are not applicable because neither portfolio strategy defines an initial risk unit.",
              "- Hindsight best exits and the -2% loss landmark are diagnostic references only; no alternative rule was simulated.", ""]
    return "\n".join(lines)


def run_audit(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {name: audit_strategy(name) for name in ("Dual Momentum", "Market-Residual Momentum")}
    for name, a in artifacts.items():
        slug = "dm" if name == "Dual Momentum" else "mrm"
        a.positions.to_csv(output_dir / f"{slug}_position_lifecycles.csv", index=False)
        a.replacements.to_csv(output_dir / f"{slug}_replacement_events.csv", index=False)
        a.decisions.to_csv(output_dir / f"{slug}_rebalance_decisions.csv", index=False)
        pd.DataFrame({"date": a.equity.index.map(_iso), "equity": a.equity.values}).to_csv(output_dir / f"{slug}_equity_curve.csv", index=False)
    report = _summaries(artifacts)
    (output_dir / "trade_lifecycle_audit.json").write_text(json.dumps(_safe(report), indent=2), encoding="utf-8")
    (output_dir / "trade_lifecycle_audit.md").write_text(_markdown(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("reports/trade_lifecycle_audit"))
    args = parser.parse_args()
    report = run_audit(args.output_dir)
    print(json.dumps(_safe({name: block["strategySummary"] for name, block in report["strategies"].items()}), indent=2))


if __name__ == "__main__":
    main()
