from __future__ import annotations

from datetime import date
import json

import numpy as np
import pandas as pd

from engine.pit_all_stocks import inspect_dataset, load_eligibility_universe


def _write_bundle(path) -> None:
    path.mkdir()
    (path / "daily").mkdir()
    manifest = {
        "schemaVersion": 1,
        "source": "synthetic licensed-fixture equivalent",
        "snapshotId": "fixture-v1",
        "coverageStart": "2020-01-01",
        "coverageEnd": "2021-12-31",
        "priceBasis": "total_return_adjusted_ohlcv",
        "survivorshipFree": True,
        "delistedSecuritiesIncluded": True,
        "delistingReturnsIncluded": True,
        "tickerHistoryIncluded": True,
        "corporateActionsIncluded": True,
        "pointInTimeSecurityTypes": True,
        "historicalVolumeIncluded": True,
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    history = pd.DataFrame([
        {
            "security_id": "10001", "ticker": "OLD", "effective_start": "2020-01-01",
            "effective_end": "2020-06-30", "known_at": "2019-12-31",
            "is_us_listed": True, "is_common_stock": True, "security_type": "common",
            "exchange": "NYSE", "is_acquired": False, "delisting_reason": None,
        },
        {
            "security_id": "10001", "ticker": "NEW", "effective_start": "2020-07-01",
            "effective_end": "2021-12-31", "known_at": "2020-06-30",
            "is_us_listed": True, "is_common_stock": True, "security_type": "common",
            "exchange": "NYSE", "is_acquired": False, "delisting_reason": None,
        },
        {
            "security_id": "10002", "ticker": "FAIL", "effective_start": "2020-01-01",
            "effective_end": "2021-06-30", "known_at": "2019-12-31",
            "is_us_listed": True, "is_common_stock": True, "security_type": "common",
            "exchange": "NASDAQ", "is_acquired": False, "delisting_reason": "bankruptcy",
        },
        {
            "security_id": "90001", "ticker": "ETF", "effective_start": "2020-01-01",
            "effective_end": "2021-12-31", "known_at": "2019-12-31",
            "is_us_listed": True, "is_common_stock": False, "security_type": "ETF",
            "exchange": "NYSE", "is_acquired": False, "delisting_reason": None,
        },
    ])
    history.to_parquet(path / "security_history.parquet")
    dates = pd.bdate_range("2020-01-01", "2021-12-31")
    rows = []
    for security_id, end, price in (
        ("10001", pd.Timestamp("2021-12-31"), 20.0),
        ("10002", pd.Timestamp("2021-06-30"), 10.0),
        ("90001", pd.Timestamp("2021-12-31"), 50.0),
    ):
        for timestamp in dates[dates <= end]:
            rows.append({
                "security_id": security_id, "date": timestamp,
                "Open": price, "High": price * 1.01, "Low": price * 0.99,
                "Close": price, "RawClose": price, "Volume": 1_000_000.0,
                "DelistingReturn": -1.0 if security_id == "10002" and timestamp == end else np.nan,
            })
    pd.DataFrame(rows).to_parquet(path / "daily" / "part.parquet")


def test_missing_bundle_is_explicitly_not_ready(tmp_path) -> None:
    status = inspect_dataset(tmp_path / "absent")

    assert status.ready is False
    assert set(status.missing_artifacts) == {
        "manifest.json", "security_history.parquet", "daily/**/*.parquet (or daily.parquet)",
    }


def test_permanent_ids_ticker_changes_delistings_and_common_stock_filter(tmp_path) -> None:
    bundle_path = tmp_path / "bundle"
    _write_bundle(bundle_path)

    status = inspect_dataset(bundle_path)
    universe = load_eligibility_universe(
        date(2020, 1, 1), date(2021, 12, 31),
        lookback_days=1, minimum_history_days=2, liquidity_lookback_days=2,
        minimum_average_dollar_volume=1.0, bundle_dir=bundle_path,
    )

    assert status.ready is True
    assert status.security_count == 3
    assert status.delisted_count == 1
    assert status.ticker_change_count == 1
    assert universe.membership_at(date(2021, 6, 25)) == {"10001", "10002"}
    assert universe.membership_at(date(2021, 7, 2)) == {"10001"}
    assert universe.ticker_at("10001", date(2020, 6, 1)) == "OLD [10001]"
    assert universe.ticker_at("10001", date(2020, 8, 1)) == "NEW [10001]"
