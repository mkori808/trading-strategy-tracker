"""Day-by-day paper-account performance: the Alpaca portfolio-history
wrapper (engine/alpaca_trading.py) and the endpoint that assembles it with
today's in-progress figure (api/main.py:execution_daily).

No network: the Alpaca client and the benchmark bars are monkeypatched to
plain objects matching the documented shapes, so these cover the assembly
logic -- unit conversion, timezone resolution, and the settled-vs-in-progress
split -- rather than re-testing alpaca-py.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

import api.main as api_main
from engine import alpaca_trading


class _FakeHistory:
    """Shape of alpaca-py's PortfolioHistory: parallel lists, unix seconds,
    profit_loss_pct as a FRACTION."""

    def __init__(self, timestamp, equity, profit_loss, profit_loss_pct, base_value=100_000.0):
        self.timestamp = timestamp
        self.equity = equity
        self.profit_loss = profit_loss
        self.profit_loss_pct = profit_loss_pct
        self.base_value = base_value


class _FakeClient:
    def __init__(self, history):
        self._history = history
        self.requests = []

    def get_portfolio_history(self, request):
        self.requests.append(request)
        return self._history


def _epoch(y: int, m: int, d: int, hour: int = 21) -> int:
    """Unix seconds for a UTC instant. 21:00 UTC is after the NY close but
    still the SAME NY calendar day -- the case a naive UTC read gets right
    by luck. See `test_late_utc_stamp_resolves_to_the_ny_session` for the
    one that doesn't."""
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def patch_client(monkeypatch):
    def _patch(history):
        client = _FakeClient(history)
        monkeypatch.setattr(alpaca_trading, "trading_client", lambda: (client, "ok"))
        return client

    return _patch


# --- the portfolio-history wrapper ---------------------------------------


def test_percent_is_scaled_once_from_alpacas_fraction(patch_client):
    patch_client(
        _FakeHistory(
            timestamp=[_epoch(2026, 8, 20)],
            equity=[101_000.0],
            profit_loss=[1_000.0],
            profit_loss_pct=[0.01],  # Alpaca's fraction for +1%
        )
    )
    result = alpaca_trading.get_portfolio_history(date(2026, 8, 17))
    assert result["available"] is True
    assert result["rows"][0]["profitLossPct"] == pytest.approx(1.0)


def test_late_utc_stamp_resolves_to_the_ny_session(patch_client):
    """01:00 UTC on the 21st is 21:00 ET on the 20th. Reading the stamp as
    UTC would file that mark under the wrong session, shifting every row and
    silently misaligning the benchmark join."""
    patch_client(
        _FakeHistory(
            timestamp=[_epoch(2026, 8, 21, hour=1)],
            equity=[101_000.0],
            profit_loss=[10.0],
            profit_loss_pct=[0.0001],
        )
    )
    rows = alpaca_trading.get_portfolio_history(date(2026, 8, 17))["rows"]
    assert rows[0]["date"] == "2026-08-20"


def test_null_equity_periods_are_skipped_not_zeroed(patch_client):
    """Alpaca emits a null equity for a period it has no mark for. Carrying
    it through as 0.0 would render as a -100% day."""
    patch_client(
        _FakeHistory(
            timestamp=[_epoch(2026, 8, 19), _epoch(2026, 8, 20)],
            equity=[None, 101_000.0],
            profit_loss=[None, 1_000.0],
            profit_loss_pct=[None, 0.01],
        )
    )
    rows = alpaca_trading.get_portfolio_history(date(2026, 8, 17))["rows"]
    assert [r["date"] for r in rows] == ["2026-08-20"]


def test_unconfigured_alpaca_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setattr(alpaca_trading, "trading_client", lambda: (None, "no credentials"))
    result = alpaca_trading.get_portfolio_history(date(2026, 8, 17))
    assert result == {"available": False, "reason": "no credentials"}


def test_broker_error_is_reported_not_raised(monkeypatch):
    class _Boom:
        def get_portfolio_history(self, request):
            raise RuntimeError("429 rate limited")

    monkeypatch.setattr(alpaca_trading, "trading_client", lambda: (_Boom(), "ok"))
    result = alpaca_trading.get_portfolio_history(date(2026, 8, 17))
    assert result["available"] is False
    assert "429 rate limited" in result["reason"]


# --- the endpoint's settled-vs-in-progress split -------------------------


@pytest.fixture
def patch_endpoint(monkeypatch):
    """Pin the endpoint's three inputs: when automation started, the settled
    series, and the live account."""

    def _patch(rows, account, *, started="2026-08-17T10:17:46", benchmark=None):
        monkeypatch.setattr(
            api_main.execution_db,
            "earliest_run_with_baseline",
            lambda: {"portfolio_value_at_start": 100_000.0, "triggered_at": started},
        )
        monkeypatch.setattr(api_main.execution_db, "automation_config", dict)
        monkeypatch.setattr(
            api_main.alpaca_trading,
            "get_portfolio_history",
            lambda start, end=None: {"available": True, "rows": list(rows), "baseValue": 100_000.0},
        )
        monkeypatch.setattr(api_main.alpaca_trading, "get_account", lambda: account)
        monkeypatch.setattr(api_main, "_benchmark_daily_pct", lambda start: benchmark or {})
        monkeypatch.setattr(api_main, "_benchmark_intraday_pct", lambda last: None)

    return _patch


def _row(day: str, equity: float) -> dict:
    return {"date": day, "equity": equity, "profitLoss": 1.0, "profitLossPct": 0.1}


def _account(equity: float, last_equity: float | None) -> dict:
    return {"available": True, "equity": equity, "lastEquity": last_equity}


def test_today_is_reported_separately_from_settled_days(patch_endpoint, monkeypatch):
    today = datetime.now(alpaca_trading.NY).date()
    patch_endpoint([_row("2026-08-20", 100_000.0)], _account(101_000.0, 100_000.0))

    result = api_main.execution_daily()

    assert [r["date"] for r in result["rows"]] == ["2026-08-20"]
    assert result["today"]["date"] == today.isoformat()
    assert result["today"]["inProgress"] is True
    assert result["today"]["profitLoss"] == pytest.approx(1_000.0)
    assert result["today"]["profitLossPct"] == pytest.approx(1.0)


def test_settled_today_wins_over_a_derived_one(patch_endpoint):
    """After the close Alpaca publishes today's real mark. Once it's in the
    series, the derived equity-minus-lastEquity figure must not ALSO appear
    -- that would show the same session twice, once unsettled."""
    today = datetime.now(alpaca_trading.NY).date().isoformat()
    patch_endpoint([_row(today, 101_000.0)], _account(101_000.0, 100_000.0))

    result = api_main.execution_daily()

    assert result["today"] is None
    assert [r["date"] for r in result["rows"]] == [today]


def test_no_today_without_a_prior_close_baseline(patch_endpoint):
    """`lastEquity` is the baseline the daily-loss breaker uses too. With no
    baseline there is no honest day figure, so report none rather than
    measuring today against inception and calling it a day."""
    patch_endpoint([_row("2026-08-20", 100_000.0)], _account(101_000.0, None))
    assert api_main.execution_daily()["today"] is None


def test_before_the_first_trade_the_series_is_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(api_main.execution_db, "earliest_run_with_baseline", lambda: None)
    monkeypatch.setattr(api_main.execution_db, "automation_config", dict)

    result = api_main.execution_daily()

    assert result == {
        "available": True,
        "reason": None,
        "startDate": None,
        "benchmarkSymbol": api_main.BENCHMARK_SYMBOL,
        "rows": [],
        "today": None,
    }


def test_benchmark_is_joined_per_session(patch_endpoint):
    patch_endpoint(
        [_row("2026-08-19", 99_000.0), _row("2026-08-20", 100_000.0)],
        _account(101_000.0, 100_000.0),
        benchmark={"2026-08-19": 0.21, "2026-08-20": -0.84},
    )

    rows = api_main.execution_daily()["rows"]

    assert [r["benchmarkPct"] for r in rows] == [pytest.approx(0.21), pytest.approx(-0.84)]


def test_a_session_without_a_benchmark_bar_is_none_not_zero(patch_endpoint):
    """Zero would read as "the benchmark was flat that day", which is a
    claim; None renders as "—", which is the absence of one."""
    patch_endpoint(
        [_row("2026-08-19", 99_000.0)],
        _account(101_000.0, 100_000.0),
        benchmark={},
    )
    assert api_main.execution_daily()["rows"][0]["benchmarkPct"] is None


# --- the benchmark helpers ------------------------------------------------


def test_benchmark_daily_pct_keys_on_the_session_date(monkeypatch):
    index = pd.to_datetime(["2026-08-19", "2026-08-20"]).tz_localize("America/New_York")
    bars = pd.DataFrame({"Close": [100.0, 101.0]}, index=index)
    monkeypatch.setattr(api_main.data_module, "get_bars", lambda *a, **k: bars)

    mapping = api_main._benchmark_daily_pct(date(2026, 8, 19))

    # The first bar has no prior close inside the frame -> no entry, rather
    # than a NaN that would serialize as null and read like missing data.
    assert mapping == {"2026-08-20": pytest.approx(1.0)}


def test_benchmark_failure_degrades_to_an_empty_mapping(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(api_main.data_module, "get_bars", _boom)
    assert api_main._benchmark_daily_pct(date(2026, 8, 19)) == {}


def test_intraday_benchmark_refuses_a_partial_current_day_bar(monkeypatch):
    """If the cached daily series already includes today, its last close is
    part of TODAY -- dividing today's price by it compares today against
    itself. Report nothing instead."""
    today = datetime.now(alpaca_trading.NY).date().isoformat()
    monkeypatch.setattr(
        api_main.quotes_module, "get_quotes", lambda syms: {syms[0]: {"price": 101.0}}
    )
    monkeypatch.setattr(
        api_main.quotes_module,
        "symbol_metadata",
        lambda sym: {"lastClose": 100.0, "closeAsOf": today},
    )
    assert api_main._benchmark_intraday_pct(None) is None


def test_intraday_benchmark_uses_a_settled_prior_close(monkeypatch):
    monkeypatch.setattr(
        api_main.quotes_module, "get_quotes", lambda syms: {syms[0]: {"price": 101.0}}
    )
    monkeypatch.setattr(
        api_main.quotes_module,
        "symbol_metadata",
        lambda sym: {"lastClose": 100.0, "closeAsOf": "2026-01-02"},
    )
    assert api_main._benchmark_intraday_pct(None) == pytest.approx(1.0)
