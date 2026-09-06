"""engine/shadow_live_mark.py: reconstructing a live 'today' equity mark for
a shadow strategy from its frozen holdings, a finalized baseline, and live
quotes -- the shadow equivalent of the real paper account's live-polled
equity."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from engine import shadow_live_mark


def _bars(closes: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": [closes["close"]]}, index=[pd.Timestamp(closes["date"])])


@pytest.fixture(autouse=True)
def stub_data(monkeypatch):
    prices = {
        "AAPL": 100.0,
        "MSFT": 200.0,
    }

    def fake_get_bars(symbol, interval, start, end):
        if symbol not in prices:
            return pd.DataFrame()
        return pd.DataFrame({"Close": [prices[symbol]]}, index=[pd.Timestamp(end)])

    monkeypatch.setattr(shadow_live_mark.data_module, "get_bars", fake_get_bars)
    return prices


def _stub_quotes(monkeypatch, quotes: dict[str, dict]):
    monkeypatch.setattr(shadow_live_mark.quotes_module, "get_quotes", lambda symbols: quotes)


def test_no_holdings_is_unavailable_not_a_crash():
    result = shadow_live_mark.compute_live_mark("mrm", {}, 100.0, date(2026, 8, 28))
    assert result["available"] is False
    assert result["inProgress"] is True
    assert "No frozen holdings" in result["reason"]


def test_no_baseline_is_unavailable():
    result = shadow_live_mark.compute_live_mark("mrm", {"AAPL": 1.0}, None, None)
    assert result["available"] is False
    assert "baseline" in result["reason"].lower()


def test_equal_weight_up_move_scales_equity_proportionally(monkeypatch):
    _stub_quotes(monkeypatch, {
        "AAPL": {"symbol": "AAPL", "price": 110.0, "source": "alpaca-iex"},
        "MSFT": {"symbol": "MSFT", "price": 220.0, "source": "alpaca-iex"},
    })
    result = shadow_live_mark.compute_live_mark(
        "mrm", {"AAPL": 0.5, "MSFT": 0.5}, 100.0, date(2026, 8, 28),
    )
    assert result["available"] is True
    # Both names up exactly 10% -> the whole mark is up 10%.
    assert result["equity"] == pytest.approx(110.0)
    assert result["profitLoss"] == pytest.approx(10.0)
    assert result["profitLossPct"] == pytest.approx(10.0)
    assert result["missingSymbols"] == []
    assert result["inProgress"] is True


def test_mixed_moves_are_weighted(monkeypatch):
    _stub_quotes(monkeypatch, {
        "AAPL": {"symbol": "AAPL", "price": 120.0, "source": "alpaca-iex"},  # +20%
        "MSFT": {"symbol": "MSFT", "price": 190.0, "source": "alpaca-iex"},  # -5%
    })
    result = shadow_live_mark.compute_live_mark(
        "volScaled", {"AAPL": 0.3, "MSFT": 0.7}, 100.0, date(2026, 8, 28),
    )
    expected_return = 0.3 * 0.20 + 0.7 * (-0.05)
    assert result["equity"] == pytest.approx(100.0 * (1 + expected_return))


def test_short_weight_moves_the_opposite_direction(monkeypatch):
    _stub_quotes(monkeypatch, {
        "AAPL": {"symbol": "AAPL", "price": 110.0, "source": "alpaca-iex"},  # +10%
    })
    result = shadow_live_mark.compute_live_mark(
        "mrm", {"AAPL": -1.0}, 100.0, date(2026, 8, 28),
    )
    assert result["equity"] == pytest.approx(90.0)


def test_one_missing_quote_contributes_zero_but_others_still_count(monkeypatch):
    _stub_quotes(monkeypatch, {
        "AAPL": {"symbol": "AAPL", "price": 110.0, "source": "alpaca-iex"},
        "MSFT": {"symbol": "MSFT", "source": "unavailable", "reason": "no print"},
    })
    result = shadow_live_mark.compute_live_mark(
        "mrm", {"AAPL": 0.5, "MSFT": 0.5}, 100.0, date(2026, 8, 28),
    )
    assert result["available"] is True
    assert result["missingSymbols"] == ["MSFT"]
    # Only AAPL's +10% on its own 0.5 weight contributes.
    assert result["equity"] == pytest.approx(100.0 * (1 + 0.5 * 0.10))


def test_all_quotes_missing_is_unavailable(monkeypatch):
    _stub_quotes(monkeypatch, {
        "AAPL": {"symbol": "AAPL", "source": "unavailable", "reason": "no print"},
        "MSFT": {"symbol": "MSFT", "source": "unavailable", "reason": "no print"},
    })
    result = shadow_live_mark.compute_live_mark(
        "mrm", {"AAPL": 0.5, "MSFT": 0.5}, 100.0, date(2026, 8, 28),
    )
    assert result["available"] is False
    assert "AAPL" in result["reason"] and "MSFT" in result["reason"]


def test_zero_weight_holdings_never_trigger_a_missing_quote(monkeypatch):
    _stub_quotes(monkeypatch, {"AAPL": {"symbol": "AAPL", "price": 110.0, "source": "alpaca-iex"}})
    result = shadow_live_mark.compute_live_mark(
        "mrm", {"AAPL": 1.0, "GHOST": 0.0}, 100.0, date(2026, 8, 28),
    )
    assert result["available"] is True
    assert result["missingSymbols"] == []


def test_broker_backed_mark_uses_the_account_growth_factor():
    result = shadow_live_mark.compute_live_mark_from_broker(
        "dm", baseline_series_equity=150.0, account_equity=103_000.0, account_last_equity=100_000.0,
    )
    assert result["available"] is True
    assert result["equity"] == pytest.approx(150.0 * 1.03)
    assert result["profitLossPct"] == pytest.approx(3.0)
    assert result["source"] == "alpaca_paper_account"


def test_broker_backed_mark_unavailable_without_live_account():
    result = shadow_live_mark.compute_live_mark_from_broker(
        "dm", baseline_series_equity=150.0, account_equity=None, account_last_equity=None,
    )
    assert result["available"] is False


def test_broker_backed_mark_unavailable_without_baseline():
    result = shadow_live_mark.compute_live_mark_from_broker(
        "dm", baseline_series_equity=None, account_equity=103_000.0, account_last_equity=100_000.0,
    )
    assert result["available"] is False
