"""engine/logging_db.py: the is_canonical migration/backfill and the
canonical-only leaderboard query.

Uses a real temporary SQLite file (via monkeypatching DB_PATH) rather than
mocking sqlite3 -- the migration/backfill logic is exactly the kind of thing
that looks right and silently isn't; only running real DDL against a real
file catches that.
"""

from __future__ import annotations

import sqlite3

import pytest

from engine import logging_db
from engine.metrics import compute_metrics

import pandas as pd


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    return logging_db


def _metrics(name="S", sharpe=None, alpha_pct=None):
    return compute_metrics(
        name, "ALL", pd.DataFrame(columns=["EntryPrice", "SL", "Size", "PnL"]),
        sharpe=sharpe, alpha_pct=alpha_pct,
    )


def test_log_run_defaults_to_canonical(db):
    db.log_run(_metrics(), ["AAPL"])
    row = db.run_history("S")[0]
    assert row["is_canonical"] == 1


def test_log_run_records_non_canonical_explicitly(db):
    db.log_run(_metrics(), ["AAPL"], params={"x": 1}, is_canonical=False)
    row = db.run_history("S")[0]
    assert row["is_canonical"] == 0
    assert row["params"] == '{"x": 1}'


def test_latest_run_per_strategy_ignores_non_canonical_rows(db):
    db.log_run(_metrics(), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(), ["MSFT"], params={"tweaked": True}, is_canonical=False)
    latest = db.latest_run_per_strategy()
    assert latest["S"]["is_canonical"] == 1


def test_latest_run_per_strategy_absent_when_only_experiments_exist(db):
    """A strategy that has only ever been run with overrides must not show
    up in the canonical leaderboard at all -- there is no default result to
    show yet."""
    db.log_run(_metrics(), ["AAPL"], is_canonical=False)
    assert "S" not in db.latest_run_per_strategy()


def test_latest_run_per_strategy_picks_the_most_recent_canonical(db):
    db.log_run(_metrics(), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(), ["AAPL"], params={"x": 1}, is_canonical=False)
    db.log_run(_metrics(), ["MSFT"], is_canonical=True)  # most recent canonical
    latest = db.latest_run_per_strategy()
    assert latest["S"]["symbols"] == '["MSFT"]'


def test_best_run_per_strategy_ignores_non_canonical_rows(db):
    db.log_run(_metrics(sharpe=0.1), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=99.0), ["MSFT"], params={"tweaked": True}, is_canonical=False)
    best = db.best_run_per_strategy()
    assert best["S"]["is_canonical"] == 1
    assert best["S"]["sharpe"] == 0.1


def test_best_run_per_strategy_picks_the_highest_sharpe_not_the_latest(db):
    """The whole point of best_run_per_strategy() vs. latest_run_per_strategy():
    an earlier canonical run with a better Sharpe must win over a more recent
    one with a worse Sharpe -- e.g. a re-run against updated data that happens
    to score worse must not bury a strategy's best honest canonical result."""
    db.log_run(_metrics(sharpe=1.5), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=0.2), ["MSFT"], is_canonical=True)  # most recent, worse
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["AAPL"]'
    assert best["S"]["sharpe"] == 1.5


def test_best_run_per_strategy_ranks_null_sharpe_last(db):
    """A run with no computed Sharpe must never outrank one with a real,
    even negative, Sharpe -- there's nothing to verify a NULL is good."""
    db.log_run(_metrics(sharpe=None), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=-2.0), ["MSFT"], is_canonical=True)
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'


def test_best_run_per_strategy_breaks_ties_on_most_recent(db):
    db.log_run(_metrics(sharpe=0.5), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=0.5), ["MSFT"], is_canonical=True)
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'


def test_best_run_per_strategy_prefers_runs_with_alpha_over_higher_sharpe_without(db):
    """A NULL alpha_pct means this run predates the benchmark-relative
    migration (see engine/logging_db.py's module docstring), not that the
    strategy has nothing to beat -- it must never outrank a more complete,
    even worse-Sharpe, later run. Regression test for the real bug this
    caught: several strategies' best-Sharpe pick was a pre-migration row,
    silently showing alpha as unavailable on the Compare tab even though
    every current run of that strategy computes it."""
    db.log_run(_metrics(sharpe=2.0, alpha_pct=None), ["AAPL"], is_canonical=True)  # pre-migration, better Sharpe
    db.log_run(_metrics(sharpe=0.3, alpha_pct=-5.0), ["MSFT"], is_canonical=True)  # post-migration, worse Sharpe
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'
    assert best["S"]["alpha_pct"] == -5.0


def test_best_run_per_strategy_alpha_completeness_is_a_noop_when_no_run_has_it(db):
    """A strategy whose engine never computes alpha at all (e.g. Overnight
    Hold) must still rank purely by Sharpe -- every row ties on
    alpha-completeness, so it falls through unaffected."""
    db.log_run(_metrics(sharpe=-1.0, alpha_pct=None), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=0.5, alpha_pct=None), ["MSFT"], is_canonical=True)
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'


def test_run_history_returns_both_canonical_and_experiment_rows(db):
    db.log_run(_metrics(), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(), ["AAPL"], params={"x": 1}, is_canonical=False)
    assert len(db.run_history("S")) == 2


def test_migration_backfills_pre_existing_rows_as_canonical(db, tmp_path):
    """Rows written before is_canonical existed (simulated here by inserting
    directly against the old schema) must read back as canonical=1, not
    NULL -- NULL would silently vanish from latest_run_per_strategy's
    WHERE is_canonical = 1 clause."""
    old_schema_conn = sqlite3.connect(tmp_path / "runs.db")
    old_schema_conn.execute(logging_db._SCHEMA)  # pre-migration columns only
    old_schema_conn.execute(
        "INSERT INTO runs (run_at, strategy_name, symbols, trades_taken) "
        "VALUES ('2020-01-01T00:00:00', 'Legacy', '[]', 5)"
    )
    old_schema_conn.commit()
    old_schema_conn.close()

    # get_connection() runs _migrate() as a side effect of opening -- this
    # is the real migration path, not a re-implementation of it.
    latest = db.latest_run_per_strategy()
    assert latest["Legacy"]["is_canonical"] == 1


def test_migration_is_idempotent(db):
    """Opening the connection twice (ALTER TABLE ADD COLUMN on an already-
    migrated DB) must not raise."""
    db.log_run(_metrics(), ["AAPL"])
    db.get_connection().close()
    db.get_connection().close()
    assert len(db.run_history("S")) == 1


# --- portfolio_runs (cross-sectional / pairs) -----------------------------------


def _portfolio_kwargs(**overrides):
    kwargs = dict(
        strategy_name="Dual Momentum",
        symbols=["AAPL", "MSFT"],
        start=None,
        end=None,
        final_equity=11000.0,
        return_pct=10.0,
        cagr_pct=2.0,
        max_drawdown_pct=5.0,
        sharpe=0.5,
        sortino=0.6,
        risk_free_rate=0.03,
    )
    kwargs.update(overrides)
    return kwargs


def test_log_portfolio_run_defaults_to_canonical(db):
    db.log_portfolio_run(**_portfolio_kwargs())
    row = db.portfolio_run_history("Dual Momentum")[0]
    assert row["is_canonical"] == 1
    assert row["final_equity"] == 11000.0
    assert row["pair_symbol_a"] is None


def test_log_portfolio_run_records_pair_fields(db):
    db.log_portfolio_run(
        **_portfolio_kwargs(
            strategy_name="Pairs / Stat Arb", pair=("AAPL", "MSFT", 0.01),
        )
    )
    row = db.portfolio_run_history("Pairs / Stat Arb")[0]
    assert row["pair_symbol_a"] == "AAPL"
    assert row["pair_symbol_b"] == "MSFT"
    assert row["pair_p_value"] == 0.01


def test_log_portfolio_run_records_no_pair_found(db):
    """A Pairs run that found no cointegrated pair is still worth logging
    -- "ran, found nothing" is different from "never ran"."""
    db.log_portfolio_run(
        **_portfolio_kwargs(strategy_name="Pairs / Stat Arb", pair=None)
    )
    row = db.portfolio_run_history("Pairs / Stat Arb")[0]
    assert row["pair_symbol_a"] is None


def test_latest_portfolio_run_per_strategy_ignores_non_canonical(db):
    db.log_portfolio_run(**_portfolio_kwargs(is_canonical=True))
    db.log_portfolio_run(**_portfolio_kwargs(final_equity=99999.0, is_canonical=False))
    latest = db.latest_portfolio_run_per_strategy()
    assert latest["Dual Momentum"]["final_equity"] == 11000.0


def test_latest_portfolio_run_per_strategy_absent_when_only_experiments_exist(db):
    db.log_portfolio_run(**_portfolio_kwargs(is_canonical=False))
    assert "Dual Momentum" not in db.latest_portfolio_run_per_strategy()


def test_best_portfolio_run_per_strategy_picks_highest_sharpe(db):
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=0.3, final_equity=10500.0))
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=1.2, final_equity=10800.0))  # older, better
    best = db.best_portfolio_run_per_strategy()
    assert best["Dual Momentum"]["final_equity"] == 10800.0


def test_best_portfolio_run_per_strategy_ignores_non_canonical(db):
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=0.1, is_canonical=True))
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=99.0, is_canonical=False))
    best = db.best_portfolio_run_per_strategy()
    assert best["Dual Momentum"]["sharpe"] == 0.1


def test_portfolio_run_history_returns_all_runs_newest_first(db):
    db.log_portfolio_run(**_portfolio_kwargs(final_equity=10500.0))
    db.log_portfolio_run(**_portfolio_kwargs(final_equity=11000.0))
    rows = db.portfolio_run_history("Dual Momentum")
    assert len(rows) == 2
    assert rows[0]["final_equity"] == 11000.0  # most recent first
