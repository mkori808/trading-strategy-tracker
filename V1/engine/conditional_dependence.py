"""Dependence-aware statistics for Conditional Edge Discovery (methodology v2).

**The problem this module fixes.** Every statistic in `engine/
conditional_stats.py` and `engine/conditional_analysis.py` (bootstrap CIs,
permutation tests, Hedges' g, the FDR correction's implicit sample size)
treats each row of an `ObservationSet` as an independent draw. That
assumption is false for both engine shapes this app produces observations
from, in different ways:

- **Cross-sectional engine** (`engine/observations.py:_cross_sectional_rows`):
  one row per (rebalance, held security). Measured directly on Dual Momentum
  and Market-Residual Momentum's discovery slices (2026-08-22 audit): 172-175
  raw rows come from only 35 unique rebalance dates, each holding ~5
  securities. Every portfolio/regime-level feature (market vol percentile,
  breadth, universe dispersion, the calendar) is COMPUTED IDENTICALLY for
  every row sharing a rebalance date -- verified empirically, not assumed --
  and all five rows also share the exact same entry/exit dates, i.e. the
  exact same realized market return over that holding period. Five rows is
  one observation of that month's regime, not five.
- **Per-symbol engine**: trades overlap in time. Measured on the same
  strategies' discovery slices: 96.7% of Connors RSI2 trades and 100% of IBS
  trades have another position open concurrently (mean 7.8 and 19.0
  concurrent positions respectively). Treating 487 or 981 trades as
  independent draws for a bootstrap CI or permutation test understates
  uncertainty by roughly the same factor the concurrency inflates it.

**What this module adds, and what it deliberately does not attempt.** It
adds a CLUSTER as the unit every dependence-aware statistic resamples or
permutes, computed one of two ways depending on engine:

- Cross-sectional: cluster = rebalance date (`entry_time`). This is exact,
  not a heuristic -- rows sharing a rebalance date genuinely share their
  outcome interval and, for regime-level features, their feature value too.
- Per-symbol: cluster = a contiguous block of `block_length` trades ordered
  by entry time, where `block_length` is estimated from the SAME strategy's
  own measured average concurrency (`observations.py:concurrency_profile`).
  This is a heuristic, disclosed as one: connected-components of the actual
  overlap graph were tried first and rejected as the cluster definition
  (`overlap_dependency_chains` below) because on a strategy with
  near-continuous overlap (IBS: 100% overlap, 981 trades collapse to 5
  connected components) it produces a degenerate effective N that is
  technically defensible but practically useless for sizing a block
  bootstrap. A moving-block length keyed to average concurrency is the same
  choice this codebase already makes elsewhere for exactly this problem --
  see `engine/prop_account.py:PropSimulationConfig.block_size` and
  `engine/research_governance.py:bootstrap_evidence`'s block bootstrap, both
  block-resampling a return series by a length chosen for how long
  dependence actually persists, not by exhaustively identifying every
  dependency chain.

**Every clustered statistic resamples/permutes CLUSTERS, never rows.**
`cluster_bootstrap_ci` draws cluster IDs with replacement and pools every row
of each drawn cluster (duplicated if drawn twice); `cluster_permutation_test`
reassigns which cluster's realized OUTCOME VECTOR is paired with which
cluster's FEATURE/bucket pattern, matched within same-size cluster groups, so
a cluster's own internal row-to-row outcome correlation is never broken by
the permutation -- only the specific pairing between one cluster's features
and another's outcomes is randomized. This single mechanism handles both
feature-level cases from the classification in `engine/pit_features.py`:

- `portfolio_regime` features are constant within every cluster (bucket
  membership is 0 or 1 for the whole cluster), so this reduces exactly to
  the textbook "permute whole clusters between groups" test.
- `security`/`mixed` features vary within a cluster (a rebalance's five
  securities are not all in the same RSI bucket), so bucket membership is a
  fractional share per cluster, and the same resampling mechanism correctly
  averages over that variation while still never breaking a cluster's
  internal outcome correlation.

**Effective N is the cluster count, not the row count, full stop.** No
attempt is made here to estimate a fractional design effect (e.g. an
intraclass-correlation-based effective-N formula) -- the coarser
"independent units = clusters" measure is simpler, auditable by inspection,
and, critically, never OVERSTATES N the way a design-effect formula's
modeling assumptions could if they were wrong for a specific feature. See
`ClusterStructure.effective_n`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

from engine import conditional_stats as stats
from engine import observations as observations_module
from engine.conditional_stats import camel_keys

ClusterMethod = Literal["iid", "rebalance_cluster", "overlap_block"]

#: Floor and ceiling on the estimated per-symbol block length. A length of 1
#: degenerates to IID (defeats the purpose); an unbounded length on a
#: pathological concurrency estimate could collapse effective N to nothing
#: useful. 60 trading days is about a quarter -- longer than any dependence
#: length actually measured on this app's strategies (see module docstring).
MIN_BLOCK_LENGTH = 2
MAX_BLOCK_LENGTH = 60

DEFAULT_CLUSTER_BOOTSTRAP_DRAWS = 2_000
DEFAULT_CLUSTER_PERMUTATIONS = 2_000


@dataclass
class ClusterStructure:
    """How one ObservationSet's rows were grouped for dependence-aware inference."""

    method: ClusterMethod
    cluster_ids: pd.Series  # per-row cluster label, aligned to the source frame's index
    n_raw: int
    n_clusters: int
    cluster_size_mean: float
    cluster_size_median: float
    cluster_size_min: int
    cluster_size_max: int
    block_length: int | None = None
    dependency_chains: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def effective_n(self) -> int:
        """Independent units for sample-size gating and reporting.

        Deliberately the cluster count, not a fractional design-effect
        estimate -- see module docstring. For `method="iid"` this equals
        `n_raw` (every row is its own cluster), so nothing changes for any
        observation set where clustering genuinely does not apply.
        """
        return self.n_clusters

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "method": self.method, "nRaw": self.n_raw, "nClusters": self.n_clusters,
            "effectiveN": self.effective_n,
            "clusterSizeMean": self.cluster_size_mean, "clusterSizeMedian": self.cluster_size_median,
            "clusterSizeMin": self.cluster_size_min, "clusterSizeMax": self.cluster_size_max,
            "blockLength": self.block_length, "dependencyChains": self.dependency_chains,
            "notes": self.notes,
        }
        return camel_keys(payload)


def assign_rebalance_clusters(frame: pd.DataFrame) -> pd.Series:
    """Cluster = rebalance date. Exact, not a heuristic -- every row sharing
    `entry_time` was decided at, and shares the outcome interval of, the same
    rebalance."""
    codes, _uniques = pd.factorize(frame["entry_time"], sort=True)
    return pd.Series(codes, index=frame.index, name="cluster_id")


def estimate_block_length(frame: pd.DataFrame) -> int:
    """Block length for a per-symbol engine's moving-block cluster, from this
    STRATEGY'S OWN measured average concurrency (not a fixed constant).

    A strategy that rarely overlaps positions gets a short block (close to
    IID); one that runs many concurrent positions gets a longer block. Both
    directions matter: a fixed block length would either under-correct a
    high-overlap strategy or unnecessarily shrink effective N on a low-overlap
    one.
    """
    profile = observations_module.concurrency_profile(frame)
    estimate = round(profile.get("averageConcurrent") or 1.0)
    return int(max(MIN_BLOCK_LENGTH, min(MAX_BLOCK_LENGTH, estimate)))


def assign_overlap_block_clusters(frame: pd.DataFrame, block_length: int) -> pd.Series:
    """Contiguous blocks of `block_length` trades, ordered by entry time.

    Ordering by entry time (not the frame's incoming row order) is what makes
    a "block" mean something temporally -- adjacent positions in time are
    the ones plausibly sharing market exposure, which is exactly the
    dependence a moving-block bootstrap is supposed to capture.
    """
    order = frame["entry_time"].to_numpy().argsort(kind="stable")
    block_of_sorted_position = np.arange(len(frame)) // max(block_length, 1)
    cluster_ids = np.empty(len(frame), dtype=int)
    cluster_ids[order] = block_of_sorted_position
    return pd.Series(cluster_ids, index=frame.index, name="cluster_id")


def overlap_dependency_chains(frame: pd.DataFrame) -> dict[str, Any]:
    """Connected components of the trade-overlap graph -- DIAGNOSTIC ONLY.

    Two trades are in the same component if their [entry, exit) intervals
    overlap, transitively. This is the most literal reading of "independent
    trades" for an overlapping-position strategy, and it is reported because
    it is strong, legible evidence for "IID resampling is not defensible" --
    but it is NOT used as the effective-N for gating or bootstrapping,
    because on a strategy with near-continuous overlap (measured: IBS's 981
    discovery trades collapse to 5 components) it produces a number too
    degenerate to size a block bootstrap with. See module docstring.
    """
    n = len(frame)
    if n == 0:
        return {"components": 0, "sizeMean": 0.0, "sizeMedian": 0.0, "sizeMax": 0}
    order = np.argsort(frame["entry_time"].to_numpy())
    entries = frame["entry_time"].to_numpy()[order]
    exits = frame["exit_time"].to_numpy()[order]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Sweep: a trade can only overlap trades whose entry is before this one's
    # exit, so once sorted by entry, checking forward until entries[j] >=
    # exits[i] is sufficient -- O(n log n) in the typical case rather than the
    # O(n^2) all-pairs check used once (offline, for verification) during the
    # audit that motivated this function.
    for i in range(n):
        for j in range(i + 1, n):
            if entries[j] >= exits[i]:
                break
            union(i, j)
    components: dict[int, int] = {}
    for i in range(n):
        root = find(i)
        components[root] = components.get(root, 0) + 1
    sizes = list(components.values())
    return {
        "components": len(components),
        "sizeMean": float(np.mean(sizes)),
        "sizeMedian": float(np.median(sizes)),
        "sizeMax": int(np.max(sizes)),
        "note": (
            "Diagnostic evidence for whether IID resampling is defensible, NOT the "
            "effective N used for bootstrapping -- see module docstring."
        ),
    }


def assign_clusters(frame: pd.DataFrame, engine: str) -> ClusterStructure:
    """Dispatch cluster assignment by engine shape."""
    n = len(frame)
    if n == 0:
        empty = pd.Series([], dtype=int)
        return ClusterStructure(
            method="iid", cluster_ids=empty, n_raw=0, n_clusters=0,
            cluster_size_mean=0.0, cluster_size_median=0.0, cluster_size_min=0, cluster_size_max=0,
        )
    if engine == "cross_sectional":
        cluster_ids = assign_rebalance_clusters(frame)
        method: ClusterMethod = "rebalance_cluster"
        dependency_chains = None
        block_length = None
    elif engine == "standard":
        block_length = estimate_block_length(frame)
        cluster_ids = assign_overlap_block_clusters(frame, block_length)
        method = "overlap_block"
        dependency_chains = overlap_dependency_chains(frame)
    else:
        cluster_ids = pd.Series(np.arange(n), index=frame.index)
        method = "iid"
        dependency_chains = None
        block_length = None

    sizes = cluster_ids.value_counts()
    notes: list[str] = []
    if method == "overlap_block" and dependency_chains and dependency_chains["components"] < sizes.size / 4:
        notes.append(
            f"The overlap-dependency graph collapses to {dependency_chains['components']} connected "
            f"component(s) versus {sizes.size} block-clusters -- this strategy's positions overlap "
            "almost continuously; treat the block-cluster effective N as an estimate of dependence "
            "LENGTH, not a claim that the strategy truly contains that many independent episodes."
        )
    return ClusterStructure(
        method=method, cluster_ids=cluster_ids, n_raw=n, n_clusters=int(sizes.size),
        cluster_size_mean=float(sizes.mean()), cluster_size_median=float(sizes.median()),
        cluster_size_min=int(sizes.min()), cluster_size_max=int(sizes.max()),
        block_length=block_length, dependency_chains=dependency_chains, notes=notes,
    )


def cluster_bootstrap_ci(
    frame: pd.DataFrame,
    outcome_col: str,
    cluster_ids: pd.Series,
    *,
    mask: pd.Series | None = None,
    draws: int = DEFAULT_CLUSTER_BOOTSTRAP_DRAWS,
    confidence: float = 0.95,
    seed: int = stats.DEFAULT_SEED,
) -> dict[str, Any]:
    """Percentile CI for the mean of `outcome_col` (restricted to `mask` if
    given), resampling whole CLUSTERS with replacement rather than rows.

    A drawn cluster contributes ALL of its rows every time it is drawn
    (duplicated on repeat draws), which is what preserves whatever
    within-cluster correlation the real data has -- a row-level IID bootstrap
    on the same data would treat five correlated rows as five independent
    ones and report an artificially narrow interval.

    A draw whose pooled rows contain none satisfying `mask` contributes no
    estimate for that draw (can happen when `mask` selects a small, cluster-
    constant regime bucket) and is dropped rather than treated as a 0 or NaN
    that would bias the interval.
    """
    unique_clusters = cluster_ids.unique()
    n_clusters = len(unique_clusters)
    if n_clusters == 0:
        return {"ciLow": None, "ciHigh": None, "draws": 0, "usableDraws": 0, "method": "unavailable (no clusters)"}
    rng = np.random.default_rng(seed)
    outcome = frame[outcome_col].to_numpy(dtype=float)
    mask_array = mask.to_numpy(dtype=bool) if mask is not None else np.ones(len(frame), dtype=bool)
    # Row indices grouped by cluster, for fast pooling per draw.
    rows_by_cluster: dict[Any, np.ndarray] = {
        cluster: np.where(cluster_ids.to_numpy() == cluster)[0] for cluster in unique_clusters
    }
    means: list[float] = []
    for _ in range(draws):
        drawn = rng.choice(unique_clusters, size=n_clusters, replace=True)
        row_indices = np.concatenate([rows_by_cluster[c] for c in drawn])
        pooled_mask = mask_array[row_indices]
        if not pooled_mask.any():
            continue
        means.append(float(outcome[row_indices[pooled_mask]].mean()))
    if len(means) < max(20, draws // 10):
        return {
            "ciLow": None, "ciHigh": None, "draws": draws, "usableDraws": len(means),
            "method": "unavailable (too few draws contained a masked row)",
        }
    alpha = (1.0 - confidence) / 2.0
    return {
        "ciLow": float(np.quantile(means, alpha)), "ciHigh": float(np.quantile(means, 1.0 - alpha)),
        "draws": draws, "usableDraws": len(means),
        "method": f"cluster bootstrap ({n_clusters} clusters resampled), {draws} draws, seed {seed}",
    }


def _cluster_vectors(
    frame: pd.DataFrame, outcome_col: str, mask: pd.Series, cluster_ids: pd.Series
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per-cluster (bucket_flags, outcomes) arrays, in the cluster's original row order."""
    outcome = frame[outcome_col].to_numpy(dtype=float)
    mask_array = mask.to_numpy(dtype=bool)
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for cluster in cluster_ids.unique():
        positions = np.where(cluster_ids.to_numpy() == cluster)[0]
        out.append((mask_array[positions], outcome[positions]))
    return out


def _statistic_from_pairing(pairs: Sequence[tuple[np.ndarray, np.ndarray]]) -> float | None:
    """In-bucket mean minus out-of-bucket mean, pooled across cluster pairs."""
    in_bucket: list[float] = []
    out_bucket: list[float] = []
    for bucket_flags, outcomes in pairs:
        in_bucket.extend(outcomes[bucket_flags].tolist())
        out_bucket.extend(outcomes[~bucket_flags].tolist())
    if not in_bucket or not out_bucket:
        return None
    return float(np.mean(in_bucket) - np.mean(out_bucket))


def cluster_permutation_test(
    frame: pd.DataFrame,
    outcome_col: str,
    mask: pd.Series,
    cluster_ids: pd.Series,
    *,
    permutations: int = DEFAULT_CLUSTER_PERMUTATIONS,
    seed: int = stats.DEFAULT_SEED,
) -> dict[str, Any]:
    """Cluster-level permutation test for "does this bucket differ from the rest".

    Reassigns which cluster's OUTCOME VECTOR pairs with which cluster's
    BUCKET-MEMBERSHIP PATTERN, matched within same-cluster-size groups, so a
    cluster's own rows always move as one unit -- their mutual correlation is
    preserved on every permutation draw, and only the cross-cluster pairing
    between features and outcomes is randomized. This is what makes the test
    valid under within-cluster dependence: a naive row-level permutation
    would independently reshuffle rows that are not independent, and can
    report a p-value far smaller than the real evidence supports (see
    `tests/test_engine/test_conditional_dependence.py`'s synthetic
    demonstration).

    For a `portfolio_regime` feature (bucket membership constant within every
    cluster), this reduces exactly to the textbook "permute whole clusters
    between the two groups" test. For a `security`/`mixed` feature (bucket
    membership varies within a cluster), it generalizes to a weighted
    exchange of clusters' realized outcome vectors, correctly reflecting that
    within-cluster rows still share the cluster's realized market outcome.

    Clusters are permuted only WITHIN groups of matching size (a stragglers'
    group of size 1 has only the identity permutation and always maps to
    itself) -- see module docstring for why exact-size matching is the right
    trade-off here rather than a more general but harder-to-verify scheme.
    """
    pairs = _cluster_vectors(frame, outcome_col, mask, cluster_ids)
    observed = _statistic_from_pairing(pairs)
    if observed is None:
        return {
            "pValue": None, "observedDifference": None, "permutations": 0,
            "method": "unavailable (mask selects none or all clusters)",
        }
    sizes = [len(flags) for flags, _ in pairs]
    size_groups: dict[int, list[int]] = {}
    for index, size in enumerate(sizes):
        size_groups.setdefault(size, []).append(index)

    rng = np.random.default_rng(seed)
    permuted_stats: list[float] = []
    outcomes_only = [outcomes for _flags, outcomes in pairs]
    bucket_flags_only = [flags for flags, _outcomes in pairs]
    for _ in range(permutations):
        new_outcome_order = list(range(len(pairs)))
        for indices in size_groups.values():
            if len(indices) > 1:
                shuffled = rng.permutation(indices)
                for source, dest in zip(indices, shuffled):
                    new_outcome_order[dest] = source
        shuffled_pairs = [
            (bucket_flags_only[i], outcomes_only[new_outcome_order[i]]) for i in range(len(pairs))
        ]
        statistic = _statistic_from_pairing(shuffled_pairs)
        if statistic is not None:
            permuted_stats.append(statistic)
    if not permuted_stats:
        return {
            "pValue": None, "observedDifference": observed, "permutations": 0,
            "method": "unavailable (no permutation produced a comparable pairing)",
        }
    extreme = sum(1 for value in permuted_stats if abs(value) >= abs(observed))
    p_value = (extreme + 1) / (len(permuted_stats) + 1)
    return {
        "pValue": float(p_value), "observedDifference": observed,
        "permutations": len(permuted_stats),
        "method": (
            f"cluster permutation ({len(pairs)} clusters, within-size-group reassignment), "
            f"{len(permuted_stats)} draws, seed {seed}"
        ),
    }


def dependence_aware_evaluate(
    frame: pd.DataFrame,
    outcome_col: str,
    mask: pd.Series,
    cluster_structure: ClusterStructure,
    *,
    draws: int = DEFAULT_CLUSTER_BOOTSTRAP_DRAWS,
    permutations: int = DEFAULT_CLUSTER_PERMUTATIONS,
    seed: int = stats.DEFAULT_SEED,
) -> dict[str, Any]:
    """The full dependence-aware readout for one condition/bucket on one
    observation frame: raw vs. effective N, cluster CI, cluster permutation p."""
    conditioned_n = int(mask.sum())
    baseline_mean = float(frame[outcome_col].mean()) if len(frame) else float("nan")
    conditioned_mean = float(frame.loc[mask, outcome_col].mean()) if conditioned_n else float("nan")
    ci = cluster_bootstrap_ci(
        frame, outcome_col, cluster_structure.cluster_ids, mask=mask, draws=draws, seed=seed,
    )
    permutation = cluster_permutation_test(
        frame, outcome_col, mask, cluster_structure.cluster_ids, permutations=permutations, seed=seed,
    )
    # Effective N of the CONDITIONED subset specifically -- the clusters that
    # actually contributed at least one in-bucket row, not the full cluster
    # structure's count. A rule retaining rows from only 8 of 43 rebalances
    # has an effective N of 8 for ITSELF, even though the discovery sample's
    # overall cluster count is 43.
    conditioned_clusters = cluster_structure.cluster_ids[mask].nunique() if conditioned_n else 0
    return {
        "rawObservations": conditioned_n,
        "effectiveObservations": int(conditioned_clusters),
        "clusterMethod": cluster_structure.method,
        "totalClusters": cluster_structure.n_clusters,
        "baselineMean": baseline_mean, "conditionalMean": conditioned_mean,
        "improvement": conditioned_mean - baseline_mean if conditioned_n else None,
        "ciLow": ci.get("ciLow"), "ciHigh": ci.get("ciHigh"), "ciMethod": ci.get("method"),
        "ciUsableDraws": ci.get("usableDraws"), "ciTotalDraws": ci.get("draws"),
        "permutationP": permutation.get("pValue"), "permutationMethod": permutation.get("method"),
        "methodologyVersion": stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
    }
