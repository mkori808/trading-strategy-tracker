"""engine/execution.py: the orchestrator that turns a cross-sectional
strategy's target weights into real Alpaca paper orders. Everything here
is monkeypatched -- no network call, no real Alpaca client -- covering
each guardrail short-circuit path and the crash-mid-batch case.

engine/alpaca_trading.py's own parsing of raw Alpaca SDK model objects is
NOT re-tested here; account_and_positions/submit_market_order/
close_symbol_position are monkeypatched directly to plain dicts matching
their documented return shape, so these tests exercise execute_rebalance's
own orchestration logic in isolation.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from engine import alpaca_trading, execution, execution_db, kill_switch

STRATEGY_NAME = "Dual Momentum"
TODAY = date(2026, 7, 20)  # a Monday


class _FakeStrategy:
    name = STRATEGY_NAME
    timeframe = "1mo"
    rebalance_frequency = "monthly"

    def __init__(self, target_weights=None, risk_free_rate=0.0):
        self._target_weights = target_weights or {}

    def rebalance(self, universe_bars, as_of):
        return dict(self._target_weights)


class _FakeCalendarDay:
    def __init__(self, d):
        self.date = d


class _FakeClock:
    def __init__(self, is_open):
        self.is_open = is_open


class _FakeClient:
    """Only the two raw-client methods execution.py calls directly
    (get_calendar for is_rebalance_due/_prior_trading_day, get_clock for
    the market-open check) -- everything else Alpaca-shaped goes through
    engine.alpaca_trading's own functions, monkeypatched separately."""

    def __init__(self, trading_days: list[date], is_open: bool = True):
        self.trading_days = trading_days
        self.is_open = is_open

    def get_calendar(self, filters):
        days = [d for d in self.trading_days if filters.start <= d <= filters.end]
        return [_FakeCalendarDay(d) for d in days]

    def get_clock(self):
        return _FakeClock(self.is_open)


def _trading_days_through(today: date, count: int = 40) -> list[date]:
    """Weekday-only trading days ending on `today` (today included only if
    it's itself a weekday) -- good enough for these tests' purposes."""
    days = []
    d = today
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_db, "DB_PATH", tmp_path / "execution.db")
    monkeypatch.setattr(execution_db, "LOGS_DIR", tmp_path)
    return execution_db


@pytest.fixture
def flag_path(tmp_path, monkeypatch):
    path = tmp_path / "kill_switch.flag"
    monkeypatch.setattr(kill_switch, "FLAG_PATH", path)
    return path


@pytest.fixture
def enabled(db):
    db.set_enabled(STRATEGY_NAME, True, "2026-07-01T00:00:00")
    return db


@pytest.fixture(autouse=True)
def stub_data_and_strategy(monkeypatch):
    """Every test needs these regardless of scenario: no network, and a
    strategy whose target weights the test controls directly rather than
    depending on real momentum-ranking math (already covered by
    tests/test_strategies/test_dual_momentum.py)."""
    monkeypatch.setattr(execution.data_module, "get_bars", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(execution.data_module, "risk_free_rate", lambda *a, **k: 0.04)


def _patch_strategy(monkeypatch, target_weights):
    monkeypatch.setattr(
        execution, "build_cross_sectional_strategy",
        lambda name, risk_free_rate: _FakeStrategy(target_weights, risk_free_rate),
    )


def _patch_account(monkeypatch, equity=100_000.0, last_equity=100_000.0, positions=None):
    monkeypatch.setattr(
        alpaca_trading, "account_and_positions",
        lambda: {
            "account": {"available": True, "equity": equity, "lastEquity": last_equity},
            "positions": positions or [],
        },
    )


def _patch_client(monkeypatch, trading_days=None, is_open=True):
    trading_days = trading_days if trading_days is not None else _trading_days_through(TODAY)
    fake = _FakeClient(trading_days, is_open=is_open)
    monkeypatch.setattr(alpaca_trading, "trading_client", lambda: (fake, "ok"))
    return fake


# --- guardrail short-circuits -------------------------------------------


def test_not_enabled_is_blocked_with_no_db_row(db, monkeypatch):
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {"AAPL": 1.0})
    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)
    assert result["status"] == "blocked_not_enabled"
    assert db.recent_runs() == []


def test_kill_switch_active_is_blocked_and_logged(enabled, flag_path, monkeypatch):
    kill_switch.activate(flatten=False)
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {"AAPL": 1.0})
    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)
    assert result["status"] == "blocked_kill_switch"
    runs = enabled.recent_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "blocked_kill_switch"


def test_alpaca_not_configured_has_no_db_row(enabled, monkeypatch):
    monkeypatch.setattr(alpaca_trading, "trading_client", lambda: (None, "no credentials"))
    _patch_strategy(monkeypatch, {"AAPL": 1.0})
    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)
    assert result["status"] == "alpaca_not_configured"
    assert enabled.recent_runs() == []


def test_not_due_today_has_no_db_row(enabled, monkeypatch):
    # A trading-day calendar that does NOT put TODAY as the first trading
    # day of its month -- e.g. a window entirely within the same month,
    # ending on today, where today isn't the earliest day in the window's
    # own month group.
    days = _trading_days_through(TODAY, count=40)
    _patch_client(monkeypatch, trading_days=days)
    _patch_strategy(monkeypatch, {"AAPL": 1.0})
    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=False, today=TODAY)
    assert result["status"] == "not_due"
    assert enabled.recent_runs() == []


def test_force_skips_the_due_date_check(enabled, monkeypatch):
    days = _trading_days_through(TODAY, count=40)
    _patch_client(monkeypatch, trading_days=days)
    _patch_strategy(monkeypatch, {})
    _patch_account(monkeypatch)
    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)
    assert result["status"] != "not_due"


def test_market_closed_is_blocked_and_logged(enabled, monkeypatch):
    _patch_client(monkeypatch, is_open=False)
    _patch_strategy(monkeypatch, {"AAPL": 1.0})
    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)
    assert result["status"] == "blocked_market_closed"
    runs = enabled.recent_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "blocked_market_closed"


def test_second_real_attempt_same_day_is_blocked(enabled, monkeypatch):
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {})
    _patch_account(monkeypatch)
    first = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)
    assert first["status"] in {"completed", "completed_with_daily_loss_halt"}
    second = execution.execute_rebalance(STRATEGY_NAME, "scheduled", force=True, today=TODAY)
    assert second["status"] == "already_running_or_done_today"


# --- happy path + order planning ----------------------------------------


def test_completed_run_submits_buy_orders(enabled, monkeypatch):
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {"AAPL": 0.5, "MSFT": 0.5})
    _patch_account(monkeypatch, equity=100_000.0, positions=[])
    submitted = []
    monkeypatch.setattr(
        alpaca_trading, "submit_market_order",
        lambda symbol, side, **kw: submitted.append((symbol, side, kw)) or {
            "id": f"order-{symbol}", "submittedAt": "2026-07-20T14:30:00",
        },
    )

    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)

    assert result["status"] == "completed"
    assert {s for s, _, _ in submitted} == {"AAPL", "MSFT"}
    assert all(side == "buy" for _, side, _ in submitted)
    run_id = result["runId"]
    orders = enabled.orders_for_run(run_id)
    assert len(orders) == 2
    assert all(o["status"] == "submitted" for o in orders)


def test_liquidates_a_position_dropped_from_target_weights(enabled, monkeypatch):
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {"AAPL": 1.0})  # MSFT no longer in the target set
    _patch_account(monkeypatch, positions=[
        {"symbol": "MSFT", "qty": 10.0, "currentPrice": 400.0},
    ])
    closed = []
    monkeypatch.setattr(
        alpaca_trading, "close_symbol_position",
        lambda symbol, client_order_id: closed.append(symbol) or {
            "id": "close-1", "submittedAt": "2026-07-20T14:30:00",
        },
    )
    monkeypatch.setattr(
        alpaca_trading, "submit_market_order",
        lambda *a, **k: {"id": "order-1", "submittedAt": "2026-07-20T14:30:00"},
    )

    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)

    assert result["status"] == "completed"
    assert closed == ["MSFT"]


def test_daily_loss_halt_skips_buys_but_not_sells(enabled, monkeypatch):
    _patch_client(monkeypatch)
    # 10% down from last_equity -- well past the default 5% halt threshold.
    _patch_account(monkeypatch, equity=90_000.0, last_equity=100_000.0, positions=[
        {"symbol": "MSFT", "qty": 10.0, "currentPrice": 400.0},
    ])
    _patch_strategy(monkeypatch, {"AAPL": 1.0})  # would need a BUY of AAPL and a liquidation of MSFT
    buy_calls = []
    close_calls = []
    monkeypatch.setattr(
        alpaca_trading, "submit_market_order",
        lambda symbol, side, **kw: buy_calls.append(symbol) or {"id": "x", "submittedAt": None},
    )
    monkeypatch.setattr(
        alpaca_trading, "close_symbol_position",
        lambda symbol, client_order_id: close_calls.append(symbol) or {"id": "y", "submittedAt": None},
    )

    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)

    assert result["status"] == "completed_with_daily_loss_halt"
    assert buy_calls == []  # the AAPL buy was suppressed
    assert close_calls == ["MSFT"]  # the MSFT liquidation (a sell) still went through


def test_a_rejected_order_does_not_kill_the_batch(enabled, monkeypatch):
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {"AAPL": 0.5, "MSFT": 0.5})
    _patch_account(monkeypatch, positions=[])

    def flaky_submit(symbol, side, **kw):
        if symbol == "AAPL":
            raise RuntimeError("Alpaca rejected the order")
        return {"id": "order-msft", "submittedAt": "2026-07-20T14:30:00"}

    monkeypatch.setattr(alpaca_trading, "submit_market_order", flaky_submit)

    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)

    assert result["status"] == "partial_failure"
    orders = {o["symbol"]: o for o in enabled.orders_for_run(result["runId"])}
    assert orders["AAPL"]["status"] == "rejected"
    assert orders["MSFT"]["status"] == "submitted"


def test_all_orders_rejected_yields_failed_status(enabled, monkeypatch):
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {"AAPL": 1.0})
    _patch_account(monkeypatch, positions=[])
    monkeypatch.setattr(
        alpaca_trading, "submit_market_order",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rejected")),
    )

    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)

    assert result["status"] == "failed"


def test_no_orders_needed_is_still_completed(enabled, monkeypatch):
    _patch_client(monkeypatch)
    _patch_strategy(monkeypatch, {})  # fully in cash, nothing to do
    _patch_account(monkeypatch, positions=[])
    result = execution.execute_rebalance(STRATEGY_NAME, "manual", force=True, today=TODAY)
    assert result["status"] == "completed"
    assert enabled.orders_for_run(result["runId"]) == []


# --- reconcile_open_orders ------------------------------------------------


def test_reconcile_updates_open_orders_from_alpaca(enabled, monkeypatch):
    run_id = enabled.claim_run(STRATEGY_NAME, "2026-07-20", "manual", "2026-07-20T14:00:00")
    order_id = enabled.log_order(
        run_id, symbol="AAPL", side="buy", order_kind="notional", notional=100.0,
        qty=None, stop_price=None, target_price=None,
        client_order_id="exec-1-AAPL", status="submitted", is_paper=1,
        alpaca_order_id="alpaca-abc",
    )
    monkeypatch.setattr(
        alpaca_trading, "get_order_status",
        lambda alpaca_order_id: {
            "available": True, "status": "filled", "filledAt": "2026-07-20T14:35:00",
            "filledQty": 0.5, "filledAvgPrice": 200.0,
        },
    )
    execution.reconcile_open_orders()
    row = enabled.orders_for_run(run_id)[0]
    assert row["status"] == "filled"
    assert row["filled_avg_price"] == 200.0


def test_reconcile_skips_orders_with_no_alpaca_id(enabled, monkeypatch):
    run_id = enabled.claim_run(STRATEGY_NAME, "2026-07-20", "manual", "2026-07-20T14:00:00")
    enabled.log_order(
        run_id, symbol="AAPL", side="buy", order_kind="notional", notional=100.0,
        qty=None, stop_price=None, target_price=None,
        client_order_id="exec-1-AAPL", status="pending", is_paper=1,
    )
    called = []
    monkeypatch.setattr(
        alpaca_trading, "get_order_status", lambda alpaca_order_id: called.append(1),
    )
    execution.reconcile_open_orders()
    assert called == []  # never attempted -- no alpaca_order_id to look up
