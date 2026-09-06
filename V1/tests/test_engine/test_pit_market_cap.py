from datetime import date

import pandas as pd
import pytest

from engine.pit_market_cap import MarketCapBands, classifications_at, market_cap_tier


def test_market_cap_tiers_have_explicit_boundaries():
    assert market_cap_tier(250_000_000) == "small"
    assert market_cap_tier(2_000_000_000) == "mid"
    assert market_cap_tier(10_000_000_000) == "large"
    assert market_cap_tier(200_000_000_000) == "mega"


def test_classification_uses_only_observations_strictly_before_decision_date():
    series = pd.Series(
        [1_000_000_000.0, 20_000_000_000.0],
        index=pd.to_datetime(["2024-01-31", "2024-02-01"]),
    )

    result = classifications_at({"permno-1": series}, date(2024, 2, 1))

    assert result["permno-1"]["marketCap"] == 1_000_000_000.0
    assert result["permno-1"]["tier"] == "small"
    assert result["permno-1"]["observationDate"] == "2024-01-31"


def test_missing_history_is_not_replaced_with_current_market_cap():
    future_only = pd.Series([300_000_000_000.0], index=pd.to_datetime(["2025-01-01"]))

    assert classifications_at({"permno-1": future_only}, date(2024, 1, 1)) == {}


def test_invalid_band_order_is_rejected():
    with pytest.raises(ValueError, match="strictly descending"):
        MarketCapBands(mega_min=10, large_min=20, mid_min=5, small_min=1)
