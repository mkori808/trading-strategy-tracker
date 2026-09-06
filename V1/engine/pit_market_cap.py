"""Point-in-time market-cap classification for licensed daily histories.

This module never substitutes today's market cap for a historical value.
Classification uses the last observation strictly before the decision date;
missing histories remain unclassified. The nominal breakpoints are an explicit
research policy, not claims about S&P index eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class MarketCapBands:
    mega_min: float = 200_000_000_000.0
    large_min: float = 10_000_000_000.0
    mid_min: float = 2_000_000_000.0
    small_min: float = 250_000_000.0

    def __post_init__(self) -> None:
        if not self.mega_min > self.large_min > self.mid_min > self.small_min > 0:
            raise ValueError("market-cap thresholds must be strictly descending and positive")


DEFAULT_BANDS = MarketCapBands()


def market_cap_tier(value: float, bands: MarketCapBands = DEFAULT_BANDS) -> str:
    if value >= bands.mega_min:
        return "mega"
    if value >= bands.large_min:
        return "large"
    if value >= bands.mid_min:
        return "mid"
    if value >= bands.small_min:
        return "small"
    return "micro"


def classifications_at(
    market_caps: dict[str, pd.Series],
    as_of: date,
    *,
    bands: MarketCapBands = DEFAULT_BANDS,
) -> dict[str, dict[str, float | str]]:
    """Return each security's latest known pre-decision cap and tier."""
    signal_time = pd.Timestamp(as_of)
    output: dict[str, dict[str, float | str]] = {}
    for security_id, raw in market_caps.items():
        series = pd.to_numeric(raw, errors="coerce").dropna().sort_index()
        prior = series.loc[series.index < signal_time]
        if prior.empty:
            continue
        value = float(prior.iloc[-1])
        if value <= 0:
            continue
        output[str(security_id)] = {
            "marketCap": value,
            "tier": market_cap_tier(value, bands),
            "observationDate": pd.Timestamp(prior.index[-1]).date().isoformat(),
        }
    return output
