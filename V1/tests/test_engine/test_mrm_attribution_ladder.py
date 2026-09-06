from __future__ import annotations

from dataclasses import fields

import numpy as np
import pandas as pd

from engine.mrm_attribution_ladder import PlainMomentumControl
from strategies.swing.frozen_research import MarketResidualMomentum


def _bars(close, volume=None):
    close = np.asarray(close, dtype=float)
    index = pd.bdate_range("2023-01-02", periods=len(close))
    volume = np.full(len(close), 1_000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * .99,
        "Close": close, "Volume": volume,
    }, index=index)


def test_required_history_days_matches_market_residual_momentum():
    # The attribution ladder's premise is that rungs C and D share every
    # mechanic except the score formula -- if their warmup requirements ever
    # diverged, the two rungs would start trading on different days and stop
    # being a clean ablation.
    plain = PlainMomentumControl(lookback=126, skip_days=5)
    residual = MarketResidualMomentum(lookback=126, skip_days=5, benchmark_bars=_bars([100.0]))
    assert plain.required_history_days() == residual.required_history_days()


def test_plain_momentum_ranks_by_raw_cumulative_return():
    strong = _bars(np.linspace(50, 100, 220))
    weak = _bars(np.linspace(50, 60, 220))
    strategy = PlainMomentumControl(lookback=126, skip_days=5, top_n=1)
    weights = strategy.rebalance({"STRONG": strong, "WEAK": weak}, strong.index[-1])
    assert weights == {"STRONG": 1.0}


def test_plain_momentum_is_deterministic_and_takes_no_market_input():
    # PlainMomentumControl has no benchmark_bars field at all -- it cannot be
    # market-conditioned even in principle. Repeated calls on the same bars
    # must agree, which is the mechanical guarantee that any C-vs-D gap the
    # ladder measures comes from D's residualization step, not from some
    # other hidden source of variation in C.
    assert not any(field.name == "benchmark_bars" for field in fields(PlainMomentumControl))
    up = _bars(np.linspace(80, 140, 220))
    down = _bars(np.linspace(140, 80, 220))
    strategy = PlainMomentumControl(lookback=126, skip_days=5, top_n=1)
    as_of = up.index[-1]
    first = strategy.rebalance({"UP": up, "DOWN": down}, as_of)
    second = strategy.rebalance({"UP": up, "DOWN": down}, as_of)
    assert first == second == {"UP": 1.0}


def test_residual_score_depends_on_the_market_series_plain_momentum_ignores():
    # Same stock bars, two different market series. MarketResidualMomentum's
    # score must move with the market it regresses against -- that
    # dependence is exactly the extra ingredient rung D has over rung C, so
    # if this ever stopped being true the two rungs would have quietly
    # collapsed into the same strategy under a different name.
    stock = _bars(np.linspace(80, 140, 220))
    rising_market = _bars(np.linspace(100, 130, 220))
    falling_market = _bars(np.linspace(130, 100, 220))
    as_of = stock.index[-1]

    under_rising_market = MarketResidualMomentum(
        lookback=63, skip_days=0, benchmark_bars=rising_market,
    ).rebalance({"AAA": stock}, as_of)
    under_falling_market = MarketResidualMomentum(
        lookback=63, skip_days=0, benchmark_bars=falling_market,
    ).rebalance({"AAA": stock}, as_of)

    # Both still select the only available symbol (top_n defaults to 5, and
    # there is one candidate) -- the market series changes the SCORE behind
    # that selection, which plain momentum has no equivalent input to move.
    assert under_rising_market == under_falling_market == {"AAA": 1.0}

    def _score(benchmark_bars: pd.DataFrame) -> float:
        strategy = MarketResidualMomentum(lookback=63, skip_days=0, benchmark_bars=benchmark_bars)
        market = strategy.benchmark_bars.loc[strategy.benchmark_bars.index < as_of, "Close"].pct_change()
        market = market.iloc[-strategy.lookback:]
        stock_returns = stock.loc[:as_of, "Close"].pct_change()
        joined = pd.concat(
            [stock_returns.rename("stock"), market.rename("market")], axis=1,
        ).dropna().iloc[-strategy.lookback:]
        x = joined["market"].to_numpy(dtype=float)
        y = joined["stock"].to_numpy(dtype=float)
        design = np.column_stack([np.ones(len(x)), x])
        alpha, beta = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y - (alpha + beta * x)
        return float(np.prod(1.0 + residual) - 1.0)

    assert _score(rising_market) != _score(falling_market)
