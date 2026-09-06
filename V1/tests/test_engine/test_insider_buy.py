"""Insider Buying engine: entry fill timing, stop vs. time exit, regime
gating, and sizing -- the machinery, not the signal-generation rules
(covered in tests/test_strategies/test_insider_buy.py). EDGAR data,
OHLCV bars, and regime are all monkeypatched/stubbed so each test controls
exactly one signal and a deliberate price path.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from engine import insider_buy as ib
from engine.data_edgar import _SCHEMA
from strategies.swing.insider_buy import InsiderBuy

NY = "America/New_York"
SIGNAL_DATE = "2026-03-02"  # a Monday
START = pd.Timestamp("2026-01-01").date()
END = pd.Timestamp("2026-06-01").date()


def _bars(closes, opens=None, highs=None, lows=None, start="2026-01-02") -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(closes), tz=NY)
    closes = pd.Series(closes, index=index, dtype=float)
    opens = pd.Series(opens, index=index, dtype=float) if opens is not None else closes
    highs = (
        pd.Series(highs, index=index, dtype=float) if highs is not None
        else pd.concat([opens, closes], axis=1).max(axis=1)
    )
    lows = (
        pd.Series(lows, index=index, dtype=float) if lows is not None
        else pd.concat([opens, closes], axis=1).min(axis=1)
    )
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes,
         "Volume": pd.Series(5_000_000, index=index)}
    )


def _flat_bars(price=100.0, n=100, start="2026-01-02") -> pd.DataFrame:
    return _bars([price] * n, start=start)


@pytest.fixture
def edgar_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    yield conn
    conn.close()


def _insert_purchase(conn, **overrides):
    row = dict(
        accession_no="acc1", transaction_index=0, issuer_cik="0001", issuer_ticker="AAA",
        issuer_name="AAA Corp", filer_cik="0002", filer_name="Insider One",
        filed_at=f"{SIGNAL_DATE}T10:00:00-05:00", signal_date=SIGNAL_DATE,
        transaction_date=SIGNAL_DATE, transaction_code="P",
        shares_transacted=1000.0, price_per_share=50.0, transaction_value=2_000_000.0,
        shares_owned_following=11000.0, shares_owned_before=10000.0,
        pct_change_holdings=10.0, ownership_nature="D", security_title="Common Stock",
        form_url="https://example.com", fetched_at=f"{SIGNAL_DATE}T10:05:00+00:00",
    )
    row.update(overrides)
    conn.execute(
        f"""INSERT INTO form4_transactions ({",".join(row)})
            VALUES ({",".join("?" for _ in row)})""",
        list(row.values()),
    )
    conn.commit()


@pytest.fixture
def stub(monkeypatch, edgar_conn):
    """AAA's bars come from whatever is installed; SPY is always Bullish
    unless a test overrides it; EDGAR reads from the in-memory conn."""
    monkeypatch.setattr(ib, "edgar_connect", lambda: edgar_conn)

    def _install(bars: pd.DataFrame, bullish: bool = True):
        monkeypatch.setattr(
            ib.data_module, "get_bars",
            lambda symbol, interval, start, end, **k: bars if symbol == "AAA" else pd.DataFrame(),
        )
        spy_bars = _flat_bars(price=400.0, n=len(bars) + 5, start=bars.index[0].date().isoformat())
        monkeypatch.setattr(ib.regime_module, "load_spy_bars", lambda start, end, **k: spy_bars)
        label = ib.regime_module.BULLISH if bullish else ib.regime_module.BEARISH
        monkeypatch.setattr(
            ib.regime_module, "regime_series",
            lambda spy: pd.Series(label, index=spy.index, dtype=object),
        )

    return edgar_conn, _install


def _run(variant, symbols=("AAA",), **kwargs):
    return ib.run_insider_buy_backtest(
        InsiderBuy(), list(symbols), START, END, variant, **kwargs
    )


# --- entry timing -------------------------------------------------------------


def test_entry_fills_at_next_trading_day_open_after_signal_date(stub):
    conn, install = stub
    _insert_purchase(conn)
    bars = _flat_bars(price=100.0, n=60, start="2026-01-02")
    install(bars)
    result = _run(ib.VARIANT_A)
    assert result.n_entries == 1
    entry_time = pd.Timestamp(result.trades.iloc[0]["EntryTime"])
    signal_ts = pd.Timestamp(SIGNAL_DATE, tz=NY)
    assert entry_time > signal_ts
    # first trading day strictly after the signal date, not two days later
    assert bars.index[bars.index > signal_ts][0] == entry_time


def test_no_qualifying_filing_produces_no_signals(stub):
    conn, install = stub
    install(_flat_bars(n=60))
    result = _run(ib.VARIANT_A)
    assert result.n_signals == 0
    assert result.n_entries == 0
    assert result.trades.empty


# --- exit machinery: stop vs. fixed time hold --------------------------------


def test_hard_stop_exits_before_time_hold_completes(stub):
    conn, install = stub
    _insert_purchase(conn)
    # Signal date 2026-03-02 falls at business-day position 41 from
    # 2026-01-02, so entry (next trading day) is position 42.
    entry_pos = 42
    # Flat up to entry, entry bar at 101, then gaps down through the 8%
    # stop (101 * 0.92 = 92.9) well before the 5-day time hold completes.
    closes = [100.0] * entry_pos + [101.0, 101.0, 90.0] + [90.0] * 50
    bars = _bars(closes, start="2026-01-02")
    install(bars)
    result = _run(ib.VARIANT_A)
    assert len(result.trades) == 1
    row = result.trades.iloc[0]
    assert row["ExitReason"] == ib.EXIT_STOP
    assert row["ExitPrice"] == pytest.approx(row["EntryPrice"] * 0.92)


def test_time_exit_fires_after_configured_hold_days_if_stop_never_hit(stub):
    conn, install = stub
    _insert_purchase(conn)
    bars = _flat_bars(price=100.0, n=60, start="2026-01-02")  # never drops
    install(bars)
    result = _run(ib.VARIANT_A)
    assert len(result.trades) == 1
    row = result.trades.iloc[0]
    assert row["ExitReason"] == ib.EXIT_TIME
    entry_pos = bars.index.get_loc(pd.Timestamp(row["EntryTime"]))
    exit_pos = bars.index.get_loc(pd.Timestamp(row["ExitTime"]))
    assert exit_pos - entry_pos == InsiderBuy().hold_days


def test_still_open_position_marked_to_market_at_window_end(stub):
    conn, install = stub
    _insert_purchase(conn, signal_date="2026-05-29", filed_at="2026-05-29T10:00:00-04:00",
                      transaction_date="2026-05-29")
    bars = _flat_bars(price=100.0, n=110, start="2026-01-02")  # runs past END
    install(bars)
    result = _run(ib.VARIANT_A)
    assert len(result.trades) == 1
    assert result.trades.iloc[0]["ExitReason"] == "still_held"


# --- regime gating -------------------------------------------------------------


def test_bearish_regime_blocks_entry(stub):
    conn, install = stub
    _insert_purchase(conn)
    install(_flat_bars(n=60), bullish=False)
    result = _run(ib.VARIANT_A)
    assert result.n_entries == 0
    assert result.n_blocked_by_regime == 1
    assert result.n_signals == 1  # still counted as a signal, just not entered


# --- sizing --------------------------------------------------------------------


def test_position_size_matches_risk_formula(stub):
    conn, install = stub
    _insert_purchase(conn)
    install(_flat_bars(price=100.0, n=60))
    result = _run(ib.VARIANT_A, cash=10_000.0)
    row = result.trades.iloc[0]
    config = InsiderBuy()
    expected_size = int(
        (10_000.0 * config.risk_pct_per_trade / 100) / (row["EntryPrice"] * config.stop_pct / 100)
    )
    assert row["Size"] == expected_size


def test_liquidity_filter_blocks_thin_symbol(stub):
    conn, install = stub
    _insert_purchase(conn)
    bars = _flat_bars(n=60)
    bars["Volume"] = 100  # far below the $5M/day threshold at $100/share
    install(bars)
    result = _run(ib.VARIANT_A)
    assert result.n_entries == 0
    assert result.n_blocked_by_liquidity == 1
