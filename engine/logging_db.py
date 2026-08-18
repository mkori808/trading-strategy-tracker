"""SQLite run-history log: every backtest run's parameters and metrics.

So "ORB with 15-min range" and "ORB with 30-min range" don't get silently
conflated, every run also stores a `params` JSON blob of the rule
parameters in effect at run time.

`is_canonical` distinguishes a strategy's registered-default run (symbols,
date range, and params all untouched -- see engine/runner.py:RunRequest)
from a one-off experiment run with overrides. Both `latest_run_per_strategy()`
and `best_run_per_strategy()` only ever consider canonical rows, so the
dashboard's leaderboard is never silently replaced by whatever parameter
sweep happened to run last. The Compare tab uses the latest canonical run;
choosing an older row after observing Sharpe would mix selection with
validation. `run_history()`
still returns every row, canonical and experimental, so the webapp can show
"your experiments" alongside the canonical run history.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from engine.metrics import BacktestMetrics, implausible_metrics
from engine.research_governance import VALIDATION_REPORT_VERSION

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

# Edge-validation outcome, added 2026-08-11. engine/validation.py already
# produced a full ValidationReport for every run, but the API computed it AFTER
# the row was logged and returned it live -- so it was never persisted and every
# historical row read "validation required" regardless of what the validation
# had actually concluded. The report is the expensive part (random-portfolio
# Monte Carlo, rolling windows, cross-universe arms); recomputing it to display
# a past run would cost minutes per row.
#
# `edge_verdict` is a short denormalised label for the leaderboard/history
# column; `validation_json` is the whole report, so selecting a run can show
# every dimension and check without a re-run.
_VALIDATION_COLUMNS = [
    ("edge_verdict", "TEXT"),
    ("validation_json", "TEXT"),
]

# Research-governance provenance. `experiment_id` links the run to the plan
# recorded before execution; `manifest_json` fingerprints code, data, config,
# dependencies, and seeds; `lifecycle_stage` is the server-enforced promotion
# state derived from dimensional evidence rather than a UI label.
_RESEARCH_COLUMNS = [
    ("experiment_id", "INTEGER"),
    ("manifest_json", "TEXT"),
    ("lifecycle_stage", "TEXT"),
    ("universe_id", "TEXT"),
]

_EXPERIMENT_NEW_COLUMNS = [
    ("universe_id", "TEXT"),
    ("pre_result_mda_pct", "REAL"),
    ("family_search_count", "INTEGER"),
]

# Added 2026-08-14 so a strategy can be promoted to a (paper-only) forward
# test despite failing validation gates -- an explicit, per-strategy,
# LOGGED override, never a silent bypass or a global switch. The user's own
# framing: they want to forward-test ANY strategy even if it doesn't pass
# every gate, which is defensible for paper capital specifically (a forward
# test's whole purpose is gathering new out-of-sample evidence -- including
# for a strategy whose historical sample was underpowered), but the override
# itself must remain visible forever on the row it applied to, not just in
# that session's UI. `override_blockers_json` freezes exactly which checks
# were failing AT OVERRIDE TIME, since a strategy's live-recomputed status
# can change on a later view (see engine/metrics.py:derive_status's own
# "recomputed, never trusted as logged" rule) -- the override's justification
# must not silently drift with it.
_FORWARD_EXPERIMENT_NEW_COLUMNS = [
    ("override_used", "INTEGER NOT NULL DEFAULT 0"),
    ("override_reason", "TEXT"),
    ("override_blockers_json", "TEXT"),
    ("override_at", "TEXT"),
]

# Shared-capital benchmark measurements for the standard engine.  The legacy
# ``alpha_pct`` column is the mean of independent per-symbol account gaps and
# is useful only for that older diagnostic.  It is not comparable with a
# portfolio engine's return-minus-SPY number, so the leaderboard must read
# these explicitly named fields instead.
_STANDARD_BENCHMARK_COLUMNS = [
    ("strategy_return_pct", "REAL"),
    ("benchmark_return_pct", "REAL"),
    ("benchmark_gap_pct", "REAL"),
    ("benchmark_name", "TEXT"),
    # Added 2026-08-12. Two identical-trades, identical-Sharpe canonical rows
    # of the same strategy showed different Gap vs SPY (e.g. Breakout from
    # Consolidation: -52.7% then -86.2%). Root cause: this benchmark is
    # computed over measured_start/measured_end (validate_standard() falls
    # back to result.start/result.end only when measured_* is unset), and a
    # canonical run's end date defaults to "today" -- so a re-run on a later
    # calendar day silently extends the SPY comparison window even when the
    # strategy itself generated no new trades in the gap. That is a real,
    # intended behavior (the benchmark should track actual measured
    # coverage), not a bug to suppress -- but it must be visible, not
    # inferred. These columns record the EXACT window that produced
    # benchmark_return_pct/benchmark_gap_pct on this row, so the basis is
    # readable from the row itself instead of reconstructed by assuming
    # which fallback applied.
    ("benchmark_window_start", "TEXT"),
    ("benchmark_window_end", "TEXT"),
]

# Exposure-matched evidence and execution economics for sparse strategies.
# These deliberately coexist with the legacy full-window benchmark fields:
# the latter remain useful descriptive context but are not the edge estimate.
_MATCHED_BENCHMARK_COLUMNS = [
    ("total_return_pct", "REAL"),
    ("average_gross_exposure_pct", "REAL"),
    ("average_net_exposure_pct", "REAL"),
    ("time_in_market_pct", "REAL"),
    ("turnover_pct", "REAL"),
    ("modeled_costs", "REAL"),
    ("matched_spy_return_pct", "REAL"),
    ("matched_spy_excess_pct", "REAL"),
    ("annualized_matched_excess_pct", "REAL"),
    ("matched_alpha_annual_pct", "REAL"),
    ("matched_beta", "REAL"),
    ("matched_benchmark_trades", "INTEGER"),
    ("missing_benchmark_trades", "INTEGER"),
]

_EXPERIMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    strategy_name TEXT NOT NULL,
    engine TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    config_json TEXT NOT NULL,
    primary_benchmark TEXT NOT NULL,
    primary_criterion TEXT NOT NULL,
    planned_universes_json TEXT NOT NULL,
    search_family TEXT NOT NULL,
    family_search_number INTEGER NOT NULL,
    is_preregistered INTEGER NOT NULL,
    status TEXT NOT NULL,
    verdict TEXT,
    UNIQUE (search_family, family_search_number)
);

CREATE TABLE IF NOT EXISTS research_equity_curves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archived_at TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    experiment_id INTEGER,
    run_fingerprint TEXT NOT NULL,
    curve_json TEXT NOT NULL,
    UNIQUE(strategy_name, run_fingerprint)
);

CREATE TABLE IF NOT EXISTS forward_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    source_table TEXT NOT NULL,
    validation_run_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    frozen_manifest_hash TEXT NOT NULL,
    frozen_config_json TEXT NOT NULL,
    benchmark TEXT NOT NULL,
    primary_criterion TEXT NOT NULL,
    min_calendar_days INTEGER NOT NULL,
    min_observations INTEGER NOT NULL,
    max_shortfall_pct REAL NOT NULL,
    status TEXT NOT NULL,
    conclusion TEXT,
    last_evaluated_at TEXT,
    locked INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_table, validation_run_id)
);

CREATE TABLE IF NOT EXISTS forward_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forward_experiment_id INTEGER NOT NULL REFERENCES forward_experiments(id),
    as_of TEXT NOT NULL,
    strategy_return_pct REAL NOT NULL,
    benchmark_return_pct REAL NOT NULL,
    trade_count INTEGER,
    realized_slippage_bps REAL,
    expected_slippage_bps REAL,
    turnover_pct REAL,
    recorded_at TEXT NOT NULL,
    UNIQUE(forward_experiment_id, as_of)
);

CREATE TABLE IF NOT EXISTS universe_sweep_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    engine TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    experiment_id INTEGER NOT NULL REFERENCES research_experiments(id),
    status TEXT NOT NULL,
    pre_result_mda_pct REAL,
    benchmark_gap_pct REAL,
    gates_passed INTEGER,
    gates_applicable INTEGER,
    verdict TEXT,
    report_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(sweep_id, strategy_name, universe_id)
);

CREATE TABLE IF NOT EXISTS frozen_neighbor_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL UNIQUE REFERENCES research_experiments(id),
    strategy_name TEXT NOT NULL,
    search_family TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    return_pct REAL,
    cagr_pct REAL,
    sharpe REAL,
    max_drawdown_pct REAL,
    trades INTEGER,
    win_rate_pct REAL,
    expectancy_r REAL,
    profit_factor REAL,
    average_exposure_pct REAL,
    benchmark_excess_pct REAL,
    modeled_costs REAL,
    supports_hypothesis INTEGER,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
"""

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
        for name, col_type in _PROVENANCE_COLUMNS + _VALIDATION_COLUMNS + _RESEARCH_COLUMNS:
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
    present_runs = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name, col_type in _STANDARD_BENCHMARK_COLUMNS + _MATCHED_BENCHMARK_COLUMNS:
        if name not in present_runs:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {col_type}")
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
    conn.executescript(_EXPERIMENT_SCHEMA)
    experiment_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(research_experiments)")
    }
    for name, col_type in _EXPERIMENT_NEW_COLUMNS:
        if name not in experiment_columns:
            conn.execute(f"ALTER TABLE research_experiments ADD COLUMN {name} {col_type}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_research_family_number "
        "ON research_experiments(search_family, family_search_number)"
    )
    forward_experiment_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(forward_experiments)")
    }
    for name, col_type in _FORWARD_EXPERIMENT_NEW_COLUMNS:
        if name not in forward_experiment_columns:
            conn.execute(f"ALTER TABLE forward_experiments ADD COLUMN {name} {col_type}")
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
    universe_id: str | None = None,
) -> int:
    # Refuse to PERSIST an impossible number. Enforced at the write boundary so
    # it covers every caller -- the CLI, the API, the Lab tab and the backfill --
    # rather than only whichever path someone remembered to guard. A bad row is
    # far more expensive than a failed run: once written it is indistinguishable
    # from a real result, and every downstream reader (leaderboard, history
    # chart, chat assistant) treats it as measured fact.
    #
    # years uses the MEASURED window, not the requested one -- the same
    # "measured, not requested" rule invested_days()/coverage_is_measurable()
    # already apply, since the requested label can overstate an intraday
    # run's real span by an order of magnitude. Falls back to start/end when
    # measured_* is unset (pre-provenance rows, or a strategy that doesn't
    # track the distinction). See implausible_metrics's `years`-aware CAGR
    # check -- this is the per-symbol counterpart to log_portfolio_run's
    # identical guard, added for the same reason (see
    # PLAUSIBLE_SUSTAINED_ANNUAL_PCT's docstring).
    span_start = metrics.measured_start or metrics.start
    span_end = metrics.measured_end or metrics.end
    years = (
        max(0.0, (span_end - span_start).days / 365.25)
        if isinstance(span_start, date) and isinstance(span_end, date) else None
    )
    problems = implausible_metrics(
        sharpe=metrics.sharpe, cagr_pct=metrics.cagr_pct,
        win_rate=metrics.win_rate, exposure_pct=metrics.exposure_pct,
        years=years,
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
                measured_start, measured_end, universe_id,
                total_return_pct, average_gross_exposure_pct, average_net_exposure_pct,
                time_in_market_pct, turnover_pct, modeled_costs,
                matched_spy_return_pct, matched_spy_excess_pct,
                annualized_matched_excess_pct, matched_alpha_annual_pct, matched_beta,
                matched_benchmark_trades, missing_benchmark_trades
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                universe_id,
                metrics.total_return_pct,
                metrics.average_gross_exposure_pct,
                metrics.average_net_exposure_pct,
                metrics.time_in_market_pct,
                metrics.turnover_pct,
                metrics.modeled_costs,
                metrics.matched_spy_return_pct,
                metrics.matched_spy_excess_pct,
                metrics.annualized_matched_excess_pct,
                metrics.matched_alpha_annual_pct,
                metrics.matched_beta,
                metrics.matched_benchmark_trades,
                metrics.missing_benchmark_trades,
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
        SELECT * FROM (
            SELECT r.*, ROW_NUMBER() OVER (
                PARTITION BY strategy_name
                ORDER BY run_at DESC, id DESC
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


def latest_run_per_strategy_by_universe(universe_id: str) -> dict[str, sqlite3.Row]:
    """Most recent run per strategy against a SPECIFIC registered universe,
    regardless of is_canonical -- a universe override always logs as an
    experiment (see engine/runner.py:RunRequest.is_default()), so requiring
    is_canonical=1 here would return nothing for every non-default universe.
    Powers the Strategies tab's universe filter: pick a universe, see what
    has actually been measured there, strategy by strategy, rather than
    triggering a fresh 25-strategy sweep on every selection."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT r.*, ROW_NUMBER() OVER (
                PARTITION BY strategy_name
                ORDER BY run_at DESC, id DESC
            ) AS rn
            FROM runs r
            WHERE universe_id = :universe_id AND metrics_version = :version
        )
        WHERE rn = 1
        """,
        {"universe_id": universe_id, "version": METRICS_VERSION},
    ).fetchall()
    conn.close()
    return {row["strategy_name"]: row for row in rows}


def latest_portfolio_run_per_strategy_by_universe(universe_id: str) -> dict[str, sqlite3.Row]:
    """Cross-sectional/pairs counterpart to
    latest_run_per_strategy_by_universe() -- same intent, portfolio_runs table."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT r.*, ROW_NUMBER() OVER (
                PARTITION BY strategy_name
                ORDER BY run_at DESC, id DESC
            ) AS rn
            FROM portfolio_runs r
            WHERE universe_id = :universe_id AND metrics_version = :version
        )
        WHERE rn = 1
        """,
        {"universe_id": universe_id, "version": METRICS_VERSION},
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

    Rows with a persisted validation report are preferred first: the
    leaderboard is now an evidence dashboard, and showing an older unvalidated
    high-Sharpe row would resurrect the permissive legacy status. Within that
    tier, rows with a real (non-NULL) alpha_pct are preferred over rows without
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
                ORDER BY (validation_json IS NULL) ASC,
                         (alpha_pct IS NULL) ASC, (sharpe IS NULL) ASC,
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
    universe_id: str | None = None,
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
    # return_pct/years closes a gap log_run() didn't need to have: per-symbol
    # BacktestMetrics carries no cumulative return_pct field to check, but
    # this function always has one, and a flat annualized-CAGR check alone
    # cannot catch a smoothly-compounding multi-year run whose CUMULATIVE
    # effect is implausible even though its annualized rate individually
    # passes -- see PLAUSIBLE_SUSTAINED_ANNUAL_PCT's docstring for the exact
    # run (92.27%/yr, +2477.8% cumulative) that motivated this.
    span_years = (
        max(0.0, (end - start).days / 365.25)
        if isinstance(start, date) and isinstance(end, date) else None
    )
    problems = implausible_metrics(
        sharpe=sharpe, cagr_pct=cagr_pct, return_pct=return_pct, years=span_years,
    )
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
                measured_start, measured_end, universe_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                universe_id,
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
        SELECT * FROM (
            SELECT r.*, ROW_NUMBER() OVER (
                PARTITION BY strategy_name
                ORDER BY run_at DESC, id DESC
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


def best_portfolio_run_per_strategy() -> dict[str, sqlite3.Row]:
    """Best-Sharpe CANONICAL portfolio run per strategy -- same shape/intent
    as best_run_per_strategy() above, for the cross-sectional/pairs table.

    Rows with a persisted validation report are preferred first so the
    displayed performance and displayed edge verdict always come from the
    same run. Rows with a real (non-NULL) status verdict are then preferred over rows
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
                ORDER BY (validation_json IS NULL) ASC,
                         (status IS NULL) ASC, (sharpe IS NULL) ASC,
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


def attach_validation(table: str, run_id: int | None, report: dict | None) -> None:
    """Persist an edge-validation report against an already-logged run.

    Separate from log_run/log_portfolio_run because validation runs AFTER the
    backtest it validates -- it replays nearby parameter arms, random
    portfolios and rolling windows over the same result, so it cannot be
    computed before the row exists. Attaching afterwards by id keeps a single
    row per run rather than writing a second one.

    Silently no-ops on a missing id or report: validation is optional (the CLI
    does not run it), and a run without one is honestly "not validated" rather
    than an error.
    """
    if run_id is None or not report:
        return
    if table not in ("runs", "portfolio_runs"):
        raise ValueError(f"unknown table {table!r}")
    verdict_payload = report.get("verdict") or {}
    verdict = verdict_payload.get("headline")
    research = report.get("research") or {}
    manifest = research.get("manifest")
    experiment_id = research.get("experimentId")
    lifecycle = verdict_payload.get("lifecycleStage") or research.get("lifecycleStage")
    conn = get_connection()
    with conn:
        conn.execute(
            f"UPDATE {table} SET edge_verdict = ?, validation_json = ?, "
            "experiment_id = ?, manifest_json = ?, lifecycle_stage = ? WHERE id = ?",
            (
                verdict,
                json.dumps(report),
                experiment_id,
                json.dumps(manifest) if manifest else None,
                lifecycle,
                run_id,
            ),
        )
    conn.close()


def attach_standard_benchmark(
    run_id: int | None,
    *,
    strategy_return_pct: float,
    benchmark_return_pct: float | None,
    benchmark_name: str = "SPY",
    benchmark_window_start: date | None = None,
    benchmark_window_end: date | None = None,
) -> None:
    """Persist the standard engine's shared-capital benchmark comparison.

    This runs after portfolio aggregation for the same reason validation is
    attached after logging: the per-symbol metrics row exists before the
    shared-capital portfolio and identical-date SPY return are computed.

    `benchmark_window_start`/`benchmark_window_end` must be the EXACT window
    the caller used to compute `benchmark_return_pct` (validate_standard's
    measured_start/measured_end, including whichever fallback applied) --
    see _STANDARD_BENCHMARK_COLUMNS for why this is recorded rather than left
    for a reader to infer.
    """
    if run_id is None:
        return
    gap = (
        None if benchmark_return_pct is None
        else float(strategy_return_pct) - float(benchmark_return_pct)
    )
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE runs SET strategy_return_pct = ?, benchmark_return_pct = ?, "
            "benchmark_gap_pct = ?, benchmark_name = ?, "
            "benchmark_window_start = ?, benchmark_window_end = ? WHERE id = ?",
            (
                strategy_return_pct, benchmark_return_pct, gap, benchmark_name,
                benchmark_window_start.isoformat() if isinstance(benchmark_window_start, date) else benchmark_window_start,
                benchmark_window_end.isoformat() if isinstance(benchmark_window_end, date) else benchmark_window_end,
                run_id,
            ),
        )
    conn.close()


def register_experiment(
    *,
    strategy_name: str,
    engine: str,
    hypothesis: str,
    config: dict,
    primary_benchmark: str,
    primary_criterion: str,
    planned_universes: list[str],
    search_family: str,
    is_preregistered: bool,
    universe_id: str | None = None,
    pre_result_mda_pct: float | None = None,
    family_search_count: int | None = None,
) -> tuple[int, int]:
    """Persist an immutable plan before a validation job sees its results."""
    conn = get_connection()
    # Reserve the next family number under a write lock. Validation jobs may
    # start concurrently; COUNT-then-INSERT outside one transaction can assign
    # the same multiplicity number to two experiments and under-correct both.
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(family_search_number), 0) "
            "FROM research_experiments WHERE search_family = ?",
            (search_family,),
        ).fetchone()
        family_search_number = int(row[0]) + 1
        cursor = conn.execute(
            """
            INSERT INTO research_experiments (
                created_at, strategy_name, engine, hypothesis, config_json,
                primary_benchmark, primary_criterion, planned_universes_json,
                search_family, family_search_number, is_preregistered, status,
                universe_id, pre_result_mda_pct, family_search_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'registered', ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                strategy_name,
                engine,
                hypothesis,
                json.dumps(config, sort_keys=True),
                primary_benchmark,
                primary_criterion,
                json.dumps(planned_universes),
                search_family,
                family_search_number,
                int(is_preregistered),
                universe_id,
                pre_result_mda_pct,
                family_search_count,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return cursor.lastrowid, family_search_number


def complete_experiment(experiment_id: int | None, status: str, verdict: str | None = None) -> None:
    if experiment_id is None:
        return
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE research_experiments SET completed_at = ?, status = ?, verdict = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), status, verdict, experiment_id),
        )
    conn.close()


def family_search_count(search_family: str) -> int:
    conn = get_connection()
    count = int(conn.execute(
        "SELECT COUNT(*) FROM research_experiments WHERE search_family = ?",
        (search_family,),
    ).fetchone()[0])
    conn.close()
    return max(1, count)


def set_family_search_count(search_family: str, count: int) -> None:
    """Freeze the full preregistered family width onto every family member."""
    if count < 1:
        raise ValueError("family search count must be positive")
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE research_experiments SET family_search_count = ? WHERE search_family = ?",
            (count, search_family),
        )
    conn.close()


def record_frozen_neighbor_result(
    *, experiment_id: int, strategy_name: str, search_family: str,
    config: dict, status: str, result: dict | None = None,
    error: str | None = None,
) -> None:
    """Persist one executed preregistered arm without creating a run-history row."""
    result = result or {}
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO frozen_neighbor_results (
                experiment_id, strategy_name, search_family, config_json,
                status, return_pct, cagr_pct, sharpe, max_drawdown_pct,
                trades, win_rate_pct, expectancy_r, profit_factor,
                average_exposure_pct, benchmark_excess_pct, modeled_costs,
                supports_hypothesis, result_json, error, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(experiment_id) DO UPDATE SET
                status=excluded.status, return_pct=excluded.return_pct,
                cagr_pct=excluded.cagr_pct, sharpe=excluded.sharpe,
                max_drawdown_pct=excluded.max_drawdown_pct,
                trades=excluded.trades, win_rate_pct=excluded.win_rate_pct,
                expectancy_r=excluded.expectancy_r,
                profit_factor=excluded.profit_factor,
                average_exposure_pct=excluded.average_exposure_pct,
                benchmark_excess_pct=excluded.benchmark_excess_pct,
                modeled_costs=excluded.modeled_costs,
                supports_hypothesis=excluded.supports_hypothesis,
                result_json=excluded.result_json, error=excluded.error,
                completed_at=excluded.completed_at
            """,
            (
                experiment_id, strategy_name, search_family,
                json.dumps(config, sort_keys=True), status,
                result.get("returnPct"), result.get("cagrPct"),
                result.get("sharpe"), result.get("maxDrawdownPct"),
                result.get("trades"), result.get("winRatePct"),
                result.get("expectancyR"), result.get("profitFactor"),
                result.get("averageExposurePct"),
                result.get("benchmarkExcessPct"), result.get("modeledCosts"),
                None if "supportsHypothesis" not in result
                else int(bool(result["supportsHypothesis"])),
                json.dumps(result, sort_keys=True), error, now,
                now if status in {"completed", "failed", "blocked"} else None,
            ),
        )
    conn.close()


def frozen_neighbor_results(search_family: str) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM frozen_neighbor_results WHERE search_family = ? ORDER BY experiment_id",
        (search_family,),
    ).fetchall()
    conn.close()
    return rows


def record_universe_sweep_cell(
    *, sweep_id: str, strategy_name: str, engine: str, universe_id: str,
    experiment_id: int, status: str, pre_result_mda_pct: float | None,
    benchmark_gap_pct: float | None = None, gates_passed: int | None = None,
    gates_applicable: int | None = None, verdict: str | None = None,
    report: dict | None = None, error: str | None = None,
) -> None:
    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.execute(
            """
            INSERT INTO universe_sweep_results (
                sweep_id, strategy_name, engine, universe_id, experiment_id,
                status, pre_result_mda_pct, benchmark_gap_pct, gates_passed,
                gates_applicable, verdict, report_json, error, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sweep_id, strategy_name, universe_id) DO UPDATE SET
                status=excluded.status,
                benchmark_gap_pct=excluded.benchmark_gap_pct,
                gates_passed=excluded.gates_passed,
                gates_applicable=excluded.gates_applicable,
                verdict=excluded.verdict,
                report_json=excluded.report_json,
                error=excluded.error,
                completed_at=excluded.completed_at
            """,
            (
                sweep_id, strategy_name, engine, universe_id, experiment_id,
                status, pre_result_mda_pct, benchmark_gap_pct, gates_passed,
                gates_applicable, verdict, json.dumps(report) if report else None,
                error, now, now if status in {"completed", "blocked", "failed"} else None,
            ),
        )
    conn.close()


def universe_sweep_matrix(sweep_id: str | None = None) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    if sweep_id is None:
        row = conn.execute(
            "SELECT sweep_id FROM universe_sweep_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
        sweep_id = str(row[0]) if row else ""
    rows = conn.execute(
        "SELECT * FROM universe_sweep_results WHERE sweep_id = ? "
        "ORDER BY strategy_name, universe_id",
        (sweep_id,),
    ).fetchall()
    conn.close()
    return rows


def experiment(experiment_id: int | None) -> sqlite3.Row | None:
    if experiment_id is None:
        return None
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM research_experiments WHERE id = ?", (experiment_id,)
    ).fetchone()
    conn.close()
    return row


def experiment_history(strategy_name: str, limit: int = 100) -> list[sqlite3.Row]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM research_experiments WHERE strategy_name = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (strategy_name, limit),
    ).fetchall()
    conn.close()
    return rows


def archive_equity_curve(
    *, strategy_name: str, experiment_id: int | None, run_fingerprint: str, equity,
) -> None:
    """Archive one daily curve for later portfolio-interaction tests."""
    import pandas as pd

    if equity is None or len(equity) < 2:
        return
    series = pd.to_numeric(equity, errors="coerce").dropna().sort_index()
    daily = series.groupby(series.index.normalize()).last().dropna()
    payload = [[timestamp.isoformat(), float(value)] for timestamp, value in daily.items()]
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO research_equity_curves "
            "(archived_at, strategy_name, experiment_id, run_fingerprint, curve_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), strategy_name, experiment_id,
             run_fingerprint, json.dumps(payload)),
        )
    conn.close()


def peer_equity_curves(strategy_name: str, limit: int = 25) -> dict[str, object]:
    """Latest archived curve per other strategy, returned as pandas Series."""
    import pandas as pd

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT c.* FROM research_equity_curves c
        JOIN (
            SELECT strategy_name, MAX(id) AS latest_id
            FROM research_equity_curves WHERE strategy_name != ? GROUP BY strategy_name
        ) latest ON latest.latest_id = c.id
        ORDER BY c.id DESC LIMIT ?
        """,
        (strategy_name, limit),
    ).fetchall()
    conn.close()
    curves = {}
    for row in rows:
        points = json.loads(row["curve_json"])
        curves[row["strategy_name"]] = pd.Series(
            [float(point[1]) for point in points],
            # Intraday archives cross daylight-saving boundaries, so their
            # ISO offsets legitimately mix -04:00 and -05:00.  Constructing a
            # DatetimeIndex directly from those aware values fails on recent
            # pandas; normalize every archive to one UTC timeline on read.
            index=pd.DatetimeIndex(pd.to_datetime(
                [point[0] for point in points], utc=True,
            )),
        )
    return curves


def strategy_equity_curves(strategy_name: str, limit: int = 50) -> dict[str, object]:
    """Archived searched configurations for combinatorial PBO estimation."""
    import pandas as pd

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM research_equity_curves WHERE strategy_name=? ORDER BY id DESC LIMIT ?",
        (strategy_name, limit),
    ).fetchall()
    conn.close()
    curves = {}
    for row in rows:
        points = json.loads(row["curve_json"])
        curves[row["run_fingerprint"]] = pd.Series(
            [float(point[1]) for point in points],
            index=pd.DatetimeIndex(pd.to_datetime(
                [point[0] for point in points], utc=True,
            )),
        )
    return curves


def canonical_portfolio_validation(
    strategy_name: str, run_id: int | None = None,
) -> tuple[sqlite3.Row | None, dict | None]:
    """Return an exact selected run or the latest canonical run and report.

    This is the authorization source for paper execution.  It deliberately
    reads the persisted report rather than recomputing or trusting a status
    string supplied by the browser. When ``run_id`` is given, that exact
    current-metrics history row may be canonical or exploratory; otherwise the
    latest validated canonical row is used by the Live tab's generic toggle.
    The caller must execute the stored row configuration, never browser-sent
    parameters, when an exploratory row is selected.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    if run_id is not None:
        row = conn.execute(
            "SELECT * FROM portfolio_runs WHERE id = ? AND strategy_name = ? "
            "AND metrics_version = ?",
            (run_id, strategy_name, METRICS_VERSION),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM portfolio_runs WHERE strategy_name = ? "
            "AND is_canonical = 1 AND metrics_version = ? "
            "AND validation_json IS NOT NULL ORDER BY run_at DESC, id DESC LIMIT 1",
            (strategy_name, METRICS_VERSION),
        ).fetchone()
    conn.close()
    if row is None or not row["validation_json"]:
        return row, None
    try:
        return row, json.loads(row["validation_json"])
    except (TypeError, json.JSONDecodeError):
        return row, None


def paper_execution_eligibility(
    strategy_name: str, run_id: int | None = None,
) -> tuple[bool, str, int | None]:
    """Backend gate for paper capital, tied to one persisted exact run."""
    row, report = canonical_portfolio_validation(strategy_name, run_id)
    if row is None:
        return False, "No current persisted validation run exists", None
    if report is None:
        return False, "The selected run has no stored validation report", row["id"]
    if report.get("version") != VALIDATION_REPORT_VERSION:
        return False, "The selected run requires evaluation with the current validation suite", row["id"]
    verdict = report.get("verdict") or {}
    if not verdict.get("forwardTestWorthy", False):
        conn = get_connection()
        try:
            override = conn.execute(
                "SELECT 1 FROM forward_experiments WHERE strategy_name = ? "
                "AND validation_run_id = ? AND override_used = 1 "
                "AND status IN ('running', 'forward_validated') LIMIT 1",
                (strategy_name, row["id"]),
            ).fetchone()
        finally:
            conn.close()
        if override is not None:
            return True, "Logged paper-execution override is active", row["id"]
        blockers = verdict.get("blockers") or []
        reason = "; ".join(str(item) for item in blockers) or verdict.get("headline") or "validation did not approve forward testing"
        return False, f"Forward-test gate did not pass: {reason}", row["id"]
    lifecycle = verdict.get("lifecycleStage") or row["lifecycle_stage"]
    if lifecycle not in {"paper_eligible", "production_eligible"}:
        return False, f"Research lifecycle is {lifecycle or 'not recorded'}, not paper eligible", row["id"]
    return True, "Forward-test gate passed", row["id"]
