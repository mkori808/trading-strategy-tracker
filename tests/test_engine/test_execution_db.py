"""engine/execution_db.py: the partial-unique-index race guard and basic
CRUD. Real temporary SQLite file (matching tests/test_engine/test_logging_db.py's
own justification: this kind of constraint logic looks right and silently
isn't -- only real DDL against a real file catches that)."""

from __future__ import annotations

import pytest

from engine import execution_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_db, "DB_PATH", tmp_path / "execution.db")
    monkeypatch.setattr(execution_db, "LOGS_DIR", tmp_path)
    return execution_db


NOW = "2026-07-20T12:00:00"


def test_is_enabled_defaults_false_for_an_unknown_strategy(db):
    assert db.is_enabled("Dual Momentum") is False


def test_set_enabled_round_trips(db):
    db.set_enabled("Dual Momentum", True, NOW)
    assert db.is_enabled("Dual Momentum") is True
    db.set_enabled("Dual Momentum", False, NOW)
    assert db.is_enabled("Dual Momentum") is False


def test_claim_run_succeeds_once_then_blocks_a_duplicate(db):
    first = db.claim_run("Dual Momentum", "2026-07-20", "manual", NOW)
    assert first is not None
    second = db.claim_run("Dual Momentum", "2026-07-20", "scheduled", NOW)
    assert second is None


def test_claim_run_allows_a_different_day_or_strategy(db):
    a = db.claim_run("Dual Momentum", "2026-07-20", "manual", NOW)
    b = db.claim_run("Dual Momentum", "2026-07-21", "manual", NOW)
    c = db.claim_run("Other Strategy", "2026-07-20", "manual", NOW)
    assert a is not None and b is not None and c is not None
    assert len({a, b, c}) == 3


def test_a_failed_real_attempt_still_occupies_the_slot(db):
    run_id = db.claim_run("Dual Momentum", "2026-07-20", "manual", NOW)
    db.update_run(run_id, status="failed", error_message="boom")
    retry = db.claim_run("Dual Momentum", "2026-07-20", "manual", NOW)
    assert retry is None


@pytest.mark.parametrize(
    "status", ["blocked_kill_switch", "blocked_not_enabled", "blocked_market_closed"]
)
def test_blocked_statuses_never_occupy_the_slot(db, status):
    db.write_blocked("Dual Momentum", "2026-07-20", "scheduled", status, NOW)
    db.write_blocked("Dual Momentum", "2026-07-20", "scheduled", status, NOW)
    # A REAL attempt must still be allowed after any number of blocks.
    real = db.claim_run("Dual Momentum", "2026-07-20", "manual", NOW)
    assert real is not None


def test_write_blocked_rejects_a_non_blocked_status(db):
    with pytest.raises(AssertionError):
        db.write_blocked("Dual Momentum", "2026-07-20", "scheduled", "completed", NOW)


def test_log_order_then_update_order(db):
    run_id = db.claim_run("Dual Momentum", "2026-07-20", "manual", NOW)
    order_id = db.log_order(
        run_id, symbol="AAPL", side="buy", order_kind="notional", notional=500.0,
        qty=None, stop_price=None, target_price=None,
        client_order_id="exec-1-AAPL", status="pending", is_paper=1,
    )
    orders = db.orders_for_run(run_id)
    assert len(orders) == 1
    assert orders[0]["status"] == "pending"

    db.update_order(order_id, status="submitted", alpaca_order_id="abc-123")
    orders = db.orders_for_run(run_id)
    assert orders[0]["status"] == "submitted"
    assert orders[0]["alpaca_order_id"] == "abc-123"


def test_open_orders_excludes_terminal_statuses(db):
    run_id = db.claim_run("Dual Momentum", "2026-07-20", "manual", NOW)
    pending_id = db.log_order(
        run_id, symbol="AAPL", side="buy", order_kind="notional", notional=100.0,
        qty=None, stop_price=None, target_price=None,
        client_order_id="exec-1-AAPL", status="pending", is_paper=1,
    )
    filled_id = db.log_order(
        run_id, symbol="MSFT", side="buy", order_kind="notional", notional=100.0,
        qty=None, stop_price=None, target_price=None,
        client_order_id="exec-1-MSFT", status="filled", is_paper=1,
    )
    open_ids = {row["id"] for row in db.open_orders()}
    assert pending_id in open_ids
    assert filled_id not in open_ids


def test_recent_runs_orders_newest_first(db):
    db.claim_run("Dual Momentum", "2026-07-18", "manual", "2026-07-18T09:00:00")
    db.claim_run("Dual Momentum", "2026-07-19", "manual", "2026-07-19T09:00:00")
    rows = db.recent_runs()
    assert rows[0]["rebalance_date"] == "2026-07-19"
    assert rows[1]["rebalance_date"] == "2026-07-18"
