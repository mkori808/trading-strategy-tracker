"""The research ledger for Conditional Edge Discovery: freezing, versioning,
persistence, and the permanent record of what has already failed.

Three jobs, all of which exist to stop the same mistake being made twice.

**1. Freezing.**  A candidate hypothesis becomes a `FrozenHypothesis` only
through `freeze()`, which writes the exact conditions, the feature contract
they depend on, the discovery sample they came from, the expected direction,
the primary metric, the minimum effect that would count, and the validation
and holdout windows -- all before any out-of-sample number exists. A
`contract_hash` over those fields is stored alongside them, so
`verify_integrity()` can prove later that the rule being validated is the
rule that was frozen.

**2. Versioning instead of editing.**  `freeze()` refuses to overwrite an
existing version. Changing a threshold produces version 2 via `amend()`, with
version 1 preserved and marked superseded. This matters because the whole
value of preregistration is that the rule could not move after seeing the
result; a mutable row would make every downstream q-value a fiction.

**3. Preserving negative results.**  Nothing is ever deleted. A hypothesis
that fails validation keeps its row, its result, and its reason, and
`previously_tested()` surfaces it the next time an equivalent rule is
proposed. Section 30 of the research brief calls this out specifically: this
application is a research lab, not a strategy marketing tool, and "we already
tried IBS in a bull regime on high volume and it failed" is a finding.

**Final-holdout consumption.**  `mark_holdout_consumed()` records that a
strategy's untouched final holdout has been looked at. Once consumed it is no
longer untouched, and `holdout_consumption()` reports that fact permanently --
the honest alternative to quietly re-using it and calling the second look a
confirmation.

Storage is `logs/runs.db` (schema in `engine/logging_db.py`), the same
database `research_experiments` already lives in.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from engine import logging_db
from engine import pit_features
from engine.conditional_stats import CURRENT_METHODOLOGY_VERSION, camel_keys

#: Status vocabulary. Deliberately includes both failure terminals -- a
#: hypothesis that failed validation and one that failed the final holdout are
#: different facts, and collapsing them would lose the distinction between
#: "never made it out of the lab" and "looked real until the last test".
STATUS_FROZEN = "Frozen"
STATUS_VALIDATION_PASSED = "Validation passed"
STATUS_VALIDATION_FAILED = "Validation failed"
STATUS_HOLDOUT_PASSED = "Holdout passed"
STATUS_HOLDOUT_FAILED = "Holdout failed"
STATUS_REJECTED = "Rejected"
STATUS_SUPERSEDED = "Superseded"

TERMINAL_FAILURES = frozenset(
    {STATUS_VALIDATION_FAILED, STATUS_HOLDOUT_FAILED, STATUS_REJECTED}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


@dataclass(frozen=True)
class FrozenHypothesis:
    """A preregistered conditional rule. Immutable by construction.

    `contract_hash` covers every field that defines what will be tested and
    what would count as a pass. It deliberately does NOT cover `status` or
    the validation results, which are written after the fact -- otherwise
    recording an outcome would look like tampering with the contract.
    """

    row_id: int | None
    hypothesis_key: str
    version: int
    frozen_at: str
    strategy_name: str
    engine: str
    outcome_column: str
    conditions: list[dict[str, Any]]
    feature_contract: dict[str, Any]
    discovery_session_id: str | None
    discovery_start: str | None
    discovery_end: str | None
    discovery_observations: int | None
    discovery_mean: float | None
    discovery_baseline_mean: float | None
    expected_direction: str
    primary_metric: str
    minimum_effect: float
    minimum_observations: int
    validation_start: str | None
    validation_end: str | None
    holdout_start: str | None
    holdout_end: str | None
    status: str
    reason: str | None
    supersedes: int | None
    superseded_by: int | None
    contract_hash: str

    def describe(self) -> str:
        clauses = "\n".join(f"  - {c.get('description', c)}" for c in self.conditions)
        return (
            f"{self.strategy_name} (v{self.version}, frozen {self.frozen_at[:10]}):\n"
            f"{clauses}\n"
            f"  Expected direction: {self.expected_direction}. Primary metric: "
            f"{self.primary_metric}. Passes only with >= {self.minimum_effect:+.3f} "
            f"improvement on >= {self.minimum_observations} out-of-sample observations."
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # feature_contract is keyed by FEATURE ID ("mkt_above_sma200", ...), not by a
        # schema field name -- camel_keys() must not touch those keys, only the value
        # dicts under them (each already camelCase from FeatureDefinition.to_dict()).
        contract = payload.pop("feature_contract")
        payload["description"] = self.describe()
        out = camel_keys(payload)
        out["featureContract"] = {key: camel_keys(value) for key, value in contract.items()}
        return out


def contract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """The subset of a hypothesis that the immutability hash covers."""
    return {
        "hypothesisKey": payload["hypothesis_key"],
        "version": payload["version"],
        "strategyName": payload["strategy_name"],
        "engine": payload["engine"],
        "outcomeColumn": payload["outcome_column"],
        "conditions": payload["conditions"],
        "featureContract": payload["feature_contract"],
        "expectedDirection": payload["expected_direction"],
        "primaryMetric": payload["primary_metric"],
        "minimumEffect": payload["minimum_effect"],
        "minimumObservations": payload["minimum_observations"],
        "validationStart": payload["validation_start"],
        "validationEnd": payload["validation_end"],
        "holdoutStart": payload["holdout_start"],
        "holdoutEnd": payload["holdout_end"],
        "discoveryStart": payload["discovery_start"],
        "discoveryEnd": payload["discovery_end"],
    }


def compute_contract_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json(contract_fields(payload)).encode()).hexdigest()


def hypothesis_key(
    strategy_name: str, outcome_column: str, conditions: Sequence[dict[str, Any]]
) -> str:
    """Stable identity for "this rule, on this strategy".

    Independent of version and of the discovery session, so amending a
    threshold produces a NEW key (a different rule) while re-proposing the
    identical rule collides with the existing one -- which is exactly what
    `previously_tested()` needs in order to say "we already ran this".
    """
    normalized = sorted(
        (str(c["feature"]), str(c["op"]), _json(c["value"])) for c in conditions
    )
    digest = hashlib.sha256(
        _json({"strategy": strategy_name, "outcome": outcome_column, "conditions": normalized}).encode()
    ).hexdigest()
    return digest[:24]


def feature_contract_for(conditions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The exact feature definitions a rule depends on, captured at freeze time.

    Stored rather than looked up later on purpose: a feature's implementation
    could change, and a validation run must be able to detect that the rule it
    is testing no longer means what it meant when it was frozen. Without this
    the "immutable hypothesis" would be immutable only in its thresholds.
    """
    out: dict[str, Any] = {}
    for condition in conditions:
        key = condition["feature"]
        definition = pit_features.FEATURES.get(key)
        if definition is not None:
            out[key] = definition.to_dict()
        else:
            out[key] = {"key": key, "available": False,
                        "unavailableReason": "Feature not present in the current registry."}
    return out


class HypothesisFrozenError(RuntimeError):
    """Raised on any attempt to change a frozen hypothesis in place."""


def _row_to_hypothesis(row: sqlite3.Row) -> FrozenHypothesis:
    return FrozenHypothesis(
        row_id=row["id"], hypothesis_key=row["hypothesis_key"], version=row["version"],
        frozen_at=row["frozen_at"], strategy_name=row["strategy_name"],
        engine=row["engine"], outcome_column=row["outcome_column"],
        conditions=json.loads(row["conditions_json"]),
        feature_contract=json.loads(row["feature_contract_json"]),
        discovery_session_id=row["discovery_session_id"],
        discovery_start=row["discovery_start"], discovery_end=row["discovery_end"],
        discovery_observations=row["discovery_observations"],
        discovery_mean=row["discovery_mean"],
        discovery_baseline_mean=row["discovery_baseline_mean"],
        expected_direction=row["expected_direction"], primary_metric=row["primary_metric"],
        minimum_effect=row["minimum_effect"], minimum_observations=row["minimum_observations"],
        validation_start=row["validation_start"], validation_end=row["validation_end"],
        holdout_start=row["holdout_start"], holdout_end=row["holdout_end"],
        status=row["status"], reason=row["reason"], supersedes=row["supersedes"],
        superseded_by=row["superseded_by"], contract_hash=row["contract_hash"],
    )


def _connect() -> sqlite3.Connection:
    conn = logging_db.get_connection()
    conn.row_factory = sqlite3.Row
    return conn


def record_session(session: Any, split: dict[str, Any] | None = None) -> int:
    """Persist a discovery session, including how many hypotheses it examined.

    The hypothesis count is stored with the session rather than recomputed
    later, because "how big was the search" is a property of the search and
    must survive any later re-reading of its output.
    """
    payload = session.to_dict() if hasattr(session, "to_dict") else dict(session)
    fdr = payload.get("fdr", {})
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT OR REPLACE INTO conditional_sessions (
                session_id, created_at, strategy_name, engine, seed, observations,
                discovery_start, discovery_end, hypotheses_examined, raw_significant,
                fdr_significant, fdr_alpha, split_json, session_json, methodology_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["sessionId"], payload["createdAt"], payload["strategyName"],
                payload["engine"], payload["seed"], payload["observations"],
                payload.get("discoveryStart"), payload.get("discoveryEnd"),
                int(fdr.get("hypothesesTested", payload.get("accounting", {}).get("totalExamined", 0))),
                fdr.get("rawSignificant"), fdr.get("fdrSignificant"), fdr.get("alpha"),
                _json(split) if split else None, _json(payload), CURRENT_METHODOLOGY_VERSION,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def load_session(session_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM conditional_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return json.loads(row["session_json"]) if row else None
    finally:
        conn.close()


def sessions_for(strategy_name: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT session_id, created_at, engine, observations, hypotheses_examined, "
            "raw_significant, fdr_significant, fdr_alpha, discovery_start, discovery_end "
            "FROM conditional_sessions WHERE strategy_name = ? ORDER BY created_at DESC",
            (strategy_name,),
        ).fetchall()
        return [camel_keys(dict(row)) for row in rows]
    finally:
        conn.close()


def freeze(
    *,
    strategy_name: str,
    engine: str,
    outcome_column: str,
    conditions: Sequence[dict[str, Any]],
    expected_direction: str,
    primary_metric: str,
    minimum_effect: float,
    minimum_observations: int,
    validation_start: str | None,
    validation_end: str | None,
    holdout_start: str | None,
    holdout_end: str | None,
    discovery_session_id: str | None = None,
    discovery_start: str | None = None,
    discovery_end: str | None = None,
    discovery_observations: int | None = None,
    discovery_mean: float | None = None,
    discovery_baseline_mean: float | None = None,
    supersedes: int | None = None,
) -> FrozenHypothesis:
    """Preregister a conditional rule. Raises if this exact rule is already frozen.

    Refusing the duplicate rather than silently re-freezing is what keeps the
    ledger's history intact: re-freezing an already-tested rule under a fresh
    timestamp would erase the fact that its result is already known.
    """
    if not conditions:
        raise ValueError("A hypothesis with no conditions is not a hypothesis.")
    if expected_direction not in ("higher", "lower"):
        raise ValueError(
            f"expected_direction must be 'higher' or 'lower', got {expected_direction!r}. "
            "A hypothesis with no declared direction cannot fail, which makes its later "
            "'pass' meaningless."
        )
    key = hypothesis_key(strategy_name, outcome_column, conditions)
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT * FROM conditional_hypotheses WHERE hypothesis_key = ? "
            "ORDER BY version DESC LIMIT 1",
            (key,),
        ).fetchone()
        if existing is not None and supersedes is None:
            raise HypothesisFrozenError(
                f"This exact rule is already frozen as version {existing['version']} "
                f"(status: {existing['status']}, frozen {existing['frozen_at'][:10]}). "
                "Use amend() to create a new version, or read its existing result -- "
                "re-freezing would erase the fact that it has already been tested."
            )
        version = (existing["version"] + 1) if existing is not None else 1
        payload = {
            "hypothesis_key": key, "version": version,
            "strategy_name": strategy_name, "engine": engine,
            "outcome_column": outcome_column, "conditions": list(conditions),
            "feature_contract": feature_contract_for(conditions),
            "expected_direction": expected_direction, "primary_metric": primary_metric,
            "minimum_effect": float(minimum_effect),
            "minimum_observations": int(minimum_observations),
            "validation_start": validation_start, "validation_end": validation_end,
            "holdout_start": holdout_start, "holdout_end": holdout_end,
            "discovery_start": discovery_start, "discovery_end": discovery_end,
        }
        contract_hash = compute_contract_hash(payload)
        cursor = conn.execute(
            """
            INSERT INTO conditional_hypotheses (
                hypothesis_key, version, frozen_at, strategy_name, engine, outcome_column,
                conditions_json, feature_contract_json, discovery_session_id,
                discovery_start, discovery_end, discovery_observations, discovery_mean,
                discovery_baseline_mean, expected_direction, primary_metric,
                minimum_effect, minimum_observations, validation_start, validation_end,
                holdout_start, holdout_end, status, reason, supersedes, superseded_by,
                contract_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key, version, _now(), strategy_name, engine, outcome_column,
                _json(list(conditions)), _json(payload["feature_contract"]),
                discovery_session_id, discovery_start, discovery_end,
                discovery_observations, discovery_mean, discovery_baseline_mean,
                expected_direction, primary_metric, float(minimum_effect),
                int(minimum_observations), validation_start, validation_end,
                holdout_start, holdout_end, STATUS_FROZEN, None, supersedes, None,
                contract_hash,
            ),
        )
        row_id = int(cursor.lastrowid)
        if supersedes is not None:
            conn.execute(
                "UPDATE conditional_hypotheses SET superseded_by = ?, status = ? WHERE id = ?",
                (row_id, STATUS_SUPERSEDED, supersedes),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM conditional_hypotheses WHERE id = ?", (row_id,)
        ).fetchone()
        return _row_to_hypothesis(row)
    finally:
        conn.close()


def amend(row_id: int, **changes: Any) -> FrozenHypothesis:
    """Create a NEW version of a frozen hypothesis. Never edits the old one.

    The old row stays exactly as it was, marked superseded and pointing at the
    replacement, so a reader can always reconstruct which rule produced which
    result.
    """
    existing = get(row_id)
    if existing is None:
        raise ValueError(f"No frozen hypothesis with row id {row_id}.")
    payload: dict[str, Any] = {
        "strategy_name": existing.strategy_name, "engine": existing.engine,
        "outcome_column": existing.outcome_column, "conditions": existing.conditions,
        "expected_direction": existing.expected_direction,
        "primary_metric": existing.primary_metric,
        "minimum_effect": existing.minimum_effect,
        "minimum_observations": existing.minimum_observations,
        "validation_start": existing.validation_start,
        "validation_end": existing.validation_end,
        "holdout_start": existing.holdout_start, "holdout_end": existing.holdout_end,
        "discovery_session_id": existing.discovery_session_id,
        "discovery_start": existing.discovery_start, "discovery_end": existing.discovery_end,
        "discovery_observations": existing.discovery_observations,
        "discovery_mean": existing.discovery_mean,
        "discovery_baseline_mean": existing.discovery_baseline_mean,
    }
    unknown = set(changes) - set(payload)
    if unknown:
        raise ValueError(f"Unknown hypothesis field(s): {sorted(unknown)}")
    payload.update(changes)
    payload["supersedes"] = row_id
    return freeze(**payload)


def get(row_id: int) -> FrozenHypothesis | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM conditional_hypotheses WHERE id = ?", (row_id,)
        ).fetchone()
        return _row_to_hypothesis(row) if row else None
    finally:
        conn.close()


def hypotheses_for(strategy_name: str | None = None) -> list[FrozenHypothesis]:
    conn = _connect()
    try:
        if strategy_name:
            rows = conn.execute(
                "SELECT * FROM conditional_hypotheses WHERE strategy_name = ? "
                "ORDER BY frozen_at DESC, version DESC",
                (strategy_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conditional_hypotheses ORDER BY frozen_at DESC, version DESC"
            ).fetchall()
        return [_row_to_hypothesis(row) for row in rows]
    finally:
        conn.close()


def verify_integrity(hypothesis: FrozenHypothesis) -> dict[str, Any]:
    """Recompute the contract hash and report whether the rule still matches.

    Also re-checks the stored feature contract against the CURRENT feature
    registry: a rule whose feature has since changed definition, or been
    withdrawn, is no longer the rule that was frozen even though its own row
    is untouched. That drift is silent otherwise, and it would invalidate an
    out-of-sample result without anything looking wrong.
    """
    payload = {
        "hypothesis_key": hypothesis.hypothesis_key, "version": hypothesis.version,
        "strategy_name": hypothesis.strategy_name, "engine": hypothesis.engine,
        "outcome_column": hypothesis.outcome_column, "conditions": hypothesis.conditions,
        "feature_contract": hypothesis.feature_contract,
        "expected_direction": hypothesis.expected_direction,
        "primary_metric": hypothesis.primary_metric,
        "minimum_effect": hypothesis.minimum_effect,
        "minimum_observations": hypothesis.minimum_observations,
        "validation_start": hypothesis.validation_start,
        "validation_end": hypothesis.validation_end,
        "holdout_start": hypothesis.holdout_start, "holdout_end": hypothesis.holdout_end,
        "discovery_start": hypothesis.discovery_start,
        "discovery_end": hypothesis.discovery_end,
    }
    recomputed = compute_contract_hash(payload)
    drift: list[str] = []
    for key, stored in hypothesis.feature_contract.items():
        current = pit_features.FEATURES.get(key)
        if current is None:
            drift.append(f"{key}: no longer present in the feature registry.")
            continue
        current_dict = current.to_dict()
        for field_name in ("description", "lookbackBars", "pitSafe", "available", "kind"):
            if stored.get(field_name) != current_dict.get(field_name):
                drift.append(
                    f"{key}.{field_name}: frozen as {stored.get(field_name)!r}, "
                    f"now {current_dict.get(field_name)!r}."
                )
    return {
        "contractHash": hypothesis.contract_hash,
        "recomputedHash": recomputed,
        "intact": recomputed == hypothesis.contract_hash,
        "featureDrift": drift,
        "featureDriftDetected": bool(drift),
    }


def record_validation(
    hypothesis: FrozenHypothesis,
    *,
    stage: str,
    passed: bool | None,
    result: dict[str, Any],
    conclusion: str,
    new_status: str | None = None,
) -> int:
    """Append one out-of-sample result and, optionally, advance the status.

    Results are appended, never replaced. Running validation a second time on
    the same frozen rule produces a second row, and the fact that it was run
    twice is itself part of the record -- repeated evaluation against the same
    held-out data is a real source of overfitting and hiding it would defeat
    the point of holding the data out.
    """
    if hypothesis.row_id is None:
        raise ValueError("Cannot record a result against an unsaved hypothesis.")
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO conditional_validation_results (
                hypothesis_row_id, stage, evaluated_at, passed, observations,
                conditional_mean, baseline_mean, improvement, conclusion, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hypothesis.row_id, stage, _now(),
                None if passed is None else int(passed),
                result.get("observations"), result.get("conditionalMean"),
                result.get("baselineMean"), result.get("improvement"),
                conclusion, _json(result),
            ),
        )
        if new_status:
            conn.execute(
                "UPDATE conditional_hypotheses SET status = ?, reason = ? WHERE id = ?",
                (new_status, conclusion, hypothesis.row_id),
            )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def validation_results(row_id: int) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conditional_validation_results WHERE hypothesis_row_id = ? "
            "ORDER BY evaluated_at",
            (row_id,),
        ).fetchall()
        out = []
        for row in rows:
            payload = dict(row)
            payload["result"] = json.loads(payload.pop("result_json"))
            out.append(camel_keys(payload))
        return out
    finally:
        conn.close()


def previously_tested(
    strategy_name: str, outcome_column: str, conditions: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """Has this exact rule been frozen and tested before?

    Called before a candidate is presented, so the UI can label it
    "previously tested and rejected" instead of offering it as a fresh idea.
    This is the concrete mechanism against researcher memory bias: a human
    reading a leaderboard cannot remember three hundred rejected subsets, and
    the ledger can.
    """
    key = hypothesis_key(strategy_name, outcome_column, conditions)
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conditional_hypotheses WHERE hypothesis_key = ? ORDER BY version",
            (key,),
        ).fetchall()
        if not rows:
            return None
        latest = rows[-1]
        return {
            "hypothesisKey": key,
            "versions": len(rows),
            "latestRowId": latest["id"],
            "latestVersion": latest["version"],
            "status": latest["status"],
            "reason": latest["reason"],
            "frozenAt": latest["frozen_at"],
            "previouslyRejected": latest["status"] in TERMINAL_FAILURES,
        }
    finally:
        conn.close()


def rejected_hypotheses(strategy_name: str | None = None) -> list[dict[str, Any]]:
    """Every hypothesis that has failed, with its reason. Never pruned."""
    conn = _connect()
    try:
        placeholders = ",".join("?" for _ in TERMINAL_FAILURES)
        params: list[Any] = list(sorted(TERMINAL_FAILURES))
        query = (
            f"SELECT * FROM conditional_hypotheses WHERE status IN ({placeholders})"
        )
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        query += " ORDER BY frozen_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_hypothesis(row).to_dict() for row in rows]
    finally:
        conn.close()


def mark_holdout_consumed(strategy_name: str, reason: str, hypothesis_key_value: str | None = None) -> None:
    """Record that a strategy's final holdout has been looked at.

    Written the moment the holdout is revealed, not after a decision is made
    about it, because the contamination happens on the look. Everything
    afterwards is measured against a sample that is no longer untouched, and
    the ledger says so rather than letting the next reader assume otherwise.
    """
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO conditional_holdout_consumption (strategy_name, hypothesis_key, "
            "consumed_at, reason) VALUES (?, ?, ?, ?)",
            (strategy_name, hypothesis_key_value, _now(), reason),
        )
        conn.commit()
    finally:
        conn.close()


def holdout_consumption(strategy_name: str) -> dict[str, Any]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conditional_holdout_consumption WHERE strategy_name = ? "
            "ORDER BY consumed_at",
            (strategy_name,),
        ).fetchall()
        return {
            "strategyName": strategy_name,
            "consumed": bool(rows),
            "consumptionCount": len(rows),
            "firstConsumedAt": rows[0]["consumed_at"] if rows else None,
            "lastConsumedAt": rows[-1]["consumed_at"] if rows else None,
            "entries": [camel_keys(dict(row)) for row in rows],
        }
    finally:
        conn.close()


def record_methodology_audit(
    *,
    strategy_name: str,
    target_type: str,
    target_key: str,
    corrected_methodology_version: str,
    corrected_inference_method: str,
    target_description: str | None = None,
    session_id: str | None = None,
    hypothesis_row_id: int | None = None,
    prior_methodology_version: str | None = None,
    prior_inference_method: str | None = None,
    prior_raw_n: int | None = None,
    prior_effective_n: int | None = None,
    prior_p_value: float | None = None,
    prior_q_value: float | None = None,
    prior_ci_low: float | None = None,
    prior_ci_high: float | None = None,
    corrected_raw_n: int | None = None,
    corrected_effective_n: int | None = None,
    corrected_p_value: float | None = None,
    corrected_q_value: float | None = None,
    corrected_ci_low: float | None = None,
    corrected_ci_high: float | None = None,
    reason: str,
) -> int:
    """Append one row of "prior evidence -> corrected evidence" to the
    permanent methodology-audit trail. Never updates or deletes an existing
    audit row, and never touches the `conditional_sessions` /
    `conditional_hypotheses` row it is about -- see this module's docstring
    on preserving negative (and, here, overstated) results.

    `target_type` is one of "session" (a whole discovery session's
    raw/FDR-significant counts, before vs. after), "candidate" (one specific
    discovered rule, identified by `target_key` = its hypothesis_id), or
    "univariate"/"interaction" (identified by feature name or a "featA|featB"
    key) for a single relationship's corrected p/q/CI.

    A candidate/univariate/interaction target never had its own
    `conditional_hypotheses` row unless it was actually frozen (most
    DM/Market-Residual-Momentum candidates never were, since the
    freezability gate blocked them before freezing) -- `hypothesis_row_id`
    is therefore nullable, and `session_id` + `target_key` is what a caller
    without a frozen row uses to find this record again.
    """
    prior_significant = prior_q_value is not None and prior_q_value <= 0.10
    corrected_significant = corrected_q_value is not None and corrected_q_value <= 0.10
    materially_weakened = bool(prior_significant and not corrected_significant)
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO conditional_methodology_audits (
                audited_at, strategy_name, session_id, hypothesis_row_id, target_type,
                target_key, target_description, prior_methodology_version,
                prior_inference_method, prior_raw_n, prior_effective_n, prior_p_value,
                prior_q_value, prior_ci_low, prior_ci_high, corrected_methodology_version,
                corrected_inference_method, corrected_raw_n, corrected_effective_n,
                corrected_p_value, corrected_q_value, corrected_ci_low, corrected_ci_high,
                materially_weakened, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(), strategy_name, session_id, hypothesis_row_id, target_type,
                target_key, target_description, prior_methodology_version,
                prior_inference_method, prior_raw_n, prior_effective_n, prior_p_value,
                prior_q_value, prior_ci_low, prior_ci_high, corrected_methodology_version,
                corrected_inference_method, corrected_raw_n, corrected_effective_n,
                corrected_p_value, corrected_q_value, corrected_ci_low, corrected_ci_high,
                int(materially_weakened), reason,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def methodology_audits_for(
    strategy_name: str | None = None, session_id: str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        query = "SELECT * FROM conditional_methodology_audits WHERE 1=1"
        params: list[Any] = []
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY audited_at"
        rows = conn.execute(query, params).fetchall()
        return [camel_keys(dict(row)) for row in rows]
    finally:
        conn.close()


def ledger(strategy_name: str | None = None) -> list[dict[str, Any]]:
    """The full research ledger: every frozen hypothesis with every result.

    One row per hypothesis version, carrying its discovery sample, its
    validation and holdout samples, its statistical evidence, its prop result
    if one was computed, and the reason it was accepted or rejected.

    Each row also carries `methodologyAudits` -- any dependence-audit records
    (`conditional_methodology_audits`) that touched this specific hypothesis
    row, so a hypothesis frozen under the pre-2026-08-22 row-level
    methodology surfaces its corrected evidence right where it is displayed,
    not only via a separate audit-trail query. A hypothesis with no matching
    audit (frozen after the fix, or never re-scored) gets an empty list --
    that is itself informative, not an omission.
    """
    out: list[dict[str, Any]] = []
    for hypothesis in hypotheses_for(strategy_name):
        payload = hypothesis.to_dict()
        payload["results"] = validation_results(hypothesis.row_id) if hypothesis.row_id else []
        payload["integrity"] = verify_integrity(hypothesis)
        payload["methodologyAudits"] = (
            [a for a in methodology_audits_for(strategy_name=hypothesis.strategy_name)
             if a.get("hypothesisRowId") == hypothesis.row_id]
            if hypothesis.row_id else []
        )
        out.append(payload)
    return out
