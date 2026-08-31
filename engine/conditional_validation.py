"""Out-of-sample validation for a frozen conditional hypothesis, and the
conditioned strategy that follows from one.

Discovery produces a rule that looked good on the sample that suggested it.
Everything in this module exists to find out whether that means anything.

**The nested split.**  `split_observations` cuts the observation set
chronologically into discovery / validation / final holdout. The cut is on
`decision_time` and is half-open, so no observation can appear in two
windows. The final holdout is not merely a third slice -- `ResearchSplit`
will not hand it over without `reveal_final_holdout()`, which writes a
consumption record to the ledger. Once looked at, it is no longer untouched,
and the ledger says so permanently rather than letting the next reader assume
otherwise.

**Purging and embargo.**  Trades overlap: a position opened three weeks
before a split boundary and closed after it spans both windows, and its
outcome is partly determined by price action inside the "unseen" data. Every
split therefore PURGES training observations whose exit falls at or after the
test window opens, and then applies an `EMBARGO_DAYS` band on top, because
the bars immediately after a purged trade are still serially correlated with
it. This is the standard purged-walk-forward construction, and it is the
difference between an honest out-of-sample number and a slightly-laundered
in-sample one.

**Walk-forward is expanding, never shuffled.**  `walk_forward` re-measures
the SAME frozen rule on successive future windows. It does not re-fit
anything -- refitting per fold would answer "can this procedure find
something each year", a different and much weaker question than "does this
specific relationship persist".

**The conditioned strategy is re-run, not filtered.**  A conditioned result
is produced by wrapping the original strategy in `ConditionedStrategy` and
running it through the existing engine with the identical universe, window,
spread model and risk-free rate -- the same wrap-don't-modify pattern
`engine/filters.py` already uses. Post-hoc deletion of rows from the trade
ledger would be wrong, not merely approximate: in the per-symbol engine a
suppressed entry frees the account, so a later signal that was blocked by an
open position now fills. Only a re-run gets that right, and only a re-run
keeps the cost model, the excursion diagnostics and the matched benchmark
consistent between the two arms.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from engine import conditional_ledger as ledger
from engine import conditional_stats as stats
from engine import observations as observations_module
from engine import pit_features
from engine.conditional_analysis import Condition, minimum_meaningful
from engine.observations import ObservationSet

#: Calendar days of embargo applied after purging. One trading week: long
#: enough to break the short-horizon autocorrelation a just-closed position
#: leaves behind, short enough not to consume a meaningful share of a
#: multi-year window.
EMBARGO_DAYS = 7

#: Default chronological split. Discovery gets the most data because it is
#: where the search happens; validation and the final holdout are equal so
#: neither is a token gesture.
DEFAULT_SPLIT = (0.60, 0.20, 0.20)

#: A walk-forward fold whose test window holds fewer than this is reported
#: but never counted toward "folds positive" -- a fold of nine trades is a
#: coin flip wearing a fold's clothing.
MIN_FOLD_OBSERVATIONS = stats.HYPOTHESIS_MIN_N


class FinalHoldoutSealed(RuntimeError):
    """Raised when the final holdout is read without being explicitly revealed."""


@dataclass
class ResearchSplit:
    """Discovery / validation / final holdout, with the last one sealed."""

    strategy_name: str
    discovery: ObservationSet
    validation: ObservationSet
    boundaries: dict[str, Any]
    _final_holdout: ObservationSet
    _revealed: bool = False

    @property
    def final_holdout(self) -> ObservationSet:
        if not self._revealed:
            raise FinalHoldoutSealed(
                f"The final holdout for {self.strategy_name} is sealed. Call "
                "reveal_final_holdout(reason=...) to open it -- doing so is recorded "
                "permanently in the research ledger, because once it has been looked at "
                "it is no longer untouched."
            )
        return self._final_holdout

    def reveal_final_holdout(self, *, reason: str, hypothesis_key: str | None = None) -> ObservationSet:
        """Open the final holdout and record that it has been consumed."""
        if not reason or not reason.strip():
            raise ValueError(
                "Revealing the final holdout requires a written reason; it becomes part of "
                "the permanent record of why this sample stopped being untouched."
            )
        ledger.mark_holdout_consumed(self.strategy_name, reason, hypothesis_key)
        self._revealed = True
        return self._final_holdout

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategyName": self.strategy_name,
            "boundaries": self.boundaries,
            "discoveryObservations": len(self.discovery),
            "validationObservations": len(self.validation),
            "finalHoldoutObservations": len(self._final_holdout),
            "finalHoldoutRevealed": self._revealed,
            "finalHoldoutSealedNote": (
                "The final holdout is not visible during feature exploration. Revealing it "
                "is recorded in the research ledger and marks it consumed."
            ),
            "embargoDays": EMBARGO_DAYS,
        }


def _purge_and_embargo(
    frame: pd.DataFrame, test_start: pd.Timestamp, *, embargo_days: int = EMBARGO_DAYS
) -> tuple[pd.DataFrame, int]:
    """Drop training rows whose outcome extends into (or right up to) the test window.

    A trade is dropped when its EXIT time is at or after `test_start` minus
    the embargo. Dropping on exit rather than entry is the whole point: the
    entry is safely in the past, but the outcome -- the thing being predicted
    -- was determined by bars the test window also contains.
    """
    if frame.empty:
        return frame, 0
    boundary = test_start - pd.Timedelta(days=embargo_days)
    exits = pd.to_datetime(frame["exit_time"], errors="coerce", utc=True)
    boundary_utc = pd.Timestamp(boundary).tz_convert("UTC") if boundary.tzinfo else pd.Timestamp(boundary, tz="UTC")
    # An unknown exit (a position still open at the window end) cannot be
    # shown to be safe, so it is purged too rather than assumed harmless.
    keep = exits.notna() & (exits < boundary_utc)
    return frame.loc[keep].reset_index(drop=True), int((~keep).sum())


def split_observations(
    observations: ObservationSet,
    *,
    fractions: tuple[float, float, float] = DEFAULT_SPLIT,
    embargo_days: int = EMBARGO_DAYS,
) -> ResearchSplit:
    """Chronological three-way split with purging at both boundaries."""
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError(f"Split fractions must sum to 1.0, got {fractions}.")
    frame = observations.frame
    if frame.empty:
        return ResearchSplit(
            strategy_name=observations.strategy_name,
            discovery=observations, validation=observations,
            boundaries={"reason": "no observations"}, _final_holdout=observations,
        )
    times = pd.to_datetime(frame["decision_time"], utc=True)
    first, last = times.min(), times.max()
    span = last - first
    discovery_end = first + span * fractions[0]
    validation_end = first + span * (fractions[0] + fractions[1])

    discovery_rows = frame.loc[times < discovery_end]
    validation_rows = frame.loc[(times >= discovery_end) & (times < validation_end)]
    holdout_rows = frame.loc[times >= validation_end]

    discovery_rows, purged_discovery = _purge_and_embargo(
        discovery_rows, discovery_end, embargo_days=embargo_days
    )
    validation_rows, purged_validation = _purge_and_embargo(
        validation_rows, validation_end, embargo_days=embargo_days
    )

    def _wrap(rows: pd.DataFrame) -> ObservationSet:
        return ObservationSet(
            strategy_name=observations.strategy_name, engine=observations.engine,
            frame=rows.reset_index(drop=True), outcome_column=observations.outcome_column,
            outcome_label=observations.outcome_label, start=observations.start,
            end=observations.end, symbols=observations.symbols,
            feature_keys=observations.feature_keys, warnings=list(observations.warnings),
            coverage={k: int(rows[k].notna().sum()) for k in observations.feature_keys}
            if not rows.empty else {k: 0 for k in observations.feature_keys},
        )

    return ResearchSplit(
        strategy_name=observations.strategy_name,
        discovery=_wrap(discovery_rows), validation=_wrap(validation_rows),
        _final_holdout=_wrap(holdout_rows),
        boundaries={
            "first": str(first), "discoveryEnd": str(discovery_end),
            "validationEnd": str(validation_end), "last": str(last),
            "fractions": list(fractions), "embargoDays": embargo_days,
            "purgedFromDiscovery": purged_discovery,
            "purgedFromValidation": purged_validation,
            "purgeRule": (
                "A training observation is dropped when its EXIT falls at or after the next "
                "window opens, minus the embargo -- its outcome was decided by bars the "
                "unseen window also contains."
            ),
        },
    )


def conditions_from_payload(payload: Sequence[dict[str, Any]]) -> list[Condition]:
    return [
        Condition(
            feature=c["feature"], op=c["op"], value=c["value"],
            raw_edge=c.get("rawEdge", c.get("raw_edge")),
            source=c.get("source", "frozen"),
        )
        for c in payload
    ]


def apply_conditions(frame: pd.DataFrame, conditions: Sequence[Condition]) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for condition in conditions:
        mask &= condition.mask(frame).fillna(False)
    return mask


def evaluate(
    observations: ObservationSet,
    conditions: Sequence[Condition],
    *,
    minimum_effect: float | None = None,
    minimum_observations: int = stats.HYPOTHESIS_MIN_N,
    expected_direction: str = "higher",
    seed: int = stats.DEFAULT_SEED,
    permutations: int = 1_000,
    label: str = "sample",
) -> dict[str, Any]:
    """Measure a frozen rule on one sample. No fitting of any kind happens here."""
    frame = observations.frame
    outcome = observations.outcome_column
    threshold = (
        minimum_effect if minimum_effect is not None else minimum_meaningful(outcome)
    )
    if frame.empty:
        return {
            "label": label, "observations": 0, "passed": False,
            "conclusion": "No observations in this window.",
            "baselineMean": None, "conditionalMean": None, "improvement": None,
        }
    mask = apply_conditions(frame, conditions)
    conditioned = frame.loc[mask, outcome].astype(float)
    baseline = frame[outcome].astype(float)
    rest = frame.loc[~mask, outcome].astype(float)
    n = int(len(conditioned))
    summary = stats.summarize(conditioned, seed=seed) if n else stats.summarize([])
    baseline_summary = stats.summarize(baseline, seed=seed)
    improvement = (
        float(summary.mean - baseline_summary.mean) if n else float("nan")
    )
    permutation = (
        stats.permutation_test(conditioned, rest, permutations=permutations, seed=seed)
        if n >= 2 and len(rest) >= 2 else {"pValue": None}
    )
    direction_ok = (
        improvement > 0 if expected_direction == "higher" else improvement < 0
    ) if n and math.isfinite(improvement) else False
    effect_ok = n and math.isfinite(improvement) and abs(improvement) >= threshold and direction_ok
    sample_ok = n >= minimum_observations
    passed = bool(effect_ok and sample_ok)

    reasons: list[str] = []
    if not sample_ok:
        reasons.append(
            f"{n} qualifying observations is below the {minimum_observations} the frozen "
            "contract requires."
        )
    if n and not direction_ok:
        reasons.append(
            f"Effect moved {'down' if improvement < 0 else 'up'} "
            f"({improvement:+.4f}) against a declared direction of '{expected_direction}'."
        )
    elif n and direction_ok and abs(improvement) < threshold:
        reasons.append(
            f"Improvement {improvement:+.4f} is below the preregistered minimum "
            f"{threshold:+.4f}."
        )
    conclusion = (
        f"{label}: {n} observations, {summary.mean:+.4f} vs unconditional "
        f"{baseline_summary.mean:+.4f} ({improvement:+.4f})."
        if n else f"{label}: no qualifying observations."
    )
    if reasons:
        conclusion += " " + " ".join(reasons)

    return {
        "label": label,
        "observations": n,
        "totalObservations": int(len(frame)),
        "retentionPct": float(n / len(frame) * 100.0) if len(frame) else 0.0,
        "baselineMean": baseline_summary.mean,
        "conditionalMean": summary.mean if n else None,
        "improvement": improvement if n else None,
        "ciLow": summary.ci_low if n else None,
        "ciHigh": summary.ci_high if n else None,
        "ciMethod": summary.ci_method if n else None,
        "winRate": summary.win_rate if n else None,
        "baselineWinRate": baseline_summary.win_rate,
        "profitFactor": summary.profit_factor if n else None,
        "medianOutcome": summary.median if n else None,
        "effectSize": stats.hedges_g(
            conditioned.to_numpy(dtype=float), rest.to_numpy(dtype=float)
        ) if n >= 2 and len(rest) >= 2 else None,
        "permutationP": permutation.get("pValue"),
        "permutationMethod": permutation.get("method"),
        "minimumEffect": threshold,
        "minimumObservations": minimum_observations,
        "expectedDirection": expected_direction,
        "directionMatched": bool(direction_ok),
        "sampleTier": stats.sample_tier(n),
        "sampleWarning": stats.sample_warning(n),
        "passed": passed,
        "conclusion": conclusion,
        "windowStart": str(frame["decision_time"].min()),
        "windowEnd": str(frame["decision_time"].max()),
    }


def walk_forward(
    observations: ObservationSet,
    conditions: Sequence[Condition],
    *,
    folds: int = 5,
    embargo_days: int = EMBARGO_DAYS,
    minimum_effect: float | None = None,
    expected_direction: str = "higher",
    seed: int = stats.DEFAULT_SEED,
) -> dict[str, Any]:
    """Re-measure the SAME rule on successive future windows.

    Expanding-origin: fold k trains on everything before its test window and
    tests on the next slice. Nothing is refitted -- the rule is frozen -- so
    the "training" side exists only to define the boundary that gets purged
    and embargoed. Random K-fold is never used: shuffling time-series
    observations lets a fold's training data contain the future of its own
    test data, which turns look-ahead into a cross-validation score.
    """
    frame = observations.frame
    if frame.empty or folds < 2:
        return {"folds": [], "usableFolds": 0, "note": "Not enough observations to fold."}
    times = pd.to_datetime(frame["decision_time"], utc=True)
    first, last = times.min(), times.max()
    span = last - first
    edges = [first + span * (i / folds) for i in range(folds + 1)]

    results: list[dict[str, Any]] = []
    for index in range(1, folds):
        test_start, test_end = edges[index], edges[index + 1]
        test_rows = frame.loc[(times >= test_start) & (times < test_end)]
        train_rows = frame.loc[times < test_start]
        train_rows, purged = _purge_and_embargo(train_rows, test_start, embargo_days=embargo_days)
        fold_observations = ObservationSet(
            strategy_name=observations.strategy_name, engine=observations.engine,
            frame=test_rows.reset_index(drop=True), outcome_column=observations.outcome_column,
            outcome_label=observations.outcome_label, start=observations.start,
            end=observations.end, symbols=observations.symbols,
            feature_keys=observations.feature_keys,
        )
        evaluation = evaluate(
            fold_observations, conditions, minimum_effect=minimum_effect,
            minimum_observations=MIN_FOLD_OBSERVATIONS,
            expected_direction=expected_direction, seed=seed + index,
            permutations=400, label=f"fold {index}",
        )
        evaluation["trainObservations"] = int(len(train_rows))
        evaluation["purgedFromTrain"] = purged
        evaluation["testStart"] = str(test_start)
        evaluation["testEnd"] = str(test_end)
        evaluation["usable"] = evaluation["observations"] >= MIN_FOLD_OBSERVATIONS
        results.append(evaluation)

    usable = [r for r in results if r["usable"]]
    improvements = [
        r["improvement"] for r in usable
        if r["improvement"] is not None and math.isfinite(r["improvement"])
    ]
    retentions = [r["retentionPct"] for r in usable]
    positive = sum(
        1 for r in usable
        if r["improvement"] is not None
        and ((r["improvement"] > 0) if expected_direction == "higher" else (r["improvement"] < 0))
    )
    return {
        "folds": results,
        "foldCount": len(results),
        "usableFolds": len(usable),
        "positiveFolds": positive,
        "positiveFoldPct": (positive / len(usable) * 100.0) if usable else None,
        "meanImprovement": float(np.mean(improvements)) if improvements else None,
        "medianImprovement": float(np.median(improvements)) if improvements else None,
        "worstFoldImprovement": float(min(improvements)) if improvements else None,
        "bestFoldImprovement": float(max(improvements)) if improvements else None,
        "improvementStd": float(np.std(improvements, ddof=1)) if len(improvements) > 1 else None,
        "retentionMeanPct": float(np.mean(retentions)) if retentions else None,
        "retentionStdPct": float(np.std(retentions, ddof=1)) if len(retentions) > 1 else None,
        "directionStable": bool(usable and positive == len(usable)),
        "embargoDays": embargo_days,
        "method": (
            "Expanding-origin walk-forward with purged and embargoed training boundaries. "
            "The rule is frozen; nothing is refitted per fold."
        ),
    }


# ---------------------------------------------------------------------------
# The conditioned strategy
# ---------------------------------------------------------------------------


class ConditionedStrategy:
    """Wraps a `strategies.base.Strategy` and gates its entries on a rule.

    Re-implements the Strategy interface and delegates stop, target and exit
    untouched -- the same pattern `engine/filters.py:FilteredStrategy` uses,
    for the same reason: the backtest engine needs no change, and the
    conditioned arm therefore runs through exactly the code path the original
    did, with the same spread model, risk-free rate and sizing.

    The gate reads features at the CURRENT bar, which is the decision bar --
    the engine fills at the next open, so gating on `bars.index[-1]` uses the
    same information the original `entry_signal` used and no more.
    """

    def __init__(
        self,
        base: Any,
        symbol: str,
        conditions: Sequence[Condition],
        feature_frame: pd.DataFrame,
    ) -> None:
        self._base = base
        self.symbol = symbol
        self._conditions = list(conditions)
        self._frame = feature_frame
        self.name = f"{getattr(base, 'name', 'strategy')} [conditioned]"
        self.timeframe = getattr(base, "timeframe", "1d")
        self.direction = getattr(base, "direction", "long")
        self.blocked = 0
        self.allowed = 0

    # -- gate ---------------------------------------------------------------
    def _conditions_hold(self, bars: pd.DataFrame) -> bool:
        if self._frame.empty or len(bars) == 0:
            return False
        snapshot = pit_features.snapshot_at(self._frame, bars.index[-1])
        if not snapshot:
            return False
        row = pd.DataFrame([snapshot])
        for condition in self._conditions:
            value = snapshot.get(condition.feature)
            if value is None:
                # An unknown feature value cannot satisfy a condition. Gating
                # OFF on unknown is the conservative direction: the
                # alternative silently trades every bar whose feature is not
                # yet warm.
                return False
            if not bool(condition.mask(row).fillna(False).iloc[0]):
                return False
        return True

    # -- Strategy interface -------------------------------------------------
    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if not self._base.entry_signal(bars):
            return False
        if self._conditions_hold(bars):
            self.allowed += 1
            return True
        self.blocked += 1
        return False

    def entry_direction(self, bars: pd.DataFrame) -> str:
        return self._base.entry_direction(bars)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return self._base.stop_price(bars, entry_price)

    def target_price(self, bars: pd.DataFrame, entry_price: float):
        return self._base.target_price(bars, entry_price)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        return self._base.exit_signal(bars)

    def __getattr__(self, item: str) -> Any:
        # Anything the engine or a validation helper reads that this wrapper
        # does not define (params, required history, timing contract) comes
        # from the wrapped strategy, so the conditioned arm keeps the
        # original's declared contract rather than an emptier one.
        return getattr(self._base, item)


def run_conditioned_backtest(
    strategy_name: str,
    conditions: Sequence[Condition],
    *,
    context: pit_features.MarketContext | None = None,
    universe: Sequence[str] | None = None,
) -> Any:
    """Re-run `strategy_name` with the rule gating its entries.

    Uses `engine/runner.py`'s own configuration for the strategy, so universe,
    interval, window, spread estimation and risk-free rate are identical to
    the original arm by construction rather than by being copied correctly.
    Never persisted: a conditioned run is an experiment and must not shadow
    the canonical leaderboard row -- the same rule
    `engine/compare_filters.py` follows.
    """
    from engine import data as data_module
    from engine.backtest import run_strategy_backtest_seeded
    from engine.runner import build_strategy, run_config

    interval, symbols, start, end = run_config(strategy_name)
    universe = list(universe) if universe else symbols
    context = context or pit_features.build_market_context(universe, start, end)
    base = build_strategy(strategy_name, start, end)

    def seed(symbol: str) -> ConditionedStrategy:
        return ConditionedStrategy(
            base, symbol, conditions, pit_features.symbol_features(symbol, context, start, end)
        )

    rf = data_module.risk_free_rate(start, end)
    return run_strategy_backtest_seeded(
        f"{strategy_name} [conditioned]", seed, symbols, interval, start, end,
        risk_free_rate=rf,
    )


# ---------------------------------------------------------------------------
# Comparison, decomposition and prop economics
# ---------------------------------------------------------------------------


def _metric_row(metrics: Any, risk: Any, years: float | None) -> dict[str, Any]:
    trades = getattr(metrics, "trades_taken", 0)
    return {
        "trades": trades,
        "tradesPerYear": (trades / years) if years else None,
        "expectancyR": getattr(metrics, "expectancy_r", None),
        "winRate": getattr(metrics, "win_rate", None),
        "profitFactor": getattr(metrics, "profit_factor", None),
        "cagrPct": getattr(metrics, "cagr_pct", None),
        "sharpe": getattr(metrics, "sharpe", None),
        "sortino": getattr(metrics, "sortino", None),
        "maxDrawdownPct": getattr(metrics, "max_drawdown_pct", None),
        "exposurePct": getattr(metrics, "exposure_pct", None),
        "alphaPct": getattr(metrics, "alpha_pct", None),
        "buyHoldReturnPct": getattr(metrics, "buy_hold_return_pct", None),
        "p95DrawdownPct": getattr(risk, "p95_drawdown_pct", None) if risk else None,
        "worstDayPct": getattr(risk, "worst_day_pct", None) if risk else None,
        "worst5dPct": getattr(risk, "worst_5d_pct", None) if risk else None,
        "annualVolPct": getattr(risk, "annualized_vol_pct", None) if risk else None,
        "returnOverP95Dd": getattr(risk, "return_over_p95_dd", None) if risk else None,
    }


def _risk_metrics(result: Any) -> Any | None:
    from engine import prop_analysis

    try:
        series, profile = prop_analysis.normalized_daily_series(result)
        return prop_analysis.compute_risk_metrics(
            series.returns,
            compounding=profile.basis == "portfolio_equity",
        )
    except Exception:  # noqa: BLE001 -- an unusable daily series withholds these columns
        return None


def compare_strategies(
    original: Any, conditioned: Any, *, original_observations: ObservationSet | None = None,
    conditioned_observations: ObservationSet | None = None,
) -> dict[str, Any]:
    """The original-vs-conditioned table, plus retention and concurrency.

    Both arms come from the same engine over the same window with the same
    cost model, so every row is a like-for-like comparison. Signal retention
    is reported prominently because an apparent improvement that leaves 4% of
    the opportunities is often not an improvement at all -- it is a smaller,
    noisier strategy.
    """
    def _years(result: Any) -> float | None:
        start, end = getattr(result, "start", None), getattr(result, "end", None)
        if start is None or end is None:
            return None
        days = (end - start).days
        return days / 365.25 if days > 0 else None

    original_risk = _risk_metrics(original)
    conditioned_risk = _risk_metrics(conditioned)
    original_row = _metric_row(original.metrics, original_risk, _years(original))
    conditioned_row = _metric_row(conditioned.metrics, conditioned_risk, _years(conditioned))

    original_trades = original_row["trades"] or 0
    conditioned_trades = conditioned_row["trades"] or 0
    deltas = {
        key: (
            conditioned_row[key] - original_row[key]
            if isinstance(original_row.get(key), (int, float))
            and isinstance(conditioned_row.get(key), (int, float))
            else None
        )
        for key in original_row
    }
    return {
        "original": original_row,
        "conditioned": conditioned_row,
        "delta": deltas,
        "retention": {
            "originalSignals": original_trades,
            "conditionedSignals": conditioned_trades,
            "retentionPct": (
                conditioned_trades / original_trades * 100.0 if original_trades else None
            ),
            "note": (
                "Signal counts come from two independent runs of the same engine, so the "
                "conditioned count is not simply a subset of the original: suppressing an "
                "entry frees the account and can let a later signal fill that the original "
                "run was too busy to take."
            ),
        },
        "concurrency": {
            "original": (
                observations_module.concurrency_profile(original_observations.frame)
                if original_observations else None
            ),
            "conditioned": (
                observations_module.concurrency_profile(conditioned_observations.frame)
                if conditioned_observations else None
            ),
        },
        "sampleWarnings": [
            w for w in (
                stats.sample_warning(conditioned_trades),
                (
                    "Conditioned arm fell below the 30-trade reliability threshold -- a "
                    "filtered result can be LESS informative than the unfiltered one it "
                    "replaced."
                    if conditioned_trades < stats.HYPOTHESIS_MIN_N <= original_trades else None
                ),
            ) if w
        ],
        "costNote": (
            "Both arms apply the same per-symbol spread estimate inside their fills, so this "
            "is net against net. Turnover, holding period and concurrency differ between the "
            "arms and are reported rather than assumed unchanged."
        ),
    }


def decompose_filter_effect(
    observations: ObservationSet, conditions: Sequence[Condition]
) -> dict[str, Any]:
    """WHY does the filter change the result -- avoidance, selection, or risk?

    Three mechanisms with very different implications, especially for a prop
    account. Removing losers and concentrating winners can produce identical
    headline expectancy while behaving completely differently in a drawdown,
    and a filter whose whole contribution is tail-risk reduction may be
    valuable even when its mean barely moves.
    """
    frame = observations.frame
    outcome = observations.outcome_column
    if frame.empty:
        return {"available": False, "reason": "No observations."}
    mask = apply_conditions(frame, conditions)
    kept = frame.loc[mask, outcome].astype(float)
    removed = frame.loc[~mask, outcome].astype(float)
    if kept.empty or removed.empty:
        return {"available": False, "reason": "The rule keeps or removes everything."}

    baseline = frame[outcome].astype(float)
    kept_losers = kept[kept <= 0]
    removed_losers = removed[removed <= 0]
    kept_winners = kept[kept > 0]
    removed_winners = removed[removed > 0]

    loss_rate_change = float(
        (len(kept_losers) / len(kept)) - (len(baseline[baseline <= 0]) / len(baseline))
    )
    mean_win_change = float(
        (kept_winners.mean() if len(kept_winners) else 0.0)
        - (baseline[baseline > 0].mean() if (baseline > 0).any() else 0.0)
    )
    tail_baseline = float(np.percentile(baseline, 5))
    tail_kept = float(np.percentile(kept, 5))
    tail_change = tail_kept - tail_baseline

    mechanisms = {
        "badTradeAvoidance": {
            "lossRateChange": loss_rate_change,
            "removedLosers": int(len(removed_losers)),
            "removedLoserMean": float(removed_losers.mean()) if len(removed_losers) else None,
            "share": float(len(removed_losers) / len(removed)),
        },
        "winnerConcentration": {
            "meanWinChange": mean_win_change,
            "removedWinners": int(len(removed_winners)),
            "removedWinnerMean": float(removed_winners.mean()) if len(removed_winners) else None,
            "keptWinRate": float((kept > 0).mean()),
        },
        "riskReduction": {
            "p05Baseline": tail_baseline,
            "p05Conditioned": tail_kept,
            "p05Change": tail_change,
            "worstBaseline": float(baseline.min()),
            "worstConditioned": float(kept.min()),
            "stdBaseline": float(baseline.std(ddof=1)),
            "stdConditioned": float(kept.std(ddof=1)) if len(kept) > 1 else None,
        },
    }
    # Attribution by which component moved most in standardised terms. Named
    # "primary", never "the reason" -- these mechanisms overlap, and a filter
    # usually does some of all three.
    scores = {
        "bad-trade avoidance": abs(loss_rate_change) * 4.0,
        "winner concentration": abs(mean_win_change),
        "risk reduction": abs(tail_change),
    }
    primary = max(scores, key=scores.get)
    return {
        "available": True,
        "mechanisms": mechanisms,
        "primaryMechanism": primary,
        "note": (
            "These mechanisms overlap; 'primary' names the one that moved most, not the "
            "sole cause. Risk reduction with an unchanged mean is often the most valuable "
            "outcome for a prop account and the least visible in a headline expectancy."
        ),
    }


def prop_comparison(
    original: Any,
    conditioned: Any,
    *,
    scenario: str = "moderate",
    n_paths: int = 2_000,
    seed: int = stats.DEFAULT_SEED,
) -> dict[str, Any]:
    """Run both arms through the existing Prop Account Analysis engine.

    Nothing about the prop model changes here: the same
    `engine/prop_account.py` rules, the same block bootstrap, the same sizing
    sweep. The only new thing is that it is run twice and differenced, so the
    question "does this filter pay for itself in prop terms" gets a computed
    answer instead of the assumed one. A filter that lowers CAGR can still
    raise expected payout by raising safe size; that is measured here, not
    presumed in either direction.
    """
    from engine import prop_account, prop_analysis

    account = prop_account.SCENARIOS.get(scenario)
    if account is None:
        raise ValueError(
            f"Unknown prop scenario {scenario!r}; choose from {sorted(prop_account.SCENARIOS)}."
        )
    sim = prop_account.PropSimulationConfig(n_paths=n_paths, seed=seed)

    def _arm(result: Any, label: str) -> dict[str, Any]:
        try:
            series, profile = prop_analysis.normalized_daily_series(result)
        except Exception as exc:  # noqa: BLE001
            return {"label": label, "available": False, "reason": f"{type(exc).__name__}: {exc}"}
        try:
            risk = prop_analysis.compute_risk_metrics(
                series.returns, compounding=profile.basis == "portfolio_equity"
            )
            sizing = prop_account.sweep_sizing(series.returns, account, sim)
        except Exception as exc:  # noqa: BLE001
            return {"label": label, "available": False, "reason": f"{type(exc).__name__}: {exc}"}
        best = sizing.max_survival
        conservative = sizing.conservative
        payout = sizing.max_payout
        return {
            "label": label,
            "available": True,
            "observations": series.observations,
            "sessionCoverage": series.session_coverage,
            "risk": risk.to_dict(),
            "sizing": sizing.to_dict(),
            "headline": {
                "safeRiskMultiplier": best.risk_multiplier if best else None,
                "safeFailureProb": best.failure_prob if best else None,
                "conservativeMultiplier": conservative.risk_multiplier if conservative else None,
                "expectedNetPayout": payout.expected_net_payout if payout else None,
                "survival12m": best.survival_12m if best else None,
                "dailyLimitFailureProb": best.daily_limit_failure_prob if best else None,
                "totalLossFailureProb": best.total_loss_failure_prob if best else None,
                "p95DrawdownPct": risk.p95_drawdown_pct,
                "worstDayPct": risk.worst_day_pct,
            },
        }

    original_arm = _arm(original, "original")
    conditioned_arm = _arm(conditioned, "conditioned")
    delta = None
    if original_arm.get("available") and conditioned_arm.get("available"):
        a, b = original_arm["headline"], conditioned_arm["headline"]
        delta = {
            key: (b[key] - a[key])
            if isinstance(a.get(key), (int, float)) and isinstance(b.get(key), (int, float))
            else None
            for key in a
        }
    return {
        "scenario": scenario,
        "accountRules": {
            "name": account.name,
            "accountSize": account.account_size,
            "maxTotalLossPct": account.max_total_loss_pct * 100.0,
            "dailyLossLimitPct": account.daily_loss_limit_pct * 100.0,
            "riskBudget": account.risk_budget,
        },
        "original": original_arm,
        "conditioned": conditioned_arm,
        "delta": delta,
        "note": (
            "Prop economics are a SEPARATE question from edge. A conditioned strategy with a "
            "lower CAGR can be the better prop candidate if it survives sizing; this is "
            "computed here rather than assumed. Neither arm's simulated economics make its "
            "edge proven."
        ),
    }


# ---------------------------------------------------------------------------
# The conditional-edge verdict
# ---------------------------------------------------------------------------

VERDICT_VALIDATED = "Validated conditional edge"
VERDICT_PROMISING = "Promising conditional structure"
VERDICT_WEAK = "Weak / unstable conditional structure"
VERDICT_NONE = "No conditional edge detected"
VERDICT_INSUFFICIENT = "Insufficient evidence"


@dataclass
class ConditionalVerdict:
    verdict: str
    headline: str
    reasons: list[str]
    blockers: list[str]
    gates: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def conditional_verdict(
    *,
    discovery: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    holdout: dict[str, Any] | None,
    walk_forward_result: dict[str, Any] | None,
    fdr_significant: bool | None,
    hypotheses_examined: int | None,
    integrity: dict[str, Any] | None,
    exploratory_features_used: bool = False,
) -> ConditionalVerdict:
    """The conditional-edge verdict -- separate from the strategy's own verdict.

    In-sample Sharpe cannot reach this function, by construction: nothing it
    reads comes from the discovery sample except the search size and the
    hypothesis's own identity. A validated conditional edge requires positive
    out-of-sample improvement of a size that was declared BEFORE the test,
    an adequate sample, temporal stability, multiple-testing survival, an
    intact frozen contract, and no dependence on a feature that is not
    point-in-time.
    """
    gates: dict[str, Any] = {}
    blockers: list[str] = []
    reasons: list[str] = []

    gates["contractIntact"] = bool(integrity and integrity.get("intact"))
    if integrity and not integrity.get("intact"):
        blockers.append(
            "The frozen contract hash does not match its own contents -- the rule being "
            "measured is not the rule that was frozen."
        )
    if integrity and integrity.get("featureDriftDetected"):
        blockers.append(
            "A feature this rule depends on has changed definition since it was frozen: "
            + "; ".join(integrity.get("featureDrift", [])[:3])
        )
    if exploratory_features_used:
        blockers.append(
            "The rule depends on an exploratory feature (calendar or non-point-in-time "
            "sector data), which may not carry a conditional-edge verdict."
        )

    gates["fdrSurvived"] = bool(fdr_significant)
    gates["hypothesesExamined"] = hypotheses_examined

    validation_passed = bool(validation and validation.get("passed"))
    holdout_passed = bool(holdout and holdout.get("passed"))
    gates["validationPassed"] = validation_passed
    gates["holdoutPassed"] = holdout_passed

    stability = bool(
        walk_forward_result
        and walk_forward_result.get("usableFolds", 0) >= 3
        and (walk_forward_result.get("positiveFoldPct") or 0.0) >= 60.0
    )
    gates["temporalStability"] = stability
    gates["usableFolds"] = walk_forward_result.get("usableFolds") if walk_forward_result else None
    gates["positiveFolds"] = walk_forward_result.get("positiveFolds") if walk_forward_result else None

    sample_ok = bool(
        validation and validation.get("observations", 0) >= stats.EXPLORATORY_N
    )
    gates["adequateOutOfSampleN"] = sample_ok

    if validation is None:
        return ConditionalVerdict(
            VERDICT_INSUFFICIENT, VERDICT_INSUFFICIENT,
            reasons, blockers + ["No out-of-sample validation has been run."], gates,
        )
    if validation.get("observations", 0) < stats.HYPOTHESIS_MIN_N:
        blockers.append(
            f"Only {validation.get('observations', 0)} qualifying out-of-sample observations; "
            f"below the {stats.HYPOTHESIS_MIN_N}-observation floor this app uses everywhere."
        )

    if blockers:
        return ConditionalVerdict(
            VERDICT_INSUFFICIENT, VERDICT_INSUFFICIENT, reasons, blockers, gates
        )

    if validation_passed:
        reasons.append(validation["conclusion"])
    if holdout and holdout.get("observations"):
        reasons.append(holdout["conclusion"])
    if walk_forward_result and walk_forward_result.get("usableFolds"):
        reasons.append(
            f"{walk_forward_result['positiveFolds']}/{walk_forward_result['usableFolds']} "
            "walk-forward folds moved in the expected direction."
        )

    if validation_passed and holdout_passed and stability and fdr_significant and sample_ok:
        return ConditionalVerdict(
            VERDICT_VALIDATED, VERDICT_VALIDATED, reasons, [], gates
        )
    if validation_passed and (holdout_passed or stability):
        return ConditionalVerdict(
            VERDICT_PROMISING, VERDICT_PROMISING,
            reasons,
            [
                reason for reason in (
                    None if fdr_significant else
                    f"Did not survive multiple-testing correction across "
                    f"{hypotheses_examined or 'the'} hypotheses examined.",
                    None if holdout_passed else "Final holdout not passed (or not yet run).",
                    None if stability else "Effect direction is not stable across walk-forward folds.",
                ) if reason
            ],
            gates,
        )
    if validation_passed:
        return ConditionalVerdict(
            VERDICT_WEAK, VERDICT_WEAK, reasons,
            ["Validation passed but the effect is neither stable across time nor confirmed "
             "on the final holdout."],
            gates,
        )
    return ConditionalVerdict(
        VERDICT_NONE, VERDICT_NONE, reasons,
        [validation.get("conclusion", "Out-of-sample improvement did not meet the "
                        "preregistered minimum.")],
        gates,
    )
