"""engine/excursion.py: MFE/MAE, exit efficiency, loss realization ratio,
entry slippage, and the report writer.

Trades are constructed directly (not run through backtesting.py) so
EntryBar/ExitBar/EntryPrice/SL/Tag are pinned to values that make the
expected R-multiples exact, matching the fixture used to validate
engine/metrics.py's r_multiples()."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import excursion


def _trade(**overrides):
    row = {
        "EntryBar": 1,
        "ExitBar": 3,
        "EntryPrice": 100.0,
        "ExitPrice": 110.0,
        "Size": 1.0,
        "PnL": 10.0,
        "SL": 95.0,
        "Tag": 5.0,
        "EntryTime": pd.Timestamp("2024-01-03"),
        "ExitTime": pd.Timestamp("2024-01-05"),
    }
    row.update(overrides)
    return row


def test_empty_trades_returns_empty_frame(daily_bars_factory):
    bars = daily_bars_factory(closes=[99, 100, 105, 110])
    out = excursion.compute_trade_excursions(bars, pd.DataFrame(columns=["EntryBar"]))
    assert out.empty


def test_winner_mfe_mae_and_exit_efficiency(daily_bars_factory):
    # signal bar (idx0) close=99, range 98-100; entry fills at 100 (idx1);
    # idx2 spikes to 115/92; exit at 110 (idx3). risk_per_share = 5 (Tag).
    bars = daily_bars_factory(
        closes=[99, 100, 105, 110],
        highs=[100, 101, 115, 111],
        lows=[98, 99, 92, 109],
    )
    trades = pd.DataFrame([_trade()])
    out = excursion.compute_trade_excursions(bars, trades)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["RealizedR"] == pytest.approx(2.0)
    assert row["MFE_R"] == pytest.approx(3.0)  # (115-100)/5
    assert row["MAE_R"] == pytest.approx(1.6)  # (100-92)/5
    assert row["ExitEfficiencyPct"] == pytest.approx(2.0 / 3.0 * 100)
    assert np.isnan(row["LossRealizationRatioPct"])
    # signal bar range is 98-100 (=2); fill at 100 is +1 above signal close 99
    assert row["EntrySlippagePct"] == pytest.approx((100 - 99) / 2 * 100)


def test_loser_mae_mfe_and_loss_realization_ratio(daily_bars_factory):
    bars = daily_bars_factory(
        closes=[99, 100, 95, 90],
        highs=[100, 101, 105, 91],
        lows=[98, 99, 85, 89],
    )
    trades = pd.DataFrame([_trade(ExitPrice=90.0, PnL=-10.0)])
    out = excursion.compute_trade_excursions(bars, trades)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["RealizedR"] == pytest.approx(-2.0)
    assert row["MFE_R"] == pytest.approx(1.0)  # (105-100)/5
    assert row["MAE_R"] == pytest.approx(3.0)  # (100-85)/5
    assert row["LossRealizationRatioPct"] == pytest.approx(2.0 / 3.0 * 100)
    assert np.isnan(row["ExitEfficiencyPct"])


def test_impossible_excursion_is_dropped_not_written(daily_bars_factory, caplog):
    # Corrupted window: claims a 2.0R realized win but the bar range between
    # EntryBar and ExitBar only ever reaches 0.8R favorable -- physically
    # impossible, must be dropped rather than reported.
    bars = daily_bars_factory(
        closes=[99, 100, 102, 104],
        highs=[100, 101, 104, 104],
        lows=[98, 99, 98, 100],
    )
    trades = pd.DataFrame([_trade()])
    with caplog.at_level("ERROR"):
        out = excursion.compute_trade_excursions(bars, trades)
    assert out.empty
    assert "MFE_R" in caplog.text


def test_valid_and_corrupted_trades_mixed(daily_bars_factory):
    bars = daily_bars_factory(
        closes=[99, 100, 105, 110, 102, 104],
        highs=[100, 101, 115, 111, 104, 104],
        lows=[98, 99, 92, 109, 98, 100],
    )
    good = _trade(EntryBar=1, ExitBar=3)
    corrupted = _trade(EntryBar=4, ExitBar=5)  # window never reaches +2R
    trades = pd.DataFrame([good, corrupted])
    out = excursion.compute_trade_excursions(bars, trades)
    assert len(out) == 1
    assert out.iloc[0]["MFE_R"] == pytest.approx(3.0)


def test_short_trade_direction_flips_mfe_mae(daily_bars_factory):
    # short entry at 100, stop 105 (risk=5); best case price falls to 90
    # (MFE), worst case price rises to 108 (MAE).
    bars = daily_bars_factory(
        closes=[101, 100, 95, 90],
        highs=[102, 101, 96, 108],
        lows=[100, 99, 90, 95],
    )
    trades = pd.DataFrame([_trade(
        Size=-1.0, SL=105.0, ExitPrice=90.0, PnL=10.0,
    )])
    out = excursion.compute_trade_excursions(bars, trades)
    row = out.iloc[0]
    assert row["Direction"] == "short"
    assert row["MFE_R"] == pytest.approx((100 - 90) / 5)
    assert row["MAE_R"] == pytest.approx((108 - 100) / 5)


def test_write_excursion_report(tmp_path, monkeypatch, daily_bars_factory):
    monkeypatch.setattr(excursion, "LOGS_DIR", tmp_path)
    bars = daily_bars_factory(
        closes=[99, 100, 105, 110],
        highs=[100, 101, 115, 111],
        lows=[98, 99, 92, 109],
    )
    trades = pd.DataFrame([_trade()])
    excursions = excursion.compute_trade_excursions(bars, trades)
    excursions["Symbol"] = "AAPL"

    excursion.write_excursion_report("Pullback to 21 EMA", excursions)

    csv_path = tmp_path / "pullback_to_21_ema_mfe_mae.csv"
    summary_path = tmp_path / "pullback_to_21_ema_mfe_mae_summary.txt"
    assert csv_path.exists()
    assert summary_path.exists()

    df = pd.read_csv(csv_path)
    assert list(df.columns) == [
        "trade_id", "symbol", "entry_date", "exit_date", "direction",
        "entry_price", "exit_price", "realized_r", "mfe_r", "mae_r",
        "exit_efficiency_pct", "loss_realization_ratio_pct", "entry_slippage_pct",
    ]
    assert df.loc[0, "symbol"] == "AAPL"
    assert df.loc[0, "mfe_r"] == pytest.approx(3.0)

    summary = summary_path.read_text()
    assert "Pullback to 21 EMA" in summary
    assert "Scatter 1" in summary
    assert "Scatter 2" in summary


def test_write_excursion_report_noop_on_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(excursion, "LOGS_DIR", tmp_path)
    excursion.write_excursion_report("S", pd.DataFrame())
    assert list(tmp_path.iterdir()) == []
