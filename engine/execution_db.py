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

import sqlite3
from pathlib import Path
from typing import Any

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
DB_PATH = LOGS_DIR / "execution.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_automation (
    strategy_name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
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
    is_paper INTEGER NOT NULL DEFAULT 1,
    error_message TEXT
);
"""

# Statuses a rebalance_runs row can carry that mean "nothing real was
# attempted" -- excluded from the uniqueness guard below so a benign block
# (kill switch, not enabled, market closed) never occupies the day's one
# real-attempt slot. Every other status (running/completed/failed/...)
# represents a genuine attempt and DOES occupy it.
_BLOCKED_STATUSES = ("blocked_kill_switch", "blocked_not_enabled", "blocked_market_closed")

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


def automation_config() -> dict[str, sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM strategy_automation").fetchall()
    conn.close()
    return {row["strategy_name"]: row for row in rows}


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
