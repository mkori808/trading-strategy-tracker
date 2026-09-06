"""Full-pipeline null calibration: how often does the ADAPTIVE discovery
procedure itself manufacture impressive-looking results from data with no
true conditional structure?

**Why this is a different question from the dependence audit.**
`engine/conditional_dependence.py` fixes a STATISTICAL bug (row-level
inference on clustered observations). This module answers a SEARCH-PROCESS
question the dependence fix does not touch: the pipeline screens features by
univariate evidence before testing pairwise interactions, and tests
interactions before assembling candidates -- each stage's selection is
DATA-DEPENDENT. Benjamini-Hochberg assumes the family of tested hypotheses
was fixed in advance; applying it after adaptive screening is not obviously
valid, and this module measures directly, empirically, whether it happens to
be close enough to valid in practice for THIS pipeline.

**What gets randomized, and what does not.** Every null dataset preserves
timestamps, cluster boundaries, every feature's own value and cross-feature
correlations, and the trade/rebalance structure exactly -- only the mapping
between a cluster's FEATURES and its realized OUTCOME is broken, via the same
same-size cluster-shuffle mechanism `engine/
conditional_dependence.py:cluster_permutation_test` uses (see
`shuffle_outcomes_by_cluster` below). This is deliberately the SAME
dependence-preserving randomization already validated there, not a new one:
using a different, less careful shuffle here would risk the null datasets
themselves having spurious structure, which would corrupt this calibration
the same way naive row-level inference corrupted the original discovery
runs.

**The pipeline replayed on each null dataset is the ORIGINAL (v1, row-level)
procedure, run with `dependence_aware=False`.** This calibrates the EXACT
adaptive search that produced this app's first reported results (Dual
Momentum's 40/105, Market-Residual Momentum's 53/103), which is the
literal question asked: "if there were no conditional edge at all, how often
would THIS EXACT PIPELINE produce results this impressive." It is not a
statement about the corrected v2 pipeline's own calibration, which is a
separate (and, given v2 already produces far fewer significant results on
the real data, presumably better-behaved) question this module does not
claim to answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from engine import conditional_analysis as ca
from engine import conditional_dependence as cd
from engine import conditional_stats as stats
from engine.observations import ObservationSet

DEFAULT_ITERATIONS = 100
DEFAULT_INNER_PERMUTATIONS = 200


def shuffle_outcomes_by_cluster(
    frame: pd.DataFrame, outcome_col: str, cluster_ids: pd.Series, *, seed: int,
) -> pd.Series:
    """Reassign whole clusters' OUTCOME vectors among clusters of the same
    size, leaving every feature column, timestamp, and within-cluster row
    order untouched. Breaks only the feature<->outcome pairing at the
    cluster level -- see module docstring."""
    rng = np.random.default_rng(seed)
    outcome = frame[outcome_col].to_numpy(dtype=float).copy()
    positions_by_cluster: dict[Any, np.ndarray] = {
        cluster: np.where(cluster_ids.to_numpy() == cluster)[0] for cluster in cluster_ids.unique()
    }
    by_size: dict[int, list[Any]] = {}
    for cluster, positions in positions_by_cluster.items():
        by_size.setdefault(len(positions), []).append(cluster)

    shuffled = outcome.copy()
    for clusters in by_size.values():
        if len(clusters) < 2:
            continue
        permuted = rng.permutation(clusters)
        for source, dest in zip(clusters, permuted):
            shuffled[positions_by_cluster[dest]] = outcome[positions_by_cluster[source]]
    return pd.Series(shuffled, index=frame.index, name=outcome_col)


def _null_observations(
    observations: ObservationSet, cluster_structure: cd.ClusterStructure, *, seed: int,
) -> ObservationSet:
    shuffled_outcome = shuffle_outcomes_by_cluster(
        observations.frame, observations.outcome_column, cluster_structure.cluster_ids, seed=seed,
    )
    null_frame = observations.frame.copy()
    null_frame[observations.outcome_column] = shuffled_outcome
    return replace(observations, frame=null_frame)


@dataclass
class NullIterationResult:
    raw_significant: int
    fdr_significant: int
    max_effect_size: float
    n_candidates: int
    best_candidate_p: float  # 1.0 if no candidates

    def to_dict(self) -> dict[str, Any]:
        return {
            "rawSignificant": self.raw_significant, "fdrSignificant": self.fdr_significant,
            "maxEffectSize": self.max_effect_size, "nCandidates": self.n_candidates,
            "bestCandidateP": self.best_candidate_p,
        }


@dataclass
class NullExperimentResult:
    strategy_name: str
    iterations: int
    inner_permutations: int
    seed: int
    cluster_structure: dict[str, Any]
    null_iterations: list[NullIterationResult]
    observed_raw_significant: int
    observed_fdr_significant: int
    observed_max_effect_size: float
    observed_n_candidates: int
    observed_best_candidate_p: float

    def _null_array(self, key: str) -> np.ndarray:
        return np.array([getattr(it, key) for it in self.null_iterations], dtype=float)

    def empirical_p_value(self, key: str, observed: float, *, greater_is_extreme: bool = True) -> float:
        """Fraction of null iterations at least as extreme as `observed`.
        (r + 1) / (m + 1) convention, matching `conditional_stats.permutation_test`."""
        null_values = self._null_array(key)
        if len(null_values) == 0:
            return float("nan")
        if greater_is_extreme:
            extreme = int((null_values >= observed).sum())
        else:
            extreme = int((null_values <= observed).sum())
        return (extreme + 1) / (len(null_values) + 1)

    def to_dict(self) -> dict[str, Any]:
        raw_sig = self._null_array("raw_significant")
        fdr_sig = self._null_array("fdr_significant")
        max_effect = self._null_array("max_effect_size")
        n_candidates = self._null_array("n_candidates")
        best_p = self._null_array("best_candidate_p")
        return {
            "strategyName": self.strategy_name, "iterations": self.iterations,
            "innerPermutations": self.inner_permutations, "seed": self.seed,
            "clusterStructure": self.cluster_structure,
            "observed": {
                "rawSignificant": self.observed_raw_significant,
                "fdrSignificant": self.observed_fdr_significant,
                "maxEffectSize": self.observed_max_effect_size,
                "nCandidates": self.observed_n_candidates,
                "bestCandidateP": self.observed_best_candidate_p,
            },
            "nullDistribution": {
                "rawSignificant": {"mean": float(raw_sig.mean()), "p95": float(np.quantile(raw_sig, 0.95)), "max": float(raw_sig.max())},
                "fdrSignificant": {"mean": float(fdr_sig.mean()), "p95": float(np.quantile(fdr_sig, 0.95)), "max": float(fdr_sig.max())},
                "maxEffectSize": {"mean": float(max_effect.mean()), "p95": float(np.quantile(max_effect, 0.95)), "max": float(max_effect.max())},
                "nCandidates": {"mean": float(n_candidates.mean()), "p95": float(np.quantile(n_candidates, 0.95)), "max": float(n_candidates.max())},
                "bestCandidateP": {"mean": float(best_p.mean()), "p05": float(np.quantile(best_p, 0.05)), "min": float(best_p.min())},
            },
            "empiricalPValues": {
                "rawSignificant": self.empirical_p_value("raw_significant", self.observed_raw_significant),
                "fdrSignificant": self.empirical_p_value("fdr_significant", self.observed_fdr_significant),
                "maxEffectSize": self.empirical_p_value("max_effect_size", self.observed_max_effect_size),
                "nCandidates": self.empirical_p_value("n_candidates", self.observed_n_candidates),
                "bestCandidateP": self.empirical_p_value(
                    "best_candidate_p", self.observed_best_candidate_p, greater_is_extreme=False,
                ),
            },
            "interpretation": (
                "Each empirical p-value is the fraction of null replications (real feature "
                "correlations and cluster structure preserved, only the feature<->outcome "
                "pairing broken) whose adaptive-pipeline output was at least as extreme as "
                "the REAL discovery run's. A small value means the original result is "
                "unusual even accounting for how many hypotheses this exact multi-stage "
                "procedure tests and how it screens interactions from univariate survivors; "
                "a large value means results this 'impressive' are a routine product of the "
                "search process itself, not evidence of a real effect."
            ),
        }


def _max_effect_size(session: ca.DiscoverySession) -> float:
    values = [
        abs(u.effect_size) for u in session.univariate
        if u.effect_size is not None and np.isfinite(u.effect_size)
    ] + [
        abs(i.effect_size) for i in session.interactions
        if i.effect_size is not None and np.isfinite(i.effect_size)
    ]
    return float(max(values)) if values else 0.0


def _best_candidate_p(session: ca.DiscoverySession) -> float:
    values = [c.permutation_p for c in session.candidates if c.permutation_p is not None]
    return float(min(values)) if values else 1.0


def run_null_experiment(
    observations: ObservationSet,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    inner_permutations: int = DEFAULT_INNER_PERMUTATIONS,
    seed: int = stats.DEFAULT_SEED,
    alpha: float = 0.10,
    observed_session: ca.DiscoverySession | None = None,
) -> NullExperimentResult:
    """Calibrate the ORIGINAL (v1, row-level) adaptive pipeline against
    `iterations` dependence-preserving null datasets built from `observations`.

    `observed_session` lets a caller pass the ALREADY-COMPUTED real v1
    session (e.g. from `engine/conditional_audit.py:reaudit_strategy`) so the
    real run is never re-executed here; if omitted, this function computes it
    itself with the same `inner_permutations`.
    """
    cluster_structure = cd.assign_clusters(observations.frame, observations.engine)
    if observed_session is None:
        observed_session = ca.run_discovery(
            observations, seed=seed, permutations=inner_permutations, alpha=alpha,
            dependence_aware=False,
        )

    null_iterations: list[NullIterationResult] = []
    for i in range(iterations):
        null_obs = _null_observations(observations, cluster_structure, seed=seed + 1 + i)
        null_session = ca.run_discovery(
            null_obs, seed=seed, permutations=inner_permutations, alpha=alpha,
            dependence_aware=False,
        )
        null_iterations.append(NullIterationResult(
            raw_significant=null_session.fdr["rawSignificant"],
            fdr_significant=null_session.fdr["fdrSignificant"],
            max_effect_size=_max_effect_size(null_session),
            n_candidates=len(null_session.candidates),
            best_candidate_p=_best_candidate_p(null_session),
        ))

    return NullExperimentResult(
        strategy_name=observations.strategy_name, iterations=iterations,
        inner_permutations=inner_permutations, seed=seed,
        cluster_structure=cluster_structure.to_dict(),
        null_iterations=null_iterations,
        observed_raw_significant=observed_session.fdr["rawSignificant"],
        observed_fdr_significant=observed_session.fdr["fdrSignificant"],
        observed_max_effect_size=_max_effect_size(observed_session),
        observed_n_candidates=len(observed_session.candidates),
        observed_best_candidate_p=_best_candidate_p(observed_session),
    )
