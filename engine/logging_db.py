"""SQLite run-history log: every backtest run's parameters and metrics.

So "ORB with 15-min range" and "ORB with 30-min range" don't get silently
conflated, every run also stores a `params` JSON blob of the rule
parameters in effect at run time.

`is_canonical` distinguishes a strategy's registered-default run (symbols,
date range, and params all untouched -- see engine/runner.py:RunRequest)
from a one-off experiment run with overrides. Both `latest_run_per_strategy()`
and `best_run_per_strategy()` only ever consider canonical rows, so the
dashboard's leaderboard is never silently replaced by whatever parameter
sweep happened to run last -- they differ only in which canonical run wins
when a strategy has been re-run more than once (most recent vs. best
Sharpe; the Compare tab uses `best_run_per_strategy()`). `run_history()`
still returns every row, canonical and experimental, so the webapp can show
"your experiments" alongside the canonical run history.
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


# Cross-sectional (Dual Momentum) and pairs (Pairs / Stat Arb) runs don't fit
# the `runs` table above -- no discrete R-multiple trades, no win rate, just
# a continuously-rebalanced or two-leg equity curve. engine/runner.py's
# run_cross_sectional/run_pairs previously didn't log anywhere at all, which
# meant the webapp's "most recent run" for these two strategies could never
# update no matter how many times you ran them. Separate, schema-appropriate
# table rather than force-fitting into `runs` (same reasoning already
# documented in engine/runner.py's run_cross_sectional/run_pairs docstrings
# for why they weren't logged there in the first place).
_PORTFOLIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbols TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    params TEXT,
    final_equity REAL,
    return_pct REAL,
    cagr_pct REAL,
    max_drawdown_pct REAL,
    sharpe REAL,
    sortino REAL,
    risk_free_rate REAL,
    pair_symbol_a TEXT,
    pair_symbol_b TEXT,
    pair_p_value REAL,
    is_canonical INTEGER NOT NULL DEFAULT 1
);
"""


# Added 2026-07-20 so portfolio runs carry a real verdict instead of the
# UI hardcoding "Backtested": SPY's buy-and-hold return over the identical
# window (the benchmark these engines are judged against, since they have
# no per-symbol alpha) and a status string from
# engine/metrics.py:portfolio_status(). Same ALTER-based, append-only
# migration pattern as _NEW_COLUMNS; rows logged before this change keep
# NULL for both, which the API renders as the old "Backtested" fallback.
_PORTFOLIO_NEW_COLUMNS = [
    ("benchmark_return_pct", "REAL"),
    ("status", "TEXT"),
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
    existing_portfolio = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_runs)")}
    for name, col_type in _PORTFOLIO_NEW_COLUMNS:
        if name not in existing_portfolio:
            conn.execute(f"ALTER TABLE portfolio_runs ADD COLUMN {name} {col_type}")


def get_connection() -> sqlite3.Connection:
    LOGS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    conn.execute(_PORTFOLIO_SCHEMA)
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


def best_run_per_strategy() -> dict[str, sqlite3.Row]:
    """Best-Sharpe CANONICAL run per strategy -- same canonical-only
    restriction as latest_run_per_strategy() (an experimental parameter
    sweep must never surface here, per CLAUDE.md's Lab-tab firewall), but
    ranks by risk-adjusted performance instead of recency. Feeds the
    Compare tab's leaderboard, so a strategy's row reflects the best honest
    result across however many times its registered config has been
    re-run (e.g. after a bug fix -- see LESSONS.md's several "corrected
    Sharpe after a bug fix" entries), not just whichever run happened last.

    Rows with a real (non-NULL) alpha_pct are preferred over rows without
    one, BEFORE ranking by Sharpe. alpha_pct only exists on runs logged
    since the benchmark-relative migration (see this module's docstring);
    a NULL here means "predates that instrumentation," not "this run had
    no benchmark to beat." Measured directly: several strategies' best-
    Sharpe row was a July-16-morning run from before alpha existed, hiding
    a worse-Sharpe-but-alpha-having later run and making the leaderboard
    silently show alpha as "--" for a strategy that computes it on every
    current run. A strategy whose engine genuinely never computes alpha at
    all (e.g. Overnight Hold -- no benchmark concept, see engine/overnight.py)
    has every canonical row tied on this criterion, so it falls through to
    the Sharpe ranking exactly as before -- no regression there.

    A run with NULL Sharpe sorts last within its alpha-completeness tier
    (`(sharpe IS NULL) ASC` puts 0/false -- has a real Sharpe -- before
    1/true), never outranking a run with a real, even negative, Sharpe.
    Ties (identical Sharpe) break on most-recent run_at."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT r.*, ROW_NUMBER() OVER (
                PARTITION BY strategy_name
                ORDER BY (alpha_pct IS NULL) ASC, (sharpe IS NULL) ASC,
                         sharpe DESC, run_at DESC, id DESC
            ) AS rn
            FROM runs r
            WHERE is_canonical = 1
        )
        WHERE rn = 1
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


def log_portfolio_run(
    strategy_name: str,
    symbols: list[str],
    start: date | None,
    end: date | None,
    final_equity: float,
    return_pct: float,
    cagr_pct: float | None,
    max_drawdown_pct: float,
    sharpe: float | None,
    sortino: float | None,
    risk_free_rate: float,
    params: dict | None = None,
    pair: tuple[str, str, float] | None = None,
    is_canonical: bool = True,
    benchmark_return_pct: float | None = None,
    status: str | None = None,
) -> int:
    """Counterpart to log_run() for the cross-sectional/pairs engines --
    see engine/runner.py's run_cross_sectional/run_pairs, which call this
    right after computing a result the same way every other `_run_*`
    helper calls log_run(). `pair` is (symbol_a, symbol_b, p_value) for a
    Pairs / Stat Arb run that found a cointegrated pair, else None (both
    for Dual Momentum, which has no pair concept, and for a Pairs run that
    found nothing to trade -- still worth logging as "ran, found no pair"
    rather than leaving no record at all). `benchmark_return_pct` is SPY's
    buy-and-hold return over the same window and `status` the verdict from
    engine/metrics.py:portfolio_status(); a None status means the run has
    no meaningful verdict (e.g. a Pairs run that found no pair)."""
    conn = get_connection()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO portfolio_runs (
                run_at, strategy_name, symbols, start_date, end_date, params,
                final_equity, return_pct, cagr_pct, max_drawdown_pct, sharpe, sortino,
                risk_free_rate, pair_symbol_a, pair_symbol_b, pair_p_value, is_canonical,
                benchmark_return_pct, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                strategy_name,
                json.dumps(symbols),
                start.isoformat() if isinstance(start, date) else start,
                end.isoformat() if isinstance(end, date) else end,
                json.dumps(params or {}),
                final_equity,
                return_pct,
                cagr_pct,
                max_drawdown_pct,
                sharpe,
                sortino,
                risk_free_rate,
                pair[0] if pair else None,
                pair[1] if pair else None,
                pair[2] if pair else None,
                int(is_canonical),
                benchmark_return_pct,
                status,
            ),
        )
    conn.close()
    return cursor.lastrowid


def latest_portfolio_run_per_strategy() -> dict[str, sqlite3.Row]:
    """Most recent CANONICAL portfolio run per strategy -- same shape/intent
    as latest_run_per_strategy() above, for the cross-sectional/pairs table."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT r.* FROM portfolio_runs r
        INNER JOIN (
            SELECT strategy_name, MAX(run_at) AS max_run_at
            FROM portfolio_runs WHERE is_canonical = 1 GROUP BY strategy_name
        ) latest
        ON r.strategy_name = latest.strategy_name AND r.run_at = latest.max_run_at
        WHERE r.is_canonical = 1
        """
    ).fetchall()
    conn.close()
    return {row["strategy_name"]: row for row in rows}


def best_portfolio_run_per_strategy() -> dict[str, sqlite3.Row]:
    """Best-Sharpe CANONICAL portfolio run per strategy -- same shape/intent
    as best_run_per_strategy() above, for the cross-sectional/pairs table.

    Rows with a real (non-NULL) status verdict are preferred over rows
    without one, BEFORE ranking by Sharpe -- the exact same
    pre-instrumentation-shadowing fix best_run_per_strategy() applies for
    alpha_pct (see its docstring and LESSONS.md): status only exists on
    runs logged since the benchmark/status migration, and without this
    tier a marginally-better-Sharpe old row silently hides the verdict on
    every re-run. Measured directly: Pairs / Stat Arb's old row
    (sharpe -0.7343, no status) outranked its instrumented re-run
    (sharpe -0.7346, real status) by 0.0003 Sharpe."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT r.*, ROW_NUMBER() OVER (
                PARTITION BY strategy_name
                ORDER BY (status IS NULL) ASC, (sharpe IS NULL) ASC,
                         sharpe DESC, run_at DESC, id DESC
            ) AS rn
            FROM portfolio_runs r
            WHERE is_canonical = 1
        )
        WHERE rn = 1
        """
    ).fetchall()
    conn.close()
    return {row["strategy_name"]: row for row in rows}


def portfolio_run_history(strategy_name: str) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        # id DESC as a tiebreaker: run_at has only second resolution, same
        # as `runs` above, so two runs in the same second would otherwise
        # sort arbitrarily rather than newest-insert-first.
        "SELECT * FROM portfolio_runs WHERE strategy_name = ? ORDER BY run_at DESC, id DESC",
        (strategy_name,),
    ).fetchall()
    conn.close()
    return rows
