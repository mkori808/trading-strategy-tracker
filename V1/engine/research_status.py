"""Research Status + Data Blocker dashboard aggregation.

**This module computes nothing new about any strategy or research result.**
Every status below is read live from the app's existing sources of truth --
`engine/conditional_ledger.py` (Conditional Edge Discovery), `engine/
dm_mrm_forward.py` + `FROZEN_DM_MRM_VOL_SCALED.md` (the frozen overlay's own
forward-test protocol), `engine/universe_registry.py` + `engine/
pit_all_stocks.py` (dataset availability) -- and `engine/research_registry.py`
supplies only the small amount of context none of those can answer on their
own (which research question a row was chasing, what a blocked dataset would
unlock, and the final interpretation of a completed full-pipeline null
calibration). Nothing here can mark a hypothesis validated, reopen a sealed
holdout, or resurrect a rejected candidate; it only reads what already
happened and reports it plainly.

**Status normalization is deliberately conservative.** `derive_conditional_status`
never returns "Validated" from discovery-stage evidence, however FDR-
significant -- only a real `Validation passed` (and, if opened, `Holdout
passed`) row in the ledger earns that label. A hypothesis with zero frozen
rows and a session that shows real candidates blocked by the effective-N gate
reads as `Blocked - Insufficient Effective N`, not `Discovery Only`, because
that IS why nothing was frozen -- collapsing the two would hide the exact
distinction (underpowered search vs. failed idea) this dashboard exists to
preserve. The explicit exception is a registered completed null-calibration
result: when the adaptive discovery output itself was unremarkable versus
chance, that final classification takes precedence and effective N remains a
secondary note rather than the primary blocker.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from engine import conditional_ledger as ledger
from engine import conditional_stats as stats
from engine import research_registry as registry

ProjectRoot = Path(__file__).resolve().parent.parent

#: Estimated validation:discovery cluster-count ratio, read from this app's
#: own default split (`engine/conditional_validation.py:DEFAULT_SPLIT`) so
#: this stays in sync with that constant rather than duplicating its value.
#: Used ONLY to disclose an estimate of whether a candidate would clear the
#: freeze gate's effective-N floor -- never to override or recompute the
#: gate's own decision. See `derive_conditional_status`.
def _estimated_validation_ratio() -> float:
    from engine.conditional_validation import DEFAULT_SPLIT

    return DEFAULT_SPLIT[1] / DEFAULT_SPLIT[0]


_ESTIMATED_VALIDATION_RATIO = _estimated_validation_ratio()

#: The dashboard's standardized status vocabulary. The 12 literal values are
#: the minimum set this feature was asked to support; "Methodology Complete"
#: and "Research Run In Progress" are the two necessary extensions for rows
#: that are not conditional-hypothesis verdicts at all (infrastructure, and
#: an in-flight preregistered batch) -- both are still reported through the
#: same normalized shape as everything else.
StatusLabel = Literal[
    "Historical Candidate", "Audit Passed", "Frozen", "Forward Testing",
    "Too Early to Evaluate", "Discovery Only", "Validation Failed", "Rejected",
    "Validated", "No Conditional Structure Detected",
    "Blocked - Insufficient Data", "Blocked - Insufficient Effective N",
    "Blocked - Missing PIT Data", "Data Required",
    "Methodology Complete", "Research Run In Progress",
    "Active Paper / Forward Observation", "Active Paper / Configuration Mismatch",
    "Paper Forward Test", "Shadow Forward Testing", "Frozen — Forward Testing",
    "Modification Unsupported / Closed",
]

#: One concise, status-driven action -- never a suggestion to re-optimize a
#: strategy that already failed validation, and never a suggestion tied to a
#: specific number a caller might be tempted to chase.
NEXT_ACTION: dict[str, str] = {
    "Historical Candidate": "Freeze the hypothesis before running out-of-sample validation.",
    "Audit Passed": "Register a forward test with a fixed evaluation protocol.",
    "Frozen": "Begin forward-test data collection, or run chronological validation.",
    "Forward Testing": "Collect unseen forward observations; no verdict before the preregistered horizon.",
    "Too Early to Evaluate": "Continue collecting forward observations; no annualized statistic or verdict yet.",
    "Discovery Only": "Run chronological validation on the strongest preregistered candidate.",
    "No Conditional Structure Detected": "Close this research run; revisit only with a new preregistered thesis or materially better data.",
    "Validation Failed": "No further action unless a new preregistered hypothesis is proposed.",
    "Rejected": "No further action unless a new preregistered hypothesis is proposed.",
    "Validated": "Consider paper deployment or a separate implementation review -- not further tuning.",
    "Blocked - Insufficient Data": "Acquire the additional data this research question requires.",
    "Blocked - Insufficient Effective N": "Acquire longer point-in-time history to raise independent cluster count.",
    "Blocked - Missing PIT Data": "Install and validate the required point-in-time dataset.",
    "Data Required": "Acquire the required dataset before attempting this research question.",
    "Methodology Complete": "Available for use in new preregistered research batches.",
    "Research Run In Progress": "Await batch completion; do not duplicate this research in parallel.",
    "Active Paper / Forward Observation": "Continue immutable paper and normalized forward observation.",
    "Active Paper / Configuration Mismatch": "Review the persisted paper configuration; do not silently relabel it canonical.",
    "Paper Forward Test": "Continue paper-forward observation; keep forward evidence distinct from historical selection evidence.",
    "Shadow Forward Testing": "Continue automatic completed-session shadow observation; do not place orders.",
    "Frozen — Forward Testing": "Continue the frozen shadow protocol without changing its specification.",
    "Modification Unsupported / Closed": "No further action; the rejected modification remains closed.",
}


def next_action(status: str) -> str:
    return NEXT_ACTION.get(status, "Review status manually -- no mapped action for this label.")


@dataclass
class ResearchStatusRow:
    name: str
    research_type: str
    status: str
    evidence_stage: str | None
    last_completed_action: str
    development_period: str | None = None
    forward_test_start: str | None = None
    validation_status: str | None = None
    holdout_status: str | None = None
    primary_blocker: str | None = None
    methodology_version: str | None = None
    methodology_superseded: bool = False
    notes: list[str] = field(default_factory=list)
    causal_chain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["nextAction"] = next_action(self.status)
        return _camel(payload)


def _camel(value: Any) -> Any:
    from engine.conditional_stats import camel_keys

    return camel_keys(value)


# ---------------------------------------------------------------------------
# Conditional Edge Discovery rows
# ---------------------------------------------------------------------------


def _session_summary(strategy_name: str) -> dict[str, Any] | None:
    sessions = ledger.sessions_for(strategy_name)
    return sessions[0] if sessions else None


def derive_conditional_status(strategy_name: str) -> ResearchStatusRow:
    """One strategy's Conditional Edge Discovery row, derived entirely from
    the ledger (`conditional_hypotheses`, `conditional_sessions`,
    `conditional_methodology_audits`) -- see module docstring for the
    normalization rules."""
    note = registry.CONDITIONAL_RESEARCH_NOTES.get(strategy_name)
    hypotheses = ledger.hypotheses_for(strategy_name)
    session = _session_summary(strategy_name)
    audits = ledger.methodology_audits_for(strategy_name=strategy_name)
    holdout = ledger.holdout_consumption(strategy_name)
    # `sessions_for()`'s summary row is a fixed, narrow SELECT (see its own
    # docstring) that does not carry `methodologyVersion` -- only the full
    # session JSON does. Loaded once here and reused below for the
    # effective-N blocking check, rather than re-fetched twice.
    full_session = ledger.load_session(session["sessionId"]) if session else None

    notes: list[str] = [note.research_question] if note else []
    causal_chain: list[str] = []
    methodology_version = (full_session or {}).get("methodologyVersion")
    session_level_audits = [a for a in audits if a["targetType"] == "session"]
    methodology_superseded = bool(
        session is not None and not methodology_version and session_level_audits
    )
    if methodology_superseded:
        methodology_version = stats.METHODOLOGY_V1_ROW_LEVEL
        notes.append(
            f"Original discovery session predates dependence-aware statistics (methodology "
            f"{stats.METHODOLOGY_V1_ROW_LEVEL}); {len(session_level_audits)} audit record(s) "
            f"recompute this session's evidence under {stats.METHODOLOGY_V2_DEPENDENCE_AWARE}. "
            "Treat the original session's raw significance counts as superseded."
        )

    if hypotheses:
        latest = hypotheses[0]
        if latest.status in (ledger.STATUS_VALIDATION_FAILED, ledger.STATUS_HOLDOUT_FAILED):
            status: str = "Validation Failed"
        elif latest.status == ledger.STATUS_REJECTED:
            status = "Rejected"
        elif latest.status == ledger.STATUS_HOLDOUT_PASSED:
            status = "Validated"
        elif latest.status == ledger.STATUS_VALIDATION_PASSED:
            status = "Frozen"  # validation passed but holdout not yet opened
        else:
            status = "Frozen"
        if latest.status in ledger.TERMINAL_FAILURES:
            notes.append(
                f"Frozen hypothesis: {latest.describe().splitlines()[1].strip() if len(latest.describe().splitlines()) > 1 else latest.conditions}"
            )
            notes.append("Remains rejected. Do not rediscover as a novel finding.")
        return ResearchStatusRow(
            name=f"{strategy_name} Conditional Edge",
            research_type="Conditional Edge Discovery",
            status=status,
            evidence_stage="Frozen",
            last_completed_action=(
                f"{latest.status} ({latest.frozen_at[:10]})" if latest.status != ledger.STATUS_FROZEN
                else f"Hypothesis frozen ({latest.frozen_at[:10]})"
            ),
            development_period=(
                f"{session['discoveryStart'][:10]} to {session['discoveryEnd'][:10]}"
                if session else None
            ),
            validation_status=latest.status,
            holdout_status=(
                "Consumed" if holdout["consumed"] else "Sealed (not opened)"
            ),
            methodology_version=methodology_version,
            methodology_superseded=methodology_superseded,
            notes=notes,
            causal_chain=causal_chain,
        )

    if session is None:
        # No session persisted at all -- check whether this is part of the
        # currently preregistered batch before assuming "not started."
        return _batch_pending_row(strategy_name, note)

    # A session exists, no hypothesis was ever frozen. Distinguish WHY:
    # effective-N-blocked candidates vs. a session with no candidates at all.
    #
    # **What this can and cannot reconstruct exactly.** The real freeze gate
    # (`conditional_edge.py:rank_freezable`) blocks a candidate by PROJECTING
    # its discovery-side effective N onto the VALIDATION SLICE's independent-
    # cluster count -- a number that is never persisted (the validation split
    # is rebuilt fresh from the raw backtest each time `analyze()`/
    # `full_workflow()` runs, and re-deriving it here would mean re-running a
    # backtest from this dashboard, which section 11 of this feature's own
    # brief explicitly forbids). What IS always persisted is each candidate's
    # DISCOVERY-side effective N (`session.candidates[].effectiveN` for a
    # session computed after the 2026-08-22 dependence audit; the equivalent
    # `correctedEffectiveN` in `conditional_methodology_audits` for the four
    # strategies manually re-audited that day, whose original session predates
    # the field). Applying this app's own default discovery:validation split
    # ratio (`engine/conditional_validation.py:DEFAULT_SPLIT`, 60:20) to that
    # discovery-side number gives a DISCLOSED ESTIMATE of the validation-
    # projected count, not the exact figure the gate itself computed -- see
    # `_ESTIMATED_VALIDATION_RATIO` below and the note this attaches whenever
    # it drives a status.
    candidates = (full_session or {}).get("candidates", [])
    candidate_audits = {
        a["targetKey"]: a for a in audits if a["targetType"] == "candidate"
    }

    def _discovery_effective_n(candidate: dict[str, Any]) -> int | None:
        if candidate.get("effectiveN") is not None:
            return candidate["effectiveN"]
        audited = candidate_audits.get(candidate.get("hypothesisId"))
        if audited and audited.get("correctedEffectiveN") is not None:
            return audited["correctedEffectiveN"]
        return None

    evaluated = [(c, _discovery_effective_n(c)) for c in candidates]
    scored = [(c, n) for c, n in evaluated if n is not None]
    likely_blocked = [
        (c, n) for c, n in scored if n * _ESTIMATED_VALIDATION_RATIO < stats.HYPOTHESIS_MIN_N
    ]

    final_interpretation = registry.CONDITIONAL_FINAL_INTERPRETATIONS.get(strategy_name)
    if final_interpretation is not None:
        notes.extend([
            final_interpretation.primary_note,
            final_interpretation.secondary_note,
        ])
        return ResearchStatusRow(
            name=f"{strategy_name} Conditional Edge",
            research_type="Conditional Edge Discovery",
            status=final_interpretation.status,
            evidence_stage="Discovery + Null Calibration",
            last_completed_action=(
                f"Discovery and full-pipeline null calibration completed "
                f"({session['createdAt'][:10]})"
            ),
            development_period=(
                f"{session['discoveryStart'][:10]} to {session['discoveryEnd'][:10]}"
            ),
            methodology_version=methodology_version,
            methodology_superseded=methodology_superseded,
            notes=notes,
            causal_chain=[],
        )

    if candidates and scored and len(likely_blocked) == len(scored):
        cluster = full_session.get("clusterStructure") or {}
        if cluster.get("nRaw") and cluster.get("effectiveN"):
            notes.append(
                f"Discovery sample: {cluster['nRaw']} raw observations resolve to only "
                f"{cluster['effectiveN']} independent clusters ({cluster.get('method', 'unknown')})."
            )
        best_candidate, best_n = max(likely_blocked, key=lambda pair: pair[1])
        notes.append(
            f"{len(candidates)} candidate(s) generated in discovery; every one has a discovery-side "
            f"effective N low enough that, projected onto the validation slice at this app's default "
            f"60:20 split ratio, none is ESTIMATED to clear the {stats.HYPOTHESIS_MIN_N}-cluster "
            f"floor (best: {best_n} discovery-side effective clusters -> "
            f"~{best_n * _ESTIMATED_VALIDATION_RATIO:.1f} estimated validation-side). This is an "
            "estimate from the discovery-side count, not the exact validation-projected figure the "
            "freeze gate itself computes."
        )
        causal_chain = [
            f"{strategy_name} Conditional Edge discovery generated candidates whose effective N "
            "(independent overlap-based trade clusters) is estimated to fall below the "
            "freeze-eligibility floor once projected onto the validation window",
            "Longer point-in-time history is required to raise the independent-cluster count",
            "Longer clean, survivorship-free history requires the U.S. All Stocks PIT bundle",
            "That PIT bundle is currently unavailable (licensed artifacts not installed)",
        ]
        return ResearchStatusRow(
            name=f"{strategy_name} Conditional Edge",
            research_type="Conditional Edge Discovery",
            status="Blocked - Insufficient Effective N",
            evidence_stage="Discovery Only",
            last_completed_action=f"Discovery session completed ({session['createdAt'][:10]})",
            development_period=(
                f"{session['discoveryStart'][:10]} to {session['discoveryEnd'][:10]}"
            ),
            primary_blocker="Raw discovery rows materially exceed independent rebalance/trade clusters.",
            methodology_version=methodology_version,
            methodology_superseded=methodology_superseded,
            notes=notes,
            causal_chain=causal_chain,
        )

    if candidates:
        notes.append(
            f"{len(candidates)} candidate(s) generated in discovery; none cleared freeze "
            "eligibility. Effective-N could not be estimated confidently enough for every "
            "candidate to attribute this specifically to the effective-N gate -- see the "
            "session's own candidate list for detail."
        )
    else:
        notes.append("Discovery ran; no candidate cleared the sample-size and interpretability rules.")
    return ResearchStatusRow(
        name=f"{strategy_name} Conditional Edge",
        research_type="Conditional Edge Discovery",
        status="Discovery Only",
        evidence_stage="Discovery Only",
        last_completed_action=f"Discovery session completed ({session['createdAt'][:10]})",
        development_period=f"{session['discoveryStart'][:10]} to {session['discoveryEnd'][:10]}",
        methodology_version=methodology_version,
        methodology_superseded=methodology_superseded,
        notes=notes,
        causal_chain=causal_chain,
    )


def _batch_pending_row(strategy_name: str, note: registry.ConditionalResearchNote | None) -> ResearchStatusRow:
    """No persisted session exists yet. Only ever claims 'in progress' when a
    REAL preregistration file names this strategy -- otherwise reports plain
    absence, never fabricated progress."""
    prereg_path = ProjectRoot / registry.CURRENT_BATCH_PREREGISTRATION_PATH
    in_current_batch = strategy_name in registry.CURRENT_BATCH_STRATEGIES
    if in_current_batch and prereg_path.exists():
        try:
            prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prereg = {}
        if strategy_name in prereg.get("strategies", {}):
            return ResearchStatusRow(
                name=f"{strategy_name} Conditional Edge",
                research_type="Conditional Edge Discovery",
                status="Research Run In Progress",
                evidence_stage=None,
                last_completed_action=f"Batch preregistered ({prereg.get('registeredAt', 'unknown date')})",
                methodology_version=prereg.get("methodologyVersion"),
                notes=[
                    "Preregistration record found; no discovery session persisted yet. "
                    "This row updates automatically once discovery completes.",
                ] + ([note.research_question] if note else []),
            )
    return ResearchStatusRow(
        name=f"{strategy_name} Conditional Edge",
        research_type="Conditional Edge Discovery",
        status="Discovery Only",
        evidence_stage=None,
        last_completed_action="No Conditional Edge Discovery session has been run for this strategy.",
        notes=[note.research_question] if note else [],
    )


# ---------------------------------------------------------------------------
# DM/MRM Volatility-Scaled Portfolio (frozen forward test, not Conditional Edge)
# ---------------------------------------------------------------------------


def derive_dm_mrm_vol_scaled_status() -> ResearchStatusRow:
    """Reads the frozen protocol's own constants and its append-only forward
    ledger -- never re-derives a verdict, since this strategy's own document
    (`FROZEN_DM_MRM_VOL_SCALED.md`) is the frozen source of truth for its
    lifecycle labels."""
    try:
        from engine import dm_mrm_vol_scaled as vol_scaled
        from engine import dm_mrm_forward as forward

        development_cutoff = vol_scaled.DEVELOPMENT_CUTOFF.isoformat()
        decisions = forward.load_decisions()
        outcomes = forward.load_outcomes()
        forward_start = decisions[0]["decisionDate"] if decisions else None
        n_sessions = len(forward.load_forward_nav())
        review = forward.review_status(n_sessions)
        available = True
    except Exception as exc:  # noqa: BLE001 -- module import/state failure degrades, never crashes the dashboard
        return ResearchStatusRow(
            name="DM/MRM Volatility-Scaled Portfolio",
            research_type="Frozen forward-test overlay",
            status="Data Required",
            evidence_stage=None,
            last_completed_action=f"Status unavailable: {type(exc).__name__}: {exc}",
        )

    notes = [
        "Capital-allocation overlay on two already-frozen, unmodified sleeves (Dual Momentum, "
        "Market-Residual Momentum) -- no signal logic of its own.",
        "Independent audit passed prior to freezing (log-return reconstruction matched shipped "
        "NAV to machine precision; look-ahead injection test passed).",
        "Minimum 12-month evaluation horizon before any interim descriptive review; promotion "
        "past 'Forward testing' requires materially more data across multiple regimes.",
    ]
    if not decisions:
        notes.append("No forward observations recorded yet.")
    return ResearchStatusRow(
        name="DM/MRM Volatility-Scaled Portfolio",
        research_type="Frozen forward-test overlay",
        status="Too Early to Evaluate" if decisions and "Too Early" in review else "Frozen — Forward Testing",
        evidence_stage="Frozen",
        last_completed_action=f"Frozen and registered for forward testing ({development_cutoff})",
        development_period=f"2021-08-23 to {development_cutoff}",
        forward_test_start=forward_start,
        validation_status="N/A (frozen capital-allocation overlay, not a Conditional Edge hypothesis)",
        holdout_status="N/A",
        methodology_version=None,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Conditional Edge v2 infrastructure
# ---------------------------------------------------------------------------


def derive_conditional_edge_infrastructure_status() -> ResearchStatusRow:
    """Derived from evidence the ledger can actually show: how many
    dependence-aware (v2) audit records exist, confirming the methodology
    has been exercised against real research, not merely implemented."""
    all_audits = ledger.methodology_audits_for()
    v2_audits = [a for a in all_audits if a["correctedMethodologyVersion"] == stats.METHODOLOGY_V2_DEPENDENCE_AWARE]
    return ResearchStatusRow(
        name="Conditional Edge v2 infrastructure",
        research_type="Infrastructure",
        status="Methodology Complete",
        evidence_stage=None,
        last_completed_action=(
            f"Dependence audit complete: {len(v2_audits)} methodology-audit record(s) recorded "
            f"under {stats.METHODOLOGY_V2_DEPENDENCE_AWARE}"
        ),
        methodology_version=stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
        notes=[
            "Dependence-aware cluster bootstrap/permutation (rebalance-cluster and overlap-block "
            "inference), effective-N freeze gating, full-pipeline adaptive-search null "
            "calibration, Benjamini-Hochberg/FDR reporting, chronological discovery/validation/"
            "final-holdout split with purge and embargo, immutable append-only research ledger, "
            "sealed final holdout never opened except after validation passes.",
            "Sound after corrections (2026-08-22 dependence audit). Not redesigned for new "
            "research runs -- new runs use it as-is.",
        ],
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def derive_forward_operation_rows() -> list[ResearchStatusRow]:
    """Operational/shadow lifecycle rows, distinct from hypothesis research."""
    from engine import dm_mrm_forward as forward

    stack = forward.forward_stack_status()
    paper = stack.get("paperAutomation") or {}
    identity = paper.get("identity") or {}
    fingerprint_ok = bool(identity.get("fingerprintMatches"))
    dm_status = (
        "Paper Forward Test" if paper.get("enabled") and fingerprint_ok
        else "Active Paper / Configuration Mismatch" if paper.get("enabled")
        else "Data Required"
    )
    rows = [ResearchStatusRow(
        name=identity.get("displayName", "Dual Momentum · Optimized paper variant"),
        research_type="Operational paper strategy",
        status=dm_status,
        evidence_stage="Paper execution",
        last_completed_action=(f"Last completed paper run {paper['lastCompletedRun']['date']}" if paper.get("lastCompletedRun") else "No completed paper run"),
        primary_blocker=(None if fingerprint_ok else identity.get("fingerprintReason", "Paper identity unavailable.")),
        notes=[
            identity.get("provenance", "Promoted historical configuration."),
            identity.get("selectionBiasNote", "Historical selection evidence is not independent forward validation."),
            "This is not canonical 189-session/monthly Dual Momentum.",
            "Alpaca account equity is separate from normalized research NAV.",
        ],
    )]
    sessions = len(forward.load_forward_nav())
    last = stack["series"][0]["lastUpdate"]
    detail_notes = {
        "Canonical Dual Momentum · 189D/Monthly": [
            "Frozen specification: 189-session lookback, monthly rebalance, top five.",
            "Normalized forward NAV and Alpaca paper account equity are separate measures.",
        ],
        "Market-Residual Momentum": [
            "Frozen canonical MRM research sleeve; normalized forward NAV only.",
        ],
        "Fixed 50/50 DM/MRM": [
            "Fixed 50% canonical DM / 50% canonical MRM allocation; research shadow only.",
        ],
    }
    for name in detail_notes:
        rows.append(ResearchStatusRow(
            name=name,
            research_type="Research shadow strategy",
            status="Shadow Forward Testing",
            evidence_stage="Forward observation",
            last_completed_action=(f"{sessions} completed sessions; last {last}" if last else "Registered; no post-cutoff session yet"),
            development_period=f"through {forward.DEVELOPMENT_CUTOFF.isoformat()}",
            notes=[*detail_notes[name], "Research-only. Cannot place Alpaca orders."],
        ))
    return rows


def derive_closed_modification_rows() -> list[ResearchStatusRow]:
    rows = []
    for name, item in registry.REGISTERED_STRATEGY_MODIFICATIONS.items():
        path = ProjectRoot / item.result_ledger_path
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        prereg_path = ProjectRoot / item.preregistration_path
        prereg = json.loads(prereg_path.read_text(encoding="utf-8")) if prereg_path.exists() else {}
        rows.append(ResearchStatusRow(
            name=name,
            research_type="Strategy modification",
            status="Modification Unsupported / Closed" if payload.get("canonicalReplacementAuthorized") is False else "Data Required",
            evidence_stage="Closed",
            last_completed_action=payload.get("verdict", "Result ledger unavailable"),
            validation_status=payload.get("validationStatus"),
            notes=[
                prereg.get("researchQuestion", "One-rebalance winner-grace modification."),
                f"Final result: {payload.get('verdict', 'unavailable')}.",
                f"Preregistration: {item.preregistration_path}",
                f"Result ledger: {item.result_ledger_path}",
                f"Historical result: {payload.get('historicalDevelopmentResult', 'unavailable')}",
                "Canonical control unchanged; this modification must not be reintroduced.",
            ],
        ))
    return rows


def build_status_dashboard() -> dict[str, Any]:
    rows: list[ResearchStatusRow] = []
    for strategy_name in registry.COMPLETED_CONDITIONAL_RESEARCH:
        rows.append(derive_conditional_status(strategy_name))
    for strategy_name in registry.CURRENT_BATCH_STRATEGIES:
        rows.append(derive_conditional_status(strategy_name))
    rows.append(derive_dm_mrm_vol_scaled_status())
    rows.extend(derive_forward_operation_rows())
    rows.extend(derive_closed_modification_rows())
    rows.append(derive_conditional_edge_infrastructure_status())

    summary = {
        "activeResearchRuns": sum(1 for r in rows if r.status == "Research Run In Progress"),
        "frozenForwardTestCandidates": sum(
            1 for r in rows if r.status in ("Forward Testing", "Frozen — Forward Testing", "Too Early to Evaluate")
        ),
        "validatedStrategies": sum(1 for r in rows if r.status == "Validated"),
        "rejectedHypotheses": sum(1 for r in rows if r.status in ("Validation Failed", "Rejected")),
        "dataBlockedResearchItems": sum(1 for r in rows if r.status.startswith("Blocked") or r.status == "Data Required"),
        "methodologyCompleteSystems": sum(1 for r in rows if r.status == "Methodology Complete"),
    }
    return {
        "rows": [r.to_dict() for r in rows],
        "summary": _camel(summary),
        "statusLabels": list(StatusLabel.__args__),
    }


# ---------------------------------------------------------------------------
# Data blockers
# ---------------------------------------------------------------------------


@dataclass
class DataBlockerRow:
    dataset: str
    status: str
    intended_coverage: str | None
    actual_availability: str
    pit_safe: bool | None
    survivorship_free: bool | None
    missing_artifacts: list[str]
    unlocks: list[str]
    blocks_research: list[str]
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return _camel(asdict(self))


def _universe_blocker_row(universe_id: str, label: str) -> DataBlockerRow:
    from engine.universe_registry import registered_universe

    definition = registered_universe(universe_id)
    interp = registry.DATA_BLOCKER_NOTES.get(universe_id, registry.DataBlockerNote((), "low"))
    intended_coverage = (
        f"{definition.coverage_start} to {definition.coverage_end}"
        if definition.coverage_start or definition.coverage_end else None
    )
    missing_artifacts: list[str] = []
    pit_safe: bool | None = None
    survivorship_free: bool | None = None
    if universe_id == "us_all_stocks_pit":
        from engine.pit_all_stocks import inspect_dataset

        pit_status = inspect_dataset()
        missing_artifacts = list(pit_status.missing_artifacts)
        pit_safe = pit_status.ready
        survivorship_free = pit_status.ready
    elif universe_id == "sp500_pit":
        pit_safe = False
        survivorship_free = False
    elif universe_id == "dow_pit":
        pit_safe = False  # partial reconstruction, not a full licensed PIT replay
        survivorship_free = False

    actual_availability = (
        "Not runnable -- declared coverage above is NOT installed/usable."
        if not definition.runnable
        else "Runnable, with the caveat below." if universe_id == "dow_pit"
        else "Runnable."
    )
    return DataBlockerRow(
        dataset=label,
        status="Blocked" if not definition.runnable else "Partial / caveated" if universe_id == "dow_pit" else "Available",
        intended_coverage=intended_coverage,
        actual_availability=definition.unavailable_reason or actual_availability,
        pit_safe=pit_safe,
        survivorship_free=survivorship_free,
        missing_artifacts=missing_artifacts,
        unlocks=list(interp.unlocks),
        blocks_research=list(interp.blocks_research),
        severity=interp.severity,
    )


def data_blockers() -> list[dict[str, Any]]:
    rows = [
        _universe_blocker_row("us_all_stocks_pit", "U.S. All Stocks PIT bundle"),
        _universe_blocker_row("sp500_pit", "S&P 500 PIT"),
        _universe_blocker_row("dow_pit", "Dow PIT"),
    ]
    # Dow gets an explicit caveat note even though it is runnable -- section 5
    # of the brief: "should not be represented as equivalent to a clean
    # licensed PIT universe."
    dow = next(r for r in rows if r.dataset == "Dow PIT")
    dow.actual_availability += (
        " Uses a static 29-symbol execution roster with partial historical reconstruction, "
        "not a full survivorship-free point-in-time replay -- acceptable for descriptive work, "
        "not equivalent to a licensed PIT universe."
    )
    return [r.to_dict() for r in rows]
