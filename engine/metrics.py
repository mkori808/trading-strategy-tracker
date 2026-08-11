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

# Lives here rather than in engine/portfolio.py so engine/backtest.py can use
# it too -- portfolio.py imports FROM backtest.py, so the reverse direction is
# a circular import. metrics.py has no engine dependencies and sits below both.
TRADING_DAYS_PER_YEAR = 252

MIN_RELIABLE_TRADES = 30

# Trade count and EXPOSURE are independent reliability facts, and MIN_RELIABLE_TRADES
# can only see the first. A strategy can clear 30 trades while being in the market
# for days: Earnings Momentum takes 42 trades over five years at 1.58% exposure,
# roughly 20 invested days.
#
# Below this floor Sharpe is not merely imprecise, it is DEGENERATE. Once idle
# cash correctly earns the risk-free rate, a ~99%-cash account returns almost
# exactly rf with almost exactly zero volatility -- so Sharpe becomes a tiny
# excess divided by a near-zero denominator. Measured on Earnings Momentum after
# the accrual fix: excess CAGR +0.0942%, implied annual volatility 0.0000%,
# Sharpe **51,844.877**. The same strategy scored -8.09 before the fix. Both
# numbers are noise from the same cause: the ratio is undefined for an account
# that is mostly T-bills, and it merely flips sign depending on which way the
# arithmetic is broken.
#
# So Sharpe/Sortino are withheld entirely below this threshold rather than
# reported with a caveat. There is no honest value to show.
MIN_INVESTED_DAYS = 30

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

# Portfolio-engine counterparts (cross-sectional/pairs -- see
# engine/logging_db.py's portfolio_runs table). These engines have no
# R-multiple trades, so the verdict is phrased in return terms, but it
# applies the SAME bar as derive_status() below: Sharpe > SHARPE_THRESHOLD vs.
# cash, and beating a benchmark (here SPY's buy-and-hold return over the
# identical window, since these engines have no per-symbol alpha).
STATUS_PORTFOLIO_POSITIVE = "Positive return - shortlist"
STATUS_PORTFOLIO_UNDERPERFORMS = "Positive return but underperforms cash/benchmark - hold"
STATUS_PORTFOLIO_NEGATIVE = "Negative return - drop"

# OUTSIDE the tier ordering, not the bottom of it. A run whose window the
# universe cannot support (engine/cross_sectional.py:InsufficientHistory --
# e.g. a 2018 start over the July-2021 Dow roster, where DOW has no price
# history before 2019-03-20) was never measured at all. It has no return, no
# Sharpe and no alpha, so it must be EXCLUDED from any ranking rather than
# sorted with nulls, where it would sink to the bottom and read as "tested and
# terrible" -- which is a different and much stronger claim than "refused".
#
# It is also distinct from STATUS_NOT_TESTED: "not yet tested" invites a
# re-run, while this run cannot be re-run at any version of the code. Without
# a terminal marker, a reader of the backfill sees rows that silently produced
# nothing and cannot tell a refusal from a crash partway through.
STATUS_INVALID_WINDOW = "Invalid window - not measurable"

# Measured under a SUPERSEDED metric convention, and not yet re-measured under
# the current one. Distinct from STATUS_NOT_TESTED for the same reason
# STATUS_INVALID_WINDOW is: "not yet tested" is a false statement about a
# strategy that has been run many times, and it invites the wrong action --
# a user reads it as "nobody has tried this" rather than "the result exists but
# its numbers were invalidated and a re-run is already in flight".
#
# The distinction matters most while a backfill is in progress, when most of
# the board legitimately has no current-version row. Showing "Not yet tested"
# there would be the same failure this whole metric overhaul was about: a label
# that reads as authoritative while being wrong about what it describes.
STATUS_AWAITING_REMEASUREMENT = "Awaiting re-measurement (metrics v1)"

# Enough trades, but not enough TIME IN THE MARKET for a risk-adjusted figure to
# mean anything. Distinct from STATUS_SAMPLE_TOO_SMALL, which is about trade
# count: a strategy can clear 30 trades and still hold positions for ~20 days
# total, and Sharpe is degenerate there (see MIN_INVESTED_DAYS). Reported as its
# own tier so "we cannot measure this" is never mistaken for "we measured it and
# it was bad".
STATUS_INSUFFICIENT_EXPOSURE = "Too little time in market to measure risk-adjusted return"

# Positive expectancy, but the risk-adjusted gate could not be EVALUATED --
# neither Sharpe nor alpha was available. Never a tier, because a tier asserts
# a verdict that was not reached.
#
# The old code skipped the whole gate when both were absent and fell through to
# "shortlist". Measured: Overnight Hold carried 28,370 trades, +0.013
# expectancy, Sharpe None and alpha None, and was the ONLY per-symbol row on the
# board holding a shortlist tier -- promotable to paper execution on two values
# that did not exist. Absence was being read as satisfaction of "Sharpe > 0.5
# AND alpha > 0".
STATUS_UNVERIFIED = "Positive expectancy - risk-adjusted verdict unavailable"

#: Statuses carrying no measured numbers. Rank/sort must skip these outright.
UNRANKABLE_STATUSES = frozenset({
    STATUS_INVALID_WINDOW,
    STATUS_AWAITING_REMEASUREMENT,
    STATUS_INSUFFICIENT_EXPOSURE,
    STATUS_UNVERIFIED,
})


# --- Plausibility floor -----------------------------------------------------
#
# NOT precision tests. These are priors a human reader carries and the codebase
# never encoded: nothing here knew that a Sharpe of 7.24 is impossible for a
# Dow-universe equity strategy, but a person reading the row knows instantly.
# That gap is why implausible results kept being caught by eye rather than by
# the suite -- eyeballing is a genuinely different oracle, using knowledge that
# lived only in someone's head. This encodes it.
#
# Every bug this exists to catch produced a number that was wrong by an ORDER
# OF MAGNITUDE, not by a subtle margin, so the bands are deliberately wide.
# A result that trips one is a bug report, not a remarkable strategy:
#   * Sharpe 7.238 / alpha +36.7% -- risk-free interest accrued at a daily rate
#     once per 5-MINUTE bar (~79x over-credit). Its phantom return scaled with
#     IDLENESS, so the intraday strategies flattered most would have been the
#     ones trading least -- the exact mirror of the original -rf/sigma bug,
#     which punished idleness at the wrong rate instead of rewarding it.
#   * Sharpe -43.48 -- calendar-day returns annualized with a 252-day constant.
# DELIBERATELY ASYMMETRIC, and the asymmetry is the whole point. A Sharpe above
# +3 on a Dow-universe equity strategy is not a remarkable result, it is a bug:
# steady large gains at low volatility do not occur here. A deeply NEGATIVE
# Sharpe is merely terrible, not impossible -- losing money steadily is easy.
#
# A symmetric (-3, 3) band was tried first and immediately produced a false
# positive on real data: VWAP Bounce / Reversion scores -8.90 from a genuine
# -44.98% CAGR at 5.51% implied volatility, 67% exposed and 7,372 trades. The
# idle-cash artifact cannot explain it at that exposure -- it is simply a
# strategy that bleeds consistently, and a tight lower bound would have refused
# to record a true result. Scalping's -43.48 is the same shape: -66.84% CAGR at
# ~1.6% volatility, death by a thousand spreads over 22,281 trades.
#
# So the lower bound is loose enough never to block a real loser, and does
# little work; the CAGR band below carries the downside. This is honest about
# where the guard has teeth rather than pretending to symmetry it cannot have.
PLAUSIBLE_SHARPE = (-50.0, 3.0)
PLAUSIBLE_CAGR_PCT = (-100.0, 200.0)


def invested_days(
    exposure_pct: float | None,
    start: date | None,
    end: date | None,
) -> float | None:
    """Trading days actually spent holding a position, from exposure x window.

    Exposure alone cannot answer the reliability question: 10.9% over five years
    is ~137 invested days (a usable sample) while 1.6% is ~20 (not one). Same
    percentage arithmetic, opposite conclusions.
    """
    if exposure_pct is None or start is None or end is None:
        return None
    span_years = (end - start).days / 365.25
    return (exposure_pct / 100.0) * span_years * TRADING_DAYS_PER_YEAR


def coverage_is_measurable(
    measured_start: date | None, measured_end: date | None
) -> bool:
    """Could this window reach MIN_INVESTED_DAYS even at 100% exposure?

    Derived from the MEASURED window, never the requested one -- an intraday run
    labelled two years covers ~50 trading days, and applying the guard to the
    label would pass every time while the data underneath could not support it.

    When this is False the run is unmeasurable BY CONSTRUCTION, the same way a
    window predating a symbol's first bar is: no strategy behaviour can rescue
    it, so it is a refusal rather than a poor score. 50 trading days at under
    60% exposure cannot produce 30 invested days -- that is arithmetic, not a
    threshold choice.
    """
    if measured_start is None or measured_end is None:
        return True  # unknown coverage is not evidence of bad coverage
    span_years = (measured_end - measured_start).days / 365.25
    return span_years * TRADING_DAYS_PER_YEAR >= MIN_INVESTED_DAYS


def implausible_metrics(
    sharpe: float | None = None,
    cagr_pct: float | None = None,
    win_rate: float | None = None,
    exposure_pct: float | None = None,
) -> list[str]:
    """Bounds violations, empty when everything is within the plausible band.

    A floor under the whole suite rather than a replacement for any test in it:
    it cannot tell a correct number from a slightly wrong one, only a correct
    number from an impossible one. That is precisely the class that repeatedly
    survived a green suite, because a fixture that shares the implementation's
    premise validates arithmetic rather than correctness.
    """
    problems: list[str] = []
    if sharpe is not None and not (PLAUSIBLE_SHARPE[0] <= sharpe <= PLAUSIBLE_SHARPE[1]):
        problems.append(f"Sharpe {sharpe:.3f} outside plausible {PLAUSIBLE_SHARPE}")
    if cagr_pct is not None and not (PLAUSIBLE_CAGR_PCT[0] <= cagr_pct <= PLAUSIBLE_CAGR_PCT[1]):
        problems.append(f"CAGR {cagr_pct:.2f}% outside plausible {PLAUSIBLE_CAGR_PCT}")
    if win_rate is not None and not (0.0 <= win_rate <= 1.0):
        problems.append(f"win rate {win_rate} outside [0, 1]")
    if exposure_pct is not None and not (0.0 <= exposure_pct <= 100.0):
        problems.append(f"exposure {exposure_pct}% outside [0, 100]")
    return problems


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
    # What buying and holding the same symbol(s) over the same window alone
    # would have returned -- alpha_pct is the STRATEGY's excess return over
    # this, so showing both lets a user see the benchmark itself, not just
    # the difference from it.
    buy_hold_return_pct: float | None = None
    # The window ACTUALLY covered by data, as opposed to the one requested.
    # They diverge badly for intraday strategies: the free data tier serves only
    # ~50 days of 5-minute bars, so a run requesting 2024-08-11 -> 2026-08-11
    # measures roughly 6.8% of its own label. Displaying the requested window
    # made Scalping's 21,108 trades read as a decade of evidence when they are
    # seven weeks of it, densely sampled. Recorded separately rather than
    # overwriting `start`/`end` so "what was asked" and "what was measured"
    # stay distinguishable -- same principle as slippage_bps being descriptive.
    measured_start: date | None = None
    measured_end: date | None = None


def derive_status(
    trades_taken: int,
    expectancy_r: float,
    sharpe: float | None = None,
    alpha_pct: float | None = None,
    invested_days_count: float | None = None,
) -> str:
    """Verdict from a run's computed numbers. Public because the API's
    leaderboard recomputes status from each stored row's numbers with
    CURRENT logic rather than trusting the status string logged at run
    time -- measured directly: Overnight Hold's best-Sharpe canonical row
    predated the Sharpe gate added 2026-07-16 and showed a stale
    'shortlist' (sharpe -0.66) that a re-run with identical numbers no
    longer produces. The stored string remains the honest historical
    record in /api/history; the leaderboard shows today's verdict."""
    if trades_taken == 0:
        return STATUS_NOT_TESTED
    if trades_taken < MIN_RELIABLE_TRADES:
        return STATUS_SAMPLE_TOO_SMALL
    if expectancy_r <= 0:
        return STATUS_NEGATIVE
    # Enough trades, but not enough time in the market for a risk-adjusted
    # figure to exist. Checked BEFORE the Sharpe gate because below this floor
    # the Sharpe being gated on is degenerate, not merely noisy: a ~99%-cash
    # account has near-zero excess return over a near-zero volatility (measured:
    # Earnings Momentum at 1.58% exposure produced Sharpe 51,844.877 after the
    # accrual fix, and -8.09 before it). Placed after the expectancy check
    # because a NEGATIVE expectancy is still a valid verdict without any
    # risk-adjusted number -- a losing strategy is droppable on its own terms.
    if invested_days_count is not None and invested_days_count < MIN_INVESTED_DAYS:
        return STATUS_INSUFFICIENT_EXPOSURE
    # Apply the Sharpe/alpha bar against whichever of the two is actually
    # available, rather than requiring both -- some engines never compute
    # alpha (e.g. engine/overnight.py has no benchmark to compare against,
    # see its _symbol_stats), and gating on "both present" let a strategy
    # with a deeply negative Sharpe read as "shortlist" purely because its
    # missing alpha short-circuited the whole check. Only skip the gate
    # entirely when neither is supplied at all (e.g. synthetic unit tests
    # that don't compute either) -- those fall back to the plain expectancy
    # gate rather than being silently downgraded.
    # A metric that is ABSENT cannot satisfy a gate. Both missing means the
    # risk-adjusted verdict was never reached, so no tier is awarded -- the old
    # `sharpe is None or ...` treated a blank as a pass, which is how a
    # 28,370-trade strategy with no Sharpe and no alpha became the board's only
    # shortlisted row.
    if sharpe is None and alpha_pct is None:
        return STATUS_UNVERIFIED
    beats_cash = sharpe is not None and sharpe > SHARPE_THRESHOLD
    beats_benchmark = alpha_pct is not None and alpha_pct > 0
    # Each half may legitimately be unavailable -- engine/overnight.py has no
    # benchmark concept, so alpha is structurally absent there rather than
    # missing. An unavailable half is neither passed nor failed: the verdict
    # rests on whichever half exists, and a row with neither is UNVERIFIED above.
    if sharpe is None:
        beats_cash = True
    if alpha_pct is None:
        beats_benchmark = True
    if not (beats_cash and beats_benchmark):
        return STATUS_UNDERPERFORMS
    return STATUS_POSITIVE


def portfolio_status(
    return_pct: float,
    sharpe: float | None,
    benchmark_return_pct: float | None,
) -> str:
    """Verdict for a portfolio-engine run (cross-sectional/pairs), mirroring
    derive_status()'s tiers with return-based language.

    The benchmark half is the SAME basis the per-symbol path uses, despite the
    different wording. Per-symbol alpha is (R_strategy - rf) - (R_benchmark - rf),
    where rf cancels to R_strategy - R_benchmark; comparing return_pct against
    benchmark_return_pct is that identical quantity evaluated as a boolean. The
    two paths were never gating on different things -- but this one never STORED
    the difference, so the leaderboard's alpha column read blank and the board
    looked like it was comparing returns here and excess returns everywhere else.
    engine/runner.py now records it (see portfolio_alpha_pct).

    Absence, however, was genuinely mishandled here long after derive_status was
    fixed: `sharpe is None or ...` let a missing value SATISFY the gate, so a
    portfolio run with no Sharpe and no benchmark scored "shortlist" outright --
    and a shortlisted canonical row is exactly what the promote-to-paper-execution
    button acts on. Not reachable with today's data (both v2 rows are complete),
    which is precisely why it survived the sweep that fixed the per-symbol side.
    """
    if return_pct <= 0:
        return STATUS_PORTFOLIO_NEGATIVE
    if sharpe is None and benchmark_return_pct is None:
        return STATUS_UNVERIFIED
    beats_cash = sharpe is None or sharpe > SHARPE_THRESHOLD
    beats_benchmark = (
        benchmark_return_pct is None or return_pct > benchmark_return_pct
    )
    if not (beats_cash and beats_benchmark):
        return STATUS_PORTFOLIO_UNDERPERFORMS
    return STATUS_PORTFOLIO_POSITIVE


def portfolio_alpha_pct(
    return_pct: float | None, benchmark_return_pct: float | None
) -> float | None:
    """Portfolio-engine alpha, on the same basis as the per-symbol column.

    Both net of the risk-free rate, which cancels -- so this is simply the
    strategy's return minus the benchmark's over the identical window. Stored so
    the leaderboard compares every row on one quantity instead of showing a
    number for twenty-four rows and a blank for two.
    """
    if return_pct is None or benchmark_return_pct is None:
        return None
    return return_pct - benchmark_return_pct


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
    measured_start: date | None = None,
    measured_end: date | None = None,
    max_drawdown_pct: float | None = None,
    sharpe: float | None = None,
    sortino: float | None = None,
    alpha_pct: float | None = None,
    beta: float | None = None,
    cagr_pct: float | None = None,
    exposure_pct: float | None = None,
    risk_free_rate: float | None = None,
    buy_hold_return_pct: float | None = None,
) -> BacktestMetrics:
    trades_taken = len(trades)
    if trades_taken == 0:
        return BacktestMetrics(
            strategy_name, symbol, start, end, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0,
            max_drawdown_pct, sharpe, sortino, STATUS_NOT_TESTED,
            alpha_pct=alpha_pct, beta=beta, cagr_pct=cagr_pct,
            exposure_pct=exposure_pct, risk_free_rate=risk_free_rate,
            buy_hold_return_pct=buy_hold_return_pct,
            measured_start=measured_start, measured_end=measured_end,
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

    # Measured window preferred over the requested one -- a day-trading run
    # labelled two years measures ~50 days, and exposure applied to the wrong
    # span would overstate invested days by an order of magnitude.
    _invested = invested_days(
        exposure_pct,
        measured_start or start,
        measured_end or end,
    )
    # WITHHELD, not reported-with-a-caveat: below the floor these are a tiny
    # excess return divided by a near-zero volatility, so the value carries no
    # information about the strategy at all. Showing a number invites it being
    # read; showing None states the truth.
    if _invested is not None and _invested < MIN_INVESTED_DAYS:
        sharpe = None
        sortino = None

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
        status=derive_status(
            trades_taken, expectancy_r, sharpe, alpha_pct,
            invested_days_count=_invested,
        ),
        alpha_pct=alpha_pct,
        beta=beta,
        cagr_pct=cagr_pct,
        exposure_pct=exposure_pct,
        risk_free_rate=risk_free_rate,
        measured_start=measured_start,
        measured_end=measured_end,
        buy_hold_return_pct=buy_hold_return_pct,
    )
