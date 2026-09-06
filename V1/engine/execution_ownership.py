"""Brokerage-account ownership and execution-integrity guardrails.

One account may host many research shadows, but only its fingerprinted owner
may submit brokerage orders.  The schema is account-keyed so additional paper
or live accounts can be registered later without changing this model.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engine import execution_db
from engine.strategy_identity import execution_fingerprint

BROKERAGE = "alpaca"
ENVIRONMENT = "paper"
GOVERNING_PRINCIPLE = (
    "One brokerage account can support many forward research strategies, but only one "
    "strategy should produce authoritative brokerage execution evidence unless position "
    "and accounting isolation are independently validated."
)


class OwnershipConflict(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _instant(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed.timestamp()


def account_key(account_id: str, environment: str = ENVIRONMENT, brokerage: str = BROKERAGE) -> str:
    return f"{brokerage}:{environment}:{account_id}"


def configured_identity(strategy_name: str) -> tuple[str, dict[str, Any]]:
    row = execution_db.automation_config().get(strategy_name)
    if row is None:
        raise OwnershipConflict(f"No persisted execution configuration exists for {strategy_name}.")
    params = json.loads(row["params"] or "{}")
    symbols = json.loads(row["symbols"] or "[]")
    return execution_fingerprint(strategy_name, params, symbols, row["validation_run_id"])


def register_account(account_id: str, *, environment: str = ENVIRONMENT, brokerage: str = BROKERAGE) -> str:
    key = account_key(account_id, environment, brokerage)
    now = _now()
    conn = execution_db.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO brokerage_accounts
               (account_key,brokerage,account_id,environment,active,multi_strategy_execution,created_at,updated_at)
               VALUES (?,?,?,?,1,0,?,?)
               ON CONFLICT(account_key) DO UPDATE SET active=1,updated_at=excluded.updated_at""",
            (key, brokerage, account_id, environment, now, now),
        )
    conn.close()
    return key


def _active_owners(conn: Any, key: str) -> list[Any]:
    return conn.execute(
        "SELECT * FROM execution_account_owners WHERE account_key=? AND active=1 ORDER BY id",
        (key,),
    ).fetchall()


def bootstrap_current_owner(account_id: str) -> dict[str, Any]:
    """Adopt the one already-enabled strategy without touching its experiment."""
    key = register_account(account_id)
    conn = execution_db.get_connection()
    owners = _active_owners(conn, key)
    enabled = [(name, row) for name, row in execution_db.automation_config().items() if bool(row["enabled"])]
    if not owners and len(enabled) == 1:
        name, row = enabled[0]
        fingerprint, payload = configured_identity(name)
        identity = payload["identity"]
        started = row["inception_at"] or row["enabled_at"] or _now()
        with conn:
            conn.execute(
                """INSERT INTO execution_account_owners
                   (account_key,strategy_id,strategy_name,strategy_fingerprint,ownership_started_at,active,created_at)
                   VALUES (?,?,?,?,?,1,?)""",
                (key, identity["key"], name, fingerprint, started, _now()),
            )
    conn.close()
    return ownership_status(account_id)


def assert_can_enable(
    account_id: str,
    strategy_name: str,
    fingerprint: str,
    strategy_id: str,
) -> None:
    key = register_account(account_id)
    conn = execution_db.get_connection()
    account = conn.execute("SELECT * FROM brokerage_accounts WHERE account_key=?", (key,)).fetchone()
    owners = _active_owners(conn, key)
    conn.close()
    if len(owners) > 1 and not bool(account["multi_strategy_execution"]):
        raise OwnershipConflict("Account-level execution ownership is inconsistent: more than one active owner. Action required.")
    if owners and not any(
        row["strategy_id"] == strategy_id and row["strategy_fingerprint"] == fingerprint for row in owners
    ):
        owner = owners[0]
        if owner["strategy_id"] == strategy_id:
            raise OwnershipConflict(
                "The requested execution configuration does not match the fingerprint currently "
                "assigned to this brokerage account. Display-name equality cannot authorize it."
            )
        display_name = (ownership_status(account_id).get("owner") or {}).get("displayName") or owner["strategy_name"]
        raise OwnershipConflict(
            f"This brokerage account is currently assigned to {display_name}. "
            "Enable the new strategy as a shadow forward test or assign it to a separate brokerage account. "
            "Account-level prop evidence requires a single executing strategy."
        )


def assert_strategy_slot(account_id: str, strategy_name: str) -> None:
    """Early name-level conflict check; fingerprint authority follows later."""
    status = bootstrap_current_owner(account_id)
    owner = status.get("owner")
    if owner and owner["strategyName"] != strategy_name:
        raise OwnershipConflict(
            f"This brokerage account is currently assigned to {owner['displayName']}. "
            "Enable the new strategy as a shadow forward test or assign it to a separate brokerage account. "
            "Account-level prop evidence requires a single executing strategy."
        )


def verify_execution_authority(account_id: str, strategy_name: str) -> tuple[bool, str | None]:
    fingerprint, payload = configured_identity(strategy_name)
    status = ownership_status(account_id)
    owner = status.get("owner")
    if owner is None:
        return False, "Brokerage account has no executing-strategy owner. Action required."
    if owner["strategyName"] != strategy_name:
        return False, (
            f"This brokerage account is assigned to {owner['displayName']}; "
            f"{strategy_name} has no execution authority."
        )
    if owner["strategyId"] != payload["identity"]["key"] or owner["strategyFingerprint"] != fingerprint:
        return False, "Executing strategy fingerprint differs from the account ownership lock. Action required."
    if not owner["fingerprintMatches"]:
        return False, "Executing strategy fingerprint differs from the account ownership lock. Action required."
    return True, None


def _event(key: str, event_type: str, detected_at: str, **details: Any) -> None:
    evidence = "|".join([
        key, event_type, str(details.get("brokerOrderId") or ""),
        str(details.get("brokerFillId") or ""), str(details.get("symbol") or ""),
    ])
    evidence_key = hashlib.sha256(evidence.encode()).hexdigest()
    conn = execution_db.get_connection()
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO execution_integrity_events
               (account_key,detected_at,event_type,severity,symbol,broker_order_id,broker_fill_id,
                attributable_strategy_id,details_json,evidence_key) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (key, detected_at, event_type, "action_required", details.get("symbol"),
             details.get("brokerOrderId"), details.get("brokerFillId"),
             details.get("attributableStrategyId"), json.dumps(details, sort_keys=True), evidence_key),
        )
    conn.close()


def monitor_account(
    account_id: str,
    orders: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    *,
    detected_at: str | None = None,
) -> dict[str, Any]:
    status = bootstrap_current_owner(account_id)
    owner = status.get("owner")
    if owner is None:
        return status
    key = status["accountKey"]
    started = _instant(owner["ownershipStartedAt"])
    detected_at = detected_at or _now()
    for order in orders:
        timestamp = order.get("submittedAt") or order.get("filledAt")
        if not timestamp or _instant(timestamp) < started:
            continue
        if not order.get("attributed") or order.get("attributedStrategyId") != owner["strategyId"]:
            event_type = "unknown_fill_attribution" if order.get("filledQty") else "unexpected_manual_order"
            _event(
                key, event_type, detected_at, symbol=order.get("symbol"),
                brokerOrderId=order.get("id"),
                brokerFillId=order.get("id") if order.get("filledQty") else None,
                attributableStrategyId=order.get("attributedStrategyId"),
                reason=order.get("attributionReason") or "No owner-attributed local execution record",
            )

    # A position outside the latest owner target is only actionable when no
    # owner-attributed order explains an in-flight entry/exit.
    conn = execution_db.get_connection()
    latest = conn.execute(
        "SELECT target_weights FROM rebalance_runs WHERE strategy_name=? AND target_weights IS NOT NULL "
        "ORDER BY id DESC LIMIT 1", (owner["strategyName"],),
    ).fetchone()
    conn.close()
    targets = set(json.loads(latest["target_weights"] or "{}").keys()) if latest else set()
    explained = {o.get("symbol") for o in orders if o.get("attributedStrategyId") == owner["strategyId"]}
    if targets:
        for position in positions:
            symbol = str(position.get("symbol"))
            if symbol not in targets and symbol not in explained:
                _event(key, "position_inconsistent_with_owner", detected_at, symbol=symbol,
                       reason="Brokerage position is absent from the latest owner target and owner order trail.")
    return ownership_status(account_id)


def ownership_status(account_id: str) -> dict[str, Any]:
    key = account_key(account_id)
    conn = execution_db.get_connection()
    account = conn.execute("SELECT * FROM brokerage_accounts WHERE account_key=?", (key,)).fetchone()
    owners = _active_owners(conn, key) if account else []
    events = conn.execute(
        "SELECT * FROM execution_integrity_events WHERE account_key=? ORDER BY id DESC", (key,),
    ).fetchall() if account else []
    conn.close()
    owner_payload = None
    if owners:
        row = owners[0]
        try:
            current, payload = configured_identity(row["strategy_name"])
            matches = current == row["strategy_fingerprint"] and bool(payload["identity"]["fingerprintMatches"])
            display_name = payload["identity"]["displayName"]
        except OwnershipConflict:
            matches = False
            display_name = row["strategy_name"]
        owner_payload = {
            "strategyId": row["strategy_id"], "strategyName": row["strategy_name"],
            "displayName": display_name, "strategyFingerprint": row["strategy_fingerprint"],
            "ownershipStartedAt": row["ownership_started_at"], "active": bool(row["active"]),
            "fingerprintMatches": matches,
        }
    issues = [{"type": row["event_type"], "severity": row["severity"], "detectedAt": row["detected_at"],
               "symbol": row["symbol"], "brokerOrderId": row["broker_order_id"],
               "brokerFillId": row["broker_fill_id"], "details": json.loads(row["details_json"])} for row in events]
    if len(owners) > 1:
        issues.insert(0, {"type": "multiple_active_execution_owners", "severity": "action_required",
                          "detectedAt": _now(), "details": {"count": len(owners)}})
    if owner_payload and not owner_payload["fingerprintMatches"]:
        issues.insert(0, {"type": "strategy_fingerprint_mismatch", "severity": "action_required",
                          "detectedAt": _now(), "details": {}})
    return {
        "accountKey": key, "brokerage": BROKERAGE, "brokerageLabel": "Alpaca Paper",
        "accountId": account_id, "environment": ENVIRONMENT, "active": bool(account["active"]) if account else False,
        "multiStrategyExecution": bool(account["multi_strategy_execution"]) if account else False,
        "executionIsolation": "Single-strategy locked", "owner": owner_payload,
        "actionRequired": bool(issues), "integrityIssues": issues,
        "principle": GOVERNING_PRINCIPLE,
    }


def current_status() -> dict[str, Any] | None:
    conn = execution_db.get_connection()
    row = conn.execute(
        "SELECT account_id FROM brokerage_accounts WHERE active=1 ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return None if row is None else ownership_status(row["account_id"])
