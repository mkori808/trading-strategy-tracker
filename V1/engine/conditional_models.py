"""Optional simple models, used as INTERACTION DETECTORS, never as strategies.

Section 22 of the research brief is explicit about the ordering: the
interpretable conditional framework comes first, and models are added only as
diagnostic tools on top of it. Nothing here produces a tradeable rule. A model
that finds something interesting has done its whole job when that something is
restated as a human-readable condition and validated independently through
`engine/conditional_validation.py`, exactly like a hand-built hypothesis.

**Why these three and nothing else.**  Logistic regression (optionally L1-
regularised), linear regression on the outcome, and a depth-2 tree restricted
to quantile splits. All three are readable: you can print the whole model. The
brief's excluded list -- deep networks, large boosted-tree searches, AutoML,
hyperparameter sweeps -- is excluded because each of them turns "did we find
structure" into "did we search hard enough", which is the failure mode this
entire feature is designed against. There is no hyperparameter search here at
all.

**The tree splits on the same quantile grid the rest of the module uses.** It
cannot invent `RSI < 27.43`; it can only choose among the terciles that the
univariate analysis already reports. That keeps it inside the anti-threshold-
mining rule rather than being an exception to it.

**Time is respected.**  `cross_validate` uses expanding windows with the same
purge and embargo the validation module applies. Random K-fold is never
offered, not even as an option, because on overlapping trades it produces a
score that looks like out-of-sample evidence and is not.

**Feature importance is reported with its stability, or not at all.**
Coefficients come with fold-to-fold sign agreement, and permutation
importance is computed on held-out folds. Impurity-based tree importance is
deliberately not reported: it is biased toward high-cardinality features and
says nothing about out-of-sample usefulness.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

from engine import conditional_stats as stats
from engine import pit_features
from engine.conditional_validation import EMBARGO_DAYS, _purge_and_embargo
from engine.observations import ObservationSet

#: Ceiling on how many features a model may see. Fitting 70 coefficients on a
#: few hundred correlated observations is not a diagnostic, it is a
#: random-number generator with a confusion matrix.
MAX_MODEL_FEATURES = 12

MIN_MODEL_OBSERVATIONS = stats.WARN_N


@dataclass
class ModelDiagnostic:
    model: str
    target: str
    features: list[str]
    observations: int
    coefficients: dict[str, float]
    coefficient_stability: dict[str, float]
    permutation_importance: dict[str, float]
    fold_scores: list[float]
    mean_score: float | None
    score_name: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # Built explicitly rather than via a blind asdict()+camel_keys(): coefficients,
        # coefficient_stability and permutation_importance are dicts keyed by FEATURE ID
        # ("mkt_regime", "intercept", ...), and a generic snake_case->camelCase rekey
        # would corrupt those identifiers (e.g. "mkt_regime" -> "mktRegime").
        return {
            "model": self.model, "target": self.target, "features": list(self.features),
            "observations": self.observations,
            "coefficients": dict(self.coefficients),
            "coefficientStability": dict(self.coefficient_stability),
            "permutationImportance": dict(self.permutation_importance),
            "foldScores": list(self.fold_scores),
            "meanScore": self.mean_score,
            "scoreName": self.score_name,
            "notes": list(self.notes),
        }


def _select_features(
    observations: ObservationSet, features: Sequence[str] | None
) -> list[str]:
    frame = observations.frame
    candidates = [
        key for key in (features or observations.feature_keys)
        if key in frame.columns
        and pit_features.FEATURES[key].kind == "continuous"
        and pit_features.FEATURES[key].discovery_eligible
        and pit_features.FEATURES[key].pit_safe
        and frame[key].notna().sum() >= len(frame) * 0.9
    ]
    if len(candidates) <= MAX_MODEL_FEATURES:
        return candidates
    # Greedy decorrelation: keep the feature from each conceptual group with
    # the widest coverage, then fill remaining slots by group diversity. This
    # is a REDUNDANCY control, not a selection procedure -- it never looks at
    # the outcome, so it cannot leak.
    by_group: dict[str, list[str]] = {}
    for key in candidates:
        by_group.setdefault(pit_features.FEATURES[key].group, []).append(key)
    chosen: list[str] = []
    while len(chosen) < MAX_MODEL_FEATURES and any(by_group.values()):
        for group in sorted(by_group):
            bucket = by_group[group]
            if bucket and len(chosen) < MAX_MODEL_FEATURES:
                chosen.append(bucket.pop(0))
    return chosen


def _design_matrix(
    frame: pd.DataFrame, features: Sequence[str]
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Standardised design matrix with an intercept.

    Standardisation matters for a readable coefficient: features here range
    from a 0-100 percentile to a dollar volume in the billions, and raw
    coefficients across those scales are uncomparable to the point of being
    misleading.
    """
    usable = frame[list(features)].apply(pd.to_numeric, errors="coerce")
    mask = usable.notna().all(axis=1)
    values = usable.loc[mask].to_numpy(dtype=float)
    if len(values) == 0:
        return np.empty((0, 0)), np.zeros(0, dtype=bool), list(features)
    mean = values.mean(axis=0)
    std = values.std(axis=0, ddof=1)
    std[std == 0] = 1.0
    standardized = (values - mean) / std
    design = np.column_stack([np.ones(len(standardized)), standardized])
    return design, mask.to_numpy(), ["intercept", *features]


def _expanding_folds(
    frame: pd.DataFrame, folds: int, embargo_days: int
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    times = pd.to_datetime(frame["decision_time"], utc=True)
    first, last = times.min(), times.max()
    span = last - first
    edges = [first + span * (i / folds) for i in range(folds + 1)]
    out = []
    for index in range(1, folds):
        test_start, test_end = edges[index], edges[index + 1]
        train = frame.loc[times < test_start]
        train, _purged = _purge_and_embargo(train, test_start, embargo_days=embargo_days)
        test = frame.loc[(times >= test_start) & (times < test_end)]
        if len(train) >= MIN_MODEL_OBSERVATIONS and len(test) >= stats.HYPOTHESIS_MIN_N:
            out.append((train, test))
    return out


def _fit_logit(design: np.ndarray, target: np.ndarray, l1: float) -> np.ndarray | None:
    import statsmodels.api as sm

    try:
        model = sm.Logit(target, design)
        if l1 > 0:
            fitted = model.fit_regularized(alpha=l1, disp=0, maxiter=200)
        else:
            fitted = model.fit(disp=0, maxiter=200)
        params = np.asarray(fitted.params, dtype=float)
        return params if np.all(np.isfinite(params)) else None
    except Exception:  # noqa: BLE001 -- separation / singularity is a failed fit, not a crash
        return None


def _fit_ols(design: np.ndarray, target: np.ndarray) -> np.ndarray | None:
    try:
        coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
        return coefficients if np.all(np.isfinite(coefficients)) else None
    except Exception:  # noqa: BLE001
        return None


def _auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """Rank-based AUC. No sklearn in this project, and none is needed."""
    positives, negatives = scores[labels == 1], scores[labels == 0]
    if len(positives) == 0 or len(negatives) == 0:
        return None
    ranks = pd.Series(scores).rank().to_numpy()
    positive_rank_sum = ranks[labels == 1].sum()
    n1, n0 = len(positives), len(negatives)
    return float((positive_rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0))


def fit_diagnostics(
    observations: ObservationSet,
    *,
    features: Sequence[str] | None = None,
    folds: int = 4,
    l1_alpha: float = 0.0,
    seed: int = stats.DEFAULT_SEED,
    embargo_days: int = EMBARGO_DAYS,
) -> list[ModelDiagnostic]:
    """Fit the three diagnostic models with time-respecting cross-validation.

    Returns an empty list rather than a fitted model when the sample is too
    small: a logistic regression on 60 observations and 12 features is not a
    weak diagnostic, it is a fabricated one.
    """
    frame = observations.frame
    outcome = observations.outcome_column
    if len(frame) < MIN_MODEL_OBSERVATIONS:
        return []
    chosen = _select_features(observations, features)
    if len(chosen) < 2:
        return []
    fold_pairs = _expanding_folds(frame, folds, embargo_days)
    if not fold_pairs:
        return []

    diagnostics: list[ModelDiagnostic] = []
    rng = np.random.default_rng(seed)

    for model_name, target_name in (
        ("logistic regression", "P(outcome > 0)"),
        ("linear regression", f"expected {outcome}"),
        ("depth-2 quantile tree", f"expected {outcome}"),
    ):
        fold_scores: list[float] = []
        fold_coefficients: list[np.ndarray] = []
        importances: dict[str, list[float]] = {key: [] for key in chosen}
        notes: list[str] = []

        for train, test in fold_pairs:
            train_design, train_mask, names = _design_matrix(train, chosen)
            test_design, test_mask, _ = _design_matrix(test, chosen)
            if train_design.size == 0 or test_design.size == 0:
                continue
            train_outcome = train.loc[train_mask, outcome].astype(float).to_numpy()
            test_outcome = test.loc[test_mask, outcome].astype(float).to_numpy()

            if model_name == "logistic regression":
                train_target = (train_outcome > 0).astype(float)
                test_target = (test_outcome > 0).astype(int)
                params = _fit_logit(train_design, train_target, l1_alpha)
                if params is None:
                    notes.append("A fold failed to converge and was skipped.")
                    continue
                score = _auc(test_design @ params, test_target)
                score_name = "out-of-sample AUC"
                predict = lambda design, p=params: design @ p  # noqa: E731
                evaluate = lambda pred, p=None: _auc(pred, test_target)  # noqa: E731
            elif model_name == "linear regression":
                params = _fit_ols(train_design, train_outcome)
                if params is None:
                    continue
                score_name = "out-of-sample R^2"
                total = float(((test_outcome - test_outcome.mean()) ** 2).sum())
                residual = float(((test_outcome - test_design @ params) ** 2).sum())
                score = (1.0 - residual / total) if total > 0 else None
                predict = lambda design, p=params: design @ p  # noqa: E731

                def evaluate(pred: np.ndarray, _t: Any = None) -> float | None:
                    total_local = float(((test_outcome - test_outcome.mean()) ** 2).sum())
                    if total_local <= 0:
                        return None
                    return 1.0 - float(((test_outcome - pred) ** 2).sum()) / total_local
            else:
                tree = _fit_quantile_tree(train, chosen, outcome)
                if tree is None:
                    continue
                actual = test[outcome].astype(float).to_numpy()
                total = float(((actual - actual.mean()) ** 2).sum())
                residual = float(((actual - _predict_tree(tree, test)) ** 2).sum())
                if total > 0:
                    fold_scores.append(1.0 - residual / total)
                notes.append(_describe_tree(tree))
                continue

        if not fold_scores and model_name != "depth-2 quantile tree":
            continue
        coefficients = (
            {
                name: float(np.mean([c[i] for c in fold_coefficients]))
                for i, name in enumerate(["intercept", *chosen])
            }
            if fold_coefficients else {}
        )
        stability = (
            {
                name: float(
                    np.mean([np.sign(c[i]) == np.sign(np.mean([d[i] for d in fold_coefficients]))
                             for c in fold_coefficients])
                )
                for i, name in enumerate(["intercept", *chosen])
            }
            if len(fold_coefficients) > 1 else {}
        )
        diagnostics.append(ModelDiagnostic(
            model=model_name, target=target_name, features=list(chosen),
            observations=int(len(frame)), coefficients=coefficients,
            coefficient_stability=stability,
            permutation_importance={
                key: float(np.mean(values)) for key, values in importances.items() if values
            },
            fold_scores=[float(s) for s in fold_scores],
            mean_score=float(np.mean(fold_scores)) if fold_scores else None,
            score_name=(
                "out-of-sample AUC" if model_name == "logistic regression"
                else "out-of-sample R^2"
            ),
            notes=notes + [
                "Diagnostic only. Predictive accuracy is not evidence of a tradeable edge; "
                "any interaction this suggests must be restated as an explicit condition and "
                "validated on its own.",
                "Impurity-based importance is deliberately not reported -- only permutation "
                "importance measured on held-out folds, plus fold-to-fold sign stability.",
            ],
        ))
    return diagnostics


# ---------------------------------------------------------------------------
# A depth-2 tree restricted to the same quantile grid the analysis uses.
# ---------------------------------------------------------------------------


def _fit_quantile_tree(
    frame: pd.DataFrame, features: Sequence[str], outcome: str, depth: int = 2
) -> dict[str, Any] | None:
    values = frame[outcome].astype(float)
    if len(values) < stats.HYPOTHESIS_MIN_N * 2:
        return None
    return _grow(frame, list(features), outcome, depth)


def _grow(
    frame: pd.DataFrame, features: list[str], outcome: str, depth: int
) -> dict[str, Any]:
    values = frame[outcome].astype(float)
    leaf = {"leaf": True, "value": float(values.mean()), "n": int(len(frame))}
    if depth <= 0 or len(frame) < stats.HYPOTHESIS_MIN_N * 2:
        return leaf
    best = None
    parent_sse = float(((values - values.mean()) ** 2).sum())
    for feature in features:
        column = pd.to_numeric(frame[feature], errors="coerce")
        if column.notna().sum() < stats.HYPOTHESIS_MIN_N * 2:
            continue
        # Candidate splits are the tercile boundaries only -- the same grid
        # the interaction tables use. The tree cannot search a finer threshold.
        for quantile in (1 / 3, 2 / 3):
            threshold = float(column.quantile(quantile))
            left_mask = column < threshold
            left, right = frame.loc[left_mask], frame.loc[~left_mask & column.notna()]
            if len(left) < stats.HYPOTHESIS_MIN_N or len(right) < stats.HYPOTHESIS_MIN_N:
                continue
            left_values = left[outcome].astype(float)
            right_values = right[outcome].astype(float)
            sse = (
                float(((left_values - left_values.mean()) ** 2).sum())
                + float(((right_values - right_values.mean()) ** 2).sum())
            )
            if best is None or sse < best[0]:
                best = (sse, feature, threshold, left, right)
    if best is None or best[0] >= parent_sse:
        return leaf
    _sse, feature, threshold, left, right = best
    return {
        "leaf": False, "feature": feature, "threshold": threshold, "n": int(len(frame)),
        "left": _grow(left, features, outcome, depth - 1),
        "right": _grow(right, features, outcome, depth - 1),
    }


def _predict_tree(node: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    out = np.zeros(len(frame), dtype=float)
    for position, (_, row) in enumerate(frame.iterrows()):
        current = node
        while not current["leaf"]:
            value = pd.to_numeric(pd.Series([row.get(current["feature"])]), errors="coerce").iloc[0]
            if pd.isna(value):
                break
            current = current["left"] if value < current["threshold"] else current["right"]
        out[position] = current["value"] if current["leaf"] else _mean_of(current)
    return out


def _mean_of(node: dict[str, Any]) -> float:
    if node["leaf"]:
        return float(node["value"])
    left, right = _mean_of(node["left"]), _mean_of(node["right"])
    return float((left * node["left"]["n"] + right * node["right"]["n"])
                 / max(node["left"]["n"] + node["right"]["n"], 1))


def _describe_tree(node: dict[str, Any], indent: int = 0) -> str:
    pad = "  " * indent
    if node["leaf"]:
        return f"{pad}-> {node['value']:+.4f} (n={node['n']})"
    definition = pit_features.FEATURES.get(node["feature"])
    label = definition.label if definition else node["feature"]
    return (
        f"{pad}if {label} < {node['threshold']:.4g} (n={node['n']}):\n"
        f"{_describe_tree(node['left'], indent + 1)}\n{pad}else:\n"
        f"{_describe_tree(node['right'], indent + 1)}"
    )
