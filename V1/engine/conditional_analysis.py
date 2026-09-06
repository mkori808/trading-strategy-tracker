"""Conditional analysis: does this strategy's edge depend on observable state?

Consumes an `ObservationSet` (engine/observations.py) and produces, in this
order: univariate conditional tables, pairwise interaction tables, a bounded
set of three-way interactions, a feature-redundancy report, and finally a
small number of human-readable candidate hypotheses.

**The search is deliberately small, and that is the point.**  The dangerous
version of this feature is a rule generator that enumerates thresholds until
something looks good; the research brief names that as the worst possible
outcome and this module is built to make it hard rather than easy:

- Continuous features are cut at QUANTILES, never at searched thresholds. A
  candidate's frozen rule snaps to an interpretable number (RSI < 30, not
  RSI < 27.43) and records both the raw quantile edge and the snapped value
  so the snapping is auditable rather than hidden.
- Pairwise interactions are not the full n-choose-2 grid. Only features that
  cleared univariate evidence are crossed, and only against each other plus
  a fixed regime axis, capped at `MAX_PAIRWISE_PAIRS`.
- Three-way interactions are generated only from pairs that already survived,
  are capped hard, and require `THREE_WAY_MIN_N` in every retained cell.
- A candidate hypothesis may combine at most `MAX_CONDITIONS` conditions, and
  each added condition must come from a DIFFERENT feature group -- stacking
  RSI(2), the 3-day return and distance from the 5-day SMA is one condition
  wearing three hats.
- Every test performed, including the ones whose results are never shown, is
  counted into `DiscoveryAccounting` and fed to the FDR correction.

**Nothing here decides anything.**  Every hypothesis this module emits is
labelled `exploratory` or `candidate` and carries `in_sample=True`. Promotion
requires freezing (engine/conditional_ledger.py) and out-of-sample
measurement (engine/conditional_validation.py). A conditional result computed
on the sample that suggested it is not evidence, and this module never
returns a status that could be mistaken for one.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from engine import conditional_stats as stats
from engine import pit_features
from engine.conditional_stats import (
    DiscoveryAccounting, EXPLORATORY_N, HYPOTHESIS_MIN_N, THREE_WAY_MIN_N, WARN_N,
    camel_keys,
)
from engine.observations import ObservationSet

#: Hard ceilings on the search. Raising these buys search power at the direct
#: cost of scientific rigor -- the trade the research brief says to refuse.
MAX_PAIRWISE_PAIRS = 60
MAX_THREE_WAY = 10
MAX_CONDITIONS = 3
MAX_CANDIDATES = 12

#: The market-state axis every promising symbol-level feature is crossed
#: with. Fixed in advance rather than chosen after seeing results, so the
#: interaction search is a preregistered grid and not a fishing expedition.
REGIME_AXIS: tuple[str, ...] = (
    "mkt_regime", "mkt_above_sma200", "mkt_vol_percentile_252", "mkt_ret_60d",
)

#: Smallest absolute improvement over the unconditional baseline that counts
#: as economically meaningful, per outcome column. Below this a difference is
#: reported but never promoted, however small its q-value: statistical
#: significance at large n says nothing about whether the effect is worth
#: trading.
MIN_MEANINGFUL_IMPROVEMENT: dict[str, float] = {
    "realized_r": 0.05,          # 0.05R per trade
    "contribution_pct": 0.10,    # 0.10pp of portfolio return per holding
}


def minimum_meaningful(outcome_column: str) -> float:
    return MIN_MEANINGFUL_IMPROVEMENT.get(outcome_column, 0.05)


@dataclass
class Bucket:
    label: str
    n: int
    mean: float
    median: float
    win_rate: float
    profit_factor: float | None
    ci_low: float
    ci_high: float
    low_edge: float | None = None
    high_edge: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return camel_keys(asdict(self))


@dataclass
class UnivariateResult:
    feature: str
    label: str
    group: str
    kind: str
    pit_safe: bool
    discovery_eligible: bool
    buckets: list[Bucket]
    n_used: int
    n_missing: int
    baseline_mean: float
    best_label: str
    best_mean: float
    worst_label: str
    worst_mean: float
    spread: float
    effect_size: float
    permutation_p: float | None
    welch_p: float | None
    monotonic: dict[str, Any]
    tier: str
    warnings: list[str] = field(default_factory=list)
    q_value: float | None = None
    fdr_significant: bool = False
    #: Dependence-aware fields (methodology v2) -- see engine/
    #: conditional_dependence.py. `effective_n` is the number of distinct
    #: clusters (rebalance dates, or overlap-based trade blocks) contributing
    #: to the best bucket, vs. `n_used`'s raw row count across ALL buckets.
    #: Both stay at their v1 defaults (effective_n = n_used, method = "iid
    #: (row-level)") when no ClusterStructure was supplied -- see
    #: `analyze_univariate`'s `cluster_structure` parameter.
    effective_n: int | None = None
    inference_method: str = "iid (row-level)"
    methodology_version: str = "conditional_edge_stats_v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["buckets"] = [b.to_dict() for b in self.buckets]
        return camel_keys(payload)


@dataclass
class InteractionCell:
    labels: tuple[str, ...]
    n: int
    mean: float
    win_rate: float
    ci_low: float
    ci_high: float
    tier: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["labels"] = list(self.labels)
        return camel_keys(payload)


@dataclass
class InteractionResult:
    features: tuple[str, ...]
    labels: tuple[str, ...]
    groups: tuple[str, ...]
    cells: list[InteractionCell]
    axis_labels: list[list[str]]
    baseline_mean: float
    best: InteractionCell | None
    worst: InteractionCell | None
    permutation_p: float | None
    effect_size: float
    n_used: int
    cells_suppressed: int
    order: int
    warnings: list[str] = field(default_factory=list)
    q_value: float | None = None
    fdr_significant: bool = False
    effective_n: int | None = None
    inference_method: str = "iid (row-level)"
    methodology_version: str = "conditional_edge_stats_v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["features"] = list(self.features)
        payload["labels"] = list(self.labels)
        payload["groups"] = list(self.groups)
        payload["cells"] = [c.to_dict() for c in self.cells]
        payload["best"] = self.best.to_dict() if self.best else None
        payload["worst"] = self.worst.to_dict() if self.worst else None
        return camel_keys(payload)


@dataclass
class Condition:
    """One clause of a candidate rule.

    `raw_edge` is the quantile boundary the discovery sample produced;
    `value` is the interpretable number the rule actually freezes at. Keeping
    both is what makes the anti-threshold-mining claim checkable rather than
    asserted -- a reader can see exactly how far the frozen number moved from
    the fitted one.
    """

    feature: str
    op: str
    value: Any
    raw_edge: float | None = None
    source: str = "quantile"

    def describe(self) -> str:
        definition = pit_features.FEATURES.get(self.feature)
        label = definition.label if definition else self.feature
        if self.op == "in":
            return f"{label} is one of {', '.join(str(v) for v in self.value)}"
        if self.op == "==":
            return f"{label} is {self.value}"
        symbol = {"<": "below", "<=": "at or below", ">": "above", ">=": "at or above"}[self.op]
        return f"{label} {symbol} {self.value:g}"

    def mask(self, frame: pd.DataFrame) -> pd.Series:
        if self.feature not in frame.columns:
            return pd.Series(False, index=frame.index)
        column = frame[self.feature]
        if self.op == "in":
            return column.isin(list(self.value))
        if self.op == "==":
            return column == self.value
        numeric = pd.to_numeric(column, errors="coerce")
        if self.op == "<":
            return numeric < self.value
        if self.op == "<=":
            return numeric <= self.value
        if self.op == ">":
            return numeric > self.value
        if self.op == ">=":
            return numeric >= self.value
        raise ValueError(f"Unsupported condition operator {self.op!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature, "op": self.op, "value": self.value,
            "rawEdge": self.raw_edge, "source": self.source,
            "description": self.describe(),
        }


@dataclass
class CandidateHypothesis:
    """A discovered conditional rule -- IN SAMPLE, and never more than that."""

    hypothesis_id: str
    strategy_name: str
    engine: str
    outcome_column: str
    conditions: list[Condition]
    origin: str
    n_conditioned: int
    n_total: int
    retention_pct: float
    baseline_mean: float
    conditional_mean: float
    improvement: float
    ci_low: float
    ci_high: float
    win_rate: float
    baseline_win_rate: float
    profit_factor: float | None
    effect_size: float
    permutation_p: float | None
    q_value: float | None
    fdr_significant: bool
    tier: str
    groups: list[str]
    discovery_start: str
    discovery_end: str
    warnings: list[str] = field(default_factory=list)
    status: str = "Candidate hypothesis - not validated"
    in_sample: bool = True
    raw_observations: int | None = None
    effective_n: int | None = None
    inference_method: str = "iid (row-level)"
    methodology_version: str = "conditional_edge_stats_v1"

    def describe(self) -> str:
        clauses = "\n".join(f"  - {c.describe()}" for c in self.conditions)
        return (
            f"{self.strategy_name} signals performed differently when:\n{clauses}\n"
            f"  Retained {self.n_conditioned} of {self.n_total} signals "
            f"({self.retention_pct:.1f}%); discovery-sample mean "
            f"{self.conditional_mean:+.4f} vs unconditional {self.baseline_mean:+.4f}."
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["conditions"] = [c.to_dict() for c in self.conditions]
        payload["description"] = self.describe()
        return camel_keys(payload)


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


def choose_bucket_count(n: int) -> int:
    """How finely a sample of `n` observations may be cut.

    Deciles need every bucket to clear the exploratory floor; quintiles need
    every bucket to clear the hypothesis floor. Below that the cut degrades
    to quartiles, terciles and finally a median split rather than producing
    ten buckets of four trades each, which is what makes a spectacular
    conditional table out of pure noise.
    """
    for count, floor in ((10, EXPLORATORY_N), (5, HYPOTHESIS_MIN_N),
                         (4, HYPOTHESIS_MIN_N), (3, HYPOTHESIS_MIN_N),
                         (2, HYPOTHESIS_MIN_N)):
        if n // count >= floor:
            return count
    return 0


def _bucket_series(
    frame: pd.DataFrame, feature: str, *, max_buckets: int | None = None
) -> tuple[pd.Series, list[dict[str, Any]], str]:
    """Bucket labels for `feature`, continuous or categorical."""
    definition = pit_features.FEATURES[feature]
    column = frame[feature]
    if definition.kind in ("categorical", "boolean"):
        labels = column.astype(object).where(column.notna(), None)
        if definition.kind == "boolean":
            labels = labels.map(lambda v: None if v is None else ("True" if bool(v) else "False"))
        else:
            labels = labels.map(lambda v: None if v is None else str(v))
        return labels, [], "category"
    numeric = pd.to_numeric(column, errors="coerce")
    usable = int(numeric.notna().sum())
    count = choose_bucket_count(usable)
    if max_buckets is not None:
        count = min(count, max_buckets)
    if count < 2:
        return pd.Series(index=frame.index, dtype=object), [], "insufficient"
    labels, edges = stats.quantile_buckets(numeric, buckets=count)
    return labels, edges, f"{count}-quantile"


# ---------------------------------------------------------------------------
# Univariate
# ---------------------------------------------------------------------------


def analyze_univariate(
    observations: ObservationSet,
    features: Sequence[str] | None = None,
    *,
    seed: int = stats.DEFAULT_SEED,
    permutations: int = 1_000,
    accounting: DiscoveryAccounting | None = None,
    cluster_structure: "cd.ClusterStructure | None" = None,
) -> list[UnivariateResult]:
    """Expectancy by bucket for each feature, independently.

    Missing values are DROPPED from that feature's analysis only -- never
    imputed to a middle value, which would fabricate a measured neutral
    reading, and never used to drop the observation from every other
    feature's table too.

    **Dependence-aware statistics (methodology v2).** When `cluster_structure`
    is supplied (as `run_discovery` now always does by default -- see its
    `dependence_aware` parameter), the permutation p-value and each bucket's
    confidence interval are computed by resampling/permuting whole CLUSTERS
    (rebalance dates for the cross-sectional engine, overlap-based trade
    blocks for the per-symbol engine) rather than individual rows -- see
    `engine/conditional_dependence.py`'s module docstring for why row-level
    inference materially overstates evidence on both this app's engine
    shapes. `cluster_structure=None` (every existing call site before the
    2026-08-22 dependence audit) reproduces the original row-level behavior
    exactly, so nothing calling this without the new parameter is affected.
    """
    frame = observations.frame
    outcome = observations.outcome_column
    keys = list(features) if features is not None else list(observations.feature_keys)
    results: list[UnivariateResult] = []
    if frame.empty:
        return results
    baseline = frame[outcome].astype(float)
    baseline_mean = float(baseline.mean())

    for feature in keys:
        definition = pit_features.FEATURES.get(feature)
        if definition is None or feature not in frame.columns:
            continue
        labels, edges, method = _bucket_series(frame, feature)
        present = labels.notna()
        n_used = int(present.sum())
        n_missing = int(len(frame) - n_used)
        if n_used < HYPOTHESIS_MIN_N or method == "insufficient":
            if accounting is not None:
                accounting.suppressed_for_sample += 1
            continue
        edge_by_label = {e["label"]: e for e in edges}
        buckets: list[Bucket] = []
        ordered = _ordered_labels(definition, labels)
        for label in ordered:
            values = frame.loc[present & (labels == label), outcome].astype(float)
            if len(values) == 0:
                continue
            summary = stats.summarize(values, seed=seed)
            edge = edge_by_label.get(label, {})
            buckets.append(Bucket(
                label=label, n=summary.n, mean=summary.mean, median=summary.median,
                win_rate=summary.win_rate, profit_factor=summary.profit_factor,
                ci_low=summary.ci_low, ci_high=summary.ci_high,
                low_edge=edge.get("low"), high_edge=edge.get("high"),
            ))
        if len(buckets) < 2:
            if accounting is not None:
                accounting.suppressed_for_sample += 1
            continue

        best = max(buckets, key=lambda b: b.mean)
        worst = min(buckets, key=lambda b: b.mean)
        best_mask = present & (labels == best.label)
        best_values = frame.loc[best_mask, outcome].astype(float)
        rest_values = frame.loc[present & (labels != best.label), outcome].astype(float)
        welch = stats.welch_t_test(best_values, rest_values)
        tier = stats.sample_tier(int(min(b.n for b in buckets)))
        warnings: list[str] = []
        warning = stats.sample_warning(int(min(b.n for b in buckets)))
        if warning:
            warnings.append(f"Smallest bucket: {warning}")
        if not definition.pit_safe:
            warnings.append(f"NOT point-in-time: {definition.pit_note}")
        if not definition.discovery_eligible:
            warnings.append(
                "Exploratory feature -- may not carry a conditional-edge verdict."
            )

        effective_n: int | None = None
        inference_method = "iid (row-level)"
        methodology_version = "conditional_edge_stats_v1"
        if cluster_structure is not None:
            from engine import conditional_dependence as cd

            dependence = cd.dependence_aware_evaluate(
                frame, outcome, best_mask, cluster_structure,
                permutations=permutations, seed=seed,
            )
            permutation_p = dependence["permutationP"]
            effective_n = dependence["effectiveObservations"]
            inference_method = dependence["clusterMethod"]
            methodology_version = dependence["methodologyVersion"]
            if dependence["ciLow"] is not None:
                best.ci_low, best.ci_high = dependence["ciLow"], dependence["ciHigh"]
            if effective_n is not None and effective_n < HYPOTHESIS_MIN_N <= best.n:
                warnings.append(
                    f"Best bucket has {best.n} raw rows but only {effective_n} independent "
                    f"clusters ({inference_method}) -- below the {HYPOTHESIS_MIN_N}-observation "
                    "reliability floor once dependence is accounted for."
                )
        else:
            permutation_p = stats.permutation_test(
                best_values, rest_values, permutations=permutations, seed=seed,
            ).get("pValue")

        results.append(UnivariateResult(
            feature=feature, label=definition.label, group=definition.group,
            kind=definition.kind, pit_safe=definition.pit_safe,
            discovery_eligible=definition.discovery_eligible, buckets=buckets,
            n_used=n_used, n_missing=n_missing, baseline_mean=baseline_mean,
            best_label=best.label, best_mean=best.mean,
            worst_label=worst.label, worst_mean=worst.mean,
            spread=float(best.mean - worst.mean),
            effect_size=stats.hedges_g(
                best_values.to_numpy(dtype=float), rest_values.to_numpy(dtype=float)
            ),
            permutation_p=permutation_p, welch_p=welch.get("pValue"),
            monotonic=stats.monotonicity([b.mean for b in buckets]),
            tier=tier, warnings=warnings, effective_n=effective_n,
            inference_method=inference_method, methodology_version=methodology_version,
        ))
        if accounting is not None:
            accounting.univariate_tested += 1
    return results


def _ordered_labels(definition: pit_features.FeatureDefinition, labels: pd.Series) -> list[str]:
    present = [l for l in labels.dropna().unique()]
    if definition.categories:
        ordered = [c for c in definition.categories if c in present]
        ordered += sorted(l for l in present if l not in definition.categories)
        return ordered
    if definition.kind == "boolean":
        return [l for l in ("False", "True") if l in present]
    return sorted(present, key=lambda l: int(l[1:]) if l.startswith("Q") and l[1:].isdigit() else 0)


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------


def _interaction_bucket_count(kind: str, n: int, order: int) -> int:
    """Coarser cuts for higher-order interactions.

    Cells multiply: three features at five buckets each is 125 cells, which
    on any realistic trade sample means most cells hold single digits. Two-way
    interactions therefore use terciles and three-way uses halves, so the
    number of cells stays in double figures.
    """
    return 3 if order == 2 else 2


def analyze_interactions(
    observations: ObservationSet,
    combinations: Sequence[tuple[str, ...]],
    *,
    seed: int = stats.DEFAULT_SEED,
    permutations: int = 1_000,
    accounting: DiscoveryAccounting | None = None,
    cluster_structure: "cd.ClusterStructure | None" = None,
) -> list[InteractionResult]:
    """Conditional expectancy for each combination of feature buckets.

    See `analyze_univariate`'s docstring for the `cluster_structure`
    parameter: when supplied, the best cell's permutation p-value and CI are
    computed by cluster permutation/bootstrap rather than row-level, and
    `cluster_structure=None` (every call site before the dependence audit)
    is unaffected.
    """
    frame = observations.frame
    outcome = observations.outcome_column
    results: list[InteractionResult] = []
    if frame.empty:
        return results
    baseline_mean = float(frame[outcome].astype(float).mean())

    for features in combinations:
        order = len(features)
        definitions = [pit_features.FEATURES.get(f) for f in features]
        if any(d is None or f not in frame.columns for d, f in zip(definitions, features)):
            continue
        max_buckets = _interaction_bucket_count("", len(frame), order)
        label_columns: list[pd.Series] = []
        axis_labels: list[list[str]] = []
        for feature, definition in zip(features, definitions):
            labels, _edges, method = _bucket_series(frame, feature, max_buckets=max_buckets)
            if method == "insufficient":
                label_columns = []
                break
            label_columns.append(labels)
            axis_labels.append(_ordered_labels(definition, labels))
        if not label_columns:
            if accounting is not None:
                accounting.suppressed_for_sample += 1
            continue

        present = pd.concat(label_columns, axis=1).notna().all(axis=1)
        n_used = int(present.sum())
        minimum = THREE_WAY_MIN_N if order >= 3 else HYPOTHESIS_MIN_N
        cells: list[InteractionCell] = []
        suppressed = 0
        for combination in _label_grid(axis_labels):
            mask = present.copy()
            for column, label in zip(label_columns, combination):
                mask &= column == label
            values = frame.loc[mask, outcome].astype(float)
            if len(values) < minimum:
                suppressed += 1
                continue
            summary = stats.summarize(values, seed=seed)
            cells.append(InteractionCell(
                labels=tuple(combination), n=summary.n, mean=summary.mean,
                win_rate=summary.win_rate, ci_low=summary.ci_low,
                ci_high=summary.ci_high, tier=stats.sample_tier(summary.n),
            ))
        if len(cells) < 2:
            if accounting is not None:
                accounting.suppressed_for_sample += 1
            continue

        best = max(cells, key=lambda c: c.mean)
        worst = min(cells, key=lambda c: c.mean)
        best_mask = present.copy()
        for column, label in zip(label_columns, best.labels):
            best_mask &= column == label
        best_values = frame.loc[best_mask, outcome].astype(float)
        rest_values = frame.loc[present & ~best_mask, outcome].astype(float)
        warnings: list[str] = []
        if suppressed:
            warnings.append(
                f"{suppressed} cell(s) fell below the {minimum}-observation floor and are "
                "not shown -- a suppressed cell is unmeasured, not zero."
            )
        for definition in definitions:
            if not definition.pit_safe:
                warnings.append(f"{definition.label} is NOT point-in-time: {definition.pit_note}")
            if not definition.discovery_eligible:
                warnings.append(
                    f"{definition.label} is exploratory and cannot carry a verdict."
                )

        effective_n: int | None = None
        inference_method = "iid (row-level)"
        methodology_version = "conditional_edge_stats_v1"
        if cluster_structure is not None:
            from engine import conditional_dependence as cd

            dependence = cd.dependence_aware_evaluate(
                frame, outcome, best_mask, cluster_structure,
                permutations=permutations, seed=seed,
            )
            permutation_p = dependence["permutationP"]
            effective_n = dependence["effectiveObservations"]
            inference_method = dependence["clusterMethod"]
            methodology_version = dependence["methodologyVersion"]
            if dependence["ciLow"] is not None:
                best.ci_low, best.ci_high = dependence["ciLow"], dependence["ciHigh"]
            if effective_n is not None and effective_n < minimum <= best.n:
                warnings.append(
                    f"Best cell has {best.n} raw rows but only {effective_n} independent "
                    f"clusters ({inference_method}) -- below the {minimum}-observation floor "
                    "once dependence is accounted for."
                )
        else:
            permutation_p = stats.permutation_test(
                best_values, rest_values, permutations=permutations, seed=seed,
            ).get("pValue")

        results.append(InteractionResult(
            features=tuple(features),
            labels=tuple(d.label for d in definitions),
            groups=tuple(d.group for d in definitions),
            cells=cells, axis_labels=axis_labels, baseline_mean=baseline_mean,
            best=best, worst=worst, permutation_p=permutation_p,
            effective_n=effective_n, inference_method=inference_method,
            methodology_version=methodology_version,
            effect_size=stats.hedges_g(
                best_values.to_numpy(dtype=float), rest_values.to_numpy(dtype=float)
            ),
            n_used=n_used, cells_suppressed=suppressed, order=order, warnings=warnings,
        ))
        if accounting is not None:
            if order >= 3:
                accounting.three_way_tested += 1
            else:
                accounting.pairwise_tested += 1
    return results


def _label_grid(axis_labels: Sequence[Sequence[str]]) -> Iterable[tuple[str, ...]]:
    from itertools import product

    return product(*axis_labels)


def plan_pairwise(
    univariate: Sequence[UnivariateResult], *, limit: int = MAX_PAIRWISE_PAIRS
) -> list[tuple[str, ...]]:
    """Which pairs to test, decided from univariate evidence only.

    The grid is: the strongest symbol-level features crossed with the fixed
    `REGIME_AXIS`, plus those features crossed with each other. Features from
    the same conceptual group are never paired -- a "RSI(2) x 3-day return"
    interaction is one signal split in two, and testing it burns a hypothesis
    to learn nothing.
    """
    ranked = sorted(
        (u for u in univariate if u.discovery_eligible and u.tier != "insufficient"),
        key=lambda u: -abs(u.effect_size) if math.isfinite(u.effect_size) else 0.0,
    )
    leaders = [u.feature for u in ranked if u.feature not in REGIME_AXIS][:8]
    group_of = {u.feature: u.group for u in univariate}
    pairs: list[tuple[str, ...]] = []
    seen: set[frozenset[str]] = set()

    def add(a: str, b: str) -> None:
        if a == b or frozenset((a, b)) in seen:
            return
        if group_of.get(a) and group_of.get(a) == group_of.get(b):
            return
        seen.add(frozenset((a, b)))
        pairs.append((a, b))

    for feature in leaders:
        for axis in REGIME_AXIS:
            add(feature, axis)
    for i, a in enumerate(leaders):
        for b in leaders[i + 1:]:
            add(a, b)
    return pairs[:limit]


def plan_three_way(
    interactions: Sequence[InteractionResult],
    observations: ObservationSet,
    *,
    limit: int = MAX_THREE_WAY,
) -> list[tuple[str, ...]]:
    """Three-way combinations, only from pairs that already showed something.

    Gated twice: the pair must have survived its own minimum-cell rule, and
    the whole observation set must be large enough that three-way cells can
    plausibly clear `THREE_WAY_MIN_N`. Below `THREE_WAY_MIN_N * 8` total
    observations (eight cells at two buckets each) no three-way test is
    generated at all, rather than generating them and suppressing every cell.
    """
    if len(observations) < THREE_WAY_MIN_N * 8:
        return []
    ranked = sorted(
        (i for i in interactions if i.order == 2 and i.permutation_p is not None),
        key=lambda i: i.permutation_p,
    )
    groups_used: set[frozenset[str]] = set()
    out: list[tuple[str, ...]] = []
    for interaction in ranked:
        for axis in REGIME_AXIS:
            if axis in interaction.features:
                continue
            combination = (*interaction.features, axis)
            group_set = frozenset(
                pit_features.FEATURES[f].group for f in combination
            )
            if len(group_set) < 3 or group_set in groups_used:
                continue
            groups_used.add(group_set)
            out.append(combination)
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------------------
# Threshold snapping and candidate generation
# ---------------------------------------------------------------------------

#: Features whose natural reading is a round number on a known scale. Snapping
#: to these instead of to the fitted quantile edge is the anti-threshold-mining
#: rule made concrete.
_SNAP_GRID: dict[str, float] = {
    "rsi2": 5.0, "rsi5": 5.0, "rsi14": 5.0,
    "ibs": 0.1,
    "vol_percentile_252": 10.0, "mkt_vol_percentile_252": 10.0,
    "volume_percentile_252": 10.0, "dollar_volume_percentile_252": 10.0,
    "gap_percentile_252": 10.0,
    "xs_momentum_percentile_120d": 10.0, "xs_ret_percentile_20d": 10.0,
    "xs_vol_percentile": 10.0,
    "breadth_above_sma20": 10.0, "breadth_above_sma50": 10.0,
    "breadth_above_sma200": 10.0, "breadth_pos_ret_20d": 10.0,
    "breadth_pos_ret_60d": 10.0,
    "rel_volume_20": 0.25,
}

#: Features where zero is the economically meaningful line (positive vs
#: negative momentum, above vs below a moving average). A quantile edge within
#: `_ZERO_BAND` of zero snaps to exactly zero rather than to 0.37.
_ZERO_BAND = 3.0


def snap_threshold(feature: str, raw: float) -> float:
    """Round a fitted quantile edge to an interpretable, defensible number."""
    if not math.isfinite(raw):
        return raw
    definition = pit_features.FEATURES.get(feature)
    step = _SNAP_GRID.get(feature)
    if step:
        return float(round(raw / step) * step)
    if definition is not None and definition.group in ("trend", "relative", "market_regime", "gap"):
        if abs(raw) <= _ZERO_BAND:
            return 0.0
    magnitude = abs(raw)
    if magnitude == 0:
        return 0.0
    # One significant figure below 1, otherwise round to the nearest
    # "half-step" of the leading digit -- 12.4 -> 12.5, 137 -> 140.
    exponent = math.floor(math.log10(magnitude))
    step = 10 ** exponent / 2.0
    return float(round(raw / step) * step)


def _tail_condition(result: UnivariateResult) -> Condition | None:
    """Turn a univariate table into ONE interpretable clause.

    Only the extreme buckets are eligible, and only when the table's own
    ordering supports the direction: a middle bucket looking good is exactly
    the shape noise takes, and turning it into a two-sided band would be
    threshold mining with extra steps.
    """
    definition = pit_features.FEATURES[result.feature]
    if definition.kind in ("categorical", "boolean"):
        best = max(result.buckets, key=lambda b: b.mean)
        if best.n < HYPOTHESIS_MIN_N:
            return None
        value = True if best.label == "True" else False if best.label == "False" else best.label
        return Condition(feature=result.feature, op="==", value=value, source="category")
    ordered = result.buckets
    if len(ordered) < 2:
        return None
    best = max(ordered, key=lambda b: b.mean)
    if best.label == ordered[0].label:
        edge = best.high_edge
        if edge is None:
            return None
        return Condition(
            feature=result.feature, op="<", value=snap_threshold(result.feature, edge),
            raw_edge=float(edge), source="lowest-quantile tail",
        )
    if best.label == ordered[-1].label:
        edge = best.low_edge
        if edge is None:
            return None
        return Condition(
            feature=result.feature, op=">", value=snap_threshold(result.feature, edge),
            raw_edge=float(edge), source="highest-quantile tail",
        )
    return None


def _hypothesis_id(strategy: str, conditions: Sequence[Condition], outcome: str) -> str:
    payload = "|".join(
        f"{c.feature}{c.op}{c.value}" for c in sorted(conditions, key=lambda c: c.feature)
    )
    digest = hashlib.sha256(f"{strategy}::{outcome}::{payload}".encode()).hexdigest()
    return digest[:16]


def _evaluate_conditions(
    observations: ObservationSet, conditions: Sequence[Condition]
) -> tuple[pd.Series, pd.DataFrame]:
    frame = observations.frame
    mask = pd.Series(True, index=frame.index)
    for condition in conditions:
        mask &= condition.mask(frame).fillna(False)
    return mask, frame.loc[mask]


def build_candidate(
    observations: ObservationSet,
    conditions: Sequence[Condition],
    *,
    origin: str,
    seed: int = stats.DEFAULT_SEED,
    permutations: int = 1_000,
    cluster_structure: "cd.ClusterStructure | None" = None,
) -> CandidateHypothesis | None:
    """Measure one conditional rule on the discovery sample.

    Returns None when the rule keeps too few observations to say anything --
    the check happens here rather than at display time, so an underpowered
    rule never becomes a `CandidateHypothesis` object that something
    downstream could promote.

    See `analyze_univariate`'s docstring for `cluster_structure`. When
    supplied, `n` is still the raw retained row count (used for retention_pct
    and the existing HYPOTHESIS_MIN_N floor, unchanged), but the permutation
    p-value, CI and `effective_n` become cluster-aware.
    """
    frame = observations.frame
    outcome = observations.outcome_column
    if frame.empty:
        return None
    mask, conditioned = _evaluate_conditions(observations, conditions)
    n = int(len(conditioned))
    if n < HYPOTHESIS_MIN_N:
        return None
    values = conditioned[outcome].astype(float)
    rest = frame.loc[~mask, outcome].astype(float)
    summary = stats.summarize(values, seed=seed)
    baseline = frame[outcome].astype(float)

    effective_n: int | None = None
    inference_method = "iid (row-level)"
    methodology_version = "conditional_edge_stats_v1"
    ci_low, ci_high = summary.ci_low, summary.ci_high
    if cluster_structure is not None:
        from engine import conditional_dependence as cd

        dependence = cd.dependence_aware_evaluate(
            frame, outcome, mask, cluster_structure, permutations=permutations, seed=seed,
        )
        permutation = {"pValue": dependence["permutationP"]}
        effective_n = dependence["effectiveObservations"]
        inference_method = dependence["clusterMethod"]
        methodology_version = dependence["methodologyVersion"]
        if dependence["ciLow"] is not None:
            ci_low, ci_high = dependence["ciLow"], dependence["ciHigh"]
    else:
        permutation = (
            stats.permutation_test(values, rest, permutations=permutations, seed=seed)
            if len(rest) >= 2 else {"pValue": None}
        )
    warnings: list[str] = []
    if effective_n is not None and effective_n < HYPOTHESIS_MIN_N <= n:
        warnings.append(
            f"{n} raw retained rows come from only {effective_n} independent clusters "
            f"({inference_method}) -- below the {HYPOTHESIS_MIN_N}-observation reliability "
            "floor once dependence is accounted for."
        )
    warning = stats.sample_warning(n)
    if warning:
        warnings.append(warning)
    groups = []
    for condition in conditions:
        definition = pit_features.FEATURES[condition.feature]
        groups.append(definition.group)
        if not definition.pit_safe:
            warnings.append(f"{definition.label} is NOT point-in-time: {definition.pit_note}")
        if not definition.discovery_eligible:
            warnings.append(
                f"{definition.label} is an exploratory feature; this rule cannot be promoted "
                "to a validated conditional edge on its evidence."
            )
    improvement = float(summary.mean - baseline.mean())
    if abs(improvement) < minimum_meaningful(outcome):
        warnings.append(
            f"Improvement of {improvement:+.4f} is below the {minimum_meaningful(outcome):.2f} "
            "economic-materiality floor for this outcome; statistically interesting is not "
            "the same as worth trading."
        )
    return CandidateHypothesis(
        hypothesis_id=_hypothesis_id(observations.strategy_name, conditions, outcome),
        strategy_name=observations.strategy_name, engine=observations.engine,
        outcome_column=outcome, conditions=list(conditions), origin=origin,
        n_conditioned=n, n_total=int(len(frame)),
        retention_pct=float(n / len(frame) * 100.0),
        baseline_mean=float(baseline.mean()), conditional_mean=summary.mean,
        improvement=improvement, ci_low=ci_low, ci_high=ci_high,
        win_rate=summary.win_rate, baseline_win_rate=float((baseline > 0).mean()),
        profit_factor=summary.profit_factor,
        effect_size=stats.hedges_g(
            values.to_numpy(dtype=float), rest.to_numpy(dtype=float)
        ) if len(rest) >= 2 else float("nan"),
        permutation_p=permutation.get("pValue"), q_value=None, fdr_significant=False,
        tier=stats.sample_tier(n), groups=groups,
        discovery_start=str(frame["decision_time"].min()),
        discovery_end=str(frame["decision_time"].max()),
        warnings=warnings, raw_observations=n, effective_n=effective_n,
        inference_method=inference_method, methodology_version=methodology_version,
    )


def generate_candidates(
    observations: ObservationSet,
    univariate: Sequence[UnivariateResult],
    interactions: Sequence[InteractionResult],
    *,
    seed: int = stats.DEFAULT_SEED,
    permutations: int = 1_000,
    limit: int = MAX_CANDIDATES,
    cluster_structure: "cd.ClusterStructure | None" = None,
) -> list[CandidateHypothesis]:
    """Assemble a small number of interpretable rules from the analysis.

    Composition rule: at most `MAX_CONDITIONS` clauses, each from a different
    feature GROUP, and every clause must have come from a feature that
    cleared univariate or interaction evidence on its own. There is no search
    over combinations of clauses -- the clauses are the ones the analysis
    already surfaced, assembled greedily in effect-size order.

    See `analyze_univariate`'s docstring for `cluster_structure`.
    """
    eligible = [
        u for u in univariate
        if u.discovery_eligible and u.pit_safe and u.tier != "insufficient"
        and u.permutation_p is not None
    ]
    ranked = sorted(
        eligible,
        key=lambda u: (u.q_value if u.q_value is not None else 1.0,
                       -abs(u.effect_size) if math.isfinite(u.effect_size) else 0.0),
    )
    clause_by_feature: dict[str, Condition] = {}
    for result in ranked:
        condition = _tail_condition(result)
        if condition is not None:
            clause_by_feature[result.feature] = condition

    candidates: list[CandidateHypothesis] = []
    seen: set[str] = set()

    def emit(conditions: Sequence[Condition], origin: str) -> None:
        if not conditions:
            return
        candidate = build_candidate(
            observations, conditions, origin=origin, seed=seed, permutations=permutations,
            cluster_structure=cluster_structure,
        )
        if candidate is None or candidate.hypothesis_id in seen:
            return
        seen.add(candidate.hypothesis_id)
        candidates.append(candidate)

    # 1. Single-clause rules from the strongest univariate features.
    for result in ranked[:6]:
        condition = clause_by_feature.get(result.feature)
        if condition:
            emit([condition], f"univariate: {result.feature}")

    # 2. Multi-clause rules, greedily adding the next strongest clause from a
    #    group not already represented.
    chosen: list[Condition] = []
    used_groups: set[str] = set()
    for result in ranked:
        condition = clause_by_feature.get(result.feature)
        if condition is None or result.group in used_groups:
            continue
        chosen.append(condition)
        used_groups.add(result.group)
        if len(chosen) >= 2:
            emit(list(chosen), f"greedy {len(chosen)}-condition composite")
        if len(chosen) >= MAX_CONDITIONS:
            break

    # 3. Rules read directly off the best interaction cells.
    for interaction in sorted(
        (i for i in interactions if i.best is not None and i.permutation_p is not None
         and all(pit_features.FEATURES[f].discovery_eligible and pit_features.FEATURES[f].pit_safe
                 for f in i.features)),
        key=lambda i: i.permutation_p,
    )[:6]:
        conditions = _conditions_from_cell(observations, interaction)
        if conditions:
            emit(conditions, f"interaction: {' x '.join(interaction.features)}")

    return candidates[:limit]


def _conditions_from_cell(
    observations: ObservationSet, interaction: InteractionResult
) -> list[Condition] | None:
    """Reconstruct interpretable clauses from an interaction's best cell.

    A cell is identified by bucket LABELS, which are meaningless outside the
    sample that produced them; a frozen rule needs real thresholds. This
    re-derives the edge of each axis's chosen bucket and snaps it, exactly as
    the univariate path does -- so an interaction-derived rule is no less
    interpretable and no more finely fitted than a univariate one.
    """
    if interaction.best is None:
        return None
    frame = observations.frame
    max_buckets = _interaction_bucket_count("", len(frame), interaction.order)
    conditions: list[Condition] = []
    for feature, label in zip(interaction.features, interaction.best.labels):
        definition = pit_features.FEATURES[feature]
        if definition.kind in ("categorical", "boolean"):
            value = True if label == "True" else False if label == "False" else label
            conditions.append(Condition(feature=feature, op="==", value=value, source="category"))
            continue
        _labels, edges, method = _bucket_series(frame, feature, max_buckets=max_buckets)
        if method == "insufficient" or not edges:
            return None
        edge = next((e for e in edges if e["label"] == label), None)
        if edge is None:
            return None
        if label == edges[0]["label"]:
            conditions.append(Condition(
                feature=feature, op="<", value=snap_threshold(feature, edge["high"]),
                raw_edge=float(edge["high"]), source="interaction cell (low tail)",
            ))
        elif label == edges[-1]["label"]:
            conditions.append(Condition(
                feature=feature, op=">", value=snap_threshold(feature, edge["low"]),
                raw_edge=float(edge["low"]), source="interaction cell (high tail)",
            ))
        else:
            # A middle bucket needs two clauses to express, which would make
            # the rule a fitted band. Refuse rather than emit one.
            return None
    return conditions


# ---------------------------------------------------------------------------
# Session orchestration
# ---------------------------------------------------------------------------


@dataclass
class DiscoverySession:
    """One complete discovery pass over one strategy's discovery sample."""

    session_id: str
    strategy_name: str
    engine: str
    outcome_column: str
    outcome_label: str
    observations: int
    discovery_start: str
    discovery_end: str
    univariate: list[UnivariateResult]
    interactions: list[InteractionResult]
    candidates: list[CandidateHypothesis]
    accounting: DiscoveryAccounting
    fdr: dict[str, Any]
    redundancy: dict[str, Any]
    coverage: list[dict[str, Any]]
    baseline: dict[str, Any]
    concurrency: dict[str, Any]
    warnings: list[str]
    seed: int
    created_at: str
    #: Dependence-aware fields (methodology v2). `None` only for a session
    #: computed with `dependence_aware=False` (the null-experiment calibration
    #: path replaying the original v1 procedure -- see
    #: engine/conditional_null_experiment.py). Every ordinary discovery run
    #: since the 2026-08-22 audit sets this.
    cluster_structure: dict[str, Any] | None = None
    methodology_version: str = "conditional_edge_stats_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id, "strategyName": self.strategy_name,
            "engine": self.engine, "outcomeColumn": self.outcome_column,
            "outcomeLabel": self.outcome_label, "observations": self.observations,
            "discoveryStart": self.discovery_start, "discoveryEnd": self.discovery_end,
            "univariate": [u.to_dict() for u in self.univariate],
            "interactions": [i.to_dict() for i in self.interactions],
            "candidates": [c.to_dict() for c in self.candidates],
            "accounting": self.accounting.to_dict(), "fdr": self.fdr,
            "redundancy": self.redundancy, "coverage": self.coverage,
            "baseline": self.baseline, "concurrency": self.concurrency,
            "warnings": self.warnings, "seed": self.seed, "createdAt": self.created_at,
            "clusterStructure": self.cluster_structure,
            "methodologyVersion": self.methodology_version,
        }


def run_discovery(
    observations: ObservationSet,
    *,
    seed: int = stats.DEFAULT_SEED,
    permutations: int = 1_000,
    alpha: float = 0.10,
    include_three_way: bool = True,
    include_exploratory_features: bool = True,
    dependence_aware: bool = True,
) -> DiscoverySession:
    """The full discovery pass: univariate -> pairwise -> three-way -> FDR.

    The FDR correction is applied ONCE, across every test the session
    performed -- univariate, pairwise and three-way together -- because that
    is the family that was actually searched. Correcting each stage
    separately would let a session run three families of a hundred tests and
    report three separately-corrected sets, which is the multiple-comparison
    problem with extra bookkeeping.

    **`dependence_aware=True` (the default since the 2026-08-22 audit).**
    Computes a `ClusterStructure` for this observation set (rebalance dates
    for the cross-sectional engine, overlap-based trade blocks for the
    per-symbol engine -- see `engine/conditional_dependence.py`) and threads
    it through every permutation test and CI in this session, replacing
    row-level inference with cluster-level inference. This does NOT change
    WHAT is tested -- the same features, the same pairwise/three-way
    selection logic, the same candidates -- only HOW each test's p-value and
    CI are computed.

    `dependence_aware=False` reproduces the original (pre-audit) row-level
    behavior exactly. Its one legitimate use is
    `engine/conditional_null_experiment.py`'s calibration of the EXACT
    procedure that produced this app's first discovery results, before this
    fix existed -- everywhere else should leave the default alone.
    """
    from engine import observations as observations_module

    created = datetime.now(timezone.utc).isoformat()
    session_id = hashlib.sha256(
        f"{observations.strategy_name}|{observations.start}|{observations.end}|"
        f"{len(observations)}|{seed}|{created}".encode()
    ).hexdigest()[:16]
    accounting = DiscoveryAccounting(session_id=session_id)
    warnings = list(observations.warnings)

    cluster_structure = None
    if dependence_aware:
        from engine import conditional_dependence as cd

        cluster_structure = cd.assign_clusters(observations.frame, observations.engine)
        if cluster_structure.method != "iid" and cluster_structure.effective_n < cluster_structure.n_raw:
            warnings.append(
                f"{cluster_structure.n_raw} raw observations resolve to "
                f"{cluster_structure.effective_n} independent clusters "
                f"({cluster_structure.method}) -- every p-value, q-value and CI in this "
                "session is computed against the CLUSTER count, not the raw row count."
            )

    features = [
        f for f in observations.feature_keys
        if include_exploratory_features or pit_features.FEATURES[f].discovery_eligible
    ]
    univariate = analyze_univariate(
        observations, features, seed=seed, permutations=permutations, accounting=accounting,
        cluster_structure=cluster_structure,
    )
    pairs = plan_pairwise(univariate)
    interactions = analyze_interactions(
        observations, pairs, seed=seed, permutations=permutations, accounting=accounting,
        cluster_structure=cluster_structure,
    )
    if include_three_way:
        three_way = plan_three_way(interactions, observations)
        if three_way:
            interactions += analyze_interactions(
                observations, three_way, seed=seed, permutations=permutations,
                accounting=accounting, cluster_structure=cluster_structure,
            )
        else:
            accounting.notes.append(
                f"No three-way interactions generated: {len(observations)} observations is "
                f"below the {THREE_WAY_MIN_N * 8} needed for eight cells at "
                f"{THREE_WAY_MIN_N} observations each."
            )

    p_values = [u.permutation_p for u in univariate] + [i.permutation_p for i in interactions]
    fdr = stats.benjamini_hochberg(p_values, alpha=alpha)
    for index, result in enumerate(univariate):
        result.q_value = fdr["qValues"][index]
        result.fdr_significant = fdr["significant"][index]
    offset = len(univariate)
    for index, interaction in enumerate(interactions):
        interaction.q_value = fdr["qValues"][offset + index]
        interaction.fdr_significant = fdr["significant"][offset + index]

    candidates = generate_candidates(
        observations, univariate, interactions, seed=seed, permutations=permutations,
        cluster_structure=cluster_structure,
    )
    # Candidates are additional hypotheses in the same family: each is a
    # distinct rule that was measured. Counting them keeps the reported search
    # size honest, and their q-values are computed against the full family.
    candidate_ps = [c.permutation_p for c in candidates]
    combined = stats.benjamini_hochberg(p_values + candidate_ps, alpha=alpha)
    for index, candidate in enumerate(candidates):
        position = len(p_values) + index
        candidate.q_value = combined["qValues"][position]
        candidate.fdr_significant = combined["significant"][position]
    accounting.notes.append(
        f"{len(candidates)} candidate rule(s) were themselves measured and are included in "
        "the family the FDR correction covers."
    )

    baseline_summary = stats.summarize(
        observations.frame[observations.outcome_column].astype(float), seed=seed
    ) if len(observations) else stats.summarize([])

    return DiscoverySession(
        session_id=session_id, strategy_name=observations.strategy_name,
        engine=observations.engine, outcome_column=observations.outcome_column,
        outcome_label=observations.outcome_label, observations=len(observations),
        discovery_start=str(observations.frame["decision_time"].min()) if len(observations) else "",
        discovery_end=str(observations.frame["decision_time"].max()) if len(observations) else "",
        univariate=univariate, interactions=interactions, candidates=candidates,
        accounting=accounting, fdr=combined,
        redundancy=stats.redundancy_report(observations.frame, observations.feature_keys),
        coverage=observations.coverage_report(),
        baseline=baseline_summary.to_dict(),
        concurrency=observations_module.concurrency_profile(observations.frame),
        warnings=warnings, seed=seed, created_at=created,
        cluster_structure=cluster_structure.to_dict() if cluster_structure is not None else None,
        methodology_version=(
            stats.METHODOLOGY_V2_DEPENDENCE_AWARE if dependence_aware
            else stats.METHODOLOGY_V1_ROW_LEVEL
        ),
    )
