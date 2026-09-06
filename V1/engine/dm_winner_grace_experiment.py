"""Preregistered Dual Momentum One-Rebalance Winner Grace v1 experiment.

The only strategy modification implemented here is frozen in
``research/dm_winner_grace_v1_preregistration.json``.  This module is a
research runner, not a canonical strategy registration.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.cross_sectional import CrossSectionalResult, run_cross_sectional_backtest
from engine.execution_calibration import spread_for_universe
from engine.prop_account import PropSimulationConfig, SCENARIOS, sweep_sizing
from engine.prop_analysis import prop_series_from_result
from engine.runner import ALPACA_COMMISSION_BPS, run_cross_sectional
from engine.trade_lifecycle_audit import (
    BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, CAPTURE_MFE_FLOOR,
    _at_or_after, _cluster_ci, _future_metrics, _iso, _mean_pct,
    _pct, _plain_timestamp, _position_row, _safe, _score_snapshot, _share,
)
from strategies.swing.dual_momentum import DualMomentum


VARIANT_NAME = "Dual Momentum - One-Rebalance Winner Grace v1"
PREREGISTRATION = Path("research/dm_winner_grace_v1_preregistration.json")
DEVELOPMENT_CUTOFF = "2026-08-22"
TOP_N = 5


@dataclass
class WinnerGraceEvent:
    date: str
    incumbent_symbol: str
    incumbent_rank: int | None
    incumbent_score: float | None
    unrealized_return: float
    deferred_incoming_symbol: str
    incoming_rank: int
    incoming_score: float
    entry_date: str
    next_rebalance_date: str | None = None
    incumbent_next_return: float | None = None
    deferred_incoming_next_return: float | None = None
    incumbent_minus_incoming: float | None = None


@dataclass
class OneRebalanceWinnerGrace(DualMomentum):
    """Stateful research variant implementing exactly the preregistered rule."""

    name = VARIANT_NAME
    prior_holdings: set[str] = field(default_factory=set, init=False, repr=False)
    lifecycle_entry_dates: dict[str, pd.Timestamp] = field(default_factory=dict, init=False, repr=False)
    grace_active: set[str] = field(default_factory=set, init=False, repr=False)
    events: list[WinnerGraceEvent] = field(default_factory=list, init=False, repr=False)
    decision_log: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def _scores(self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, float]:
        scores: dict[str, float] = {}
        for symbol, bars in universe_bars.items():
            hist = bars.loc[:as_of]
            if len(hist) < self.lookback_trading_days + 1:
                continue
            base = float(hist.Close.iloc[-self.lookback_trading_days - 1])
            now = float(hist.Close.iloc[-1])
            if base > 0:
                scores[symbol] = now / base - 1.0
        return scores

    def rebalance(self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, float]:
        scores = self._scores(universe_bars, as_of)
        ordered = sorted(scores, key=lambda s: (-scores[s], s))
        ranks = {symbol: rank for rank, symbol in enumerate(ordered, start=1)}
        canonical = [s for s in ordered if scores[s] > self.risk_free_rate][:self.top_n]
        if not self.prior_holdings:
            target = canonical
            for symbol in target:
                self.lifecycle_entry_dates[symbol] = as_of
            self.prior_holdings = set(target)
            self.decision_log.append({"date": _iso(as_of), "canonical": canonical, "target": target,
                                      "grace": [], "expired": [], "losersExited": []})
            return {symbol: 1.0 / len(target) for symbol in target} if target else {}

        canonical_set = set(canonical)
        outgoing = self.prior_holdings - canonical_set
        expired = sorted(outgoing & self.grace_active)
        eligible: list[tuple[float, str, float]] = []
        losers_exited: list[str] = []
        for symbol in sorted(outgoing - self.grace_active):
            bars = universe_bars.get(symbol)
            entry_day = self.lifecycle_entry_dates.get(symbol)
            if bars is None or entry_day is None:
                losers_exited.append(symbol)
                continue
            entry_open = _at_or_after(bars, entry_day, "Open")
            prior_close = float(bars.Close.dropna().iloc[-1]) if len(bars.Close.dropna()) else None
            if entry_open is None or prior_close is None:
                losers_exited.append(symbol)
                continue
            unrealized = prior_close / entry_open - 1.0
            if unrealized > 0:
                eligible.append((float(ranks.get(symbol, math.inf)), symbol, unrealized))
            else:
                losers_exited.append(symbol)

        eligible.sort(key=lambda item: (item[0], item[1]))
        grace_names = [symbol for _, symbol, _ in eligible]
        continuing_canonical = [s for s in canonical if s in self.prior_holdings]
        canonical_incoming = [s for s in canonical if s not in self.prior_holdings]
        # A grace event must defer a real incoming name.  This also preserves
        # canonical cash slots when the absolute filter supplies < top_n.
        eligible = eligible[:len(canonical_incoming)]
        grace_names = [symbol for _, symbol, _ in eligible]
        target_size = len(canonical)
        available_for_incoming = target_size - len(continuing_canonical) - len(grace_names)
        admitted_incoming = canonical_incoming[:max(available_for_incoming, 0)]
        deferred = canonical_incoming[max(available_for_incoming, 0):]
        target = continuing_canonical + grace_names + admitted_incoming
        if len(target) != target_size:
            raise RuntimeError(f"Winner Grace v1 capacity reconciliation failed at {as_of}: {target}")
        if len(deferred) != len(grace_names):
            raise RuntimeError(f"Grace/deferred pairing failed at {as_of}")

        for (_, incumbent, unrealized), incoming in zip(eligible, deferred):
            self.events.append(WinnerGraceEvent(
                date=_iso(as_of), incumbent_symbol=incumbent,
                incumbent_rank=int(ranks[incumbent]) if incumbent in ranks else None,
                incumbent_score=float(scores[incumbent]) if incumbent in scores else None,
                unrealized_return=float(unrealized), deferred_incoming_symbol=incoming,
                incoming_rank=int(ranks[incoming]), incoming_score=float(scores[incoming]),
                entry_date=_iso(self.lifecycle_entry_dates[incumbent]),
            ))

        leaving = self.prior_holdings - set(target)
        entering = set(target) - self.prior_holdings
        for symbol in leaving:
            self.lifecycle_entry_dates.pop(symbol, None)
        for symbol in entering:
            self.lifecycle_entry_dates[symbol] = as_of
        self.decision_log.append({
            "date": _iso(as_of), "canonical": canonical, "target": target,
            "grace": grace_names, "deferred": deferred, "expired": expired,
            "losersExited": losers_exited,
        })
        self.prior_holdings = set(target)
        self.grace_active = set(grace_names)
        return {symbol: 1.0 / len(target) for symbol in target} if target else {}


def _daily_metrics(result: CrossSectionalResult) -> dict[str, Any]:
    equity = result.equity_curve.groupby(result.equity_curve.index.normalize()).last()
    returns = equity.pct_change().dropna()
    years = len(returns) / 252.0
    vol = float(returns.std(ddof=1) * np.sqrt(252))
    dd = equity / equity.cummax() - 1.0
    longest = current = 0
    for flag in dd < 0:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    monthly = equity.copy()
    monthly.index = monthly.index.tz_localize(None) if monthly.index.tz is not None else monthly.index
    monthly_returns = monthly.resample("ME").last().pct_change().dropna()
    def worst_roll(window: int) -> float | None:
        values = (1.0 + returns).rolling(window).apply(np.prod, raw=True) - 1.0
        return float(values.min()) if len(values.dropna()) else None
    average_equity = float(equity.mean())
    annual_turnover = result.total_traded_notional / average_equity / max(years, .01)
    return {
        "cagrPct": result.cagr_pct, "cumulativeReturnPct": result.return_pct,
        "annualizedVolatilityPct": vol * 100, "sharpe": result.sharpe, "sortino": result.sortino,
        "maxDrawdownPct": result.max_drawdown_pct, "maxDrawdownDurationSessions": longest,
        "worstDayPct": float(returns.min() * 100), "worstRolling5Pct": _safe(worst_roll(5) * 100),
        "worstRolling20Pct": _safe(worst_roll(20) * 100),
        "worstMonthPct": float(monthly_returns.min() * 100),
        "positiveMonthPct": float((monthly_returns > 0).mean() * 100),
        "totalTradedNotional": result.total_traded_notional,
        "annualizedTurnoverMultiple": annual_turnover, "transactionCosts": result.total_costs,
    }


def _snapshots(result: CrossSectionalResult, strategy: DualMomentum) -> list[pd.DataFrame]:
    bars = result.validation_bars or {}
    membership = result.membership_at_runtime
    output = []
    for day in result.rebalances.date:
        eligible = membership(day.date()) if membership else set(bars)
        output.append(_score_snapshot("Dual Momentum", strategy, bars, day, eligible))
    return output


def _reconstruct(result: CrossSectionalResult, strategy: DualMomentum) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    bars = result.validation_bars or {}
    rebs = result.rebalances.sort_values("date").reset_index(drop=True)
    snaps = _snapshots(result, strategy)
    active: dict[str, dict[str, Any]] = {}
    positions: list[dict[str, Any]] = []
    entries = exits = 0
    for i, row in rebs.iterrows():
        day, holdings = row.date, set(row.holdings)
        previous = set(rebs.iloc[i - 1].holdings) if i else set()
        for symbol in holdings - previous:
            entries += 1
            snap = snaps[i]
            active[symbol] = {"entry": day, "rank": snap.loc[symbol, "rank"] if symbol in snap.index else None,
                              "score": snap.loc[symbol, "score"] if symbol in snap.index else None}
        for symbol in previous - holdings:
            exits += 1
            info = active.pop(symbol)
            snap = snaps[i]
            rank = snap.loc[symbol, "rank"] if symbol in snap.index else None
            score = snap.loc[symbol, "score"] if symbol in snap.index else None
            item = _position_row(result.strategy_name, symbol, info["entry"], day, bars[symbol],
                                 info["rank"], rank, info["score"], score, "portfolio removal")
            if item:
                item.update(_future_metrics(bars[symbol], day, item["exit_price"]))
                positions.append(item)
    position_frame = pd.DataFrame(positions)
    replacements: list[dict[str, Any]] = []
    lookup = {(x["exit_date"], x["symbol"]): x for x in positions}
    for i in range(1, len(rebs) - 1):
        day, next_day = rebs.iloc[i].date, rebs.iloc[i + 1].date
        old, new = set(rebs.iloc[i - 1].holdings), set(rebs.iloc[i].holdings)
        outgoing, incoming = old - new, new - old
        snap = snaps[i]
        rank = lambda s: (float(snap.loc[s, "rank"]) if s in snap.index else math.inf, s)
        for out, inc in zip(sorted(outgoing, key=rank), sorted(incoming, key=rank)):
            out0, out1 = _at_or_after(bars[out], day, "Open"), _at_or_after(bars[out], next_day, "Open")
            in0, in1 = _at_or_after(bars[inc], day, "Open"), _at_or_after(bars[inc], next_day, "Open")
            if not all((out0, out1, in0, in1)): continue
            completed = lookup.get((_iso(day), out))
            advantage = in1 / in0 - out1 / out0
            replacements.append({"rebalance_date": _iso(day), "outgoing": out, "incoming": inc,
                                 "outgoing_state": "winner" if completed and completed["realized_return"] > 0 else "loser",
                                 "incoming_return": in1 / in0 - 1.0,
                                 "outgoing_counterfactual_return": out1 / out0 - 1.0,
                                 "replacement_advantage": advantage})
    return position_frame, pd.DataFrame(replacements), {"entries": entries, "exits": exits, "open": len(active)}


def _lifecycle_summary(positions: pd.DataFrame, replacements: pd.DataFrame) -> dict[str, Any]:
    winners = positions[positions.realized_return > 0]
    captures = winners.loc[winners.close_mfe >= CAPTURE_MFE_FLOOR, "mfe_capture_ratio"].dropna()
    losers = replacements[replacements.outgoing_state == "loser"] if len(replacements) else replacements
    return {"completedPositions": len(positions), "medianHoldingSessions": float(positions.holding_sessions.median()),
            "medianWinnerMfeCapturePct": float(captures.median() * 100) if len(captures) else None,
            "meanWinnerGivebackPct": _mean_pct(winners.profit_given_back),
            "medianWinnerGivebackPct": _pct(winners.profit_given_back),
            "meanPostExit20dPct": _mean_pct(positions.post_exit_20d_return),
            "replacementN": len(replacements), "replacementWinRatePct": _share(replacements.replacement_advantage > 0),
            "averageReplacementAdvantagePct": _mean_pct(replacements.replacement_advantage),
            "loserReplacementN": len(losers), "loserReplacementWinRatePct": _share(losers.replacement_advantage > 0) if len(losers) else None,
            "loserReplacementAdvantagePct": _mean_pct(losers.replacement_advantage) if len(losers) else None}


def _complete_grace_events(variant: OneRebalanceWinnerGrace, result: CrossSectionalResult) -> pd.DataFrame:
    bars = result.validation_bars or {}
    rebalance_dates = list(result.rebalances.sort_values("date").date)
    by_date = {_iso(day): i for i, day in enumerate(rebalance_dates)}
    rows = []
    for event in variant.events:
        row = event.__dict__.copy()
        i = by_date[event.date]
        if i + 1 >= len(rebalance_dates):
            rows.append(row)
            continue
        day, next_day = rebalance_dates[i], rebalance_dates[i + 1]
        incumbent = bars[event.incumbent_symbol]
        incoming = bars[event.deferred_incoming_symbol]
        inc0, inc1 = _at_or_after(incumbent, day, "Open"), _at_or_after(incumbent, next_day, "Open")
        new0, new1 = _at_or_after(incoming, day, "Open"), _at_or_after(incoming, next_day, "Open")
        if all((inc0, inc1, new0, new1)):
            row.update({"next_rebalance_date": _iso(next_day),
                        "incumbent_next_return": inc1 / inc0 - 1.0,
                        "deferred_incoming_next_return": new1 / new0 - 1.0,
                        "incumbent_minus_incoming": inc1 / inc0 - new1 / new0})
        rows.append(row)
    frame = pd.DataFrame(rows)
    if len(frame):
        frame["rank_category"] = pd.cut(
            frame.incumbent_rank.fillna(np.inf), bins=[5, 7, 10, np.inf],
            labels=["rank 6-7", "rank 8-10", "rank >10 or unranked"], right=True,
        ).astype(str)
    return frame


def _canonical_incoming_events(result: CrossSectionalResult, strategy: DualMomentum) -> pd.DataFrame:
    bars, rebs = result.validation_bars or {}, result.rebalances.sort_values("date").reset_index(drop=True)
    snaps = _snapshots(result, strategy)
    rows = []
    for i in range(1, len(rebs) - 1):
        day, next_day = rebs.iloc[i].date, rebs.iloc[i + 1].date
        prior, current = set(rebs.iloc[i - 1].holdings), set(rebs.iloc[i].holdings)
        for symbol in current - prior:
            p0, p1 = _at_or_after(bars[symbol], day, "Open"), _at_or_after(bars[symbol], next_day, "Open")
            if p0 and p1:
                snap = snaps[i]
                rows.append({"date": _iso(day), "symbol": symbol,
                             "rank": int(snap.loc[symbol, "rank"]), "score": float(snap.loc[symbol, "score"]),
                             "next_return": p1 / p0 - 1.0})
    return pd.DataFrame(rows)


def _evidence_concentration(events: pd.DataFrame) -> dict[str, Any]:
    valid = events.dropna(subset=["incumbent_minus_incoming"])
    if valid.empty: return {"events": 0}
    positive = valid.loc[valid.incumbent_minus_incoming > 0, "incumbent_minus_incoming"].sort_values(ascending=False)
    positive_total = float(positive.sum())
    shares = {f"top{k}ShareOfPositivePct": (float(positive.iloc[:k].sum() / positive_total * 100)
                                               if positive_total > 0 else None) for k in (1, 3, 5)}
    cluster_means, cluster_sums = [], []
    for cluster in valid.date.unique():
        keep = valid[valid.date != cluster]
        if len(keep):
            cluster_means.append(float(keep.incumbent_minus_incoming.mean()))
            cluster_sums.append(float(keep.incumbent_minus_incoming.sum() / TOP_N))
    return {"events": len(valid), "positiveEvents": int((valid.incumbent_minus_incoming > 0).sum()),
            "negativeEvents": int((valid.incumbent_minus_incoming < 0).sum()), **shares,
            "top1Event": _safe(valid.loc[valid.incumbent_minus_incoming.idxmax()].to_dict()),
            "worstEvent": _safe(valid.loc[valid.incumbent_minus_incoming.idxmin()].to_dict()),
            "leaveOneClusterOutMeanRangePct": ([min(cluster_means) * 100, max(cluster_means) * 100]
                                                if cluster_means else [None, None]),
            "leaveOneClusterOutCumulativeRangePct": ([min(cluster_sums) * 100, max(cluster_sums) * 100]
                                                      if cluster_sums else [None, None])}


def _portfolio_concentration(result: CrossSectionalResult) -> dict[str, Any]:
    bars = result.validation_bars or {}
    rebs = result.rebalances.sort_values("date").reset_index(drop=True)
    pairwise = []
    counts, hhi = [], []
    for i, row in rebs.iterrows():
        symbols = list(row.holdings)
        counts.append(len(symbols))
        weights = list(row.holdings.values())
        hhi.append(sum(float(w) ** 2 for w in weights))
        if i + 1 >= len(rebs) or len(symbols) < 2: continue
        start, end = row.date, rebs.iloc[i + 1].date
        returns = pd.DataFrame({s: bars[s].loc[(bars[s].index >= start) & (bars[s].index < end), "Close"].pct_change()
                                for s in symbols}).dropna(how="all")
        corr = returns.corr(min_periods=5).to_numpy()
        values = corr[np.triu_indices_from(corr, k=1)]
        values = values[np.isfinite(values)]
        if len(values): pairwise.append(float(values.mean()))
    return {"averageHoldings": float(np.mean(counts)), "minimumHoldings": int(min(counts)),
            "maximumHoldings": int(max(counts)), "averageWeightHhi": float(np.mean(hhi)),
            "effectiveEqualWeightNames": float(1.0 / np.mean(hhi)),
            "averageWithinPortfolioPairwiseCorrelation": float(np.mean(pairwise)) if pairwise else None,
            "sectorConcentration": None,
            "sectorCaveat": "No point-in-time historical sector-classification ledger is installed; current labels were not backfilled."}


def _prop_summary(result: CrossSectionalResult) -> dict[str, Any]:
    series, exposure, decomposition = prop_series_from_result(result)
    sizing = sweep_sizing(series.returns, SCENARIOS["conservative"], PropSimulationConfig())
    def point(value: Any) -> dict[str, Any] | None:
        return value.to_dict() if value is not None else None
    return {"series": series.to_dict(), "exposure": exposure.to_dict(), "decomposition": decomposition.to_dict(),
            "accountScenario": {"name": SCENARIOS["conservative"].name,
                                "accountSize": SCENARIOS["conservative"].account_size,
                                "maxTotalLossPct": SCENARIOS["conservative"].max_total_loss_pct,
                                "dailyLossLimitPct": SCENARIOS["conservative"].daily_loss_limit_pct},
            "conservativeSizing": point(sizing.conservative), "maxSurvivalSizing": point(sizing.max_survival),
            "maxPayoutSizing": point(sizing.max_payout), "boundaryReached": sizing.boundary_reached,
            "unresolvedAtBoundary": sizing.unresolved_at_boundary}


def _comparison_rows(control: dict[str, Any], modified: dict[str, Any]) -> dict[str, Any]:
    return {key: {"canonical": control.get(key), "winnerGrace": modified.get(key),
                  "difference": (modified[key] - control[key]
                                 if isinstance(control.get(key), (int, float)) and isinstance(modified.get(key), (int, float)) else None)}
            for key in control.keys() | modified.keys()}


def _rank_diagnostics(events: pd.DataFrame) -> dict[str, Any]:
    output = {}
    for category, frame in events.dropna(subset=["incumbent_minus_incoming"]).groupby("rank_category", observed=True):
        output[str(category)] = {"events": len(frame), "clusters": frame.date.nunique(),
                                 "winRatePct": _share(frame.incumbent_minus_incoming > 0),
                                 "meanDifferencePct": _mean_pct(frame.incumbent_minus_incoming),
                                 "medianDifferencePct": _pct(frame.incumbent_minus_incoming),
                                 "incumbentReturnPct": _mean_pct(frame.incumbent_next_return),
                                 "deferredIncomingReturnPct": _mean_pct(frame.deferred_incoming_next_return)}
    return output


def _deferred_diagnostics(events: pd.DataFrame, canonical_incoming: pd.DataFrame) -> dict[str, Any]:
    valid = events.dropna(subset=["deferred_incoming_next_return"])
    if valid.empty or canonical_incoming.empty: return {"events": len(valid)}
    return_cutoff = float(canonical_incoming.next_return.quantile(.75))
    score_cutoff = float(canonical_incoming.score.quantile(.75))
    return {"events": len(valid), "meanReturnPct": _mean_pct(valid.deferred_incoming_next_return),
            "medianReturnPct": _pct(valid.deferred_incoming_next_return),
            "canonicalIncomingMeanReturnPct": _mean_pct(canonical_incoming.next_return),
            "rank1SharePct": _share(valid.incoming_rank == 1),
            "topQuartileMomentumScoreSharePct": _share(valid.incoming_score >= score_cutoff),
            "laterLargeWinnerSharePct": _share(valid.deferred_incoming_next_return >= return_cutoff),
            "largeWinnerDefinition": "top quartile of next-period returns among all canonical incoming events",
            "extremeMomentumDefinition": "top quartile of scores among all canonical incoming events"}


def _own_capital(metrics: dict[str, Any]) -> dict[str, Any]:
    capital = 100_000.0
    return {"startingValue": capital,
            "endingValue": capital * (1.0 + metrics["cumulativeReturnPct"] / 100.0),
            "cumulativeProfit": capital * metrics["cumulativeReturnPct"] / 100.0,
            "maxDrawdownDollars": capital * metrics["maxDrawdownPct"] / 100.0,
            "worstDayDollars": capital * metrics["worstDayPct"] / 100.0}


def _result_markdown(report: dict[str, Any]) -> str:
    perf = report["performanceComparison"]
    life = report["lifecycleComparison"]
    lines = ["# Dual Momentum - One-Rebalance Winner Grace v1", "",
             f"**Final classification: {report['finalClassification']}**", "",
             "All historical results are development evidence. No clean historical holdout remains.", "",
             "## Canonical vs modified performance", "",
             "| Metric | Canonical DM | Winner Grace | Difference |", "|---|---:|---:|---:|"]
    perf_fields = (("CAGR", "cagrPct", "%"), ("Cumulative return", "cumulativeReturnPct", "%"),
                   ("Annualized volatility", "annualizedVolatilityPct", "%"), ("Sharpe", "sharpe", ""),
                   ("Sortino", "sortino", ""), ("Max drawdown", "maxDrawdownPct", "%"),
                   ("Max DD duration", "maxDrawdownDurationSessions", " sessions"), ("Worst day", "worstDayPct", "%"),
                   ("Worst rolling 5", "worstRolling5Pct", "%"), ("Worst rolling 20", "worstRolling20Pct", "%"),
                   ("Worst month", "worstMonthPct", "%"), ("Positive months", "positiveMonthPct", "%"),
                   ("Annual turnover", "annualizedTurnoverMultiple", "x"), ("Transaction costs", "transactionCosts", "$"))
    def fmt(value: Any, suffix: str = "") -> str:
        return "N/A" if value is None else f"{value:.2f}{suffix}"
    for label, key, suffix in perf_fields:
        row = perf[key]
        lines.append(f"| {label} | {fmt(row['canonical'],suffix)} | {fmt(row['winnerGrace'],suffix)} | {fmt(row['difference'],suffix)} |")
    lines += ["", "## Lifecycle changes", "", "| Metric | Canonical DM | Winner Grace | Difference |", "|---|---:|---:|---:|"]
    for label, key, suffix in (("Median hold", "medianHoldingSessions", " sessions"),
                               ("Winner MFE capture", "medianWinnerMfeCapturePct", "%"),
                               ("Mean winner giveback", "meanWinnerGivebackPct", "%"),
                               ("Post-exit 20d", "meanPostExit20dPct", "%"),
                               ("Replacement win rate", "replacementWinRatePct", "%"),
                               ("Replacement advantage", "averageReplacementAdvantagePct", "%"),
                               ("Loser replacement advantage", "loserReplacementAdvantagePct", "%")):
        row = life[key]
        lines.append(f"| {label} | {fmt(row['canonical'],suffix)} | {fmt(row['winnerGrace'],suffix)} | {fmt(row['difference'],suffix)} |")
    grace = report["graceEconomics"]
    lines += ["", "## Grace-event economics", "",
              f"- {grace['rawEvents']} grace events across {grace['effectiveN']} rebalance clusters; {grace['canonicalExitSharePct']:.2f}% of canonical exits affected.",
              f"- Incumbent beat deferred incoming in {grace['winRatePct']:.2f}% of events; mean difference {grace['meanDifferencePct']:.2f}%, median {grace['medianDifferencePct']:.2f}%.",
              f"- Rebalance-cluster 95% CI: {grace['ci95Pct'][0]:.2f}% to {grace['ci95Pct'][1]:.2f}%.",
              f"- Equal-slot arithmetic contribution: {grace['equalSlotCumulativePct']:.2f}% versus an actual net portfolio difference of {report['decomposition']['actualNetReturnDifferencePct']:.2f}%.", "",
              "## Rank diagnostic", "", "| Exit-rank category | Events | Win rate | Mean incumbent-minus-incoming |", "|---|---:|---:|---:|"]
    for category, row in report["rankDiagnostics"].items():
        lines.append(f"| {category} | {row['events']} | {fmt(row['winRatePct'],'%')} | {fmt(row['meanDifferencePct'],'%')} |")
    deferred = report["deferredIncomingDiagnostics"]
    lines += ["", "## Deferred incoming names", "",
              "| Metric | Result |", "|---|---:|",
              f"| Deferred events | {deferred['events']} |",
              f"| Mean next-period return | {fmt(deferred.get('meanReturnPct'),'%')} |",
              f"| Canonical incoming mean | {fmt(deferred.get('canonicalIncomingMeanReturnPct'),'%')} |",
              f"| Rank-1 share | {fmt(deferred.get('rank1SharePct'),'%')} |",
              f"| Extreme-momentum share | {fmt(deferred.get('topQuartileMomentumScoreSharePct'),'%')} |",
              f"| Later-large-winner share | {fmt(deferred.get('laterLargeWinnerSharePct'),'%')} |"]
    concentration = report["concentrationOfEvidence"]
    lines += ["", "## Evidence concentration and leave-one-cluster-out", "",
              "| Metric | Result |", "|---|---:|",
              f"| Positive / negative events | {concentration['positiveEvents']} / {concentration['negativeEvents']} |",
              f"| Top 1 share of positive contribution | {fmt(concentration.get('top1ShareOfPositivePct'),'%')} |",
              f"| Top 3 share | {fmt(concentration.get('top3ShareOfPositivePct'),'%')} |",
              f"| Top 5 share | {fmt(concentration.get('top5ShareOfPositivePct'),'%')} |",
              f"| LOO mean range | {fmt(concentration['leaveOneClusterOutMeanRangePct'][0],'%')} to {fmt(concentration['leaveOneClusterOutMeanRangePct'][1],'%')} |",
              f"| LOO cumulative range | {fmt(concentration['leaveOneClusterOutCumulativeRangePct'][0],'%')} to {fmt(concentration['leaveOneClusterOutCumulativeRangePct'][1],'%')} |"]
    side = report["sideEffects"]
    lines += ["", "## Side effects", "",
              "| Metric | Canonical DM | Winner Grace |", "|---|---:|---:|",
              f"| Average holdings | {fmt(side['canonical']['averageHoldings'])} | {fmt(side['winnerGrace']['averageHoldings'])} |",
              f"| Effective equal-weight names | {fmt(side['canonical']['effectiveEqualWeightNames'])} | {fmt(side['winnerGrace']['effectiveEqualWeightNames'])} |",
              f"| Average within-portfolio correlation | {fmt(side['canonical']['averageWithinPortfolioPairwiseCorrelation'])} | {fmt(side['winnerGrace']['averageWithinPortfolioPairwiseCorrelation'])} |",
              f"| Total entries | {perf['entries']['canonical']} | {perf['entries']['winnerGrace']} |",
              f"| Total exits | {perf['exits']['canonical']} | {perf['exits']['winnerGrace']} |",
              "", side['canonical']['sectorCaveat']]
    own = report["ownCapital100k"]
    lines += ["", "## $100,000 historical illustration", "",
              "| Metric | Canonical DM | Winner Grace |", "|---|---:|---:|",
              f"| Ending value | ${own['canonical']['endingValue']:,.0f} | ${own['winnerGrace']['endingValue']:,.0f} |",
              f"| Cumulative profit | ${own['canonical']['cumulativeProfit']:,.0f} | ${own['winnerGrace']['cumulativeProfit']:,.0f} |",
              f"| Max drawdown dollars | ${own['canonical']['maxDrawdownDollars']:,.0f} | ${own['winnerGrace']['maxDrawdownDollars']:,.0f} |",
              f"| Worst-day dollars | ${own['canonical']['worstDayDollars']:,.0f} | ${own['winnerGrace']['worstDayDollars']:,.0f} |"]
    prop = report["propAccount"]
    lines += ["", "## Prop-account comparison", "",
              "Existing standard sweep: Conservative $100,000 account, 4% total-loss limit, 2% daily-loss limit, 5,000 block-bootstrap paths.", "",
              "| Conservative sizing metric | Canonical DM | Winner Grace |", "|---|---:|---:|"]
    for label, key, suffix in (("Risk multiplier","risk_multiplier","x"),("Failure probability","failure_prob","%"),
                               ("Daily-limit breach probability","daily_limit_failure_prob","%"),
                               ("Total-drawdown breach probability","total_loss_failure_prob","%"),
                               ("Expected net payout","expected_net_payout","$")):
        c = prop['canonical']['conservativeSizing'][key]
        m = prop['winnerGrace']['conservativeSizing'][key]
        if key.endswith("prob"): c, m = c * 100, m * 100
        if suffix == "$":
            lines.append(f"| {label} | ${c:,.0f} | ${m:,.0f} |")
        else: lines.append(f"| {label} | {fmt(c,suffix)} | {fmt(m,suffix)} |")
    lines += ["", "## Verdict", "", report["verdictExplanation"], "",
              "The canonical Dual Momentum registration and the frozen DM/MRM portfolio were not changed.", ""]
    return "\n".join(lines)


def run_experiment(output_dir: Path) -> dict[str, Any]:
    if not PREREGISTRATION.exists():
        raise FileNotFoundError("Preregistration must exist before results are computed.")
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    if prereg.get("statusAtRegistration") != "preregistered_before_modified_strategy_results":
        raise RuntimeError("Preregistration status is not frozen.")

    canonical = run_cross_sectional("Dual Momentum", persist=False)
    bars = canonical.validation_bars or {}
    variant = OneRebalanceWinnerGrace(risk_free_rate=canonical.risk_free_rate)
    spreads = {s: spread_for_universe(s, canonical.start, canonical.end, None) for s in canonical.symbols}
    modified = run_cross_sectional_backtest(
        VARIANT_NAME, variant, canonical.symbols, canonical.start, canonical.end,
        risk_free_rate=canonical.risk_free_rate, rebalance_frequency="monthly",
        spread_by_symbol=spreads, commission_bps=ALPACA_COMMISSION_BPS,
        bars_by_symbol=bars, membership_at=canonical.membership_at_runtime,
        universe_key=canonical.universe_key,
    )
    canonical_strategy = DualMomentum(risk_free_rate=canonical.risk_free_rate)
    control_positions, control_replacements, control_counts = _reconstruct(canonical, canonical_strategy)
    modified_positions, modified_replacements, modified_counts = _reconstruct(modified, canonical_strategy)
    events = _complete_grace_events(variant, modified)
    incoming = _canonical_incoming_events(canonical, canonical_strategy)
    valid_events = events.dropna(subset=["incumbent_minus_incoming"])
    inference = _cluster_ci(valid_events, "incumbent_minus_incoming", "date")
    control_perf, modified_perf = _daily_metrics(canonical), _daily_metrics(modified)
    control_perf.update(control_counts); modified_perf.update(modified_counts)
    control_life = _lifecycle_summary(control_positions, control_replacements)
    modified_life = _lifecycle_summary(modified_positions, modified_replacements)
    concentration = _evidence_concentration(valid_events)
    simple_event = float(valid_events.incumbent_minus_incoming.sum() / TOP_N)
    actual_return_diff = modified.return_pct - canonical.return_pct
    loser_exit_count = sum(len(row["losersExited"]) for row in variant.decision_log)
    loser_grace_violations = sum(bool(set(row["losersExited"]) & set(row["grace"])) for row in variant.decision_log)
    grace_economics = {
        "rawEvents": len(valid_events), "effectiveN": valid_events.date.nunique(),
        "canonicalExitSharePct": len(valid_events) / max(control_counts["exits"], 1) * 100,
        "winRatePct": _share(valid_events.incumbent_minus_incoming > 0),
        "meanDifferencePct": _mean_pct(valid_events.incumbent_minus_incoming),
        "medianDifferencePct": _pct(valid_events.incumbent_minus_incoming),
        "ci95Pct": [x * 100 for x in inference["ci95"]],
        "clusterBootstrap": inference, "equalSlotCumulativePct": simple_event * 100,
    }
    performance_comparison = _comparison_rows(control_perf, modified_perf)
    lifecycle_comparison = _comparison_rows(control_life, modified_life)
    evidence_checks = {
        "meanPositive": grace_economics["meanDifferencePct"] > 0,
        "medianPositive": grace_economics["medianDifferencePct"] > 0,
        "netReturnImproved": modified.return_pct > canonical.return_pct,
        "winnerLifecycleImproved": (modified_life["medianWinnerMfeCapturePct"] > control_life["medianWinnerMfeCapturePct"]
                                    or modified_life["meanWinnerGivebackPct"] < control_life["meanWinnerGivebackPct"]),
        "loserRulePreserved": loser_grace_violations == 0,
        "maxDrawdownWithinLimit": modified.max_drawdown_pct - canonical.max_drawdown_pct <= 2.0,
        "worstDayWithinLimit": abs(modified_perf["worstDayPct"]) - abs(control_perf["worstDayPct"]) <= 1.0,
        "effectiveNAtLeast12": grace_economics["effectiveN"] >= 12,
        "topEventBelowHalfPositiveContribution": (concentration.get("top1ShareOfPositivePct") or 100) < 50,
    }
    if all(evidence_checks.values()):
        classification = "Historical candidate - requires forward validation"
        explanation = "Every preregistered historical-candidate criterion passed, but the full history was development data and cannot validate the rule."
    elif (not evidence_checks["meanPositive"] or not evidence_checks["netReturnImproved"]
          or not evidence_checks["loserRulePreserved"] or not evidence_checks["maxDrawdownWithinLimit"]
          or not evidence_checks["worstDayWithinLimit"]):
        classification = "Modification unsupported"
        explanation = "The modification failed at least one preregistered primary, net-performance, mechanism-preservation, or tail-risk condition."
    else:
        classification = "Interesting historical candidate - insufficient independent evidence"
        explanation = "Mechanism economics were promising, but one or more preregistered lifecycle, breadth, concentration, or effective-N criteria failed."

    control_concentration, modified_concentration = _portfolio_concentration(canonical), _portfolio_concentration(modified)
    report = {
        "strategyName": VARIANT_NAME, "preregistration": str(PREREGISTRATION),
        "developmentPeriod": prereg["developmentPeriod"], "validationStatus": "No clean historical holdout remains",
        "implementationAudit": {"canonicalParameters": {"lookbackTradingDays": 189, "topN": 5, "rebalanceFrequency": "monthly"},
                                "sameDates": canonical.start == modified.start and canonical.end == modified.end,
                                "sameSymbols": canonical.symbols == modified.symbols,
                                "sameMembership": canonical.universe_key == modified.universe_key,
                                "sameCostVector": True, "loserOutgoingCount": loser_exit_count,
                                "loserGraceViolations": loser_grace_violations,
                                "loggedGraceEventsReconcile": sum(len(row["grace"]) for row in variant.decision_log) == len(events),
                                "eachGraceHasOneDeferredIncoming": sum(len(row.get("deferred", [])) for row in variant.decision_log) == len(events),
                                "allGraceStatesStrictlyPositive": bool((events.unrealized_return > 0).all()),
                                "targetCountMatchesCanonical": bool((modified.rebalances.holdings.map(len).to_numpy()
                                                                    == canonical.rebalances.holdings.map(len).to_numpy()).all()),
                                "graceNeverChained": _verify_no_chained_grace(variant.decision_log)},
        "performanceComparison": performance_comparison,
        "lifecycleComparison": lifecycle_comparison,
        "graceEconomics": grace_economics, "rankDiagnostics": _rank_diagnostics(valid_events),
        "deferredIncomingDiagnostics": _deferred_diagnostics(valid_events, incoming),
        "concentrationOfEvidence": concentration,
        "decomposition": {"winnerGraceEqualSlotArithmeticPct": simple_event * 100,
                          "loserReplacementDirectModificationPct": 0.0,
                          "actualNetReturnDifferencePct": actual_return_diff,
                          "interactionCompoundingAndCostPct": actual_return_diff - simple_event * 100,
                          "note": "Event arithmetic is gross and equal-slot; residual includes compounding, sizing drift, later path interactions, and cost differences."},
        "sideEffects": {"canonical": control_concentration, "winnerGrace": modified_concentration,
                        "higherLaterTurnover": modified.total_traded_notional > canonical.total_traded_notional,
                        "transactionCostDifference": modified.total_costs - canonical.total_costs,
                        "worstDayDifferencePct": modified_perf["worstDayPct"] - control_perf["worstDayPct"]},
        "ownCapital100k": {"canonical": _own_capital(control_perf), "winnerGrace": _own_capital(modified_perf)},
        "propAccount": {"canonical": _prop_summary(canonical), "winnerGrace": _prop_summary(modified)},
        "evidenceChecks": evidence_checks, "finalClassification": classification,
        "verdictExplanation": explanation,
        "forwardTestRecommendation": ("Freeze this distinct variant for observations strictly after the 2026-08-22 cutoff."
                                      if classification == "Historical candidate - requires forward validation"
                                      else "Do not start a frozen forward test from this result."),
        "canonicalChanged": False, "frozenDmMrmPortfolioChanged": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(output_dir / "grace_events.csv", index=False)
    control_positions.to_csv(output_dir / "canonical_positions.csv", index=False)
    modified_positions.to_csv(output_dir / "winner_grace_positions.csv", index=False)
    control_replacements.to_csv(output_dir / "canonical_replacements.csv", index=False)
    modified_replacements.to_csv(output_dir / "winner_grace_replacements.csv", index=False)
    pd.DataFrame(variant.decision_log).to_json(output_dir / "decision_log.json", orient="records", indent=2)
    (output_dir / "dm_winner_grace_v1_results.json").write_text(json.dumps(_safe(report), indent=2), encoding="utf-8")
    (output_dir / "dm_winner_grace_v1_report.md").write_text(_result_markdown(report), encoding="utf-8")
    ledger = {"strategyName": VARIANT_NAME, "strategyVersion": "v1", "canonicalControl": "Dual Momentum canonical",
              "preregistration": str(PREREGISTRATION), "developmentCutoff": DEVELOPMENT_CUTOFF,
              "historicalDevelopmentResult": str(output_dir / "dm_winner_grace_v1_results.json"),
              "validationStatus": report["validationStatus"], "effectiveN": grace_economics["effectiveN"],
              "verdict": classification, "forwardEvidenceBeginsAfter": DEVELOPMENT_CUTOFF,
              "canonicalReplacementAuthorized": False}
    Path("research/dm_winner_grace_v1_ledger.json").write_text(json.dumps(_safe(ledger), indent=2), encoding="utf-8")
    return report


def _verify_no_chained_grace(log: list[dict[str, Any]]) -> bool:
    prior: set[str] = set()
    for row in log:
        current = set(row.get("grace", []))
        if prior & current:
            return False
        prior = current
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("reports/dm_winner_grace_v1"))
    args = parser.parse_args()
    report = run_experiment(args.output_dir)
    print(json.dumps({"classification": report["finalClassification"],
                      "graceEconomics": report["graceEconomics"],
                      "evidenceChecks": report["evidenceChecks"]}, indent=2))


if __name__ == "__main__":
    main()
