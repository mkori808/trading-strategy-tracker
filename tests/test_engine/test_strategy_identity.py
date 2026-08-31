from engine import alpaca_trading
from engine import strategy_identity


def test_order_summary_counts_only_nonterminal_orders_as_open():
    summary = alpaca_trading.summarize_order_states([
        {"status": "filled"}, {"status": "filled"}, {"status": "canceled"},
        {"status": "rejected"}, {"status": "accepted"}, {"status": "pending_cancel"},
    ])
    assert summary == {
        "openPending": 2, "filled": 2, "canceled": 1, "rejected": 1, "recentHistory": 6,
    }


def test_canonical_dm_identity_is_explicit():
    identity = strategy_identity.identify_execution_config(
        "Dual Momentum",
        {"lookback_trading_days": 189, "top_n": 5, "rebalance_frequency": "monthly"},
        [], None,
    )
    assert identity["displayName"] == "Canonical Dual Momentum · 189D/Monthly"
    assert identity["variant"] == "canonical"
    assert identity["fingerprintMatches"] is True


def test_promoted_dm_fingerprint_uses_validation_run(monkeypatch):
    class Row(dict):
        pass

    expected_symbols = ["A", "B"]
    selected = Row(params='{"lookback_trading_days": 63, "rebalance_frequency": "daily"}', symbols='["A", "B"]')
    from engine import logging_db
    monkeypatch.setattr(logging_db, "canonical_portfolio_validation", lambda name, run_id: (selected, None))
    identity = strategy_identity.identify_execution_config(
        "Dual Momentum", {"lookback_trading_days": 63, "rebalance_frequency": "daily"},
        expected_symbols, 33,
    )
    assert identity["displayName"] == "Dual Momentum · Optimized 63D/Daily"
    assert identity["provenance"] == "Historically optimized · requires OOS confirmation"
    assert identity["selectionContext"] == "Selected from config search"
    assert identity["actualConfig"]["top_n"] == 5
    assert identity["fingerprintMatches"] is True

    mismatch = strategy_identity.identify_execution_config(
        "Dual Momentum", {"lookback_trading_days": 63, "rebalance_frequency": "daily"},
        ["A"], 33,
    )
    assert mismatch["fingerprintMatches"] is False
