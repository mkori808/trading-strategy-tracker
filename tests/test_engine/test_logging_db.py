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


def _metrics(name="S"):
    return compute_metrics(name, "ALL", pd.DataFrame(columns=["EntryPrice", "SL", "Size", "PnL"]))


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
