"""Append-only prospective account snapshots and frozen optimized-DM prop shadows.

Observation only: this module has no order-submission imports or functions.
Both shadows consume the same Alpaca paper account stream and never change it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine import alpaca_trading, execution_db, execution_ownership
from engine.strategy_identity import execution_fingerprint

DB_PATH = Path(__file__).resolve().parent.parent / "logs" / "prop_forward.db"
PREREG = Path(__file__).resolve().parent.parent / "research" / "optimized_dm_prop_forward_v1_preregistration.json"
CAPITAL_PREREG = Path(__file__).resolve().parent.parent / "research" / "prop_vs_self_funded_v1_preregistration.json"
SAMPLE_INTERVAL_SECONDS = 300
FRESH_SECONDS = 2 * SAMPLE_INTERVAL_SECONDS
ACCOUNT_SIZE = 100_000.0
DAILY_LIMIT = 3_000.0
MAX_DRAWDOWN = 6_000.0
TARGET = 8_000.0
PAYOUT_SPLIT = 0.80
FEE = 500.0
PAYOUT_SESSIONS = 21
SHADOWS = {
    "optimized_dm_020_trailing": {"label": "Conservative", "scale": 0.20, "drawdownRule": "trailing_to_breakeven"},
    "optimized_dm_025_static": {"label": "Static reference", "scale": 0.25, "drawdownRule": "static"},
}
SELF_FUNDED_SHADOWS = {
    "optimized_dm_self_funded_020": {"label": "Self-funded 0.20x", "startingCapital": 20_000.0, "fixedExposure": 20_000.0},
    "optimized_dm_self_funded_025": {"label": "Self-funded 0.25x", "startingCapital": 25_000.0, "fixedExposure": 25_000.0},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS program_metadata (
 key TEXT PRIMARY KEY, preregistered_at TEXT NOT NULL, preregistration_sha256 TEXT NOT NULL,
 strategy_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_equity_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL UNIQUE, session_date TEXT NOT NULL,
 account_equity REAL NOT NULL, prior_close_equity REAL, cash REAL NOT NULL,
 realized_session_pl REAL, unrealized_session_pl REAL, total_session_pl REAL,
 gross_exposure REAL NOT NULL, net_exposure REAL NOT NULL, turnover REAL NOT NULL DEFAULT 0,
 positions_json TEXT NOT NULL, contributions_json TEXT NOT NULL, orders_json TEXT NOT NULL,
 strategy_fingerprint TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'alpaca_paper'
);
CREATE TABLE IF NOT EXISTS shadow_marks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, shadow_key TEXT NOT NULL, snapshot_id INTEGER NOT NULL,
 observed_at TEXT NOT NULL, session_date TEXT NOT NULL, underlying_return REAL NOT NULL,
 scaled_pnl REAL NOT NULL, equity_before REAL NOT NULL, equity_after REAL NOT NULL,
 phase TEXT NOT NULL, peak_equity REAL NOT NULL, daily_threshold REAL NOT NULL,
 drawdown_threshold REAL NOT NULL, distance_to_daily_breach REAL NOT NULL,
 distance_to_drawdown_breach REAL NOT NULL, state_json TEXT NOT NULL,
 UNIQUE(shadow_key, snapshot_id)
);
CREATE TABLE IF NOT EXISTS shadow_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, shadow_key TEXT NOT NULL, observed_at TEXT NOT NULL,
 session_date TEXT NOT NULL, event_type TEXT NOT NULL, phase TEXT NOT NULL,
 equity_before REAL, threshold REAL, amount_beyond REAL, payload_json TEXT NOT NULL,
 UNIQUE(shadow_key, observed_at, event_type)
);
CREATE TABLE IF NOT EXISTS shadow_state (
 shadow_key TEXT PRIMARY KEY, last_snapshot_id INTEGER NOT NULL, state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_outcomes (
 session_date TEXT PRIMARY KEY, opening_equity REAL NOT NULL, maximum_equity REAL NOT NULL,
 minimum_equity REAL NOT NULL, closing_equity REAL NOT NULL, realized_pl REAL,
 unrealized_pl REAL, total_pl REAL, maximum_gross_exposure REAL NOT NULL,
 maximum_abs_net_exposure REAL NOT NULL, realized_turnover REAL NOT NULL DEFAULT 0, snapshot_count INTEGER NOT NULL,
 maximum_sampling_gap_seconds REAL, completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS self_funded_marks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, shadow_key TEXT NOT NULL, snapshot_id INTEGER NOT NULL,
 observed_at TEXT NOT NULL, session_date TEXT NOT NULL, underlying_return REAL NOT NULL,
 fixed_exposure REAL NOT NULL, pnl REAL NOT NULL, equity_before REAL NOT NULL,
 equity_after REAL NOT NULL, peak_equity REAL NOT NULL, drawdown REAL NOT NULL,
 state_json TEXT NOT NULL, UNIQUE(shadow_key, snapshot_id)
);
CREATE TABLE IF NOT EXISTS self_funded_state (
 shadow_key TEXT PRIMARY KEY, last_snapshot_id INTEGER NOT NULL, state_json TEXT NOT NULL
);
"""


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    raw_columns = {row[1] for row in conn.execute("PRAGMA table_info(raw_equity_snapshots)")}
    if "turnover" not in raw_columns:
        conn.execute("ALTER TABLE raw_equity_snapshots ADD COLUMN turnover REAL NOT NULL DEFAULT 0")
    session_columns = {row[1] for row in conn.execute("PRAGMA table_info(session_outcomes)")}
    if "realized_turnover" not in session_columns:
        conn.execute("ALTER TABLE session_outcomes ADD COLUMN realized_turnover REAL NOT NULL DEFAULT 0")
    conn.row_factory = sqlite3.Row
    return conn


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fingerprint() -> tuple[str, dict[str, Any]]:
    config = execution_db.automation_config().get("Dual Momentum")
    if config is None:
        return "missing", {"fingerprintMatches": False, "fingerprintReason": "Dual Momentum automation configuration is missing."}
    params = json.loads(config["params"] or "{}")
    symbols = json.loads(config["symbols"] or "[]")
    return execution_fingerprint("Dual Momentum", params, symbols, config["validation_run_id"])


def ensure_program(conn: sqlite3.Connection) -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    fingerprint, config = _fingerprint()
    expected = prereg["strategy"]
    actual_params = dict(config.get("params") or {})
    actual_params.setdefault("top_n", 5)
    locked = bool(
        config.get("validationRunId") == expected["validationRunId"]
        and actual_params == expected["parameters"]
        and config.get("symbols") == expected["universe"]
        and config.get("identity", {}).get("fingerprintMatches")
    )
    payload = {"preregistration": prereg, "currentConfig": config, "fingerprintLocked": locked}
    digest = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    existing = conn.execute("SELECT * FROM program_metadata WHERE key='optimized_dm_prop_v1'").fetchone()
    if existing is None:
        with conn:
            conn.execute(
                "INSERT INTO program_metadata VALUES (?,?,?,?,?)",
                ("optimized_dm_prop_v1", prereg["preregisteredAt"], digest, fingerprint, _json(payload)),
            )
    elif existing["preregistration_sha256"] != digest or existing["strategy_fingerprint"] != fingerprint:
        locked = False
    return {**payload, "fingerprint": fingerprint, "fingerprintLocked": locked, "preregistrationSha256": digest}


def ensure_self_funded_program(conn: sqlite3.Connection, fingerprint: str) -> dict[str, Any]:
    """Register the comparator independently so it can never block prop marks."""
    prereg = json.loads(CAPITAL_PREREG.read_text(encoding="utf-8"))
    digest = hashlib.sha256(CAPITAL_PREREG.read_bytes()).hexdigest()
    key = "optimized_dm_self_funded_v1"
    existing = conn.execute("SELECT * FROM program_metadata WHERE key=?", (key,)).fetchone()
    locked = True
    payload = {"preregistration": prereg, "observationOnly": True, "historicalBackfill": False}
    if existing is None:
        with conn:
            conn.execute(
                "INSERT INTO program_metadata VALUES (?,?,?,?,?)",
                (key, prereg["preregisteredAt"], digest, fingerprint, _json(payload)),
            )
    elif existing["preregistration_sha256"] != digest or existing["strategy_fingerprint"] != fingerprint:
        locked = False
    return {"fingerprintLocked": locked, "preregistrationSha256": digest}


def append_snapshot(snapshot: dict[str, Any], path: Path = DB_PATH) -> tuple[int, bool]:
    """Append one raw broker observation; identical timestamps are idempotent."""
    conn = get_connection(path)
    program = ensure_program(conn)
    if not program["fingerprintLocked"]:
        conn.close()
        raise RuntimeError("Optimized DM strategy fingerprint differs from the frozen forward preregistration.")
    positions = snapshot.get("positions") or []
    for position in positions:
        position["weight"] = float(position.get("marketValue") or 0.0) / float(snapshot["equity"])
    unrealized_values = [p.get("unrealizedIntradayPl") for p in positions]
    unrealized = (
        0.0 if not positions else
        sum(float(value) for value in unrealized_values if value is not None)
        if any(value is not None for value in unrealized_values) else None
    )
    total = snapshot.get("totalSessionPl")
    realized = None if total is None or unrealized is None else float(total) - unrealized
    gross = sum(abs(float(p.get("marketValue") or 0.0)) for p in positions)
    net = sum((1 if p.get("side", "long") == "long" else -1) * abs(float(p.get("marketValue") or 0.0)) for p in positions)
    turnover = sum(
        abs(float(order.get("filledQty") or 0.0) * float(order.get("filledAvgPrice") or 0.0))
        for order in (snapshot.get("orders") or [])
        if order.get("filledAt", "")[:10] == snapshot["sessionDate"]
    ) / float(snapshot["equity"])
    previous = conn.execute(
        "SELECT observed_at,account_equity,positions_json FROM raw_equity_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    # The first mark can only carry Alpaca's session-to-date open-position P&L.
    # Later marks use an interval attribution: change in signed market value less
    # signed fill cash flow. The reconciliation bucket makes cash, fees, and any
    # closed-position/data residual explicit instead of silently assigning it.
    contributions = {str(p.get("symbol")): p.get("unrealizedIntradayPl") for p in positions}
    if previous is not None:
        def signed_values(items: list[dict[str, Any]]) -> dict[str, float]:
            return {
                str(item.get("symbol")): (1.0 if item.get("side", "long") == "long" else -1.0)
                * abs(float(item.get("marketValue") or 0.0))
                for item in items
            }

        prior_values = signed_values(json.loads(previous["positions_json"]))
        current_values = signed_values(positions)
        fill_flows: dict[str, float] = {}
        prior_at = datetime.fromisoformat(previous["observed_at"].replace("Z", "+00:00"))
        current_at = datetime.fromisoformat(snapshot["observedAt"].replace("Z", "+00:00"))
        for order in snapshot.get("orders") or []:
            filled_at = order.get("filledAt")
            if not filled_at:
                continue
            filled_dt = datetime.fromisoformat(filled_at.replace("Z", "+00:00"))
            if prior_at < filled_dt <= current_at:
                symbol = str(order.get("symbol"))
                direction = 1.0 if order.get("side") == "buy" else -1.0
                fill_flows[symbol] = fill_flows.get(symbol, 0.0) + direction * (
                    float(order.get("filledQty") or 0.0) * float(order.get("filledAvgPrice") or 0.0)
                )
        contributions = {
            symbol: current_values.get(symbol, 0.0) - prior_values.get(symbol, 0.0) - fill_flows.get(symbol, 0.0)
            for symbol in sorted(set(prior_values) | set(current_values) | set(fill_flows))
        }
        residual = float(snapshot["equity"]) - float(previous["account_equity"]) - sum(contributions.values())
        contributions["__cash_fees_and_unattributed__"] = residual
    values = (
        snapshot["observedAt"], snapshot["sessionDate"], float(snapshot["equity"]),
        snapshot.get("priorCloseEquity"), float(snapshot["cash"]), realized, unrealized, total,
        gross, net, turnover, _json(positions), _json(contributions), _json(snapshot.get("orders") or []),
        program["fingerprint"], snapshot.get("source", "alpaca_paper"),
    )
    with conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO raw_equity_snapshots
            (observed_at,session_date,account_equity,prior_close_equity,cash,realized_session_pl,
             unrealized_session_pl,total_session_pl,gross_exposure,net_exposure,turnover,positions_json,
             contributions_json,orders_json,strategy_fingerprint,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
    row = conn.execute("SELECT id FROM raw_equity_snapshots WHERE observed_at=?", (snapshot["observedAt"],)).fetchone()
    snapshot_id = int(row["id"])
    inserted = cursor.rowcount == 1
    if inserted:
        process_snapshot(conn, snapshot_id)
    conn.close()
    return snapshot_id, inserted


def _initial_state(session: str) -> dict[str, Any]:
    return {"phase": "challenge", "equity": ACCOUNT_SIZE, "peak": ACCOUNT_SIZE,
            "dayStart": ACCOUNT_SIZE, "sessionDate": session, "fundedSessions": 0,
            "attempts": 1, "failures": 0, "fundedTransitions": 0, "payouts": 0,
            "grossPayout": 0.0, "fees": FEE, "firstFundedAt": None, "firstPayoutAt": None,
            "maxDrawdown": 0.0, "worstIntradayLoss": 0.0, "completedSessions": 0}


def _event(conn: sqlite3.Connection, key: str, row: sqlite3.Row, kind: str, state: dict, **payload: Any) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO shadow_events (shadow_key,observed_at,session_date,event_type,phase,equity_before,threshold,amount_beyond,payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (key, row["observed_at"], row["session_date"], kind, state["phase"], payload.get("equityBefore"), payload.get("threshold"), payload.get("amountBeyond"), _json(payload)),
    )


def _roll_session(conn: sqlite3.Connection, key: str, row: sqlite3.Row, state: dict) -> None:
    if row["session_date"] == state["sessionDate"]:
        return
    state["completedSessions"] += 1
    if state["phase"] == "funded":
        state["fundedSessions"] += 1
        if state["fundedSessions"] % PAYOUT_SESSIONS == 0 and state["equity"] > ACCOUNT_SIZE:
            gross = state["equity"] - ACCOUNT_SIZE
            payout = gross * PAYOUT_SPLIT
            state["payouts"] += 1; state["grossPayout"] += payout
            state["firstPayoutAt"] = state["firstPayoutAt"] or row["observed_at"]
            _event(conn, key, row, "payout", state, grossProfit=gross, traderPayout=payout, payoutSplit=PAYOUT_SPLIT)
            state["equity"] = state["peak"] = ACCOUNT_SIZE
    state["sessionDate"] = row["session_date"]
    state["dayStart"] = state["equity"]


def process_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> None:
    row = conn.execute("SELECT * FROM raw_equity_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    previous_raw = conn.execute("SELECT * FROM raw_equity_snapshots WHERE id<? ORDER BY id DESC LIMIT 1", (snapshot_id,)).fetchone()
    for key, spec in SHADOWS.items():
        cached = conn.execute("SELECT * FROM shadow_state WHERE shadow_key=?", (key,)).fetchone()
        state = _initial_state(row["session_date"]) if cached is None else json.loads(cached["state_json"])
        _roll_session(conn, key, row, state)
        underlying_return = 0.0 if previous_raw is None else row["account_equity"] / previous_raw["account_equity"] - 1.0
        scaled_pnl = ACCOUNT_SIZE * spec["scale"] * underlying_return
        before = float(state["equity"])
        proposed = before + scaled_pnl
        state["peak"] = max(float(state["peak"]), proposed)
        state["maxDrawdown"] = max(float(state["maxDrawdown"]), float(state["peak"]) - proposed)
        state["worstIntradayLoss"] = min(float(state["worstIntradayLoss"]), proposed - float(state["dayStart"]))
        daily_threshold = float(state["dayStart"]) - DAILY_LIMIT
        if spec["drawdownRule"] == "static":
            drawdown_threshold = ACCOUNT_SIZE - MAX_DRAWDOWN
        else:
            drawdown_threshold = min(float(state["peak"]) - MAX_DRAWDOWN, ACCOUNT_SIZE)
        daily_breach = proposed <= daily_threshold
        drawdown_breach = proposed <= drawdown_threshold
        breach_kind = "daily_loss" if daily_breach else "drawdown" if drawdown_breach else None
        if breach_kind:
            threshold = daily_threshold if daily_breach else drawdown_threshold
            _event(conn, key, row, "breach", state, rule=breach_kind, equityBefore=before,
                   breachedEquity=proposed, threshold=threshold, amountBeyond=threshold - proposed)
            state["failures"] += 1; state["attempts"] += 1; state["fees"] += FEE
            state["phase"] = "challenge"; state["equity"] = state["peak"] = ACCOUNT_SIZE
            state["dayStart"] = ACCOUNT_SIZE
        else:
            state["equity"] = proposed
            if state["phase"] == "challenge" and proposed >= ACCOUNT_SIZE + TARGET:
                state["fundedTransitions"] += 1
                state["firstFundedAt"] = state["firstFundedAt"] or row["observed_at"]
                _event(conn, key, row, "funded_transition", state, equityBefore=before, target=ACCOUNT_SIZE + TARGET)
                state["phase"] = "funded"; state["equity"] = state["peak"] = ACCOUNT_SIZE
                state["dayStart"] = ACCOUNT_SIZE; state["fundedSessions"] = 0
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO shadow_marks
                (shadow_key,snapshot_id,observed_at,session_date,underlying_return,scaled_pnl,equity_before,equity_after,
                 phase,peak_equity,daily_threshold,drawdown_threshold,distance_to_daily_breach,distance_to_drawdown_breach,state_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key,snapshot_id,row["observed_at"],row["session_date"],underlying_return,scaled_pnl,before,state["equity"],
                 state["phase"],state["peak"],daily_threshold,drawdown_threshold,state["equity"]-daily_threshold,
                 state["equity"]-drawdown_threshold,_json(state)),
            )
            conn.execute(
                "INSERT INTO shadow_state VALUES (?,?,?) ON CONFLICT(shadow_key) DO UPDATE SET last_snapshot_id=excluded.last_snapshot_id,state_json=excluded.state_json",
                (key,snapshot_id,_json(state)),
            )
    _process_self_funded_snapshot(conn, row, previous_raw, snapshot_id)
    if previous_raw is not None and previous_raw["session_date"] != row["session_date"]:
        finalize_session(conn, previous_raw["session_date"], row["observed_at"])


def _process_self_funded_snapshot(
    conn: sqlite3.Connection, row: sqlite3.Row, previous_raw: sqlite3.Row | None, snapshot_id: int
) -> None:
    """Advance passive fixed-exposure ledgers from the same broker mark.

    No prop rules, payout mechanics, or order paths are present.  The first
    prospective mark establishes the ledger baseline and therefore has zero
    return even if older raw observations happen to exist.
    """
    program = ensure_self_funded_program(conn, row["strategy_fingerprint"])
    if not program["fingerprintLocked"]:
        return
    for key, spec in SELF_FUNDED_SHADOWS.items():
        cached = conn.execute("SELECT * FROM self_funded_state WHERE shadow_key=?", (key,)).fetchone()
        if cached is None:
            state = {
                "equity": spec["startingCapital"], "peak": spec["startingCapital"],
                "startingCapital": spec["startingCapital"], "fixedExposure": spec["fixedExposure"],
                "maxDrawdown": 0.0, "worstIntradayLoss": 0.0,
                "sessionDate": row["session_date"], "dayStart": spec["startingCapital"],
                "completedSessions": 0,
            }
            underlying_return = 0.0
        else:
            state = json.loads(cached["state_json"])
            if row["session_date"] != state["sessionDate"]:
                state["completedSessions"] += 1
                state["sessionDate"] = row["session_date"]
                state["dayStart"] = state["equity"]
            underlying_return = (
                0.0 if previous_raw is None
                else float(row["account_equity"]) / float(previous_raw["account_equity"]) - 1.0
            )
        pnl = float(spec["fixedExposure"]) * underlying_return
        before = float(state["equity"])
        state["equity"] = before + pnl
        state["peak"] = max(float(state["peak"]), float(state["equity"]))
        drawdown = float(state["peak"]) - float(state["equity"])
        state["maxDrawdown"] = max(float(state["maxDrawdown"]), drawdown)
        state["worstIntradayLoss"] = min(
            float(state["worstIntradayLoss"]), float(state["equity"]) - float(state["dayStart"])
        )
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO self_funded_marks
                (shadow_key,snapshot_id,observed_at,session_date,underlying_return,fixed_exposure,pnl,
                 equity_before,equity_after,peak_equity,drawdown,state_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, snapshot_id, row["observed_at"], row["session_date"], underlying_return,
                 spec["fixedExposure"], pnl, before, state["equity"], state["peak"], drawdown, _json(state)),
            )
            conn.execute(
                "INSERT INTO self_funded_state VALUES (?,?,?) ON CONFLICT(shadow_key) DO UPDATE SET last_snapshot_id=excluded.last_snapshot_id,state_json=excluded.state_json",
                (key, snapshot_id, _json(state)),
            )


def finalize_session(conn: sqlite3.Connection, session_date: str, completed_at: str) -> None:
    rows = conn.execute("SELECT * FROM raw_equity_snapshots WHERE session_date=? ORDER BY observed_at", (session_date,)).fetchall()
    if not rows:
        return
    stamps = [datetime.fromisoformat(r["observed_at"]) for r in rows]
    gaps = [(b-a).total_seconds() for a,b in zip(stamps, stamps[1:])]
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO session_outcomes
            (session_date,opening_equity,maximum_equity,minimum_equity,closing_equity,realized_pl,
             unrealized_pl,total_pl,maximum_gross_exposure,maximum_abs_net_exposure,realized_turnover,
             snapshot_count,maximum_sampling_gap_seconds,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_date,rows[0]["account_equity"],max(r["account_equity"] for r in rows),min(r["account_equity"] for r in rows),
             rows[-1]["account_equity"],rows[-1]["realized_session_pl"],rows[-1]["unrealized_session_pl"],rows[-1]["total_session_pl"],
             max(r["gross_exposure"] for r in rows),max(abs(r["net_exposure"]) for r in rows),max(r["turnover"] for r in rows),len(rows),max(gaps) if gaps else None,completed_at),
        )


def finalize_prior_sessions(
    completed_at: str, *, path: Path | None = None,
) -> list[str]:
    """Seal raw sessions once the broker clock has advanced to a later date.

    The first snapshot of a new session already seals the preceding one in
    ``process_snapshot``.  This complementary closed-market path prevents a
    Friday or pre-holiday session from remaining pending until the next market
    open.  It deliberately refuses to seal the clock's current calendar date:
    ``isOpen == False`` can also mean pre-market, an intraday halt, or an early
    collector start, none of which proves that today's observation is final.
    """
    stamp = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("completed_at must include a timezone offset")
    current_et_date = stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    conn = get_connection(path or DB_PATH)
    pending = [
        str(row[0])
        for row in conn.execute(
            """SELECT DISTINCT raw.session_date
               FROM raw_equity_snapshots raw
               LEFT JOIN session_outcomes done ON done.session_date=raw.session_date
               WHERE done.session_date IS NULL AND raw.session_date<?
               ORDER BY raw.session_date""",
            (current_et_date,),
        ).fetchall()
    ]
    for session_date in pending:
        finalize_session(conn, session_date, completed_at)
    conn.close()
    return pending


def collect_once() -> dict[str, Any]:
    """Collect one live snapshot when the market is open; never fabricates gaps."""
    clock = alpaca_trading.get_clock()
    if not clock.get("available"):
        return {"collected": False, "reason": clock.get("reason", "Alpaca unavailable")}
    if not clock.get("isOpen"):
        completed_at = clock.get("timestamp")
        finalized = finalize_prior_sessions(str(completed_at)) if completed_at else []
        return {
            "collected": False, "reason": "market_closed",
            "finalizedSessions": finalized,
        }
    bundle = alpaca_trading.account_snapshot()
    account = bundle["account"]
    if not account.get("available"):
        return {"collected": False, "reason": account.get("reason", "Alpaca unavailable")}
    integrity = execution_ownership.monitor_account(
        account["accountNumber"], bundle["orders"], bundle["positions"],
        detected_at=clock["timestamp"],
    )
    if integrity["actionRequired"]:
        return {"collected": False, "reason": "account_contamination", "integrity": integrity}
    observed = clock["timestamp"]
    snapshot = {"observedAt": observed, "sessionDate": observed[:10], "equity": account["equity"],
                "priorCloseEquity": account.get("lastEquity"), "cash": account["cash"],
                "totalSessionPl": account["equity"]-account["lastEquity"] if account.get("lastEquity") else None,
                "positions": bundle["positions"], "orders": bundle["orders"]}
    snapshot_id, inserted = append_snapshot(snapshot)
    return {"collected": inserted, "snapshotId": snapshot_id, "observedAt": observed}


def status(path: Path = DB_PATH, now: datetime | None = None) -> dict[str, Any]:
    conn = get_connection(path); program = ensure_program(conn)
    self_program = ensure_self_funded_program(conn, program["fingerprint"])
    latest = conn.execute("SELECT * FROM raw_equity_snapshots ORDER BY observed_at DESC LIMIT 1").fetchone()
    current_parent_positions = [] if latest is None else json.loads(latest["positions_json"])
    sessions = conn.execute("SELECT COUNT(*) n FROM session_outcomes").fetchone()["n"]
    now = now or datetime.now(timezone.utc)
    freshness = None if latest is None else (now-datetime.fromisoformat(latest["observed_at"])).total_seconds()
    gap_rows = conn.execute("SELECT COUNT(*) n FROM session_outcomes WHERE maximum_sampling_gap_seconds>?", (FRESH_SECONDS,)).fetchone()["n"]
    session_rows = conn.execute("SELECT * FROM session_outcomes ORDER BY session_date").fetchall()
    raw_rows = conn.execute("SELECT orders_json FROM raw_equity_snapshots ORDER BY id").fetchall()
    order_ids: set[str] = set(); slippage: list[float] = []; partial: list[bool] = []
    for raw in raw_rows:
        for order in json.loads(raw["orders_json"]):
            order_id = str(order.get("id"))
            if order_id in order_ids:
                continue
            order_ids.add(order_id)
            if order.get("adverseSlippageBps") is not None: slippage.append(float(order["adverseSlippageBps"]))
            if order.get("partialFill") is not None: partial.append(bool(order["partialFill"]))
    next_checkpoint = next((point for point in (20, 60, 126, 252) if sessions < point), None)

    def settled_history(table: str, key: str) -> list[dict[str, Any]]:
        """One immutable closing equity per finalized session.

        Intraday marks remain available for risk controls, but account-style
        performance must not present an unfinished mark as a settled close.
        """
        return [dict(row) for row in conn.execute(
            f"""SELECT marks.session_date AS date, marks.equity_after AS equity
                FROM {table} marks
                JOIN session_outcomes sessions ON sessions.session_date=marks.session_date
                WHERE marks.shadow_key=? AND marks.id=(
                    SELECT MAX(latest.id) FROM {table} latest
                    WHERE latest.shadow_key=marks.shadow_key
                      AND latest.session_date=marks.session_date
                )
                ORDER BY marks.session_date""",
            (key,),
        ).fetchall()]

    shadows = []
    for key,spec in SHADOWS.items():
        cached=conn.execute("SELECT state_json FROM shadow_state WHERE shadow_key=?",(key,)).fetchone()
        state=_initial_state("") if cached is None else json.loads(cached["state_json"])
        events=conn.execute("SELECT event_type,COUNT(*) n FROM shadow_events WHERE shadow_key=? GROUP BY event_type",(key,)).fetchall()
        counts={r["event_type"]:r["n"] for r in events}
        mark=conn.execute("SELECT * FROM shadow_marks WHERE shadow_key=? ORDER BY id DESC LIMIT 1",(key,)).fetchone()
        current_dd_util = 0.0 if mark is None else max(0.0, 1.0 - float(mark["distance_to_drawdown_breach"]) / MAX_DRAWDOWN)
        shadows.append({"key":key,**spec,"sessions":sessions,"state":state["phase"].title(),"currentEquity":state["equity"],
                        "netPayout":state["grossPayout"]-state["fees"],"grossPayout":state["grossPayout"],"fees":state["fees"],
                        "breaches":counts.get("breach",0),"attempts":state["attempts"],"failures":state["failures"],
                        "drawdownUtilization":current_dd_util,"maximumObservedDrawdown":state["maxDrawdown"],
                        "dailyLimitUtilization":0.0 if mark is None else max(0.0,1.0-float(mark["distance_to_daily_breach"])/DAILY_LIMIT),
                        "annualizedBreachFrequency":None if sessions<252 else counts.get("breach",0)/sessions*252,
                        "distanceToDailyBreach":None if mark is None else mark["distance_to_daily_breach"],
                        "distanceToDrawdownBreach":None if mark is None else mark["distance_to_drawdown_breach"],
                        "history": settled_history("shadow_marks", key)})
    self_funded = []
    for key, spec in SELF_FUNDED_SHADOWS.items():
        cached = conn.execute("SELECT state_json FROM self_funded_state WHERE shadow_key=?", (key,)).fetchone()
        state = ({"equity": spec["startingCapital"], "peak": spec["startingCapital"],
                  "maxDrawdown": 0.0, "worstIntradayLoss": 0.0, "completedSessions": 0}
                 if cached is None else json.loads(cached["state_json"]))
        mark_count = conn.execute("SELECT COUNT(*) n FROM self_funded_marks WHERE shadow_key=?", (key,)).fetchone()["n"]
        self_funded.append({
            "key": key, **spec, "observations": mark_count,
            "sessions": state.get("completedSessions", 0), "currentEquity": state["equity"],
            "netProfit": float(state["equity"]) - float(spec["startingCapital"]),
            "returnOnCommittedCapital": float(state["equity"]) / float(spec["startingCapital"]) - 1.0,
            "maximumObservedDrawdown": state["maxDrawdown"],
            "drawdownPctCommitted": float(state["maxDrawdown"]) / float(spec["startingCapital"]),
            "worstIntradayLoss": state["worstIntradayLoss"], "stopoutRule": None,
            "propFees": 0.0, "payoutSplit": 1.0,
            "history": settled_history("self_funded_marks", key),
        })
    maturity="Not started" if sessions==0 else "Too early" if sessions<20 else "Forward collecting" if sessions<60 else "Interim descriptive evidence" if sessions<252 else "Mature enough for review"
    warnings=[]
    ownership = execution_ownership.current_status()
    if not program["fingerprintLocked"]: warnings.append("Strategy fingerprint differs from the frozen preregistration; collection is blocked.")
    if not self_program["fingerprintLocked"]: warnings.append("Self-funded comparator preregistration or strategy fingerprint changed; comparator marks are blocked.")
    if latest is None: warnings.append("No prospective intraday snapshots have been collected yet.")
    elif freshness is not None and freshness > FRESH_SECONDS and latest["session_date"] == now.astimezone(ZoneInfo("America/New_York")).date().isoformat():
        warnings.append(f"Operational Integrity: latest snapshot is {freshness/60:.0f} minutes old; if the market is open, sampling is stale.")
    if gap_rows: warnings.append(f"Operational Integrity: {gap_rows} completed sessions contain sampling gaps over {FRESH_SECONDS//60} minutes.")
    if ownership and ownership["actionRequired"]:
        warnings.append("Operational Integrity — account contamination or execution-ownership violation. Action required; new prop observations are blocked.")
    conn.close()
    return {"program":"Optimized DM Prop Shadows","evidenceClass":"prospective_prop_shadow","historicalEvidenceKeptSeparate":True,
            "samplingIntervalSeconds":SAMPLE_INTERVAL_SECONDS,"lastSnapshot":None if latest is None else latest["observed_at"],
            "samplingAgeSeconds":freshness,"completedSessions":sessions,"maturity":maturity,"warnings":warnings,"shadows":shadows,
            "nextCheckpointSessions":next_checkpoint,
            "lifetime":{"realizedTurnover":sum(float(row["realized_turnover"]) for row in session_rows),
                        "medianAdverseSlippageBps":None if not slippage else float(__import__("statistics").median(slippage)),
                        "partialFillRate":None if not partial else sum(partial)/len(partial),
                        "annualizedMetricsWithheld":sessions<252},
            "historicalAssumptionComparison":{"status":"Too early" if sessions<20 else "Descriptive only","historicalStudyUnchanged":True},
            "controlsPreserved":["Canonical DM","Canonical MRM","Fixed 50/50 DM/MRM","Vol-Scaled DM/MRM"],
            "fingerprintLocked":program["fingerprintLocked"],
            "parentStrategyId":"dm_optimized_63d_daily","parentStrategyFingerprint":program["fingerprint"],
            "currentParentPositions":current_parent_positions,
            "parentLinkageLocked":bool(program["fingerprintLocked"] and (not ownership or (ownership.get("owner") or {}).get("strategyFingerprint") == program["fingerprint"])),
            "selfFundedShadows":self_funded,"selfFundedFingerprintLocked":self_program["fingerprintLocked"],
            "accountIntegrity":ownership}
