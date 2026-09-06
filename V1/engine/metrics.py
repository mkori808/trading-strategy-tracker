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
import math

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

# The measured effect is smaller than what this DESIGN could resolve. Same
# principle as MIN_INVESTED_DAYS and coverage_is_measurable(): a metric that
# cannot be resolved must not be dressed as a verdict.
#
# Measured on Dual Momentum: minimum detectable alpha 12.00%/yr against a
# claimed effect of ~2%/yr. The run could not have produced an interpretable
# result in EITHER direction -- it could neither establish +4%/yr nor rule out
# -7%/yr -- yet it carried a "shortlist" tier for months.
#
# EXPECT THIS TO APPLY TO NEARLY EVERY ROW. Excess CAGRs on this board run
# +0.04% to +0.50% against MDAs in the single-to-double-digit percent range. A
# board that goes almost entirely unrankable is the HONEST output of an
# instrument that cannot resolve what is being asked of it. Do not widen the
# band or add exceptions when it looks blank -- that is the same loop that
# produced the original false positive, one level up.
STATUS_UNDERPOWERED = "Effect smaller than this design can detect"

# A portfolio-engine gate specifically for the "constituent has zero bars
# during the required lookback" case (the DOW zero-bar bug engine/cross_
# sectional.py:allow_incomplete_warmup / result.incomplete_warmup exists to
# track). Measured directly: two runs (Dual Momentum x sp500_current, x
# sp600_current) reported "holdout passed" and a graded return-based verdict
# ("Positive return - shortlist") while warmup_validity had already failed
# in the SAME validation report -- the status string never knew about it,
# because portfolio_status() was computed from return/Sharpe/benchmark alone.
# A run built on incomplete required history is not measuring the strategy
# on the labeled universe; it must never carry a graded verdict, positive OR
# negative, regardless of how the numbers otherwise look.
STATUS_INCOMPLETE_WARMUP = "Incomplete warmup -- some constituents lack required lookback"

#: Statuses carrying no measured numbers. Rank/sort must skip these outright.
UNRANKABLE_STATUSES = frozenset({
    STATUS_INVALID_WINDOW,
    STATUS_AWAITING_REMEASUREMENT,
    STATUS_INSUFFICIENT_EXPOSURE,
    STATUS_UNVERIFIED,
    STATUS_INCOMPLETE_WARMUP,
    STATUS_UNDERPOWERED,
})

# Average pairwise correlation assumed when converting nominal bets into
# INDEPENDENT ones. Named, not buried, because it is the softest number in the
# whole calculation and it does most of the work: five Dow mega-caps held in the
# same month are far from independent. If the true correlation is HIGHER than
# this, every MDA on the board is optimistic and every design looks better than
# it is. Exposed in the API response so it reads as an assumption rather than a
# measurement.
ASSUMED_PAIRWISE_CORRELATION = 0.5


def minimum_detectable_alpha_pct(
    cagr_pct: float | None,
    sharpe: float | None,
    risk_free_rate: float | None,
    years: float | None,
) -> float | None:
    """Smallest annual alpha this run could have resolved at t=2.

    Derived from ALREADY-STORED fields, so it costs nothing per run:

        annual vol = (CAGR - rf) / Sharpe          (inverting the Sharpe ratio)
        SE(alpha)  = vol / sqrt(years)
        MDA        = 2 * SE(alpha)

    Uses TOTAL volatility rather than residual-from-a-factor-model, which makes
    this CONSERVATIVE: residual vol is strictly lower, so a factor regression
    would report a smaller MDA. Measured on Dual Momentum -- total-vol MDA
    17.7%/yr against the 4-factor regression's 12.00%/yr. Erring toward "harder
    to detect" is the safe direction for a gate whose failure mode is awarding
    tiers to noise.

    Requires no network access, so it can run on every backtest.
    """
    if None in (cagr_pct, sharpe, risk_free_rate, years) or not sharpe or years <= 0:
        return None
    annual_vol = (cagr_pct / 100.0 - risk_free_rate) / sharpe
    if annual_vol <= 0:
        return None
    return 2.0 * (annual_vol / (years ** 0.5)) * 100.0


def independent_bets_per_year(
    positions: int | None,
    rebalances_per_year: float | None,
    pairwise_corr: float = ASSUMED_PAIRWISE_CORRELATION,
) -> float | None:
    """Effective independent bets per year, after a cross-correlation haircut.

    Nominal breadth (positions x rebalances) badly overstates a co-moving
    universe. The haircut sqrt(1 / (1 + (n-1) * rho)) is the standard correction
    for averaging correlated signals.

    `pairwise_corr` is a parameter, not a constant folded into the formula, so a
    caller can show what was assumed and vary it. See
    ASSUMED_PAIRWISE_CORRELATION.
    """
    if not positions or not rebalances_per_year or positions < 1:
        return None
    haircut = (1.0 / (1.0 + (positions - 1) * pairwise_corr)) ** 0.5
    return positions * rebalances_per_year * haircut


# --- Plausibility floor -----------------------------------------------------
#
# These guards still reject impossible return, exposure, and win-rate values.
# Sharpe no longer has a finite plausibility band: extreme ratios can arise
# from small denominators and sparse exposure, and those weaknesses belong in
# the visible sample-coverage, power, stability, and MDA evidence rather than a
# hard refusal that prevents the run from being stored and inspected.
PLAUSIBLE_CAGR_PCT = (-100.0, 200.0)

# PLAUSIBLE_CAGR_PCT bounds a single ANNUALIZED rate. It does not, by itself,
# bound what a MULTI-YEAR run can cumulatively report: compounding its own
# 200%/yr ceiling over 5 years allows +24,200% cumulative, which is not a
# bound in any practical sense. Measured directly: a Dual Momentum run
# against the S&P 500 "current" universe (unscaled top_n=5 -- see
# FROZEN_DUAL_MOMENTUM.md's scaling-rule section on why that is a different,
# far more concentrated rule than the frozen one) reported 92.27%/yr CAGR
# and a mutually-consistent +2477.8% cumulative return over ~5 years. Both
# numbers individually pass the flat single-period guard because a
# smoothly-compounding curve has no single implausible data point to catch
# -- only the CUMULATIVE effect of sustaining a very high rate for years is
# implausible. 50%/yr is deliberately generous relative to real, audited,
# multi-year fund track records (few in financial history have sustained
# more, before fees) -- used only for the COMPOUNDING check on windows long
# enough for sustained-rate implausibility to matter; a single wild year up
# to PLAUSIBLE_CAGR_PCT's own ceiling is left alone, since compounding
# doesn't apply within one year and a genuine outlier year is not the same
# claim as sustaining one for half a decade.
PLAUSIBLE_SUSTAINED_ANNUAL_PCT = 50.0


def plausible_return_bounds(years: float | None) -> tuple[float, float]:
    """Cumulative-return band for a run spanning `years`.

    <= 1 year: PLAUSIBLE_CAGR_PCT applied directly (uncompounded -- a single
    period's return and its annualized rate are the same order of magnitude).
    > 1 year: PLAUSIBLE_SUSTAINED_ANNUAL_PCT compounded over the actual span,
    since sustaining a rate for multiple years running is a categorically
    different, far less plausible claim than posting it once.
    """
    if years is None or years <= 0:
        return PLAUSIBLE_CAGR_PCT
    if years <= 1.0:
        return PLAUSIBLE_CAGR_PCT
    lo, hi = -100.0, PLAUSIBLE_SUSTAINED_ANNUAL_PCT
    return (
        ((1 + lo / 100) ** years - 1) * 100,
        ((1 + hi / 100) ** years - 1) * 100,
    )


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
    return_pct: float | None = None,
    years: float | None = None,
) -> list[str]:
    """Bounds violations, empty when everything is within the plausible band.

    A floor under the whole suite rather than a replacement for any test in it:
    it cannot tell a correct number from a slightly wrong one, only a correct
    number from an impossible one. That is precisely the class that repeatedly
    survived a green suite, because a fixture that shares the implementation's
    premise validates arithmetic rather than correctness.

    `years` unlocks two ADDITIONAL checks, both additive to the flat
    `cagr_pct` check above, never a replacement for it:

    1. `cagr_pct` itself, re-checked against the tighter
       PLAUSIBLE_SUSTAINED_ANNUAL_PCT ceiling once `years` > 1 -- this is
       the symmetric check both writers can run, since both log_run
       (BacktestMetrics.start/end) and log_portfolio_run (its own start/end
       params) always have a window to derive `years` from, even though
       only log_portfolio_run also has a raw `return_pct` to check directly.
    2. `return_pct`, when the caller has one, against the CUMULATIVE band
       plausible_return_bounds(years) compounds from the same ceiling --
       belt-and-suspenders for the case where return_pct and cagr_pct were
       computed inconsistently (a different bug than the one either check
       alone catches).

    Pass whichever of `return_pct`/`years` the caller has; each check is
    skipped, not guessed, when its inputs are missing.
    """
    problems: list[str] = []
    # Sharpe is diagnostic evidence, not an authorization bound. Extreme
    # finite values remain visible so the validation suite can explain them
    # through exposure, sample coverage, stability, and MDA instead of
    # refusing to store the run. Only non-finite arithmetic is invalid data.
    if sharpe is not None and not math.isfinite(float(sharpe)):
        problems.append(f"Sharpe {sharpe} is not finite")
    if cagr_pct is not None and not (PLAUSIBLE_CAGR_PCT[0] <= cagr_pct <= PLAUSIBLE_CAGR_PCT[1]):
        problems.append(f"CAGR {cagr_pct:.2f}% outside plausible {PLAUSIBLE_CAGR_PCT}")
    elif (
        cagr_pct is not None and years is not None and years > 1.0
        and not (-100.0 <= cagr_pct <= PLAUSIBLE_SUSTAINED_ANNUAL_PCT)
    ):
        problems.append(
            f"CAGR {cagr_pct:.2f}% sustained over {years:.2f}y outside the "
            f"multi-year plausible ceiling of {PLAUSIBLE_SUSTAINED_ANNUAL_PCT}%/yr "
            "-- passes the single-period band but sustaining this rate for this "
            "long is a bug report, not a result"
        )
    if win_rate is not None and not (0.0 <= win_rate <= 1.0):
        problems.append(f"win rate {win_rate} outside [0, 1]")
    if exposure_pct is not None and not (0.0 <= exposure_pct <= 100.0):
        problems.append(f"exposure {exposure_pct}% outside [0, 100]")
    if return_pct is not None and years is not None:
        lo, hi = plausible_return_bounds(years)
        if not (lo <= return_pct <= hi):
            problems.append(
                f"cumulative return {return_pct:.1f}% over {years:.2f}y outside "
                f"plausible ({lo:.0f}%, {hi:.0f}%) -- a sustained rate this "
                "high for this long is a bug report, not a result"
            )
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
    total_return_pct: float | None = None
    average_gross_exposure_pct: float | None = None
    average_net_exposure_pct: float | None = None
    time_in_market_pct: float | None = None
    turnover_pct: float | None = None
    modeled_costs: float | None = None
    matched_spy_return_pct: float | None = None
    matched_spy_excess_pct: float | None = None
    annualized_matched_excess_pct: float | None = None
    matched_alpha_annual_pct: float | None = None
    matched_beta: float | None = None
    matched_benchmark_trades: int = 0
    missing_benchmark_trades: int = 0


def derive_status(
    trades_taken: int,
    expectancy_r: float,
    sharpe: float | None = None,
    alpha_pct: float | None = None,
    invested_days_count: float | None = None,
    mda_pct: float | None = None,
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
    # The effect is smaller than this design could resolve, so no tier is
    # awarded in EITHER direction. Checked before the Sharpe/alpha gate because
    # passing that gate on an unresolvable effect is precisely the failure --
    # the measured value is inside the noise band, so its sign is not evidence.
    if (
        mda_pct is not None
        and alpha_pct is not None
        and abs(alpha_pct) < mda_pct
    ):
        return STATUS_UNDERPOWERED
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
    mda_pct: float | None = None,
    alpha_annual_pct: float | None = None,
    warmup_ok: bool = True,
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

    `warmup_ok=False` (some constituents lacked the required lookback --
    engine/cross_sectional.py's result.incomplete_warmup) short-circuits
    everything below to STATUS_INCOMPLETE_WARMUP, checked BEFORE return sign
    or any other gate: a run built on incomplete required history is not a
    measurement of the strategy on the labeled universe, so it must never
    carry a graded verdict either way. Measured directly: two runs read
    "Positive return - shortlist" with a failed warmup_validity check sitting
    in the SAME validation report, because this function had no way to know.
    """
    if not warmup_ok:
        return STATUS_INCOMPLETE_WARMUP
    if return_pct <= 0:
        return STATUS_PORTFOLIO_NEGATIVE
    if sharpe is None and benchmark_return_pct is None:
        return STATUS_UNVERIFIED
    # Same underpowered gate as derive_status(), but the comparison MUST be in
    # matching units. `return_pct` and `benchmark_return_pct` are CUMULATIVE
    # over the window while MDA is ANNUAL: on Dual Momentum that is 58.6pp
    # cumulative against a 17.7%/yr MDA, which passes the gate for a purely
    # dimensional reason. Annualized, the same run is +6.4%/yr against 17.7%/yr
    # and is correctly underpowered.
    #
    # So the caller must supply `alpha_annual_pct`; a cumulative difference is
    # never substituted for it.
    if mda_pct is not None and alpha_annual_pct is not None:
        if abs(alpha_annual_pct) < mda_pct:
            return STATUS_UNDERPOWERED
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
        # A no-trade strategy is cash, not a sample with a measurable Sharpe.
        # Risk-free accrual can differ from its analytical rate by machine
        # epsilon; dividing that epsilon by near-zero volatility produced
        # ratios in the billions. Withhold both ratios: they are undefined.
        sharpe = None
        sortino = None
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
