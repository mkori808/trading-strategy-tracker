"""Metrics matching strategy_tracker.xlsx's definitions exactly:

    Win Rate = Wins / Trades Taken
    Expectancy (R) = (Win Rate x Avg Win R) - (Loss Rate x Avg Loss R)
    Profit Factor = Gross Wins / Gross Losses

R-multiples are computed per trade as PnL / (initial risk per share x size),
where initial risk per share = |entry price - stop price| at entry time.
Real backtest runs (engine.backtest) capture that risk in the trade's Tag
column at order-submission time, rather than relying on the trade's SL
column after the fact -- backtesting.py nulls a closed trade's SL once its
contingent stop order is done firing, e.g. for a trade that closed on a stop
gapped through on a big move, which would otherwise turn a real loss into a
NaN. Synthetic trades (e.g. in unit tests) may omit Tag; risk per share
falls back to |EntryPrice - SL| in that case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

MIN_RELIABLE_TRADES = 30

# A positive R-expectancy alone isn't enough to shortlist a strategy -- see
# LESSONS.md, "The shortlist didn't survive a benchmark comparison". Sharpe
# is measured against a real risk-free rate (engine/data.py:risk_free_rate),
# not the 0% backtesting.py defaults to, and alpha is measured against the
# strategy's own buy-and-hold on the same symbols/window.
SHARPE_THRESHOLD = 0.5

STATUS_NOT_TESTED = "Not yet tested"
STATUS_SAMPLE_TOO_SMALL = "Sample too small (<30 trades)"
STATUS_POSITIVE = "Positive expectancy - shortlist"
STATUS_UNDERPERFORMS = "Positive expectancy but underperforms cash/benchmark - hold"
STATUS_NEGATIVE = "Negative expectancy - drop"


@dataclass
class BacktestMetrics:
    strategy_name: str
    symbol: str
    start: date | None
    end: date | None
    trades_taken: int
    wins: int
    losses: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float
    profit_factor: float
    max_drawdown_pct: float | None
    sharpe: float | None
    sortino: float | None
    status: str
    alpha_pct: float | None = None
    beta: float | None = None
    cagr_pct: float | None = None
    exposure_pct: float | None = None
    risk_free_rate: float | None = None


def _status(
    trades_taken: int,
    expectancy_r: float,
    sharpe: float | None = None,
    alpha_pct: float | None = None,
) -> str:
    if trades_taken == 0:
        return STATUS_NOT_TESTED
    if trades_taken < MIN_RELIABLE_TRADES:
        return STATUS_SAMPLE_TOO_SMALL
    if expectancy_r <= 0:
        return STATUS_NEGATIVE
    # Apply the Sharpe/alpha bar against whichever of the two is actually
    # available, rather than requiring both -- some engines never compute
    # alpha (e.g. engine/overnight.py has no benchmark to compare against,
    # see its _symbol_stats), and gating on "both present" let a strategy
    # with a deeply negative Sharpe read as "shortlist" purely because its
    # missing alpha short-circuited the whole check. Only skip the gate
    # entirely when neither is supplied at all (e.g. synthetic unit tests
    # that don't compute either) -- those fall back to the plain expectancy
    # gate rather than being silently downgraded.
    if sharpe is not None or alpha_pct is not None:
        beats_cash = sharpe is None or sharpe > SHARPE_THRESHOLD
        beats_benchmark = alpha_pct is None or alpha_pct > 0
        if not (beats_cash and beats_benchmark):
            return STATUS_UNDERPERFORMS
    return STATUS_POSITIVE


def r_multiples(trades: pd.DataFrame) -> pd.Series:
    fallback = (trades["EntryPrice"] - trades["SL"]).abs()
    if "Tag" in trades.columns:
        risk_per_share = pd.to_numeric(trades["Tag"], errors="coerce").fillna(fallback)
    else:
        risk_per_share = fallback
    size = trades["Size"].abs()
    denom = risk_per_share * size
    return trades["PnL"] / denom.where(denom != 0)


def compute_metrics(
    strategy_name: str,
    symbol: str,
    trades: pd.DataFrame,
    start: date | None = None,
    end: date | None = None,
    max_drawdown_pct: float | None = None,
    sharpe: float | None = None,
    sortino: float | None = None,
    alpha_pct: float | None = None,
    beta: float | None = None,
    cagr_pct: float | None = None,
    exposure_pct: float | None = None,
    risk_free_rate: float | None = None,
) -> BacktestMetrics:
    trades_taken = len(trades)
    if trades_taken == 0:
        return BacktestMetrics(
            strategy_name, symbol, start, end, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0,
            max_drawdown_pct, sharpe, sortino, STATUS_NOT_TESTED,
            alpha_pct=alpha_pct, beta=beta, cagr_pct=cagr_pct,
            exposure_pct=exposure_pct, risk_free_rate=risk_free_rate,
        )

    r = r_multiples(trades)
    wins_mask = trades["PnL"] > 0
    losses_mask = ~wins_mask

    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    win_rate = wins / trades_taken
    loss_rate = losses / trades_taken

    avg_win_r = float(r[wins_mask].mean()) if wins else 0.0
    avg_loss_r = float(r[losses_mask].abs().mean()) if losses else 0.0
    expectancy_r = (win_rate * avg_win_r) - (loss_rate * avg_loss_r)

    gross_wins = float(trades.loc[wins_mask, "PnL"].sum())
    gross_losses = float(-trades.loc[losses_mask, "PnL"].sum())
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    return BacktestMetrics(
        strategy_name=strategy_name,
        symbol=symbol,
        start=start,
        end=end,
        trades_taken=trades_taken,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        expectancy_r=expectancy_r,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
        sharpe=sharpe,
        sortino=sortino,
        status=_status(trades_taken, expectancy_r, sharpe, alpha_pct),
        alpha_pct=alpha_pct,
        beta=beta,
        cagr_pct=cagr_pct,
        exposure_pct=exposure_pct,
        risk_free_rate=risk_free_rate,
    )
