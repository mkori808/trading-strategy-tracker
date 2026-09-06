from api import main


def test_strategy_catalog_distinguishes_data_blocked_from_untested(monkeypatch):
    monkeypatch.setattr(main, "latest_run_per_strategy", lambda: {})
    monkeypatch.setattr(main, "latest_portfolio_run_per_strategy", lambda: {})
    monkeypatch.setattr(main, "strategies_awaiting_remeasurement", lambda: set())
    rows = main.list_strategies()
    blocked = [row for row in rows if row["name"] in main.UNAVAILABLE_RESEARCH_STRATEGIES]
    assert blocked
    assert all(row["implementationStatus"] == "unavailable" for row in blocked)
    assert all(row["unavailableReason"] for row in blocked)
    available = [row for row in rows if row["name"] not in main.UNAVAILABLE_RESEARCH_STRATEGIES]
    assert all(row["implementationStatus"] == "implemented" for row in available)
