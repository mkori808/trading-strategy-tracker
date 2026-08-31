"""Statistics for Conditional Edge Discovery.

Everything that turns a subset of trade outcomes into a defensible claim
lives here: bucketing, uncertainty, effect size, multiple-testing control and
randomization tests. It knows nothing about strategies, features or engines,
which is what lets `tests/test_engine/test_conditional_stats.py` pin each
piece against a closed-form or simulated answer instead of against whatever
the market happened to do.

Four positions this module takes deliberately.

**Bootstrap first, parametric second.** Trade R-multiples are bounded below
by roughly -1 and unbounded above; they are skewed, fat-tailed and nothing
like normal. A t-interval on 40 of them is a statement about a distribution
the data does not have. `bootstrap_mean_ci` resamples instead, and the
normal-theory standard error is reported beside it as a cross-check, never
as the headline.

**A p-value is never the conclusion.** Every comparison also carries an
effect size (Hedges' g, which corrects the small-sample bias in Cohen's d)
and the raw difference in the units the operator actually trades. A
difference can be significant at any n if you collect enough of it; the
question the research brief asks is whether it is *economically* meaningful,
and that is a separate number that must be visible next to the q-value.

**Multiple testing is accounted for, not disclosed and forgotten.**
`benjamini_hochberg` returns a q-value per hypothesis and the count that
survives. The count of hypotheses examined is an input the caller must
supply, and `DiscoveryAccounting` refuses to let it be smaller than the
number of results handed to it -- under-reporting the search is the single
easiest way to make a data-mined result look preregistered.

**Sample-size floors come from the existing power framework, not from new
numbers invented here.** `MIN_RELIABLE_TRADES` (30) is
`engine/metrics.py`'s own threshold, already used to mark a strategy "sample
too small" everywhere else in this app; the exploratory and warning tiers are
multiples of it rather than a second, unrelated scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

from engine.metrics import MIN_RELIABLE_TRADES

#: Sample-size tiers, expressed as multiples of the app's existing
#: "sample too small" threshold so this feature cannot drift away from what
#: the leaderboard already enforces.
#:
#: - Below MIN_RELIABLE_TRADES (30): no hypothesis may be generated at all.
#: - Below EXPLORATORY_N (50): reportable, labelled exploratory, never a
#:   candidate.
#: - Below WARN_N (100): a candidate, but flagged as thin everywhere it is
#:   shown.
#: - Three-way interactions require THREE_WAY_MIN_N in every cell, because
#:   the number of cells grows multiplicatively while the sample does not.
HYPOTHESIS_MIN_N = MIN_RELIABLE_TRADES
EXPLORATORY_N = MIN_RELIABLE_TRADES * 2
WARN_N = MIN_RELIABLE_TRADES * 4
THREE_WAY_MIN_N = MIN_RELIABLE_TRADES * 5

SampleTier = Literal["insufficient", "exploratory", "thin", "adequate"]

DEFAULT_BOOTSTRAP_DRAWS = 2_000
DEFAULT_PERMUTATIONS = 2_000

#: Fixed default so a discovery session is reproducible. Any caller wanting
#: a different draw passes its own seed and it is recorded in the ledger.
DEFAULT_SEED = 20260822

#: Methodology versions for Conditional Edge Discovery's statistical layer.
#:
#: v1 treated every observation ROW as independent -- correct for nothing
#: this app actually produces, since a per-symbol engine's overlapping
#: trades and a cross-sectional engine's one-row-per-held-security rebalance
#: both violate it. The 2026-08-22 dependence audit (see
#: engine/conditional_dependence.py) found this materially overstated
#: significance on the cross-sectional strategies specifically: ~172-175 raw
#: rows resolved to only ~35 independent rebalance clusters, a ~5x gap.
#:
#: v1 rows already written (`conditional_sessions`/`conditional_hypotheses`)
#: are NOT rewritten -- `methodology_version` is NULL on them, which is
#: itself the historical fact ("computed before this distinction existed").
#: New rows record their version explicitly. See
#: engine/conditional_ledger.py's audit table for the v1->v2 delta on
#: results computed before the fix.
METHODOLOGY_V1_ROW_LEVEL = "conditional_edge_stats_v1"
METHODOLOGY_V2_DEPENDENCE_AWARE = "conditional_edge_stats_v2_dependence_aware"
CURRENT_METHODOLOGY_VERSION = METHODOLOGY_V2_DEPENDENCE_AWARE


def _to_camel(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def camel_keys(value: Any) -> Any:
    """Recursively rekey dict keys from snake_case to camelCase.

    Every JSON payload this API layer serves elsewhere is camelCase (see
    api/main.py's own comment on mapping snake_case DB columns to the
    camelCase API convention); dataclasses in this feature use Python's
    snake_case field names internally via `dataclasses.asdict()`, and this is
    the single place that bridges the two rather than hand-writing the
    mapping at every call site. Safe to apply more than once: an
    already-camelCase key has no underscore and round-trips unchanged. Only
    keys are rewritten -- string VALUES (feature keys like "rsi2" used
    elsewhere as lookups) are left untouched.
    """
    if isinstance(value, dict):
        return {
            (_to_camel(k) if isinstance(k, str) else k): camel_keys(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [camel_keys(v) for v in value]
    return value


def sample_tier(n: int) -> SampleTier:
    if n < HYPOTHESIS_MIN_N:
        return "insufficient"
    if n < EXPLORATORY_N:
        return "exploratory"
    if n < WARN_N:
        return "thin"
    return "adequate"


def sample_warning(n: int) -> str | None:
    tier = sample_tier(n)
    if tier == "insufficient":
        return (
            f"{n} observations is below the app's {HYPOTHESIS_MIN_N}-trade reliability "
            "threshold; no hypothesis may be generated from this subset."
        )
    if tier == "exploratory":
        return f"{n} observations: exploratory only, not a candidate hypothesis."
    if tier == "thin":
        return f"{n} observations: thin sample -- treat the effect size as poorly resolved."
    return None


@dataclass(frozen=True)
class Summary:
    """Descriptive statistics for one subset of outcomes."""

    n: int
    mean: float
    median: float
    std: float
    standard_error: float
    win_rate: float
    profit_factor: float | None
    ci_low: float
    ci_high: float
    ci_method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n, "mean": self.mean, "median": self.median, "std": self.std,
            "standardError": self.standard_error, "winRate": self.win_rate,
            "profitFactor": self.profit_factor, "ciLow": self.ci_low,
            "ciHigh": self.ci_high, "ciMethod": self.ci_method,
        }


def _clean(values: Sequence[float] | pd.Series | np.ndarray) -> np.ndarray:
    array = np.asarray(
        values.to_numpy(dtype=float) if isinstance(values, pd.Series) else values,
        dtype=float,
    )
    return array[np.isfinite(array)]


def profit_factor(values: np.ndarray) -> float | None:
    """Gross wins / gross losses.

    Reported next to the mean because the two can disagree in direction --
    R-multiples are normalised by each trade's own risk, so tight stops on
    winners and wide stops on losers can produce a positive mean while gross
    losses still exceed gross wins. CLAUDE.md's rule applies here unchanged:
    when they disagree, believe profit factor about the dollars.
    """
    wins = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return float(wins / losses)


def bootstrap_mean_ci(
    values: Sequence[float] | pd.Series | np.ndarray,
    *,
    draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    confidence: float = 0.95,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float, str]:
    """Percentile bootstrap interval for the mean.

    Percentile rather than BCa: BCa's acceleration term is itself estimated
    from a jackknife on the same small sample that motivated bootstrapping in
    the first place, and on 40-100 skewed observations that correction is
    noisier than the bias it removes. The interval is reported with its
    method attached so nobody has to guess which one produced it.
    """
    array = _clean(values)
    if len(array) < 2:
        return (float("nan"), float("nan"), "unavailable (n < 2)")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(array), size=(draws, len(array)))
    means = array[indices].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    return (
        float(np.quantile(means, alpha)),
        float(np.quantile(means, 1.0 - alpha)),
        f"percentile bootstrap, {draws} draws, seed {seed}",
    )


def summarize(
    values: Sequence[float] | pd.Series | np.ndarray,
    *,
    draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    seed: int = DEFAULT_SEED,
) -> Summary:
    array = _clean(values)
    n = int(len(array))
    if n == 0:
        return Summary(0, float("nan"), float("nan"), float("nan"), float("nan"),
                       float("nan"), None, float("nan"), float("nan"), "unavailable (n = 0)")
    std = float(array.std(ddof=1)) if n > 1 else float("nan")
    se = std / math.sqrt(n) if n > 1 and math.isfinite(std) else float("nan")
    low, high, method = bootstrap_mean_ci(array, draws=draws, seed=seed)
    return Summary(
        n=n, mean=float(array.mean()), median=float(np.median(array)), std=std,
        standard_error=se, win_rate=float((array > 0).mean()),
        profit_factor=profit_factor(array), ci_low=low, ci_high=high, ci_method=method,
    )


def hedges_g(treatment: np.ndarray, control: np.ndarray) -> float:
    """Standardised mean difference with the small-sample correction.

    Cohen's d is biased upward at small n -- exactly the regime a conditional
    subset lives in -- so the J correction is applied. Sign convention:
    positive means the conditioned subset did better.
    """
    n1, n2 = len(treatment), len(control)
    if n1 < 2 or n2 < 2:
        return float("nan")
    v1, v2 = treatment.var(ddof=1), control.var(ddof=1)
    pooled = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
    if pooled <= 0 or not math.isfinite(pooled):
        return float("nan")
    d = (treatment.mean() - control.mean()) / math.sqrt(pooled)
    correction = 1.0 - (3.0 / (4.0 * (n1 + n2) - 9.0))
    return float(d * correction)


def permutation_test(
    treatment: Sequence[float] | pd.Series | np.ndarray,
    control: Sequence[float] | pd.Series | np.ndarray,
    *,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = DEFAULT_SEED,
    alternative: Literal["two-sided", "greater"] = "two-sided",
) -> dict[str, Any]:
    """How often would shuffling produce a gap this large?

    This is the empirical answer to "the feature has no real relationship
    with the outcome" -- outcomes are reassigned at random to the two groups
    while both group SIZES are held fixed, so nothing but the association is
    destroyed. It makes no distributional assumption at all, which is why it
    is the primary evidence here and the t-test is a cross-check.

    The p-value uses the (r + 1) / (m + 1) convention: a permutation test can
    never honestly report p = 0, because the observed arrangement is itself
    one of the arrangements under the null.
    """
    a, b = _clean(treatment), _clean(control)
    if len(a) < 2 or len(b) < 2:
        return {
            "pValue": None, "permutations": 0, "observedDifference": None,
            "method": "unavailable (a group had fewer than 2 observations)",
        }
    observed = float(a.mean() - b.mean())
    pooled = np.concatenate([a, b])
    rng = np.random.default_rng(seed)
    n1 = len(a)
    extreme = 0
    for _ in range(permutations):
        rng.shuffle(pooled)
        difference = pooled[:n1].mean() - pooled[n1:].mean()
        if alternative == "greater":
            extreme += difference >= observed
        else:
            extreme += abs(difference) >= abs(observed)
    p_value = (extreme + 1) / (permutations + 1)
    return {
        "pValue": float(p_value),
        "permutations": permutations,
        "observedDifference": observed,
        "alternative": alternative,
        "method": (
            f"label permutation, {permutations} shuffles, seed {seed}; "
            "p = (exceedances + 1) / (permutations + 1)"
        ),
    }


def welch_t_test(
    treatment: Sequence[float] | pd.Series | np.ndarray,
    control: Sequence[float] | pd.Series | np.ndarray,
) -> dict[str, Any]:
    """Welch's unequal-variance t-test, as a parametric cross-check only.

    Reported so a reader can see whether the permutation p-value and the
    parametric one agree. When they disagree, the permutation result is the
    one to believe here: trade outcomes are not normal, and Welch corrects
    for unequal variance, not for skew.
    """
    a, b = _clean(treatment), _clean(control)
    if len(a) < 2 or len(b) < 2:
        return {"tStatistic": None, "pValue": None, "degreesOfFreedom": None}
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    denominator = va + vb
    if denominator <= 0:
        return {"tStatistic": None, "pValue": None, "degreesOfFreedom": None}
    t = (a.mean() - b.mean()) / math.sqrt(denominator)
    df = denominator ** 2 / (
        va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1)
    )
    try:
        from scipy import stats  # local import: scipy is only needed here

        p = float(2.0 * stats.t.sf(abs(t), df))
    except Exception:  # noqa: BLE001 -- fall back to a normal approximation
        p = float(2.0 * (1.0 - _normal_cdf(abs(t))))
    return {"tStatistic": float(t), "pValue": p, "degreesOfFreedom": float(df)}


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def benjamini_hochberg(
    p_values: Sequence[float | None], *, alpha: float = 0.10
) -> dict[str, Any]:
    """Benjamini-Hochberg false-discovery-rate control.

    FDR rather than family-wise error (Bonferroni): this is a screening
    stage whose output is *preregistered and then validated out of sample*,
    so the cost of one false candidate is a wasted validation run, while the
    cost of Bonferroni's conservatism on a few hundred correlated hypotheses
    is missing every real effect. The out-of-sample stage is what actually
    protects the conclusion -- FDR just decides what is worth spending it on.

    `q_values` are monotonised (the standard step-up enforcement) so a
    hypothesis can never carry a smaller q than a more significant one.
    Entries with no p-value keep `None` and are excluded from the correction
    rather than being assigned a passing q by default.
    """
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None and math.isfinite(p)]
    q_values: list[float | None] = [None] * len(p_values)
    if not indexed:
        return {
            "alpha": alpha, "qValues": q_values, "hypothesesTested": len(p_values),
            "rawSignificant": 0, "fdrSignificant": 0, "significant": [False] * len(p_values),
            "method": "Benjamini-Hochberg (no testable p-values)",
        }
    indexed.sort(key=lambda pair: pair[1])
    m = len(indexed)
    running = 1.0
    for rank in range(m, 0, -1):
        index, p = indexed[rank - 1]
        running = min(running, p * m / rank)
        q_values[index] = float(running)
    significant = [
        q is not None and q <= alpha for q in q_values
    ]
    raw_significant = sum(1 for _, p in indexed if p <= 0.05)
    return {
        "alpha": alpha,
        "qValues": q_values,
        "hypothesesTested": len(p_values),
        "testableHypotheses": m,
        "rawSignificant": raw_significant,
        "fdrSignificant": sum(significant),
        "significant": significant,
        "method": f"Benjamini-Hochberg step-up at FDR alpha = {alpha}",
    }


def monotonicity(bucket_means: Sequence[float]) -> dict[str, Any]:
    """Does expectancy move in one direction across ordered buckets?

    Spearman rank correlation between bucket index and bucket mean, plus a
    strict-monotonicity flag. A monotone relationship is far more credible
    than a single spiking bucket: the latter is what pure noise looks like
    once you have cut a sample five ways, and reporting the two identically
    is how a discovery engine fools its owner.
    """
    means = [m for m in bucket_means if m is not None and math.isfinite(m)]
    if len(means) < 3:
        return {"spearman": None, "strictlyMonotonic": False, "direction": None}
    order = np.arange(len(means), dtype=float)
    values = np.asarray(means, dtype=float)
    ranks = pd.Series(values).rank().to_numpy()
    centered_order = order - order.mean()
    centered_ranks = ranks - ranks.mean()
    denominator = math.sqrt(float((centered_order ** 2).sum() * (centered_ranks ** 2).sum()))
    rho = float((centered_order * centered_ranks).sum() / denominator) if denominator > 0 else 0.0
    increasing = all(b > a for a, b in zip(values, values[1:]))
    decreasing = all(b < a for a, b in zip(values, values[1:]))
    return {
        "spearman": rho,
        "strictlyMonotonic": bool(increasing or decreasing),
        "direction": "increasing" if increasing else "decreasing" if decreasing else None,
    }


def quantile_buckets(
    values: pd.Series, *, buckets: int, labels: Sequence[str] | None = None
) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Assign observations to quantile buckets of `values`.

    Uses `qcut` with duplicate-edge dropping, so a feature with heavy ties
    (relative volume pinned at 1.0, a sentinel-filled event distance) yields
    FEWER buckets rather than empty ones or an exception. The realised edges
    are returned so a frozen hypothesis can record the exact thresholds it
    was discovered at, instead of a bucket label that means something
    different on the next sample.
    """
    finite = values[np.isfinite(values.astype(float))] if len(values) else values
    if finite.empty or buckets < 2:
        return pd.Series(index=values.index, dtype=object), []
    try:
        assigned, edges = pd.qcut(
            finite, q=buckets, labels=False, retbins=True, duplicates="drop"
        )
    except (ValueError, IndexError):
        return pd.Series(index=values.index, dtype=object), []
    # All-identical (or too-few-distinct-value) input collapses `edges` to a
    # single point; qcut then assigns every row NaN rather than raising, so
    # this must be checked explicitly rather than inferred from an exception.
    valid_assignments = assigned.dropna()
    if valid_assignments.empty:
        return pd.Series(index=values.index, dtype=object), []
    realised = int(valid_assignments.max()) + 1
    if realised < 2:
        return pd.Series(index=values.index, dtype=object), []
    names = list(labels) if labels and len(labels) == realised else [
        f"Q{i + 1}" for i in range(realised)
    ]
    out = pd.Series(index=values.index, dtype=object)
    # Rows that landed on a dropped duplicate edge come back NaN from qcut
    # even when most of the sample was assigned fine -- leave those
    # unlabelled (missing) rather than crashing on int(NaN).
    out.loc[valid_assignments.index] = [names[int(a)] for a in valid_assignments]
    edge_rows = [
        {"label": names[i], "low": float(edges[i]), "high": float(edges[i + 1])}
        for i in range(realised)
    ]
    return out, edge_rows


@dataclass
class DiscoveryAccounting:
    """The running tally of everything a discovery session looked at.

    Held explicitly rather than inferred, because the number that matters is
    how many hypotheses were EXAMINED, not how many were reported. A session
    that tests 300 relationships and shows the best 10 has still tested 300,
    and the FDR correction has to be told so.
    """

    session_id: str
    univariate_tested: int = 0
    pairwise_tested: int = 0
    three_way_tested: int = 0
    suppressed_for_sample: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total_examined(self) -> int:
        return self.univariate_tested + self.pairwise_tested + self.three_way_tested

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "univariateTested": self.univariate_tested,
            "pairwiseTested": self.pairwise_tested,
            "threeWayTested": self.three_way_tested,
            "suppressedForSample": self.suppressed_for_sample,
            "totalExamined": self.total_examined,
            "notes": self.notes,
        }


def redundancy_report(
    frame: pd.DataFrame, keys: Sequence[str], *, threshold: float = 0.8
) -> dict[str, Any]:
    """Pairwise Spearman correlation between features, with clusters.

    Answers the question section 21 of the research brief raises: RSI, the
    3-day return and distance from a short moving average are three names for
    one thing, and treating three correlated confirmations as three
    independent pieces of evidence is how a single mediocre effect gets
    reported as a converging body of results.

    Spearman rather than Pearson because several features are bounded
    percentiles or ranks, where a monotone relationship matters and a linear
    one does not. Clusters are single-linkage groups above `threshold`, which
    deliberately over-merges: for a warning, a false "these might be the same
    thing" is far cheaper than a missed one.
    """
    numeric = [
        k for k in keys
        if k in frame.columns and pd.api.types.is_numeric_dtype(frame[k]) and frame[k].notna().sum() > 10
    ]
    if len(numeric) < 2:
        return {"pairs": [], "clusters": [], "threshold": threshold}
    correlation = frame[numeric].corr(method="spearman")
    pairs = []
    parent = {k: k for k in numeric}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(numeric):
        for b in numeric[i + 1:]:
            rho = correlation.loc[a, b]
            if pd.isna(rho):
                continue
            if abs(float(rho)) >= threshold:
                pairs.append({"a": a, "b": b, "spearman": float(rho)})
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    clusters: dict[str, list[str]] = {}
    for key in numeric:
        clusters.setdefault(find(key), []).append(key)
    return {
        "threshold": threshold,
        "pairs": sorted(pairs, key=lambda p: -abs(p["spearman"])),
        "clusters": [sorted(members) for members in clusters.values() if len(members) > 1],
        "method": f"Spearman rank correlation, single-linkage clusters at |rho| >= {threshold}",
    }
