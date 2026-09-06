"""SQLite log of live entry-signal detections -- see engine/live_scanner.py.

Distinct from engine/logging_db.py's `runs` table (backtest results): this
records a single strategy firing entry_signal on a real live bar, not a full
backtest run, so it needs its own table shape entirely.

`regime_state`/`trend_template_pass` are stored as CONTEXT, not as a gate.
The canonical backtest (engine/runner.py:run_backtest) does not route
through engine/filters.py's FilteredStrategy either -- that wrapper is only
exercised by engine/compare_filters.py's exploratory comparison -- so gating
live alerts through it would make them inconsistent with what the Compare
tab's backtest numbers already represent. An alert is logged whenever
entry_signal fires, full stop; the filter state travels alongside it so the
user can judge for themselves whether a given signal also would have
cleared the optional pre-trade filters.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
DB_PATH = LOGS_DIR / "signals.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    bar_timestamp TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    price REAL,
    timeframe TEXT,
    regime_state TEXT,
    trend_template_pass INTEGER
);
"""

# One alert per (strategy, symbol, bar) -- NOT per session. Some strategies
# (VWAP Bounce, Mean Reversion Scalp) legitimately fire more than once a day,
# and per-session dedup would silently suppress those repeat signals.
_SCHEMA_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_alerts_dedup
ON signal_alerts (strategy_name, symbol, bar_timestamp);
"""


def get_connection() -> sqlite3.Connection:
    LOGS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    conn.execute(_SCHEMA_INDEX)
    return conn


def log_signal(
    detected_at: str,
    bar_timestamp: str,
    strategy_name: str,
    symbol: str,
    direction: str,
    price: float | None,
    timeframe: str,
    regime_state: str | None,
    trend_template_pass: bool | None,
) -> bool:
    """Insert one alert. Returns False (no-op) if this exact
    (strategy, symbol, bar) combination was already logged by an earlier
    scan cycle -- the dedup key that lets scan_once() run every cycle
    without re-alerting on a signal it already reported."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO signal_alerts (
                    detected_at, bar_timestamp, strategy_name, symbol, direction,
                    price, timeframe, regime_state, trend_template_pass
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detected_at, bar_timestamp, strategy_name, symbol, direction,
                    price, timeframe, regime_state,
                    None if trend_template_pass is None else int(trend_template_pass),
                ),
            )
            inserted = cursor.rowcount > 0
        return inserted
    finally:
        conn.close()


def recent_signals(limit: int = 100) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM signal_alerts ORDER BY bar_timestamp DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows
