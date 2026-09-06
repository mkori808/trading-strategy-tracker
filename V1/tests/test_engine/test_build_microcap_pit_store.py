from __future__ import annotations

import pandas as pd
import pytest

from engine.build_microcap_pit_store import BOTTOM_QUINTILE, stability_report


def test_stability_report_flags_months_below_the_preregistered_portfolio_sizes() -> None:
    frame = pd.DataFrame({
        "date": ["2020-01-31", "2020-02-29", "2020-03-31"],
        "totalWithMarketcap": [5000, 5000, 100],
        "eligibleCount": [1000, 1000, 20],
        "eligibleMarketcapCeiling": [300.0, 305.0, 10.0],
    })
    report = stability_report(frame)
    assert report["minEligibleCountAnyMonth"] == 20
    assert report["minEligibleCountDate"] == "2020-03-31"
    assert report["belowPortfolioSizeThresholdMonths"]["below30"] == 1
    assert report["belowPortfolioSizeThresholdMonths"]["below50"] == 1


def test_stability_report_handles_empty_frame() -> None:
    assert stability_report(pd.DataFrame()) == {"status": "no data"}


def test_bottom_quintile_fraction_matches_the_preregistration() -> None:
    # research/microcap_momentum_v1_preregistration.json specifies "bottom
    # quintile" explicitly -- pinned here so a refactor can't silently drift
    # the store's universe definition away from what was preregistered.
    assert BOTTOM_QUINTILE == pytest.approx(0.20)
