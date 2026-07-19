"""Common interface every strategy module implements.

Mirrors the sketch in CLAUDE.md, extended with `entry_direction` so
strategies that trade both sides (News Fade, Mean Reversion Scalp) can tell
the engine which side triggered without changing `entry_signal`'s bool
contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd

Direction = Literal["long", "short", "both"]


class Strategy(ABC):
    name: str
    timeframe: str
    direction: Direction

    @abstractmethod
    def entry_signal(self, bars: pd.DataFrame) -> bool:
        """True if the strategy's entry rule fires on the last bar of
        `bars`. Must not look past the last row."""

    def entry_direction(self, bars: pd.DataFrame) -> Literal["long", "short"]:
        """Which side to enter when entry_signal is True. Strategies with
        direction 'both' must override this."""
        if self.direction == "both":
            raise NotImplementedError(
                f"{self.name} trades both directions and must override entry_direction"
            )
        return self.direction  # type: ignore[return-value]

    @abstractmethod
    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        """Stop-loss price for a position entered at `entry_price`."""

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        """Take-profit price, or None if the strategy relies on exit_signal
        instead of a fixed target."""
        return None

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        """True if an open position should be closed on the last bar of
        `bars`, independent of stop/target. Used by signal-exit strategies
        (e.g. EMA crossover) that don't have a fixed target."""
        return False
