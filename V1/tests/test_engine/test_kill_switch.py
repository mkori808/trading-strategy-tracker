"""engine/kill_switch.py: the flag-file lifecycle, and that
engine/alpaca_trading.py's order-submission functions actually refuse to
call the broker while it's active -- the one function that talks to the
broker's order endpoint must never trust it was called correctly."""

from __future__ import annotations

import pytest

from engine import alpaca_trading, kill_switch


@pytest.fixture
def flag_path(tmp_path, monkeypatch):
    path = tmp_path / "kill_switch.flag"
    monkeypatch.setattr(kill_switch, "FLAG_PATH", path)
    return path


def test_inactive_by_default(flag_path):
    assert kill_switch.is_active() is False


def test_activate_sets_the_flag(flag_path):
    kill_switch.activate(flatten=False)
    assert kill_switch.is_active() is True
    assert flag_path.exists()


def test_deactivate_clears_the_flag(flag_path):
    kill_switch.activate(flatten=False)
    kill_switch.deactivate()
    assert kill_switch.is_active() is False


def test_deactivate_is_safe_when_never_activated(flag_path):
    kill_switch.deactivate()  # must not raise
    assert kill_switch.is_active() is False


def test_activate_sets_flag_even_if_flatten_fails(flag_path, monkeypatch):
    def boom():
        raise RuntimeError("Alpaca unreachable")

    monkeypatch.setattr(alpaca_trading, "trading_client", lambda: (None, "no credentials"))
    result = kill_switch.activate(flatten=True)
    assert kill_switch.is_active() is True
    assert result["flagSet"] is True
    assert result["flattened"] is False
    assert result["error"] == "no credentials"


def test_activate_flatten_calls_close_all_positions(flag_path, monkeypatch):
    calls = []

    class FakeClient:
        def close_all_positions(self, cancel_orders):
            calls.append(cancel_orders)

    monkeypatch.setattr(alpaca_trading, "trading_client", lambda: (FakeClient(), "ok"))
    result = kill_switch.activate(flatten=True)
    assert calls == [True]
    assert result["flattened"] is True


def test_submit_market_order_refuses_while_active(flag_path, monkeypatch):
    kill_switch.activate(flatten=False)
    called = []
    monkeypatch.setattr(
        alpaca_trading, "trading_client",
        lambda: called.append("should not be called") or (None, "unused"),
    )
    with pytest.raises(RuntimeError, match="Kill switch is active"):
        alpaca_trading.submit_market_order(
            "AAPL", "buy", notional=100.0, client_order_id="test-1",
        )
    assert called == []  # never even reached trading_client()


def test_close_symbol_position_refuses_while_active(flag_path, monkeypatch):
    kill_switch.activate(flatten=False)
    called = []
    monkeypatch.setattr(
        alpaca_trading, "trading_client",
        lambda: called.append("should not be called") or (None, "unused"),
    )
    with pytest.raises(RuntimeError, match="Kill switch is active"):
        alpaca_trading.close_symbol_position("AAPL", "test-1")
    assert called == []
