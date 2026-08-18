"""Pre-registered frozen V1 anomaly hypotheses from the 2026-08 protocol.

Defaults are the canonical V1 definitions. Parameter fields expose only the
neighbors declared before results; changing one creates an experiment and
never replaces V1 as the canonical row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.event_timing import TimingContract, default_timing_contract
from engine.indicators import atr, rsi, sma
from strategies.cross_sectional import CrossSectionalStrategy
from strategies.params import param_field


@dataclass
class High52WeekMomentum(CrossSectionalStrategy):
    name = "52-Week-High Momentum"
    timeframe = "1mo"
    high_window: int = param_field(252, label="High window", minimum=189, maximum=252, step=63)
    momentum_lookback: int = param_field(126, label="Momentum lookback", minimum=126, maximum=189, step=63)
    skip_days: int = param_field(5, label="Recent sessions skipped", minimum=0, maximum=21, step=1)
    top_n: int = param_field(5, label="Positions held", minimum=3, maximum=10, step=1)
    rebalance_frequency: str = param_field("monthly", label="Rebalance frequency", choices=["monthly", "every_20_sessions"])

    def required_history_days(self) -> int:
        return max(self.high_window, self.momentum_lookback + 1)

    def rebalance(self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, float]:
        ranks: dict[str, float] = {}
        for symbol, bars in universe_bars.items():
            close = bars.loc[:as_of, "Close"].dropna()
            if len(close) < self.required_history_days():
                continue
            high = float(close.iloc[-self.high_window:].max())
            base = float(close.iloc[-self.momentum_lookback - 1])
            momentum_close = float(close.iloc[-self.skip_days - 1])
            now = float(close.iloc[-1])
            if high <= 0 or base <= 0 or momentum_close / base - 1.0 <= 0:
                continue
            ranks[symbol] = now / high
        selected = sorted(ranks, key=ranks.get, reverse=True)[: self.top_n]
        return {symbol: 1.0 / len(selected) for symbol in selected} if selected else {}


@dataclass
class MarketResidualMomentum(CrossSectionalStrategy):
    name = "Market-Residual Momentum"
    timeframe = "1mo"
    benchmark_bars: pd.DataFrame | None = None
    lookback: int = param_field(126, label="Residual lookback", minimum=63, maximum=189, step=63)
    skip_days: int = param_field(5, label="Recent sessions skipped", minimum=0, maximum=21, step=1)
    top_n: int = param_field(5, label="Positions held", minimum=3, maximum=10, step=1)
    rebalance_frequency: str = param_field("monthly", label="Rebalance frequency", choices=["monthly"])

    def required_history_days(self) -> int:
        return self.lookback + self.skip_days + 2

    def rebalance(self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, float]:
        if self.benchmark_bars is None or self.benchmark_bars.empty:
            return {}
        scores: dict[str, float] = {}
        # The portfolio executes at Open[as_of]. Stock histories supplied by
        # the engine already stop strictly before that session, so SPY must do
        # the same. Including as_of left the two 126-session samples offset by
        # one row and silently produced an all-cash portfolio.
        market = self.benchmark_bars.loc[self.benchmark_bars.index < as_of, "Close"].pct_change()
        if self.skip_days:
            market = market.iloc[:-self.skip_days]
        market = market.iloc[-self.lookback:]
        for symbol, bars in universe_bars.items():
            stock = bars.loc[:as_of, "Close"].pct_change()
            if self.skip_days:
                stock = stock.iloc[:-self.skip_days]
            joined = pd.concat([stock.rename("stock"), market.rename("market")], axis=1).dropna().iloc[-self.lookback:]
            if len(joined) < self.lookback:
                continue
            x = joined["market"].to_numpy(dtype=float)
            y = joined["stock"].to_numpy(dtype=float)
            design = np.column_stack([np.ones(len(x)), x])
            alpha, beta = np.linalg.lstsq(design, y, rcond=None)[0]
            residual = y - (alpha + beta * x)
            if np.any(residual <= -1.0):
                continue
            scores[symbol] = float(np.prod(1.0 + residual) - 1.0)
        selected = sorted(scores, key=scores.get, reverse=True)[: self.top_n]
        return {symbol: 1.0 / len(selected) for symbol in selected} if selected else {}


class FrozenEventStrategy:
    timeframe = "1d"
    requires_earnings_exclusion = True
    holding_sessions = 5
    borrow_assumption: str | None = None

    def earnings_mode(self) -> str:
        """How event sessions enter this pre-registered diagnostic arm."""
        return "excluded" if self.requires_earnings_exclusion else "included"

    @classmethod
    def timing_contract(cls) -> TimingContract:
        return default_timing_contract(engine="frozen_event")

    def signal(self, bars: pd.DataFrame, market_bars: pd.DataFrame) -> str | None:
        raise NotImplementedError

    def exit_due(self, bars: pd.DataFrame, entry_position: int, current_position: int) -> bool:
        return current_position >= entry_position + self.holding_sessions - 1


@dataclass
class NegativeVolumeShockReversal(FrozenEventStrategy):
    name = "Negative Return + Volume Shock Reversal"
    return_threshold: float = param_field(-0.04, label="One-day return threshold", minimum=-0.06, maximum=-0.03, step=0.01)
    volume_ratio: float = param_field(2.0, label="Volume ratio", minimum=1.5, maximum=3.0, step=0.5)
    trend_filter: bool = param_field(True, label="Require Close above SMA200")
    holding_sessions: int = param_field(3, label="Holding sessions", minimum=2, maximum=7, step=1)

    def signal(self, bars: pd.DataFrame, market_bars: pd.DataFrame) -> str | None:
        if len(bars) < 201:
            return None
        ret = float(bars["Close"].iloc[-1] / bars["Close"].iloc[-2] - 1.0)
        median_volume = float(bars["Volume"].iloc[-21:-1].median())
        ratio = float(bars["Volume"].iloc[-1] / median_volume) if median_volume > 0 else 0.0
        trend_ok = not self.trend_filter or float(bars["Close"].iloc[-1]) > float(sma(bars["Close"], 200).iloc[-1])
        return "long" if ret <= self.return_threshold and ratio >= self.volume_ratio and trend_ok else None


@dataclass
class VolumeShockContinuation(FrozenEventStrategy):
    direction: str = "long"
    volume_ratio: float = param_field(2.0, label="Volume ratio", minimum=1.5, maximum=3.0, step=0.5)
    close_location_threshold: float = param_field(0.80, label="Close-location threshold", minimum=0.70, maximum=0.90, step=0.10)
    holding_sessions: int = param_field(5, label="Holding sessions", minimum=3, maximum=10, step=1)
    event_mode: str = param_field(
        "excluded", label="Earnings-event mode",
        choices=["excluded", "earnings_only_diagnostic"],
    )

    @property
    def name(self) -> str:
        return f"Volume-Shock Continuation ({self.direction.title()})"

    def signal(self, bars: pd.DataFrame, market_bars: pd.DataFrame) -> str | None:
        if len(bars) < 22:
            return None
        prior = float(bars["Close"].iloc[-2])
        ret = float(bars["Close"].iloc[-1] / prior - 1.0) if prior > 0 else 0.0
        med = float(bars["Volume"].iloc[-21:-1].median())
        volume_ratio = float(bars["Volume"].iloc[-1] / med) if med > 0 else 0.0
        high, low, close = (float(bars[column].iloc[-1]) for column in ("High", "Low", "Close"))
        location = (close - low) / (high - low) if high > low else 0.5
        if self.direction == "long":
            return "long" if ret > 0 and volume_ratio >= self.volume_ratio and location >= self.close_location_threshold else None
        return "short" if ret < 0 and volume_ratio >= self.volume_ratio and location <= 1.0 - self.close_location_threshold else None

    def earnings_mode(self) -> str:
        return self.event_mode


@dataclass
class MaxLotteryReversal(FrozenEventStrategy):
    name = "MAX Lottery-Return Reversal (Short)"
    max_threshold: float = param_field(0.08, label="MAX5 threshold", minimum=0.05, maximum=0.12, step=0.01)
    volume_ratio: float = param_field(1.5, label="Event-day volume ratio", minimum=1.0, maximum=2.0, step=0.5)
    holding_sessions: int = param_field(5, label="Holding sessions", minimum=2, maximum=10, step=1)
    earnings_exclusion: bool = param_field(True, label="Exclude earnings reactions")
    borrow_assumption = "Assumes historical borrow availability; locate fees and rejections are not modeled."

    def signal(self, bars: pd.DataFrame, market_bars: pd.DataFrame) -> str | None:
        if len(bars) < 26:
            return None
        returns = bars["Close"].pct_change()
        recent = returns.iloc[-5:]
        event_time = recent.idxmax()
        event_return = float(recent.loc[event_time])
        event_position = bars.index.get_loc(event_time)
        if event_position < 20:
            return None
        med = float(bars["Volume"].iloc[event_position - 20:event_position].median())
        ratio = float(bars["Volume"].iloc[event_position] / med) if med > 0 else 0.0
        return "short" if event_return >= self.max_threshold and ratio >= self.volume_ratio else None

    def earnings_mode(self) -> str:
        return "excluded" if self.earnings_exclusion else "included"


@dataclass
class VolatilityConditionedPullback(FrozenEventStrategy):
    name = "Volatility-Conditioned Pullback"
    requires_earnings_exclusion = False
    rsi_threshold: float = param_field(5.0, label="RSI2 threshold", minimum=2.0, maximum=10.0, step=1.0)
    atr_percentile_low: float = param_field(0.20, label="ATR percentile lower bound", minimum=0.0, maximum=0.50, step=0.10)
    atr_percentile_high: float = param_field(0.80, label="ATR percentile upper bound", minimum=0.50, maximum=1.0, step=0.10)
    market_trend_filter: bool = param_field(True, label="Require SPY above SMA200")
    holding_sessions: int = param_field(5, label="Maximum holding sessions", minimum=3, maximum=7, step=1)

    def signal(self, bars: pd.DataFrame, market_bars: pd.DataFrame) -> str | None:
        if len(bars) < 252 or len(market_bars) < 200:
            return None
        close = bars["Close"]
        if float(close.iloc[-1]) <= float(sma(close, 200).iloc[-1]):
            return None
        if self.market_trend_filter and float(market_bars["Close"].iloc[-1]) <= float(sma(market_bars["Close"], 200).iloc[-1]):
            return None
        if float(rsi(close, 2).iloc[-1]) > self.rsi_threshold:
            return None
        normalized_atr = (atr(bars, 14) / close).dropna()
        history = normalized_atr.iloc[-252:]
        if len(history) < 60:
            return None
        percentile = float((history.iloc[:-1] <= history.iloc[-1]).mean())
        return "long" if self.atr_percentile_low <= percentile <= self.atr_percentile_high else None

    def exit_due(self, bars: pd.DataFrame, entry_position: int, current_position: int) -> bool:
        close = bars["Close"]
        signal_exit = current_position >= entry_position and float(close.iloc[-1]) > float(sma(close, 5).iloc[-1])
        return signal_exit or super().exit_due(bars, entry_position, current_position)


@dataclass
class UnavailableResearchStrategy:
    unavailable_reason: str = ""
    timeframe = "1d"

    @classmethod
    def timing_contract(cls) -> TimingContract:
        return default_timing_contract(engine="unavailable")
