"""Conditional Edge Discovery: the workflow that ties the pieces together.

    existing strategy
      -> historical candidate trades/signals   (engine/runner.py, unchanged)
      -> point-in-time features at signal time (engine/pit_features.py)
      -> observation dataset                   (engine/observations.py)
      -> conditional analysis                  (engine/conditional_analysis.py)
      -> candidate hypotheses                  (engine/conditional_analysis.py)
      -> frozen hypothesis                     (engine/conditional_ledger.py)
      -> unseen-data validation                (engine/conditional_validation.py)
      -> conditioned strategy + prop economics (engine/conditional_validation.py)
      -> accepted / rejected conditional edge

This module owns the ORDER and the guardrails between those steps, and holds
no analysis of its own. Two properties it is responsible for:

**Discovery never sees validation or holdout data.** `analyze()` runs the
search on `split.discovery` only. The validation slice is measured exactly
once per frozen hypothesis, and the final holdout stays sealed until someone
explicitly reveals it and accepts a permanent consumption record.

**Nothing here can change a strategy.** Every backtest it triggers goes
through `engine/runner.py` with `persist=False`, so a conditional experiment
can never overwrite a canonical leaderboard row -- the same discipline
`engine/compare_filters.py` and `engine/compare_universe.py` already follow,
applied to a feature that makes running experiments trivial and frequent.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

import pandas as pd

from engine import conditional_analysis, conditional_ledger, conditional_stats, conditional_validation
from engine import observations as observations_module
from engine import pit_features
from engine.conditional_analysis import Condition, DiscoverySession
from engine.observations import ObservationSet
from engine.conditional_validation import ResearchSplit

#: Strategies this feature has been exercised against. Not a restriction --
#: `analyze()` accepts any strategy either supported engine can run -- but the
#: named starting set from the research brief, and what the CLI defaults to.
INITIAL_STRATEGIES: tuple[str, ...] = (
    "Connors Mean Reversion (RSI2)",
    "Internal Bar Strength (IBS)",
    "Market-Residual Momentum",
    "Dual Momentum",
)

CROSS_SECTIONAL_NAMES: frozenset[str] = frozenset(
    {"Dual Momentum", "52-Week-High Momentum", "Market-Residual Momentum"}
)


def engine_for(strategy_name: str) -> str:
    return "cross_sectional" if strategy_name in CROSS_SECTIONAL_NAMES else "standard"


def _run_strategy(strategy_name: str) -> Any:
    """Produce the strategy's own historical result. Never persisted.

    Deliberately calls the same entry points the leaderboard uses rather than
    reconstructing a run, so the observations describe the strategy as the
    rest of the app measures it -- not a lookalike configured here.
    """
    from engine import runner

    if engine_for(strategy_name) == "cross_sectional":
        return runner.run_cross_sectional(strategy_name, persist=False)
    return runner.run_backtest(strategy_name, persist=False)


@dataclass
class ConditionalStudy:
    """Everything one discovery pass produced, ready to serialise."""

    strategy_name: str
    engine: str
    observations: ObservationSet
    split: ResearchSplit
    session: DiscoverySession
    feature_availability: dict[str, Any]
    models: list[dict[str, Any]] = field(default_factory=list)
    prior_results: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategyName": self.strategy_name,
            "engine": self.engine,
            "totalObservations": len(self.observations),
            "split": self.split.to_dict(),
            "session": self.session.to_dict(),
            "featureAvailability": self.feature_availability,
            "models": self.models,
            "priorResults": self.prior_results,
            "warnings": self.warnings,
            "holdoutConsumption": conditional_ledger.holdout_consumption(self.strategy_name),
            "disclosure": DISCLOSURE,
        }


#: Shown wherever a discovery result is displayed. Not decoration: the numbers
#: in a discovery session are in-sample by construction, and a reader arriving
#: at a table of conditional expectancies has to be told that before reading
#: any of them.
DISCLOSURE = {
    "inSample": (
        "Every number in the discovery session is measured on the sample that suggested it. "
        "None of it is evidence of an edge. A candidate becomes evidence only after being "
        "frozen and measured on data it has never touched."
    ),
    "multipleTesting": (
        "The hypotheses-examined count covers every relationship tested, including those not "
        "shown. FDR q-values are computed across that whole family."
    ),
    "sampleSize": (
        "A spectacular conditional result on a small subset is the expected appearance of "
        "noise, not a rare finding. Bucket counts are shown beside every expectancy."
    ),
}


def build_observations_for(
    strategy_name: str, *, result: Any | None = None
) -> tuple[ObservationSet, Any]:
    """Observation dataset for `strategy_name`, running the backtest if needed."""
    engine = engine_for(strategy_name)
    result = result if result is not None else _run_strategy(strategy_name)
    observations = observations_module.build_observations(result, engine=engine)
    return observations, result


def analyze(
    strategy_name: str,
    *,
    result: Any | None = None,
    seed: int = conditional_stats.DEFAULT_SEED,
    permutations: int = 1_000,
    alpha: float = 0.10,
    fractions: tuple[float, float, float] = conditional_validation.DEFAULT_SPLIT,
    include_models: bool = False,
    include_exploratory_features: bool = True,
    persist: bool = True,
) -> ConditionalStudy:
    """Run one full discovery pass on a strategy's DISCOVERY sample only."""
    observations, result = build_observations_for(strategy_name, result=result)
    split = conditional_validation.split_observations(observations, fractions=fractions)
    session = conditional_analysis.run_discovery(
        split.discovery, seed=seed, permutations=permutations, alpha=alpha,
        include_exploratory_features=include_exploratory_features,
    )

    warnings_out = list(observations.warnings)
    if len(split.discovery) < conditional_stats.WARN_N:
        warnings_out.append(
            f"Discovery sample is {len(split.discovery)} observations. Below "
            f"{conditional_stats.WARN_N} the conditional tables are describing noise as "
            "reliably as signal."
        )

    models: list[dict[str, Any]] = []
    if include_models:
        from engine import conditional_models

        models = [d.to_dict() for d in conditional_models.fit_diagnostics(
            split.discovery, seed=seed
        )]

    prior: dict[str, Any] = {}
    for candidate in session.candidates:
        record = conditional_ledger.previously_tested(
            strategy_name, observations.outcome_column,
            [c.to_dict() for c in candidate.conditions],
        )
        if record:
            prior[candidate.hypothesis_id] = record
            candidate.warnings.append(
                f"Previously tested: frozen {record['frozenAt'][:10]}, current status "
                f"'{record['status']}'."
            )

    study = ConditionalStudy(
        strategy_name=strategy_name, engine=observations.engine,
        observations=observations, split=split, session=session,
        feature_availability=pit_features.availability_report(observations.engine),
        models=models, prior_results=prior, warnings=warnings_out,
    )
    if persist:
        conditional_ledger.record_session(session, split=split.to_dict())
    return study


def freeze_candidate(
    study: ConditionalStudy,
    hypothesis_id: str,
    *,
    minimum_effect: float | None = None,
    minimum_observations: int = conditional_stats.HYPOTHESIS_MIN_N,
) -> conditional_ledger.FrozenHypothesis:
    """Preregister one discovered candidate against the unseen windows.

    The pass bar is set HERE, before any out-of-sample number exists. Its
    default is the same economic-materiality floor the discovery stage uses,
    so a rule cannot be frozen against a bar chosen to fit whatever the
    validation sample later shows.
    """
    candidate = next(
        (c for c in study.session.candidates if c.hypothesis_id == hypothesis_id), None
    )
    if candidate is None:
        raise ValueError(
            f"No candidate {hypothesis_id!r} in session {study.session.session_id}."
        )
    boundaries = study.split.boundaries
    return conditional_ledger.freeze(
        strategy_name=study.strategy_name, engine=study.engine,
        outcome_column=candidate.outcome_column,
        conditions=[c.to_dict() for c in candidate.conditions],
        expected_direction="higher" if candidate.improvement >= 0 else "lower",
        primary_metric=study.observations.outcome_label,
        minimum_effect=(
            minimum_effect if minimum_effect is not None
            else conditional_analysis.minimum_meaningful(candidate.outcome_column)
        ),
        minimum_observations=minimum_observations,
        validation_start=boundaries.get("discoveryEnd"),
        validation_end=boundaries.get("validationEnd"),
        holdout_start=boundaries.get("validationEnd"),
        holdout_end=boundaries.get("last"),
        discovery_session_id=study.session.session_id,
        discovery_start=candidate.discovery_start, discovery_end=candidate.discovery_end,
        discovery_observations=candidate.n_conditioned,
        discovery_mean=candidate.conditional_mean,
        discovery_baseline_mean=candidate.baseline_mean,
    )


def validate_frozen(
    hypothesis: conditional_ledger.FrozenHypothesis,
    study: ConditionalStudy,
    *,
    seed: int = conditional_stats.DEFAULT_SEED,
    permutations: int = 1_000,
    record: bool = True,
) -> dict[str, Any]:
    """Measure a frozen hypothesis on the validation slice and walk-forward.

    The final holdout is NOT touched here. Passing validation is what earns a
    hypothesis the right to consume the holdout, and combining the two would
    remove the only stage at which a rule can fail cheaply.
    """
    conditions = conditional_validation.conditions_from_payload(hypothesis.conditions)
    integrity = conditional_ledger.verify_integrity(hypothesis)
    result = conditional_validation.evaluate(
        study.split.validation, conditions,
        minimum_effect=hypothesis.minimum_effect,
        minimum_observations=hypothesis.minimum_observations,
        expected_direction=hypothesis.expected_direction,
        seed=seed, permutations=permutations, label="validation",
    )
    result["walkForward"] = conditional_validation.walk_forward(
        study.observations, conditions,
        minimum_effect=hypothesis.minimum_effect,
        expected_direction=hypothesis.expected_direction, seed=seed,
    )
    result["integrity"] = integrity
    result["decomposition"] = conditional_validation.decompose_filter_effect(
        study.split.validation, conditions
    )
    if record:
        conditional_ledger.record_validation(
            hypothesis, stage="validation", passed=result["passed"], result=result,
            conclusion=result["conclusion"],
            new_status=(
                conditional_ledger.STATUS_VALIDATION_PASSED if result["passed"]
                else conditional_ledger.STATUS_VALIDATION_FAILED
            ),
        )
    return result


def consume_final_holdout(
    hypothesis: conditional_ledger.FrozenHypothesis,
    study: ConditionalStudy,
    *,
    reason: str,
    seed: int = conditional_stats.DEFAULT_SEED,
    permutations: int = 1_000,
    record: bool = True,
) -> dict[str, Any]:
    """Open the final holdout for one hypothesis. Permanently recorded."""
    conditions = conditional_validation.conditions_from_payload(hypothesis.conditions)
    holdout = study.split.reveal_final_holdout(
        reason=reason, hypothesis_key=hypothesis.hypothesis_key
    )
    result = conditional_validation.evaluate(
        holdout, conditions, minimum_effect=hypothesis.minimum_effect,
        minimum_observations=hypothesis.minimum_observations,
        expected_direction=hypothesis.expected_direction,
        seed=seed, permutations=permutations, label="final holdout",
    )
    result["consumptionReason"] = reason
    if record:
        conditional_ledger.record_validation(
            hypothesis, stage="holdout", passed=result["passed"], result=result,
            conclusion=result["conclusion"],
            new_status=(
                conditional_ledger.STATUS_HOLDOUT_PASSED if result["passed"]
                else conditional_ledger.STATUS_HOLDOUT_FAILED
            ),
        )
    return result


def conditioned_comparison(
    hypothesis: conditional_ledger.FrozenHypothesis,
    *,
    original_result: Any | None = None,
    prop_scenario: str = "moderate",
    prop_paths: int = 2_000,
    include_prop: bool = True,
) -> dict[str, Any]:
    """Build the conditioned strategy and compare it with the original.

    Only supported for the per-symbol engine right now. The cross-sectional
    engine allocates weights across a ranked universe, so "drop this holding"
    is not a filter but a re-allocation decision -- the freed weight has to go
    somewhere, and every choice (cash, redistribute, next-ranked name) is a
    different strategy rather than the same one conditioned. Reported as
    unsupported rather than resolved with an arbitrary convention.
    """
    if hypothesis.engine != "standard":
        return {
            "available": False,
            "reason": (
                "Conditioned-strategy construction is implemented for the per-symbol engine "
                "only. Dropping a holding from a cross-sectional rebalance leaves weight that "
                "must be reallocated, and every reallocation rule (hold cash, redistribute "
                "pro-rata, promote the next-ranked name) is a DIFFERENT strategy rather than "
                "the same one conditioned. The conditional analysis above still applies; only "
                "the rebuilt-equity-curve comparison does not."
            ),
        }
    conditions = conditional_validation.conditions_from_payload(hypothesis.conditions)
    original = original_result if original_result is not None else _run_strategy(
        hypothesis.strategy_name
    )
    conditioned = conditional_validation.run_conditioned_backtest(
        hypothesis.strategy_name, conditions
    )
    original_observations = observations_module.build_observations(original, engine="standard")
    conditioned_observations = observations_module.build_observations(
        conditioned, engine="standard"
    )
    payload: dict[str, Any] = {
        "available": True,
        "comparison": conditional_validation.compare_strategies(
            original, conditioned,
            original_observations=original_observations,
            conditioned_observations=conditioned_observations,
        ),
        "decomposition": conditional_validation.decompose_filter_effect(
            original_observations, conditions
        ),
    }
    if include_prop:
        try:
            payload["prop"] = conditional_validation.prop_comparison(
                original, conditioned, scenario=prop_scenario, n_paths=prop_paths,
            )
        except Exception as exc:  # noqa: BLE001 -- prop is additive evidence, never a blocker
            payload["prop"] = {
                "available": False, "reason": f"{type(exc).__name__}: {exc}",
            }
    return payload


def full_workflow(
    strategy_name: str,
    *,
    seed: int = conditional_stats.DEFAULT_SEED,
    permutations: int = 1_000,
    freeze_top: int = 1,
    consume_holdout: bool = False,
    holdout_reason: str = "",
    include_conditioned: bool = True,
    include_prop: bool = True,
    include_models: bool = False,
    persist: bool = True,
) -> dict[str, Any]:
    """Discovery -> freeze -> validate -> (holdout) -> conditioned -> verdict.

    `freeze_top` is how many discovered candidates get preregistered. It
    defaults to ONE. Freezing every candidate would silently re-run the
    multiple-comparison problem at the validation stage, where there is no
    correction to catch it -- so the count is a deliberate, visible knob
    rather than "validate everything and see what passes".
    """
    study = analyze(
        strategy_name, seed=seed, permutations=permutations,
        include_models=include_models, persist=persist,
    )
    outcome = study.observations.outcome_column
    payload: dict[str, Any] = {
        "study": study.to_dict(),
        "hypotheses": [],
        "verdict": None,
    }
    ranked, underpowered = rank_freezable(study)
    payload["notFrozen"] = underpowered
    if not ranked:
        payload["verdict"] = conditional_validation.ConditionalVerdict(
            conditional_validation.VERDICT_NONE,
            "No candidate hypothesis met the minimum sample and interpretability rules.",
            [], [
                f"{study.session.accounting.total_examined} relationships were examined on "
                f"{len(study.split.discovery)} discovery observations; none produced an "
                "interpretable rule that could clear the sample floor on the validation "
                "window."
            ],
            {"hypothesesExamined": study.session.accounting.total_examined},
        ).to_dict()
        return payload

    verdicts: list[dict[str, Any]] = []
    for candidate in ranked[:max(freeze_top, 0)]:
        conditions_payload = [c.to_dict() for c in candidate.conditions]
        prior = conditional_ledger.previously_tested(strategy_name, outcome, conditions_payload)
        if prior and not prior["previouslyRejected"]:
            frozen = conditional_ledger.get(prior["latestRowId"])
        elif prior:
            verdicts.append({
                "candidate": candidate.to_dict(),
                "previouslyTested": prior,
                "verdict": conditional_validation.ConditionalVerdict(
                    conditional_validation.VERDICT_NONE,
                    "Previously tested and rejected",
                    [], [f"Frozen {prior['frozenAt'][:10]}, status '{prior['status']}': "
                         f"{prior.get('reason') or 'no reason recorded'}."],
                    {"previouslyRejected": True},
                ).to_dict(),
            })
            continue
        else:
            frozen = freeze_candidate(study, candidate.hypothesis_id)

        validation = validate_frozen(
            frozen, study, seed=seed, permutations=permutations, record=persist
        )
        holdout = None
        if consume_holdout and validation["passed"]:
            holdout = consume_final_holdout(
                frozen, study,
                reason=holdout_reason or (
                    f"Hypothesis {frozen.hypothesis_key} passed validation; final confirmation."
                ),
                seed=seed, permutations=permutations, record=persist,
            )
        conditioned = None
        if include_conditioned and validation["passed"]:
            conditioned = conditioned_comparison(
                frozen, include_prop=include_prop
            )
        exploratory = any(
            not pit_features.FEATURES[c.feature].discovery_eligible
            or not pit_features.FEATURES[c.feature].pit_safe
            for c in candidate.conditions
            if c.feature in pit_features.FEATURES
        )
        verdict = conditional_validation.conditional_verdict(
            discovery=candidate.to_dict(), validation=validation, holdout=holdout,
            walk_forward_result=validation.get("walkForward"),
            fdr_significant=candidate.fdr_significant,
            hypotheses_examined=study.session.fdr.get("hypothesesTested"),
            integrity=validation.get("integrity"),
            exploratory_features_used=exploratory,
        )
        verdicts.append({
            "candidate": candidate.to_dict(),
            "frozen": frozen.to_dict(),
            "validation": validation,
            "holdout": holdout,
            "conditioned": conditioned,
            "verdict": verdict.to_dict(),
        })

    payload["hypotheses"] = verdicts
    payload["verdict"] = _study_verdict(study, verdicts)
    return payload



def rank_freezable(
    study: ConditionalStudy, *, minimum_observations: int = conditional_stats.HYPOTHESIS_MIN_N
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Order candidates for freezing, and separate out the ones that cannot win.

    **Methodology v2 (2026-08-22 dependence audit): the gate is evaluated on
    EFFECTIVE N (independent clusters), never on raw retained rows.** Measured
    directly on this app's own strategies: Dual Momentum and Market-Residual
    Momentum's ~172-175 raw discovery rows resolve to only ~35 independent
    rebalance clusters (five securities held at the same rebalance share one
    outcome interval and, for regime-level features, one feature value --
    they are one observation of that month, not five). A raw-row floor of 30
    would have looked satisfiable on subsets that, in cluster terms, retain
    single digits of independent evidence -- exactly the failure section 12
    of the dependence audit calls out ("40 security rows generated by 8
    monthly rebalances must NOT satisfy a 30-observation requirement").

    A rule retaining `retention_pct` of signals retains roughly that same
    SHARE of clusters too, so this projects the candidate's own discovery-side
    `effective_n` (independent clusters actually contributing an in-bucket
    row -- see `engine/conditional_dependence.py:dependence_aware_evaluate`)
    onto the validation slice's cluster count. If that projection falls below
    the reliability floor, the rule CANNOT pass validation regardless of its
    merit, so it is reported under `notFrozen` with the arithmetic rather
    than silently dropped -- freezing it anyway would burn a validation slice
    on a test whose result is already determined, and would leave a "failed
    validation" record that reads as evidence against the rule when it is
    really evidence about the sample.

    A candidate computed under the pre-audit methodology (`effective_n is
    None`, i.e. `dependence_aware=False` was used to produce this study) has
    no cluster information to project and falls back to the original raw-row
    projection -- reported as such in each blocked entry's `inferenceMethod`.

    Among the survivors, ordering is q-value first, then the LARGER effective
    N, then effect size. Preferring the larger effective N (not raw n)
    matters: sorting by raw improvement alone systematically promotes the
    smallest, noisiest clusters, which is the exact selection bias this whole
    feature exists to resist.
    """
    from engine import conditional_dependence as cd

    validation_frame = study.split.validation.frame
    validation_clusters = cd.assign_clusters(validation_frame, study.engine)
    freezable: list[Any] = []
    blocked: list[dict[str, Any]] = []
    for candidate in study.session.candidates:
        if candidate.effective_n is not None and candidate.n_conditioned:
            cluster_share = candidate.effective_n / max(
                cd.assign_clusters(study.split.discovery.frame, study.engine).n_clusters, 1
            )
            projected = validation_clusters.effective_n * cluster_share
            basis = f"{validation_clusters.effective_n} independent {validation_clusters.method} clusters"
            inference_method = candidate.inference_method
        else:
            validation_raw_n = len(study.split.validation.frame)
            projected = validation_raw_n * candidate.retention_pct / 100.0
            basis = f"{validation_raw_n} raw rows (no cluster information available)"
            inference_method = "iid (row-level, pre-dependence-audit)"
        if projected < minimum_observations:
            blocked.append({
                "hypothesisId": candidate.hypothesis_id,
                "description": candidate.describe(),
                "retentionPct": candidate.retention_pct,
                "projectedValidationObservations": projected,
                "minimumObservations": minimum_observations,
                "inferenceMethod": inference_method,
                "reason": (
                    f"Retaining {candidate.retention_pct:.1f}% of signals projects to about "
                    f"{projected:.1f} EFFECTIVE observations on a validation window of "
                    f"{basis}, below the {minimum_observations} required. This rule cannot "
                    "pass validation regardless of its merit, so it is not frozen."
                ),
            })
            continue
        freezable.append(candidate)
    freezable.sort(
        key=lambda c: (
            c.q_value if c.q_value is not None else 1.0,
            -(c.effective_n if c.effective_n is not None else c.n_conditioned),
            -abs(c.improvement),
        )
    )
    return freezable, blocked


def _study_verdict(study: ConditionalStudy, verdicts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The strategy-level answer, which is allowed to be 'no'.

    "We examined N relationships and none survived" is a result, and it is
    reported in the same shape and with the same prominence as a positive one.
    """
    ranked_order = [
        conditional_validation.VERDICT_VALIDATED,
        conditional_validation.VERDICT_PROMISING,
        conditional_validation.VERDICT_WEAK,
        conditional_validation.VERDICT_INSUFFICIENT,
        conditional_validation.VERDICT_NONE,
    ]
    best = None
    for name in ranked_order:
        for entry in verdicts:
            if entry["verdict"]["verdict"] == name:
                best = entry
                break
        if best:
            break
    examined = study.session.fdr.get("hypothesesTested", study.session.accounting.total_examined)
    if best is None:
        return {
            "verdict": conditional_validation.VERDICT_NONE,
            "headline": conditional_validation.VERDICT_NONE,
            "hypothesesExamined": examined,
            "reasons": [],
            "blockers": ["No hypothesis was evaluated."],
        }
    payload = dict(best["verdict"])
    payload["hypothesesExamined"] = examined
    payload["fdrSignificant"] = study.session.fdr.get("fdrSignificant")
    payload["rawSignificant"] = study.session.fdr.get("rawSignificant")
    payload["discoveryObservations"] = len(study.split.discovery)
    payload["validationObservations"] = len(study.split.validation)
    return payload
