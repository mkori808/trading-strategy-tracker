"""Methodology audit: recompute ALREADY-TESTED Conditional Edge Discovery
evidence under the dependence-aware statistics (methodology v2), and record
the before/after in the permanent research ledger.

**This module runs no new search.** `reaudit_strategy` rebuilds the exact
same discovery `ObservationSet` and chronological split the original session
used (deterministic replay -- verified byte-for-byte against the stored
session JSON in the 2026-08-22 audit: identical n, identical baseline mean),
then calls `engine/conditional_analysis.py:run_discovery` TWICE on that same
discovery slice -- once with `dependence_aware=False` (reproducing the
original row-level procedure exactly) and once with the new default
`dependence_aware=True`. Univariate and interaction feature SELECTION is
identical between the two calls (verified: `plan_pairwise`/`plan_three_way`
rank by Hedges' g, which this audit does not change, not by p-value), so
this is a faithful "same hypotheses, corrected evidence" comparison, not a
new search.

**Candidates are re-scored from their STORED conditions, not regenerated.**
`generate_candidates`' greedy composite-assembly order depends on q-value
ranking, which the dependence-aware correction can reorder -- confirmed
directly: replaying full discovery under v1 vs v2 on the same data does not
always produce the same set of candidate hypothesis IDs, because a
lower-ranked single-feature clause can out-rank a previously-favored one once
its p-value is corrected. To guarantee the audit re-scores the EXACT rule
that was originally reported (not a different composite the corrected
ranking would have preferred), each of a strategy's stored candidates is
re-evaluated by calling `conditional_analysis.build_candidate` directly on
its ORIGINAL conditions with the v2 `ClusterStructure` supplied -- bypassing
`generate_candidates`'s search entirely.

**Nothing here touches a sealed final holdout, freezes anything, or
resurrects a rejected hypothesis.** Every write goes to
`conditional_methodology_audits` (append-only) via
`engine/conditional_ledger.py:record_methodology_audit`; the original
`conditional_sessions`/`conditional_hypotheses` rows are read, never written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine import conditional_analysis as ca
from engine import conditional_dependence as cd
from engine import conditional_edge
from engine import conditional_ledger as ledger
from engine import conditional_stats as stats
from engine import conditional_validation as cv

#: FDR alpha the original sessions were run at (see the stored session JSON's
#: `fdr.alpha` -- 0.10 for all four strategies audited here). Re-used rather
#: than re-read per session so a caller auditing a session whose JSON is
#: unavailable (already-cleared cache) still gets a consistent recount.
DEFAULT_AUDIT_ALPHA = 0.10


@dataclass
class CandidateAudit:
    hypothesis_id: str
    description: str
    prior: dict[str, Any] | None
    corrected: dict[str, Any] | None
    materially_weakened: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesisId": self.hypothesis_id, "description": self.description,
            "prior": self.prior, "corrected": self.corrected,
            "materiallyWeakened": self.materially_weakened,
        }


@dataclass
class StrategyAudit:
    strategy_name: str
    session_id: str
    cluster_structure: dict[str, Any]
    prior_raw_significant: int
    prior_fdr_significant: int
    corrected_raw_significant: int
    corrected_fdr_significant: int
    hypotheses_tested: int
    candidates: list[CandidateAudit]
    largest_p_value_changes: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategyName": self.strategy_name, "sessionId": self.session_id,
            "clusterStructure": self.cluster_structure,
            "priorRawSignificant": self.prior_raw_significant,
            "priorFdrSignificant": self.prior_fdr_significant,
            "correctedRawSignificant": self.corrected_raw_significant,
            "correctedFdrSignificant": self.corrected_fdr_significant,
            "hypothesesTested": self.hypotheses_tested,
            "candidates": [c.to_dict() for c in self.candidates],
            "largestPValueChanges": self.largest_p_value_changes,
        }


def _stored_candidate_conditions(stored_session: dict[str, Any]) -> list[dict[str, Any]]:
    return stored_session.get("candidates", [])


def reaudit_strategy(
    strategy_name: str,
    session_id: str,
    *,
    result: Any | None = None,
    permutations: int = 1_000,
    seed: int = stats.DEFAULT_SEED,
    alpha: float = DEFAULT_AUDIT_ALPHA,
) -> StrategyAudit:
    """Recompute one strategy's already-reported discovery evidence under
    the dependence-aware methodology, matched hypothesis-for-hypothesis
    against the ORIGINAL v1 procedure replayed on the identical data."""
    observations, _result = conditional_edge.build_observations_for(strategy_name, result=result)
    split = cv.split_observations(observations)

    session_v1 = ca.run_discovery(
        split.discovery, seed=seed, permutations=permutations, alpha=alpha,
        dependence_aware=False,
    )
    session_v2 = ca.run_discovery(
        split.discovery, seed=seed, permutations=permutations, alpha=alpha,
        dependence_aware=True,
    )

    # Univariate/interaction selection is identical between the two calls
    # (see module docstring) -- match by identity key and pair up p/q values
    # for the "largest changes" report.
    changes: list[dict[str, Any]] = []
    v1_by_feature = {u.feature: u for u in session_v1.univariate}
    for u2 in session_v2.univariate:
        u1 = v1_by_feature.get(u2.feature)
        if u1 is None or u1.permutation_p is None or u2.permutation_p is None:
            continue
        changes.append({
            "targetType": "univariate", "targetKey": u2.feature, "label": u2.label,
            "priorP": u1.permutation_p, "correctedP": u2.permutation_p,
            "priorQ": u1.q_value, "correctedQ": u2.q_value,
            "priorCiLow": u1.buckets[[b.label for b in u1.buckets].index(u1.best_label)].ci_low
            if u1.best_label in [b.label for b in u1.buckets] else None,
            "priorCiHigh": u1.buckets[[b.label for b in u1.buckets].index(u1.best_label)].ci_high
            if u1.best_label in [b.label for b in u1.buckets] else None,
            "correctedCiLow": u2.buckets[[b.label for b in u2.buckets].index(u2.best_label)].ci_low
            if u2.best_label in [b.label for b in u2.buckets] else None,
            "correctedCiHigh": u2.buckets[[b.label for b in u2.buckets].index(u2.best_label)].ci_high
            if u2.best_label in [b.label for b in u2.buckets] else None,
            "priorEffectSize": u1.effect_size, "correctedEffectSize": u2.effect_size,
            "priorRawN": u1.n_used, "correctedEffectiveN": u2.effective_n,
            "deltaP": abs(u2.permutation_p - u1.permutation_p),
        })
    v1_by_interaction = {i.features: i for i in session_v1.interactions}
    for i2 in session_v2.interactions:
        i1 = v1_by_interaction.get(i2.features)
        if i1 is None or i1.permutation_p is None or i2.permutation_p is None:
            continue
        changes.append({
            "targetType": "interaction", "targetKey": "|".join(i2.features),
            "label": " x ".join(i2.labels),
            "priorP": i1.permutation_p, "correctedP": i2.permutation_p,
            "priorQ": i1.q_value, "correctedQ": i2.q_value,
            "priorEffectSize": i1.effect_size, "correctedEffectSize": i2.effect_size,
            "priorRawN": i1.n_used, "correctedEffectiveN": i2.effective_n,
            "deltaP": abs(i2.permutation_p - i1.permutation_p),
        })
    changes.sort(key=lambda c: -c["deltaP"])

    # Candidates: re-scored from stored conditions, not regenerated -- see
    # module docstring.
    stored = ledger.load_session(session_id)
    candidate_audits: list[CandidateAudit] = []
    if stored is not None:
        cluster_structure = cd.assign_clusters(split.discovery.frame, observations.engine)
        for stored_candidate in _stored_candidate_conditions(stored):
            conditions = cv.conditions_from_payload(stored_candidate["conditions"])
            corrected = ca.build_candidate(
                split.discovery, conditions, origin=stored_candidate.get("origin", "audit"),
                seed=seed, permutations=permutations, cluster_structure=cluster_structure,
            )
            prior_p = stored_candidate.get("permutationP")
            prior_q = stored_candidate.get("qValue")
            corrected_p = corrected.permutation_p if corrected else None
            corrected_q = None  # candidate-level q-values are family-dependent; not re-derived per-candidate here
            weakened = bool(
                prior_q is not None and prior_q <= alpha
                and (corrected is None or corrected.effective_n is None
                     or corrected.effective_n < stats.HYPOTHESIS_MIN_N)
            )
            candidate_audits.append(CandidateAudit(
                hypothesis_id=stored_candidate["hypothesisId"],
                description=stored_candidate.get("description", ""),
                prior={
                    "methodologyVersion": stats.METHODOLOGY_V1_ROW_LEVEL,
                    "inferenceMethod": "iid (row-level)",
                    "rawN": stored_candidate.get("nConditioned"),
                    "effectiveN": None,
                    "pValue": prior_p, "qValue": prior_q,
                    "ciLow": stored_candidate.get("ciLow"), "ciHigh": stored_candidate.get("ciHigh"),
                },
                corrected={
                    "methodologyVersion": stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
                    "inferenceMethod": corrected.inference_method if corrected else cluster_structure.method,
                    "rawN": corrected.raw_observations if corrected else stored_candidate.get("nConditioned"),
                    "effectiveN": corrected.effective_n if corrected else None,
                    "pValue": corrected_p, "qValue": corrected_q,
                    "ciLow": corrected.ci_low if corrected else None,
                    "ciHigh": corrected.ci_high if corrected else None,
                } if corrected is not None else None,
                materially_weakened=weakened,
            ))

    return StrategyAudit(
        strategy_name=strategy_name, session_id=session_id,
        cluster_structure=cluster_structure.to_dict() if stored is not None else session_v2.cluster_structure,
        prior_raw_significant=session_v1.fdr["rawSignificant"],
        prior_fdr_significant=session_v1.fdr["fdrSignificant"],
        corrected_raw_significant=session_v2.fdr["rawSignificant"],
        corrected_fdr_significant=session_v2.fdr["fdrSignificant"],
        hypotheses_tested=session_v1.fdr["hypothesesTested"],
        candidates=candidate_audits,
        largest_p_value_changes=changes[:10],
    )


def persist_strategy_audit(
    audit: StrategyAudit, *, reason: str, hypothesis_row_id_by_candidate: dict[str, int] | None = None,
) -> list[int]:
    """Append every record in one `StrategyAudit` to the permanent ledger.

    `hypothesis_row_id_by_candidate` links a candidate's hypothesis_id to a
    real `conditional_hypotheses` row when one exists (Connors RSI2 and IBS's
    one frozen-and-rejected hypothesis each; every DM/Market-Residual-
    Momentum candidate was never frozen and gets a session-only record).
    """
    hypothesis_row_id_by_candidate = hypothesis_row_id_by_candidate or {}
    row_ids: list[int] = []

    row_ids.append(ledger.record_methodology_audit(
        strategy_name=audit.strategy_name, target_type="session",
        target_key=audit.strategy_name, session_id=audit.session_id,
        target_description=(
            f"Whole-session raw/FDR-significant recount across all "
            f"{audit.hypotheses_tested} hypotheses examined."
        ),
        prior_methodology_version=stats.METHODOLOGY_V1_ROW_LEVEL,
        prior_inference_method="iid (row-level)",
        prior_raw_n=None, prior_effective_n=None, prior_p_value=None, prior_q_value=None,
        corrected_methodology_version=stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
        corrected_inference_method=audit.cluster_structure.get("method", "unknown"),
        corrected_raw_n=audit.cluster_structure.get("nRaw"),
        corrected_effective_n=audit.cluster_structure.get("effectiveN"),
        corrected_p_value=None, corrected_q_value=None,
        reason=(
            f"{reason} Raw significant {audit.prior_raw_significant} -> "
            f"{audit.corrected_raw_significant}; FDR significant "
            f"{audit.prior_fdr_significant} -> {audit.corrected_fdr_significant}."
        ),
    ))

    for candidate in audit.candidates:
        prior = candidate.prior or {}
        corrected = candidate.corrected or {}
        row_ids.append(ledger.record_methodology_audit(
            strategy_name=audit.strategy_name, target_type="candidate",
            target_key=candidate.hypothesis_id, session_id=audit.session_id,
            hypothesis_row_id=hypothesis_row_id_by_candidate.get(candidate.hypothesis_id),
            target_description=candidate.description,
            prior_methodology_version=prior.get("methodologyVersion"),
            prior_inference_method=prior.get("inferenceMethod"),
            prior_raw_n=prior.get("rawN"), prior_effective_n=prior.get("effectiveN"),
            prior_p_value=prior.get("pValue"), prior_q_value=prior.get("qValue"),
            prior_ci_low=prior.get("ciLow"), prior_ci_high=prior.get("ciHigh"),
            corrected_methodology_version=stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
            corrected_inference_method=corrected.get("inferenceMethod", "unknown"),
            corrected_raw_n=corrected.get("rawN"), corrected_effective_n=corrected.get("effectiveN"),
            corrected_p_value=corrected.get("pValue"), corrected_q_value=corrected.get("qValue"),
            corrected_ci_low=corrected.get("ciLow"), corrected_ci_high=corrected.get("ciHigh"),
            reason=reason,
        ))
    return row_ids
