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

from engine.metrics import BacktestMetrics, implausible_metrics

class ImplausibleMetrics(ValueError):
    """A computed metric fell outside the plausibility floor.

    Its own type so the backfill can count it separately from both a
    deliberate refusal (InsufficientHistory) and an unexpected crash --
    three different facts that must not collapse into one tally."""


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


# Metric provenance, added 2026-08-10. A stored row's numbers are only
# interpretable as the TUPLE (metrics_version, return_basis, costs charged) --
# these are not independent facts, which is why they land in one migration
# rather than several. Without them a reader has to date-reason about which
# convention applied to which row, and that is precisely how a cross-sectional
# path that charged ZERO transaction costs sat unnoticed next to per-symbol
# strategies that all paid estimate_spread().
#
# Why this exists at all: engine/metrics.py:SHARPE_THRESHOLD was 0.5 while the
# best Sharpe any per-symbol strategy could achieve was -0.16, because
# engine/portfolio.py annualized calendar-day returns with a 252-day constant
# and idle cash was charged the risk-free rate it was never credited. SPY
# buy-and-hold itself scored 0.371 against that 0.5 bar. Fixing the metric
# changes every historical number, so rows are VERSIONED rather than
# overwritten: the pre-fix values are the honest record of what the tool
# reported when LESSONS.md's "no strategy clears the shortlist" conclusion was
# written, and deleting them would make that conclusion look unreasonable
# rather than correct-given-the-evidence.
_PROVENANCE_COLUMNS = [
    ("metrics_version", "INTEGER"),
    ("return_basis", "TEXT"),
    ("slippage_bps", "REAL"),
    ("commission_bps", "REAL"),
    # What was actually COVERED by data, vs. start_date/end_date which record
    # what was REQUESTED. Intraday runs measure ~50 days of 5-minute bars no
    # matter what window they ask for, so a day-trading row labelled
    # 2024-08-11 -> 2026-08-11 describes 6.8% of its own label. Both are kept:
    # the request is what the user configured, the measurement is what the
    # numbers describe, and conflating them is how 21,108 trades from seven
    # weeks came to read as a decade of evidence.
    ("measured_start", "TEXT"),
    ("measured_end", "TEXT"),
]

# Version 1: all four measurement fixes landed, plus the alpha-basis change
# the fourth one forced.
#   1. calendar-day annualization  (engine/portfolio.py:annualized_stats)
#   2. cross-sectional costs       (engine/runner.py:run_cross_sectional)
#   3. warmup preload              (engine/cross_sectional.py, raises on short history)
#   4. idle-cash rf accrual        (engine/backtest.py:accrue_idle_cash)
#   5. alpha as excess-over-cash   (replacing Jensen alpha, forced by 4)
#
# (5) was not on the original list. Crediting idle cash raised the strategy's
# return but not the fully-invested benchmark's, and Jensen alpha scales the
# benchmark leg by a beta that is near zero for a mostly-cash strategy -- so
# alpha absorbed the accrued interest almost 1:1 (+18.8 to +19.5pp against
# +19.6pp of interest) and flipped four strategies from "hold" to "shortlist"
# without one trade changing. Caught before any backfill precisely because the
# distortion was FLATTERING and a flattering result got more scrutiny, not
# less. A version-0 row and a version-1 row are not comparable on return,
# alpha, Sharpe, Sortino, CAGR or max drawdown.
# Version 2 (2026-08-11): four further convention changes, all of which alter
# what a stored row MEANS, so v1 rows are not comparable to v2 rows either.
#   1. invested-days floor -- Sharpe/Sortino withheld below MIN_INVESTED_DAYS,
#      where a ~99%-cash account makes the ratio degenerate (measured: Earnings
#      Momentum scored 51,844.877 after the accrual fix and -8.09 before it;
#      both are noise from the same near-zero denominator).
#   2. pooled equity-curve aggregation replacing the mean of per-symbol Sharpes.
#      A mean of ratios is not a ratio of means and the two disagreed in SIGN
#      (Turnaround Tuesday: +2.26 Sharpe against -0.11% excess CAGR). Also puts
#      the per-symbol and cross-sectional engines on one definition at last.
#   3. absent metrics can no longer satisfy a gate. Overnight Hold held the
#      board's only shortlist tier on Sharpe None AND alpha None -- a
#      28,370-trade row promotable to paper execution on two values that did
#      not exist.
#   4. measured_start/measured_end stored, with a coverage refusal derived from
#      them rather than from the requested window.
METRICS_VERSION = 2

# Rows that predate the concept entirely. Not backfilled with a guess: their
# return basis is whatever the engine happened to do at the time, which is
# total return with no cash accrual, and asserting anything more specific
# would be inventing provenance.
RETURN_BASIS_LEGACY = "legacy_total_unadjusted"

# Return net of the risk-free rate -- what the strategy ADDED over holding
# T-bills. Chosen over moving every tier threshold to compare against rf,
# because a per-window threshold would reintroduce exactly the per-window
# constants the differential calibration oracle exists to avoid (rf is 2.69%
# across 2018-2026 which spans ZIRP, but 3.65% across 2021-2026).
RETURN_BASIS_EXCESS = "excess_over_cash"

# Alpha under this basis is DEFINED as (R_strategy - rf) - (R_benchmark - rf),
# and is written that way in engine/backtest.py deliberately even though the rf
# terms cancel to R_strategy - R_benchmark.
#
# Do not "simplify" it without reading this. The original requirement was to
# verify that the benchmark's risk-free series matched the strategy's -- same
# window, same 13-week T-bill source, same compounding -- because a mismatch
# would bias every alpha in the same direction and be invisible in exactly the
# way the Jensen-alpha bug nearly was. Writing the basis as excess-minus-excess
# means neither rf series survives into the result, so that mismatch cannot
# occur at all. The cancellation IS the safety property: a test for agreement
# between two series can rot, the algebra cannot. The explicit form is the only
# remaining record of why no such check exists.


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
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

    # Provenance lands on BOTH tables -- a per-symbol run and a portfolio run
    # are equally uninterpretable without it.
    for table in ("runs", "portfolio_runs"):
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, col_type in _PROVENANCE_COLUMNS:
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
    # Stamp pre-existing rows as explicit legacy rather than leaving NULL, so
    # "unversioned" can be told apart from "written before the column existed".
    # Costs stay NULL on purpose: NULL means UNKNOWN, not zero. Writing 0 would
    # be accurate for the cross-sectional rows (which genuinely charged nothing)
    # but flatly wrong for per-symbol rows (which paid estimate_spread), and one
    # honest sentinel across both tables beats two different lies.
    #
    # ONCE, guarded by a marker -- NOT on every connection. Running it per
    # connection makes it a trap rather than a migration: any row inserted by a
    # code path that forgets the provenance columns gets silently relabelled
    # "legacy" the next time anything opens the database. That is not
    # hypothetical. log_run() was missed in the original change, so the first
    # backfilled run (a fully corrected, version-1 ORB result) was written with
    # a NULL version and then stamped legacy by the very next get_connection()
    # -- including the diagnostic query run to check on it. A NULL that survives
    # is a visible bug; a NULL silently rewritten as legacy is a corrupted
    # provenance record that reads as intentional.
    applied = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'provenance_legacy_stamp'"
    ).fetchone()
    if applied is None:
        for table in ("runs", "portfolio_runs"):
            conn.execute(
                f"UPDATE {table} SET metrics_version = 0, return_basis = ? "
                "WHERE metrics_version IS NULL",
                (RETURN_BASIS_LEGACY,),
            )
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('provenance_legacy_stamp', ?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )

    # The stamp and its marker MUST land atomically. Without this commit they
    # sit in an implicit transaction whose fate depends on whichever unrelated
    # `with conn:` block happens to run next -- and if the stamp persists while
    # the marker is lost, the guard above silently reverts to the original
    # every-connection behaviour. Observed directly: the marker was written
    # twice with different timestamps (00:32:25, then 00:32:43), which a
    # PRIMARY KEY makes impossible unless the first was rolled back.
    conn.commit()


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
    slippage_bps: float | None = None,
    commission_bps: float | None = None,
    return_basis: str = RETURN_BASIS_EXCESS,
) -> int:
    # Refuse to PERSIST an impossible number. Enforced at the write boundary so
    # it covers every caller -- the CLI, the API, the Lab tab and the backfill --
    # rather than only whichever path someone remembered to guard. A bad row is
    # far more expensive than a failed run: once written it is indistinguishable
    # from a real result, and every downstream reader (leaderboard, history
    # chart, chat assistant) treats it as measured fact.
    problems = implausible_metrics(
        sharpe=metrics.sharpe, cagr_pct=metrics.cagr_pct,
        win_rate=metrics.win_rate, exposure_pct=metrics.exposure_pct,
    )
    if problems:
        raise ImplausibleMetrics(
            f"refusing to log {metrics.strategy_name!r}: " + "; ".join(problems)
        )
    conn = get_connection()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO runs (
                run_at, strategy_name, symbols, start_date, end_date, params,
                trades_taken, wins, losses, win_rate, avg_win_r, avg_loss_r,
                expectancy_r, profit_factor, max_drawdown_pct, sharpe, sortino, status,
                alpha_pct, beta, cagr_pct, exposure_pct, risk_free_rate, is_canonical,
                metrics_version, return_basis, slippage_bps, commission_bps,
                measured_start, measured_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                METRICS_VERSION,
                return_basis,
                slippage_bps,
                commission_bps,
                metrics.measured_start.isoformat() if metrics.measured_start else None,
                metrics.measured_end.isoformat() if metrics.measured_end else None,
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
            FROM runs WHERE is_canonical = 1 AND metrics_version = :version GROUP BY strategy_name
        ) latest
        ON r.strategy_name = latest.strategy_name AND r.run_at = latest.max_run_at
        -- run_at has only second resolution, so a canonical and a
        -- non-canonical row CAN share a timestamp -- without this, the
        -- join would match both and Python's dict-building could silently
        -- keep the non-canonical one.
        WHERE r.is_canonical = 1 AND r.metrics_version = :version
        """,
        {"version": METRICS_VERSION},
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
            WHERE is_canonical = 1 AND metrics_version = :version
        )
        WHERE rn = 1
        """,
        {"version": METRICS_VERSION},
    ).fetchall()
    conn.close()
    return {row["strategy_name"]: row for row in rows}


def run_history(strategy_name: str) -> list[sqlite3.Row]:
    """Run history for the History tab -- CURRENT metrics version only.

    Pre-fix rows are excluded rather than shown-and-labelled. They were kept in
    the database as the honest record of what the tool reported when
    LESSONS.md's conclusions were written, but they are not comparable to
    current rows on ANY column: Sharpe, alpha, return, CAGR and max drawdown
    all changed convention (see METRICS_VERSION). Displaying them beside
    corrected rows in one table invites exactly the comparison that is invalid,
    and the numbers themselves are not merely imprecise -- e.g. a Sharpe of
    -8.09 that was an artifact of charging idle cash a rate it never earned.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM runs WHERE strategy_name = ? AND metrics_version = ? "
        "ORDER BY run_at DESC",
        (strategy_name, METRICS_VERSION),
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
    slippage_bps: float | None = None,
    commission_bps: float | None = None,
    return_basis: str = RETURN_BASIS_LEGACY,
    measured_start: date | None = None,
    measured_end: date | None = None,
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
    problems = implausible_metrics(sharpe=sharpe, cagr_pct=cagr_pct)
    if problems:
        raise ImplausibleMetrics(
            f"refusing to log {strategy_name!r}: " + "; ".join(problems)
        )
    conn = get_connection()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO portfolio_runs (
                run_at, strategy_name, symbols, start_date, end_date, params,
                final_equity, return_pct, cagr_pct, max_drawdown_pct, sharpe, sortino,
                risk_free_rate, pair_symbol_a, pair_symbol_b, pair_p_value, is_canonical,
                benchmark_return_pct, status,
                metrics_version, return_basis, slippage_bps, commission_bps,
                measured_start, measured_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                METRICS_VERSION,
                return_basis,
                slippage_bps,
                commission_bps,
                measured_start.isoformat() if isinstance(measured_start, date) else measured_start,
                measured_end.isoformat() if isinstance(measured_end, date) else measured_end,
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
            FROM portfolio_runs WHERE is_canonical = 1 AND metrics_version = :version GROUP BY strategy_name
        ) latest
        ON r.strategy_name = latest.strategy_name AND r.run_at = latest.max_run_at
        WHERE r.is_canonical = 1 AND r.metrics_version = :version
        """,
        {"version": METRICS_VERSION},
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
            WHERE is_canonical = 1 AND metrics_version = :version
        )
        WHERE rn = 1
        """,
        {"version": METRICS_VERSION},
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
        "SELECT * FROM portfolio_runs WHERE strategy_name = ? AND metrics_version = ? "
        "ORDER BY run_at DESC, id DESC",
        (strategy_name, METRICS_VERSION),
    ).fetchall()
    conn.close()
    return rows


def strategies_awaiting_remeasurement() -> set[str]:
    """Names that have a superseded-version canonical row but no current one.

    Lets the API say "measured, then invalidated, re-run pending" instead of
    "Not yet tested" -- which is simply false for a strategy that has been run
    dozens of times, and points a reader at the wrong action.
    """
    conn = get_connection()
    old = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT strategy_name FROM runs "
            "WHERE is_canonical = 1 AND metrics_version < ?", (METRICS_VERSION,)
        )
    } | {
        r[0] for r in conn.execute(
            "SELECT DISTINCT strategy_name FROM portfolio_runs "
            "WHERE is_canonical = 1 AND metrics_version < ?", (METRICS_VERSION,)
        )
    }
    current = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT strategy_name FROM runs "
            "WHERE is_canonical = 1 AND metrics_version = ?", (METRICS_VERSION,)
        )
    } | {
        r[0] for r in conn.execute(
            "SELECT DISTINCT strategy_name FROM portfolio_runs "
            "WHERE is_canonical = 1 AND metrics_version = ?", (METRICS_VERSION,)
        )
    }
    conn.close()
    return old - current
