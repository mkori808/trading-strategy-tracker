"""SQLite run-history log: every backtest run's parameters and metrics.

So "ORB with 15-min range" and "ORB with 30-min range" don't get silently
conflated, every run also stores a `params` JSON blob of the rule
parameters in effect at run time.

`is_canonical` distinguishes a strategy's registered-default run (symbols,
date range, and params all untouched -- see engine/runner.py:RunRequest)
from a one-off experiment run with overrides. `latest_run_per_strategy()`
only considers canonical rows, so the dashboard's leaderboard is never
silently replaced by whatever parameter sweep happened to run last;
`run_history()` still returns both kinds so the webapp can show "your
experiments" alongside the canonical run history.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from engine.metrics import BacktestMetrics

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
DB_PATH = LOGS_DIR / "runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbols TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    params TEXT,
    trades_taken INTEGER,
    wins INTEGER,
    losses INTEGER,
    win_rate REAL,
    avg_win_r REAL,
    avg_loss_r REAL,
    expectancy_r REAL,
    profit_factor REAL,
    max_drawdown_pct REAL,
    sharpe REAL,
    sortino REAL,
    status TEXT
);
"""

# Added after the 2026-07-16 quant review (see LESSONS.md) so run history
# carries benchmark-relative numbers, not just R-multiples. ALTER-based
# migration so existing local run history isn't discarded; new columns are
# NULL on rows logged before this change.
_NEW_COLUMNS = [
    ("alpha_pct", "REAL"),
    ("beta", "REAL"),
    ("cagr_pct", "REAL"),
    ("exposure_pct", "REAL"),
    ("risk_free_rate", "REAL"),
    ("is_canonical", "INTEGER"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name, col_type in _NEW_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {col_type}")
    # Every row logged before is_canonical existed really was canonical --
    # there was no other kind of run yet. Backfill rather than leave NULL,
    # which would silently vanish from latest_run_per_strategy's WHERE clause.
    conn.execute("UPDATE runs SET is_canonical = 1 WHERE is_canonical IS NULL")


def get_connection() -> sqlite3.Connection:
    LOGS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    _migrate(conn)
    return conn


def log_run(
    metrics: BacktestMetrics,
    symbols: list[str],
    params: dict | None = None,
    is_canonical: bool = True,
) -> int:
    conn = get_connection()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO runs (
                run_at, strategy_name, symbols, start_date, end_date, params,
                trades_taken, wins, losses, win_rate, avg_win_r, avg_loss_r,
                expectancy_r, profit_factor, max_drawdown_pct, sharpe, sortino, status,
                alpha_pct, beta, cagr_pct, exposure_pct, risk_free_rate, is_canonical
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                metrics.strategy_name,
                json.dumps(symbols),
                metrics.start.isoformat() if isinstance(metrics.start, date) else metrics.start,
                metrics.end.isoformat() if isinstance(metrics.end, date) else metrics.end,
                json.dumps(params or {}),
                metrics.trades_taken,
                metrics.wins,
                metrics.losses,
                metrics.win_rate,
                metrics.avg_win_r,
                metrics.avg_loss_r,
                metrics.expectancy_r,
                metrics.profit_factor,
                metrics.max_drawdown_pct,
                metrics.sharpe,
                metrics.sortino,
                metrics.status,
                metrics.alpha_pct,
                metrics.beta,
                metrics.cagr_pct,
                metrics.exposure_pct,
                metrics.risk_free_rate,
                int(is_canonical),
            ),
        )
    conn.close()
    return cursor.lastrowid


def latest_run_per_strategy() -> dict[str, sqlite3.Row]:
    """Most recent CANONICAL run per strategy -- an experimental parameter
    sweep must never silently replace what the dashboard leaderboard shows
    for a strategy's registered default configuration."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT r.* FROM runs r
        INNER JOIN (
            SELECT strategy_name, MAX(run_at) AS max_run_at
            FROM runs WHERE is_canonical = 1 GROUP BY strategy_name
        ) latest
        ON r.strategy_name = latest.strategy_name AND r.run_at = latest.max_run_at
        -- run_at has only second resolution, so a canonical and a
        -- non-canonical row CAN share a timestamp -- without this, the
        -- join would match both and Python's dict-building could silently
        -- keep the non-canonical one.
        WHERE r.is_canonical = 1
        """
    ).fetchall()
    conn.close()
    return {row["strategy_name"]: row for row in rows}


def run_history(strategy_name: str) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM runs WHERE strategy_name = ? ORDER BY run_at DESC",
        (strategy_name,),
    ).fetchall()
    conn.close()
    return rows
