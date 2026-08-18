"""SQLite log for automated paper-order execution (`engine/execution.py`).

Distinct from `engine/logging_db.py` (backtest run metrics) and
`engine/signals_db.py` (day-trading entry-signal detections, no orders):
this is the only table in the project that records real broker order
submissions. Deliberately mode-neutral naming (not `paper_*`) -- the
`orders.is_paper` column already anticipates a live row someday, so the
module/table names shouldn't need a rename+migration when that day comes.

Same conventions as `engine/logging_db.py`: module-level `_SCHEMA`
constants, idempotent `CREATE TABLE IF NOT EXISTS` run on every
`get_connection()` call, `sqlite3.Row` access, parameterized writes
inside `with conn:` blocks.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
DB_PATH = LOGS_DIR / "execution.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_automation (
    strategy_name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    params TEXT NOT NULL DEFAULT '{}',
    universe_id TEXT,
    symbols TEXT,
    validation_run_id INTEGER,
    inception_policy TEXT,
    inception_status TEXT,
    inception_validation_run_id INTEGER,
    inception_at TEXT,
    inception_equity REAL,
    inherited_positions TEXT,
    enabled_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rebalance_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    rebalance_date TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    status TEXT NOT NULL,
    strategy_params TEXT,
    portfolio_value_at_start REAL,
    target_weights TEXT,
    daily_loss_pct_at_start REAL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rebalance_run_id INTEGER NOT NULL REFERENCES rebalance_runs(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_kind TEXT NOT NULL,
    qty REAL,
    notional REAL,
    stop_price REAL,
    target_price REAL,
    client_order_id TEXT NOT NULL UNIQUE,
    alpaca_order_id TEXT,
    status TEXT NOT NULL,
    submitted_at TEXT,
    filled_at TEXT,
    filled_qty REAL,
    filled_avg_price REAL,
    reference_price REAL,
    expected_qty REAL,
    is_paper INTEGER NOT NULL DEFAULT 1,
    error_message TEXT
);
"""

# Statuses a rebalance_runs row can carry that mean "nothing real was
# attempted" -- excluded from the uniqueness guard below so a benign block
# (kill switch, not enabled, market closed) never occupies the day's one
# real-attempt slot. Every other status (running/completed/failed/...)
# represents a genuine attempt and DOES occupy it.
_BLOCKED_STATUSES = (
    "blocked_kill_switch",
    "blocked_not_enabled",
    "blocked_market_closed",
    "blocked_validation",
)

# SQLite's partial-index WHERE clause can't take bound parameters the way a
# normal query can -- these are a fixed module constant, never user input,
# so inlining them as string literals is safe.
_SCHEMA_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rebalance_runs_one_live_attempt_per_day "
    "ON rebalance_runs (strategy_name, rebalance_date) WHERE status NOT IN ("
    + ",".join(f"'{s}'" for s in _BLOCKED_STATUSES) + ")"
)


def get_connection() -> sqlite3.Connection:
    LOGS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    # Lightweight migration for databases created before live parameters
    # were configurable. SQLite cannot add a column conditionally in SQL.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(strategy_automation)")}
    if "params" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN params TEXT NOT NULL DEFAULT '{}'")
    if "validation_run_id" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN validation_run_id INTEGER")
    if "universe_id" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN universe_id TEXT")
    if "symbols" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN symbols TEXT")
    if "inception_policy" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN inception_policy TEXT")
    if "inception_status" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN inception_status TEXT")
    if "inception_validation_run_id" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN inception_validation_run_id INTEGER")
    if "inception_at" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN inception_at TEXT")
    if "inception_equity" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN inception_equity REAL")
    if "inherited_positions" not in columns:
        conn.execute("ALTER TABLE strategy_automation ADD COLUMN inherited_positions TEXT")
    order_columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
    if "reference_price" not in order_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN reference_price REAL")
    if "expected_qty" not in order_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN expected_qty REAL")
    conn.execute(_SCHEMA_INDEX)
    return conn


def is_enabled(strategy_name: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT enabled FROM strategy_automation WHERE strategy_name = ?", (strategy_name,)
    ).fetchone()
    conn.close()
    return bool(row[0]) if row else False


def set_enabled(strategy_name: str, enabled: bool, now: str) -> None:
    """Upsert -- a strategy has no row here until the user touches its
    toggle for the first time, and every strategy is OFF (the `is_enabled`
    default above) until that happens. Never auto-created as enabled."""
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO strategy_automation (strategy_name, enabled, enabled_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(strategy_name) DO UPDATE SET
                enabled = excluded.enabled,
                enabled_at = CASE WHEN excluded.enabled = 1 THEN excluded.enabled_at
                                   ELSE strategy_automation.enabled_at END,
                updated_at = excluded.updated_at
            """,
            (strategy_name, int(enabled), now if enabled else None, now),
        )
    conn.close()


def set_config(
    strategy_name: str,
    enabled: bool,
    params: str,
    now: str,
    validation_run_id: int | None = None,
    universe_id: str | None = None,
    symbols: str | None = None,
) -> None:
    """Persist one configuration and the exact validation authorizing it."""
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO strategy_automation (
                strategy_name, enabled, params, universe_id, symbols,
                validation_run_id, enabled_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_name) DO UPDATE SET
                enabled = excluded.enabled,
                params = excluded.params,
                universe_id = COALESCE(excluded.universe_id, strategy_automation.universe_id),
                symbols = COALESCE(excluded.symbols, strategy_automation.symbols),
                validation_run_id = COALESCE(
                    excluded.validation_run_id, strategy_automation.validation_run_id
                ),
                enabled_at = CASE WHEN excluded.enabled = 1 THEN excluded.enabled_at
                                  ELSE strategy_automation.enabled_at END,
                updated_at = excluded.updated_at
            """,
            (
                strategy_name, int(enabled), params, universe_id, symbols, validation_run_id,
                now if enabled else None, now,
            ),
        )
    conn.close()


def params_for(strategy_name: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT params FROM strategy_automation WHERE strategy_name = ?", (strategy_name,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def selected_universe_for(strategy_name: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT universe_id FROM strategy_automation WHERE strategy_name = ?", (strategy_name,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def selected_symbols_for(strategy_name: str) -> list[str] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT symbols FROM strategy_automation WHERE strategy_name = ?", (strategy_name,)
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    value = json.loads(row[0])
    return [str(symbol) for symbol in value] if isinstance(value, list) else None


def validation_run_id_for(strategy_name: str) -> int | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT validation_run_id FROM strategy_automation WHERE strategy_name = ?",
        (strategy_name,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def automation_config() -> dict[str, sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM strategy_automation").fetchall()
    conn.close()
    return {row["strategy_name"]: row for row in rows}


def configure_inception(
    strategy_name: str, policy: str, validation_run_id: int, now: str,
) -> None:
    """Attach an explicit forward-test inception policy to one promoted run.

    A different validation run always starts a fresh pending inception. Re-enabling
    the same run preserves an already-recorded baseline; before initialization the
    user may still change the policy without manufacturing a second experiment.
    """
    if policy not in {"adopt", "flatten"}:
        raise ValueError(f"Unknown inception policy {policy!r}")
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM strategy_automation WHERE strategy_name=?", (strategy_name,)
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError("Execution configuration must be saved before inception")
    is_new_run = row["inception_validation_run_id"] != validation_run_id
    is_pending = row["inception_status"] in (None, "pending")
    with conn:
        if is_new_run:
            conn.execute(
                """
                UPDATE strategy_automation SET
                    inception_policy=?, inception_status='pending',
                    inception_validation_run_id=?, inception_at=NULL,
                    inception_equity=NULL, inherited_positions=NULL,
                    updated_at=?
                WHERE strategy_name=?
                """,
                (policy, validation_run_id, now, strategy_name),
            )
        elif is_pending:
            conn.execute(
                "UPDATE strategy_automation SET inception_policy=?, updated_at=? WHERE strategy_name=?",
                (policy, now, strategy_name),
            )
    conn.close()


def inception_for(strategy_name: str) -> dict[str, Any]:
    """Return the active run's inception state.

    Legacy rows predate this schema. They remain blocked until the user makes an
    explicit choice; silently assuming adoption would recreate the provenance gap.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM strategy_automation WHERE strategy_name=?", (strategy_name,)
    ).fetchone()
    conn.close()
    if row is None:
        return {
            "policy": None, "status": "policy_required", "validationRunId": None,
            "inceptionAt": None, "equity": None, "inheritedPositions": [],
            "legacyDefault": True,
        }
    positions = json.loads(row["inherited_positions"] or "[]")
    return {
        "policy": row["inception_policy"],
        "status": row["inception_status"] or "policy_required",
        "validationRunId": row["inception_validation_run_id"],
        "inceptionAt": row["inception_at"],
        "equity": row["inception_equity"],
        "inheritedPositions": positions if isinstance(positions, list) else [],
        "legacyDefault": row["inception_policy"] is None,
    }


def record_inception(
    strategy_name: str, equity: float, positions: list[dict[str, Any]], now: str,
) -> None:
    """Freeze the marked-to-market account baseline and inherited inventory."""
    snapshot = [
        {
            "symbol": str(position.get("symbol", "")),
            "qty": float(position.get("qty") or 0.0),
            "marketValue": (
                float(position.get("qty") or 0.0) * float(position.get("currentPrice"))
                if position.get("currentPrice") is not None else None
            ),
        }
        for position in positions
    ]
    conn = get_connection()
    with conn:
        conn.execute(
            """
            UPDATE strategy_automation SET inception_status='initialized',
                inception_at=?, inception_equity=?, inherited_positions=?, updated_at=?
            WHERE strategy_name=?
            """,
            (now, float(equity), json.dumps(snapshot, sort_keys=True), now, strategy_name),
        )
    conn.close()


def set_inception_status(strategy_name: str, status: str, now: str) -> None:
    if status not in {"pending", "flattening", "initialized"}:
        raise ValueError(f"Unknown inception status {status!r}")
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE strategy_automation SET inception_status=?, updated_at=? WHERE strategy_name=?",
            (status, now, strategy_name),
        )
    conn.close()


def has_open_orders_for_strategy(strategy_name: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT 1 FROM orders o
        JOIN rebalance_runs r ON r.id=o.rebalance_run_id
        WHERE r.strategy_name=? AND o.status NOT IN ('filled', 'rejected', 'canceled')
        LIMIT 1
        """,
        (strategy_name,),
    ).fetchone()
    conn.close()
    return row is not None


def claim_run(strategy_name: str, rebalance_date: str, trigger_source: str, now: str) -> int | None:
    """Insert a 'running' row -- the one real-attempt claim for this
    strategy/day. Returns the new run's id, or None if the partial unique
    index already rejected a concurrent/duplicate real attempt (manual
    button racing the scheduler, or a second manual click)."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO rebalance_runs (
                    strategy_name, rebalance_date, trigger_source, triggered_at, status
                ) VALUES (?, ?, ?, ?, 'running')
                """,
                (strategy_name, rebalance_date, trigger_source, now),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def write_blocked(
    strategy_name: str, rebalance_date: str, trigger_source: str, status: str, now: str,
    error_message: str | None = None,
) -> int:
    """A benign short-circuit (not enabled / kill switch / market closed)
    -- always allowed to insert (excluded from the uniqueness guard), so
    every attempt leaves an audit trail even when nothing ran."""
    assert status in _BLOCKED_STATUSES, f"{status!r} is not a blocked status"
    conn = get_connection()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO rebalance_runs (
                strategy_name, rebalance_date, trigger_source, triggered_at, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (strategy_name, rebalance_date, trigger_source, now, status, error_message),
        )
        run_id = cursor.lastrowid
    conn.close()
    return run_id


def update_run(run_id: int, **fields: Any) -> None:
    if not fields:
        return
    conn = get_connection()
    with conn:
        set_clause = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE rebalance_runs SET {set_clause} WHERE id = ?",
            (*fields.values(), run_id),
        )
    conn.close()


def log_order(run_id: int, **fields: Any) -> int:
    """Written BEFORE the Alpaca submission call (status='pending') so a
    crash between 'Alpaca accepted the order' and 'we recorded it' never
    leaves an order at the broker with zero local record -- the row
    already exists and just needs its status/alpaca_order_id filled in."""
    conn = get_connection()
    with conn:
        columns = ["rebalance_run_id", *fields.keys()]
        placeholders = ", ".join("?" * len(columns))
        cursor = conn.execute(
            f"INSERT INTO orders ({', '.join(columns)}) VALUES ({placeholders})",
            (run_id, *fields.values()),
        )
    conn.close()
    return cursor.lastrowid


def update_order(order_id: int, **fields: Any) -> None:
    if not fields:
        return
    conn = get_connection()
    with conn:
        set_clause = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE orders SET {set_clause} WHERE id = ?",
            (*fields.values(), order_id),
        )
    conn.close()


def recent_runs(limit: int = 50) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM rebalance_runs ORDER BY triggered_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


def orders_for_run(run_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM orders WHERE rebalance_run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    conn.close()
    return rows


def open_orders() -> list[sqlite3.Row]:
    """Non-terminal orders -- for engine/execution.py:reconcile_open_orders()
    to refresh against Alpaca's own order status."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM orders WHERE status NOT IN ('filled', 'rejected', 'canceled')"
    ).fetchall()
    conn.close()
    return rows


def fill_calibration(symbol: str | None = None, minimum_fills: int = 5) -> dict[str, Any]:
    """Observed adverse slippage and fill ratios from reconciled broker fills."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    query = (
        "SELECT symbol, side, filled_avg_price, reference_price, filled_qty, expected_qty "
        "FROM orders WHERE status='filled' AND filled_avg_price IS NOT NULL "
        "AND reference_price IS NOT NULL"
    )
    params: tuple[Any, ...] = ()
    if symbol:
        query += " AND symbol=?"
        params = (symbol,)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    slippage = []
    fill_ratios = []
    for row in rows:
        direction = 1.0 if row["side"] == "buy" else -1.0
        slippage.append(
            direction * (float(row["filled_avg_price"]) / float(row["reference_price"]) - 1.0) * 10_000.0
        )
        if row["expected_qty"] not in (None, 0) and row["filled_qty"] is not None:
            fill_ratios.append(min(1.0, float(row["filled_qty"]) / float(row["expected_qty"])))
    import statistics
    enough = len(slippage) >= minimum_fills
    sorted_slippage = sorted(slippage)
    p95_index = max(0, min(len(sorted_slippage) - 1, int(0.95 * len(sorted_slippage)))) if slippage else 0
    return {
        "symbol": symbol,
        "fills": len(slippage),
        "minimumFills": minimum_fills,
        "calibrated": enough,
        "medianAdverseSlippageBps": statistics.median(slippage) if slippage else None,
        "p95AdverseSlippageBps": sorted_slippage[p95_index] if slippage else None,
        "meanFillRatio": statistics.mean(fill_ratios) if fill_ratios else None,
        "partialFillRate": (
            sum(value < 0.999 for value in fill_ratios) / len(fill_ratios) if fill_ratios else None
        ),
    }


# A run where at least some real trading happened -- excludes the benign
# blocks (not_enabled/kill_switch/market_closed, which can't reach here
# anyway per the partial unique index) and "failed" (zero orders got
# through). Used for the live-tracking summary's "how many real rebalance
# cycles has this account been through" count and its all-time P&L
# baseline -- both want "the first/count of runs that actually traded",
# not every attempt ever logged.
_COMPLETED_STATUSES = ("completed", "completed_with_daily_loss_halt", "partial_failure")


def earliest_run_with_baseline() -> sqlite3.Row | None:
    """The oldest completed run (across every strategy sharing this Alpaca
    account -- see api/main.py's /api/live/execution/summary docstring for
    why this is deliberately account-level, not per-strategy), used as the
    starting-equity baseline for "all-time P&L since this account started
    automated trading". Not limited to any fetch-size window (unlike
    recent_runs()) -- this must stay correct no matter how many runs have
    accumulated since."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(_COMPLETED_STATUSES))
    row = conn.execute(
        f"""
        SELECT * FROM rebalance_runs
        WHERE status IN ({placeholders}) AND portfolio_value_at_start IS NOT NULL
        ORDER BY triggered_at ASC, id ASC LIMIT 1
        """,
        _COMPLETED_STATUSES,
    ).fetchone()
    conn.close()
    return row


def count_completed_runs() -> int:
    conn = get_connection()
    placeholders = ",".join("?" * len(_COMPLETED_STATUSES))
    count = conn.execute(
        f"SELECT COUNT(*) FROM rebalance_runs WHERE status IN ({placeholders})",
        _COMPLETED_STATUSES,
    ).fetchone()[0]
    conn.close()
    return count
