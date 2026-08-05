"""Dual Momentum Pullback Swing.

This is deliberately a *separate* strategy from the monthly, portfolio-level
``Dual Momentum`` implementation.  It uses the same two momentum ideas as a
gate for individual long swing entries:

* market absolute momentum: SPY must beat the return available from cash;
* relative momentum: the candidate must beat SPY over the same lookback.

Once that regime is present, it buys a short-term RSI pullback to a rising
EMA.  The normal per-symbol engine then models the short-term stop/target and
mean-reversion exit, which a monthly rotation engine cannot do.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr, ema, rsi, sma
from strategies.base import Strategy
from strategies.params import param_field
from strategies.swing._utils import is_bullish_candle


@dataclass
class DualMomentumPullbackSwing(Strategy):
    name = "Dual Momentum Pullback Swing"
    timeframe = "1d"
    direction = "long"

    # Injected by the runner for the exact backtest window; they are not UI
    # knobs because changing either changes the definition of the signal.
    benchmark_bars: pd.DataFrame
    risk_free_rate: float = 0.0

    momentum_lookback: int = param_field(
        189, label="Momentum lookback (trading days)", minimum=63, maximum=378, step=21,
        help="Both SPY's absolute-momentum and the stock-vs-SPY relative-momentum lookback.",
    )
    trend_sma_period: int = param_field(
        50, label="Trend SMA period", minimum=20, maximum=200, step=10,
    )
    pullback_ema_period: int = param_field(
        21, label="Pullback EMA period", minimum=10, maximum=50, step=1,
    )
    ema_slope_lookback: int = param_field(
        10, label="EMA slope lookback (bars)", minimum=3, maximum=30, step=1,
    )
    rsi_period: int = param_field(
        2, label="Pullback RSI period", minimum=2, maximum=10, step=1,
    )
    rsi_threshold: int = param_field(
        10, label="RSI oversold threshold", minimum=1, maximum=40, step=1,
    )
    pullback_atr_tolerance: float = param_field(
        0.5, label="Pullback tolerance (x ATR)", minimum=0.1, maximum=2.0, step=0.1,
        help="How close the day's low must come to the rising EMA, measured in ATRs.",
    )
    stop_atr_multiple: float = param_field(
        2.0, label="Stop distance (x ATR)", minimum=1.0, maximum=5.0, step=0.5,
    )
    target_risk_multiple: float = param_field(
        2.0, label="Target (x risk)", minimum=0.5, maximum=5.0, step=0.5,
    )
    exit_sma_period: int = param_field(
        5, label="Mean-reversion exit SMA period", minimum=2, maximum=20, step=1,
        help="Exit when price closes back above this short-term average; the ATR stop and target remain active.",
    )

    def _benchmark_closes(self, bars: pd.DataFrame) -> pd.Series | None:
        """Benchmark closes aligned only through the current signal bar.

        ``reindex`` ensures an asset holiday/missing bar is harmless, while
        slicing to ``bars.index`` makes the no-look-ahead boundary explicit.
        """
        if "Close" not in self.benchmark_bars:
            return None
        benchmark = self.benchmark_bars.loc[:bars.index[-1], "Close"]
        benchmark = benchmark.reindex(bars.index).ffill()
        return None if benchmark.isna().any() else benchmark

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        required = max(
            self.momentum_lookback + 1,
            self.trend_sma_period,
            self.pullback_ema_period + self.ema_slope_lookback,
            self.rsi_period + 1,
        )
        if len(bars) < required:
            return False
        benchmark = self._benchmark_closes(bars)
        if benchmark is None or len(benchmark) < self.momentum_lookback + 1:
            return False

        asset_return = bars["Close"].iloc[-1] / bars["Close"].iloc[-self.momentum_lookback - 1] - 1
        benchmark_return = benchmark.iloc[-1] / benchmark.iloc[-self.momentum_lookback - 1] - 1
        cash_return = (1 + self.risk_free_rate) ** (self.momentum_lookback / 252) - 1
        momentum_gate = benchmark_return > cash_return and asset_return > benchmark_return
        if not momentum_gate:
            return False

        pullback_ema = ema(bars["Close"], self.pullback_ema_period)
        trend = sma(bars["Close"], self.trend_sma_period)
        last = bars.iloc[-1]
        trend_gate = (
            last["Close"] > trend.iloc[-1]
            and pullback_ema.iloc[-1] > pullback_ema.iloc[-self.ema_slope_lookback]
            and last["Close"] >= pullback_ema.iloc[-1]
        )
        near_ema = abs(last["Low"] - pullback_ema.iloc[-1]) <= self.pullback_atr_tolerance * atr(bars).iloc[-1]
        oversold = rsi(bars["Close"], self.rsi_period).iloc[-1] < self.rsi_threshold
        return bool(trend_gate and near_ema and oversold and is_bullish_candle(last))

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price - self.stop_atr_multiple * atr(bars).iloc[-1]

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        stop = self.stop_price(bars, entry_price)
        return entry_price + self.target_risk_multiple * (entry_price - stop)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.exit_sma_period:
            return False
        return bool(bars["Close"].iloc[-1] > sma(bars["Close"], self.exit_sma_period).iloc[-1])
