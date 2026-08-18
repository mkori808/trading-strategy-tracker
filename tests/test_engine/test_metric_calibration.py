"""Calibration harness: validate the METRIC against an independent oracle,
and validate the shortlist GATE against the metric.

Why this file exists
--------------------
`engine/metrics.py:SHARPE_THRESHOLD` was 0.5 while the maximum Sharpe
achievable by any per-symbol strategy in the whole project was -0.16 --
a gate and a metric that were never validated against each other. Eleven
strategies with positive expectancy were all filtered out by a bar that
nothing could clear, and the resulting "no strategy clears the shortlist"
finding was recorded in LESSONS.md as an empirical result rather than the
measurement artifact it was.

Every test here is deliberately RED until its own bug is fixed. They are
NOT redundant with each other: each fixture is chosen to be maximally
sensitive to exactly one defect, because the obvious fixture (SPY
buy-and-hold) is ~100% invested, never rebalances, and has no lookback --
so it only exercises the calendar-day mismatch and would go green with the
other three bugs fully intact.

    reference oracle   -> annualize_sharpe() below, ~15 lines, no engine code
    calendar-vs-252    -> test_portfolio_sharpe_matches_reference
    idle-cash rf       -> test_never_entering_strategy_is_not_charged_rf
    warmup preload     -> test_cross_sectional_preloads_lookback_history
    execution costs    -> test_cross_sectional_runner_applies_costs

Deliberately differential, never a hardcoded target. `risk_free_rate()`
legitimately varies by window (2.69% over 2018-2026, which spans ZIRP, vs
3.65% over 2021-2026), so a magic number would need one constant per
window and would bake today's rf source in as an assumption. Asserting
"engine == independent reference on the same series" needs no constants
and keeps holding if the rf source is later corrected.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

from engine.backtest import accrue_idle_cash
from engine.cross_sectional import run_cross_sectional_backtest
from engine.portfolio import annualized_stats

TRADING_DAYS = 252

# ~252 days/year, matching a real exchange calendar. Plain business days give
# ~260 and silently skew any test that annualizes with 252.
_MARKET_DAY = CustomBusinessDay(calendar=USFederalHolidayCalendar())


def annualize_sharpe(equity: pd.Series, risk_free_rate: float) -> float:
    """Independent reference implementation -- the oracle the engine is
    checked against. Intentionally the textbook definition, written without
    reference to engine code so a shared bug can't hide in both:

      * returns are taken over TRADING days only (the series' own index),
        never resampled onto a calendar that invents flat weekend bars;
      * the risk-free rate is subtracted from the ANNUALIZED return, and
        annualization uses 252 to match the same 252 used for volatility.

    The bug this exists to catch is a mismatch between those last two: the
    engine resamples to calendar days (~365/yr, ~113 of them forced to 0%
    by ffill) and then annualizes with 252, which understates the numerator
    by a factor of 252/365 while shrinking the denominator by only
    sqrt(252/365). The two biases partially cancel, so the net error is a
    plausible-looking ~0.7x rather than an obviously broken number -- which
    is exactly why it survived this long unnoticed.
    """
    returns = equity.pct_change().dropna()
    if returns.empty or returns.std(ddof=1) == 0:
        raise ValueError("degenerate series")
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    ann_vol = returns.std(ddof=1) * np.sqrt(TRADING_DAYS)
    return float((cagr - risk_free_rate) / ann_vol)


def _synthetic_equity(
    ann_return: float = 0.1324,
    ann_vol: float = 0.1721,
    years: int = 5,
    start: str = "2021-08-06",
    seed: int = 7,
) -> pd.Series:
    """A daily equity curve on real TRADING days (weekends absent, like every
    curve the engines actually produce), with its annualized return and
    volatility pinned to exactly the requested values.

    The random draw is standardized to the target moments rather than merely
    seeded: an unstandardized sample of 1260 normals has a mean standard
    error of ~0.0003/day, i.e. +/-8% of annualized drift, which is enough to
    land a nominally +0.5-Sharpe fixture on a NEGATIVE realized Sharpe and
    make the assertion's failure message meaningless. Pinning the moments
    makes the expected value knowable by hand, so a failure reports the size
    of the engine's error rather than the size of a random draw.

    The index honours market HOLIDAYS, not just weekends. `bdate_range` alone
    yields ~260 days/year, so annualizing it with 252 disagrees with a
    calendar-elapsed CAGR by ~0.03 of Sharpe purely as a fixture artifact --
    enough to look like a residual engine bug after the real one is fixed. A
    holiday-aware calendar lands at ~252/year, where the two agree as they do
    on real market data.

    Defaults are SPY's real 2021-2026 shape: 13.24% CAGR at 17.21% vol.
    """
    days = TRADING_DAYS * years
    index = pd.bdate_range(start, periods=days, freq=_MARKET_DAY)
    rng = np.random.default_rng(seed)
    raw = rng.normal(size=days)
    daily_vol = ann_vol / np.sqrt(TRADING_DAYS)
    daily_log = np.log1p(ann_return) / TRADING_DAYS
    returns = np.expm1((raw - raw.mean()) / raw.std(ddof=1) * daily_vol + daily_log)

    # The fixture must fail on its OWN terms before it is allowed to indict the
    # engine. A red test that is red for the wrong reason is worse than no test:
    # plain bdate_range yields ~260 bars/year, and annualizing that with 252
    # produces a ~0.03 Sharpe discrepancy that looks exactly like a residual
    # engine bug -- it briefly did, after the real calendar-resample bug was
    # fixed. The differential oracle could not catch it because the oracle and
    # the fixture shared the same calendar assumption.
    observed = len(index) / ((index[-1] - index[0]).days / 365.25)
    assert 249 <= observed <= 255, (
        f"fixture supplies {observed:.1f} bars/year, not ~{TRADING_DAYS}; "
        "any engine comparison against it is measuring the calendar, not the engine"
    )
    return pd.Series(10_000 * np.cumprod(1 + returns), index=index)


# --------------------------------------------------------------------------
# 1. calendar-vs-trading-day annualization (engine/portfolio.py)
# --------------------------------------------------------------------------

def test_portfolio_sharpe_matches_reference():
    """RED until annualized_stats() stops resampling onto calendar days.

    Measured on the real canonical Dual Momentum run, the engine reports
    0.573 where the reference gives 0.805 (ratio 0.712); on SPY
    buy-and-hold 2021-2026 it reports 0.371 where the truth is 0.558.
    """
    equity = _synthetic_equity()
    rf = 0.0364  # the real 2021-2026 value; any rate exposes the same gap
    _cagr, engine_sharpe, _sortino = annualized_stats(equity, rf, cash_accrued=True)
    assert engine_sharpe == pytest.approx(annualize_sharpe(equity, rf), abs=0.02)


def test_gate_admits_passive_spy():
    """The gate/metric calibration itself, stated as the property that was
    actually violated: a plain buy-and-hold of the benchmark must be able to
    clear the shortlist bar. It could not -- SPY's true Sharpe over the
    canonical window is 0.558 against a threshold of 0.5, but the engine
    scored it 0.371 and rejected it. If passive SPY can't pass, no gate
    verdict on any active strategy carries information.
    """
    from engine.metrics import SHARPE_THRESHOLD

    equity = _synthetic_equity()  # SPY's real 2021-2026 shape: 13.24% @ 17.21%
    rf = 0.0364

    reference = annualize_sharpe(equity, rf)
    assert reference > SHARPE_THRESHOLD, (
        f"fixture is not a passing benchmark (reference Sharpe {reference:.3f}) -- "
        "fix the fixture, not the engine"
    )
    _cagr, engine_sharpe, _sortino = annualized_stats(equity, rf, cash_accrued=True)
    assert engine_sharpe > SHARPE_THRESHOLD, (
        f"passive SPY scores {engine_sharpe:.3f} against a {SHARPE_THRESHOLD} bar "
        f"(true value {reference:.3f}) -- the benchmark itself cannot clear the "
        "shortlist gate, so no gate verdict on an active strategy is informative"
    )


# --------------------------------------------------------------------------
# 2. idle cash is charged rf it never had the chance to earn
# --------------------------------------------------------------------------

def test_never_entering_strategy_is_not_charged_rf():
    """RED until uninvested cash earns the risk-free rate.

    The cleanest possible discriminator for this bug, and the one that
    reproduces the -8.09 (Earnings Momentum, 1.6% exposure) and -12.13
    (Anchored VWAP, 0.13% exposure) rows directly: a strategy that never
    enters holds cash for the entire window. Under correct treatment its
    excess return over cash is zero, so Sharpe is 0 or undefined -- never a
    large negative. Today the engine credits that cash with 0% while still
    subtracting rf in the numerator, producing -rf/~0.

    A strategy is not "worse than T-bills" for declining to trade; it IS
    T-bills. The current arithmetic charges it for a drag a real account
    holding the same cash would never experience.

    Exercised through the ENGINE rather than annualized_stats() directly:
    accrual belongs where the cash balance is actually known, so the metric
    function legitimately just computes on whatever curve it is handed.
    Asserting against the metric in isolation would test the wrong layer --
    and an earlier draft of this test did exactly that, passing a perfectly
    flat curve which has zero variance and trips annualized_stats()'s
    degenerate-std guard. It returned None, the assertion passed, and the bug
    was never exercised at all.
    """
    rf = 0.0364
    index = pd.bdate_range("2021-01-01", "2023-12-29", freq=_MARKET_DAY)
    frame = pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6},
        index=index,
    )

    class _NeverEnters:
        def required_history_days(self) -> int:
            return 0

        def rebalance(self, universe_bars, as_of):
            return {}  # permanently in cash

    import engine.cross_sectional as cs

    original = cs.data_module.get_bars
    cs.data_module.get_bars = lambda _s, _i, start, stop: frame.loc[str(start) : str(stop)]
    try:
        result = run_cross_sectional_backtest(
            "all cash", _NeverEnters(), ["AAA"],
            index[0].date(), index[-1].date(), risk_free_rate=rf,
        )
    finally:
        cs.data_module.get_bars = original

    assert result.sharpe is None or abs(result.sharpe) < 0.5, (
        f"a permanently-in-cash strategy scores Sharpe {result.sharpe}, which is "
        "~-rf over a near-zero volatility. Idle cash must EARN the risk-free "
        "rate it is being charged: a strategy that declines to trade is not "
        "worse than T-bills, it IS T-bills. This is the artifact behind "
        "Earnings Momentum (-8.09) and Anchored VWAP (-12.13)"
    )
    # And the accrual must have the right SIGN -- interest earned, not charged.
    assert result.final_equity > 10_000.0, (
        f"final equity {result.final_equity:.2f} did not grow at the risk-free "
        "rate while fully in cash -- accrual is missing or negatively signed"
    )


def test_reference_holds_on_the_fully_corrected_engine():
    """Re-assert the oracle against the engine with ALL fixes in.

    This exists because test_portfolio_sharpe_matches_reference went green
    after fix 1 (calendar-day annualization) and stayed green through three
    more fixes without proving anything about any of them -- SPY buy-and-hold
    is ~100% invested, never rebalances and has no lookback, so it is blind to
    the cost, warmup and idle-cash paths by construction. A test that cannot
    fail is not evidence.

    The expected value is DERIVED from the oracle rather than carried forward
    as a constant. The 0.558 figure measured earlier in this work was computed
    under a different convention (before the calendar fix, and before alpha
    moved to an excess-over-cash basis), so hardcoding it would pin the engine
    to a superseded definition -- the same failure the differential design
    exists to avoid.
    """
    rf = 0.0364
    equity = _synthetic_equity()

    reference = annualize_sharpe(equity, rf)
    _cagr, engine_sharpe, engine_sortino = annualized_stats(equity, rf, cash_accrued=True)

    assert engine_sharpe == pytest.approx(reference, abs=0.02)
    # Sortino shares the numerator, so a numerator-only regression would show
    # up in both; asserting it is ordered against Sharpe catches a denominator
    # swap that leaves Sharpe itself looking correct.
    assert engine_sortino > engine_sharpe, (
        "Sortino must exceed Sharpe for a series with any upside asymmetry -- "
        "downside deviation cannot exceed total volatility"
    )


def test_accrued_interest_matches_idle_cash_exactly():
    """The accrual must credit rf on the IDLE fraction only -- no more, no less.

    This is the specific error class that nearly shipped. Crediting cash is
    correct, but if it is applied on the wrong days, at the wrong rate, or to
    the whole account rather than the uninvested part, the result is a
    uniformly FLATTERING distortion: every return rises, every Sharpe rises,
    and nothing looks broken. It was caught by hand only because alpha moved
    +18.8 to +19.5pp against +19.6pp of cumulative interest -- a suspiciously
    exact match. That coincidence is now an assertion.

    Total interest earned must equal cumulative rf compounded over the idle
    fraction of the window, so a half-invested account earns half the interest
    of an idle one.
    """
    rf = 0.0364
    days = TRADING_DAYS * 2
    index = pd.bdate_range("2021-01-04", periods=days, freq=_MARKET_DAY)
    flat = pd.Series(10_000.0, index=index)  # no price movement: interest only
    closes = pd.Series(100.0, index=index)

    # Fully idle: every day's interest accrues. Expected value is stated in
    # ELAPSED YEARS, not in periods/252 -- the rate is derived from the
    # series' own sampling frequency, so pinning it to 252 would re-assert
    # the very assumption that over-credited intraday runs ~79x.
    idle = accrue_idle_cash(flat, pd.DataFrame(), closes, rf)
    elapsed_years = (index[-1] - index[0]).days / 365.25
    accrued_fraction = (days - 1) / days  # first bar earns nothing (shifted)
    expected = 10_000.0 * (1.0 + rf) ** (elapsed_years * accrued_fraction)
    assert idle.iloc[-1] == pytest.approx(expected, rel=1e-4), (
        "a fully idle account did not compound at exactly the risk-free rate"
    )

    # Half invested for the whole window: exactly half the interest.
    half = pd.DataFrame(
        {"EntryBar": [0], "ExitBar": [days - 1], "Size": [50.0]}  # 50 * $100 = $5,000
    )
    partial = accrue_idle_cash(flat, half, closes, rf)
    half_expected = 10_000.0 * (1.0 + rf) ** (0.5 * elapsed_years * accrued_fraction)
    assert partial.iloc[-1] == pytest.approx(half_expected, rel=1e-3), (
        f"a 50%-invested account earned {partial.iloc[-1] - 10_000:.2f} of interest, "
        f"not the {half_expected - 10_000:.2f} its idle half is owed -- accrual is "
        "being applied to the wrong base"
    )
    assert partial.iloc[-1] < idle.iloc[-1], "half-invested must earn less than fully idle"


def test_accrual_is_invariant_to_bar_frequency():
    """The same window must accrue the same interest on 5-minute and daily bars.

    Interest is a property of ELAPSED TIME, not of how finely the window was
    sampled. The first implementation applied a daily rate once per bar, so a
    day-trading strategy on 5-minute bars (~19,841 bars/year, not 252) was
    credited ~79x too much: measured on ORB, 3,042 bars at ~40% idle compounded
    to a spurious 1.21x in under two months and produced Sharpe 7.24 against a
    true value near zero.

    Caught only because 7.24 is implausible on sight. This asserts the
    invariant instead, so a future interval (1-minute bars, hourly) cannot
    reintroduce it silently -- the failure was in the RATE, and a wrong rate
    produces a plausible-looking number at any frequency close enough to daily.
    """
    rf = 0.0364
    daily_index = pd.bdate_range("2022-01-03", periods=TRADING_DAYS, freq=_MARKET_DAY)
    # Same span, 78 five-minute bars per session.
    intraday_index = pd.date_range(
        daily_index[0], daily_index[-1], periods=TRADING_DAYS * 78
    )

    daily = accrue_idle_cash(
        pd.Series(10_000.0, index=daily_index), pd.DataFrame(),
        pd.Series(100.0, index=daily_index), rf,
    )
    intraday = accrue_idle_cash(
        pd.Series(10_000.0, index=intraday_index), pd.DataFrame(),
        pd.Series(100.0, index=intraday_index), rf,
    )

    assert intraday.iloc[-1] == pytest.approx(daily.iloc[-1], rel=1e-3), (
        f"same window accrued {daily.iloc[-1] - 10_000:.2f} on daily bars but "
        f"{intraday.iloc[-1] - 10_000:.2f} on 5-minute bars -- the rate is being "
        "applied per BAR rather than per unit of elapsed time"
    )


# --------------------------------------------------------------------------
# 3. cross-sectional warmup preload
# --------------------------------------------------------------------------

def test_cross_sectional_preloads_lookback_history():
    """RED until run_cross_sectional_backtest fetches history from BEFORE
    the requested start.

    A property assertion, not a numeric one, so it won't churn when returns
    change. engine/cross_sectional.py fetches get_bars(start, end) and
    strategies/swing/dual_momentum.py skips any symbol with fewer than
    lookback+1 bars, so the portfolio is 100% cash for the first lookback+1
    TRADING days of every window -- 274 days (~13 months) at lookback=273.

    This also silently confounds every lookback comparison: sweeping
    105/147/273 changes the dead period to ~5/~7/~13 months, so those runs
    cover materially different investment periods and the return difference
    can't be attributed to the lookback.

    engine/regime.py:REGIME_WARMUP_DAYS already solves exactly this for the
    filter layer, with a comment noting it exists so comparisons "measure
    warmup rather than the filter." The lesson simply never reached here.
    """
    lookback = 60
    requested_start = pd.Timestamp("2022-01-03").date()
    end = pd.Timestamp("2022-12-30").date()
    fetched: dict[str, tuple] = {}

    class _Ranker:
        """Records the first date on which it could actually rank anything."""

        first_ranked: pd.Timestamp | None = None

        def required_history_days(self) -> int:
            return lookback + 1

        def rebalance(self, universe_bars, as_of):
            for _symbol, bars in universe_bars.items():
                if len(bars.loc[:as_of]) < lookback + 1:
                    return {}
            if self.first_ranked is None:
                self.first_ranked = as_of
            return {s: 1.0 / len(universe_bars) for s in universe_bars}

    index = pd.bdate_range("2020-01-01", "2023-01-31")
    frame = pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6},
        index=index,
    )

    def _fake_get_bars(symbol, _interval, start, stop):
        fetched[symbol] = (start, stop)
        return frame.loc[str(start) : str(stop)]

    import engine.cross_sectional as cs

    original = cs.data_module.get_bars
    cs.data_module.get_bars = _fake_get_bars
    try:
        ranker = _Ranker()
        run_cross_sectional_backtest(
            "warmup probe", ranker, ["AAA", "BBB"], requested_start, end,
            rebalance_frequency="monthly",
        )
    finally:
        cs.data_module.get_bars = original

    fetch_start = min(s for s, _e in fetched.values())
    assert fetch_start < requested_start, (
        f"fetched from {fetch_start}, which is the traded window's own start -- "
        "no warmup history was preloaded, so the strategy cannot rank until "
        f"{lookback + 1} trading days INTO the window it is supposed to trade"
    )
    assert ranker.first_ranked is not None
    assert ranker.first_ranked.date() <= requested_start + pd.Timedelta(days=31)


# --------------------------------------------------------------------------
# 4. cross-sectional execution costs
# --------------------------------------------------------------------------

def test_cross_sectional_runner_applies_costs():
    """RED until engine/runner.py:run_cross_sectional passes a non-zero cost.

    The engine SUPPORTS slippage_bps/commission_bps and applies them
    correctly -- testing the engine directly would pass and prove nothing.
    The defect is one level up: the runner calls it with neither argument,
    so both default to 0.0 and Dual Momentum's headline number is
    cost-free, while every per-symbol strategy pays a per-symbol
    estimate_spread(). That makes the leaderboard's only shortlisted row
    the one row not paying costs -- and it is a daily-rebalance
    configuration, where zero costs flatter most.

    So this asserts on the WIRING: whatever the runner hands the engine
    must be non-zero.
    """
    import engine.runner as runner

    captured: dict = {}

    def _spy(*args, **kwargs):
        captured.update(kwargs)
        raise _Stop

    class _Stop(Exception):
        pass

    original_backtest = runner.run_cross_sectional_backtest
    original_rf = runner.data_module.risk_free_rate
    runner.run_cross_sectional_backtest = _spy
    runner.data_module.risk_free_rate = lambda _s, _e: 0.0364
    try:
        with pytest.raises(_Stop):
            runner.run_cross_sectional("Dual Momentum")
    finally:
        runner.run_cross_sectional_backtest = original_backtest
        runner.data_module.risk_free_rate = original_rf

    # Asserts the EFFECTIVE cost, not one particular argument's presence: the
    # runner may charge a flat slippage_bps or a per-symbol spread map (which
    # is what matches engine/backtest.py). Either satisfies the requirement;
    # what must never happen is that the engine is handed nothing and silently
    # trades free.
    per_symbol = captured.get("spread_by_symbol") or {}
    effective_bps = (
        captured.get("slippage_bps", 0.0)
        + captured.get("commission_bps", 0.0)
        + 10_000.0 * (sum(per_symbol.values()) / len(per_symbol) if per_symbol else 0.0)
    )
    assert effective_bps > 0.0, (
        "run_cross_sectional passes no slippage/commission, so this strategy "
        "backtests at zero transaction cost while every per-symbol strategy "
        "pays estimate_spread() -- the leaderboard is not comparing like with like"
    )


def test_annualized_stats_refuses_an_unaccrued_curve():
    """The idle-cash contract is enforced by the signature, not by discipline.

    `cash_accrued` is required and keyword-only, so a NEW engine that computes
    Sharpe cannot silently inherit the artifact: omitting it is a TypeError at
    the call site, and passing False with a real rf raises.

    This exists because the original accrual fix landed at 2 of 7 call sites.
    The five missed engines (overnight, pairs, portfolio, dividend_hybrid,
    insider_buy) all received the calendar-day fix for free, because that lives
    INSIDE annualized_stats, and silently kept the idle-cash artifact, which
    lives in the callers. Fixing a shared function does not fix its callers, and
    nothing in the test suite noticed for two turns.
    """
    equity = _synthetic_equity()

    with pytest.raises(TypeError):
        annualized_stats(equity, 0.0364)  # omitted entirely

    with pytest.raises(ValueError, match="credits idle cash"):
        annualized_stats(equity, 0.0364, cash_accrued=False)

    # rf == 0 has no drag to mis-charge, so an un-accrued curve is fine there.
    assert annualized_stats(equity, 0.0, cash_accrued=False)[1] is not None


def test_sparse_event_curve_does_not_compress_multiweek_gaps_into_daily_returns():
    dates = pd.DatetimeIndex(["2021-01-04", "2021-01-05", "2021-02-01", "2021-02-02"])
    sparse = pd.Series([10_000.0, 10_100.0, 10_100.0, 10_201.0], index=dates)
    _cagr, sharpe, _sortino = annualized_stats(sparse, 0.0, cash_accrued=True)
    expanded = sparse.reindex(pd.bdate_range(dates[0], dates[-1]), method="ffill")
    _cagr, expected_sharpe, _sortino = annualized_stats(expanded, 0.0, cash_accrued=True)
    # Two 1% event returns separated by cash should not be annualized as if
    # three consecutive trading days produced the whole move.
    assert sharpe is not None
    assert sharpe == pytest.approx(expected_sharpe)


def test_every_engine_declares_its_cash_treatment():
    """No call site may omit the declaration -- checked across the codebase.

    A per-instance test would have passed after fixing two engines. This asserts
    the PROPERTY over every caller, so adding an eighth engine that forgets is a
    failure here rather than a wrong number on the leaderboard.
    """
    import re
    from pathlib import Path

    engine_dir = Path(__file__).resolve().parents[2] / "engine"
    offenders = []
    for path in engine_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path.name == "portfolio.py":
            source = source.split("def annualized_stats", 1)[-1].split("\ndef ", 1)[-1]
        for match in re.finditer(r"annualized_stats\((.{0,200}?)\)", source, re.S):
            if "cash_accrued" not in match.group(1):
                offenders.append(f"{path.name}: {match.group(0)[:70]}")

    assert not offenders, (
        "call sites not declaring cash treatment:\n  " + "\n  ".join(offenders)
    )


def test_absent_metrics_never_satisfy_the_gate():
    """A missing value is not a passing value.

    `derive_status` skipped the risk-adjusted gate entirely when BOTH Sharpe and
    alpha were absent, falling through to the shortlist tier. Overnight Hold --
    28,370 trades, +0.013 expectancy, Sharpe None, alpha None -- was therefore
    the only per-symbol row on the board holding a shortlist verdict, and the
    Lab tab's promote button acts on canonical shortlisted rows. A blank became
    a recommendation to trade.

    A gate that cannot be evaluated yields no tier, and the result must be
    unrankable so it cannot sort among measured rows either.
    """
    from engine.metrics import (
        STATUS_POSITIVE,
        STATUS_UNDERPERFORMS,
        STATUS_UNVERIFIED,
        UNRANKABLE_STATUSES,
        derive_status,
    )

    assert derive_status(28_370, 0.013, None, None) == STATUS_UNVERIFIED
    assert STATUS_UNVERIFIED in UNRANKABLE_STATUSES

    # One half absent is still judged on the half that exists.
    assert derive_status(200, 0.1, 1.2, None) == STATUS_POSITIVE
    assert derive_status(200, 0.1, 0.2, None) == STATUS_UNDERPERFORMS
    assert derive_status(200, 0.1, None, -5.0) == STATUS_UNDERPERFORMS


def test_pooled_curve_replaces_mean_of_ratios():
    """Risk-adjusted stats come from ONE portfolio curve, not averaged ratios.

    A mean of per-symbol Sharpes can disagree in sign with a mean of per-symbol
    CAGRs, because a mean of ratios is not a ratio of means -- measured on
    Turnaround Tuesday (+2.26 Sharpe against -0.11% excess CAGR) and Gap Fade
    (+0.69 against -0.20%). It is also silently sensitive to how many symbols
    traded, the same denominator defect as the exposure/Sharpe mismatch.

    Built here from two sleeves with deliberately different lengths, so the
    outer-join/ffill path is exercised: a shorter sleeve must hold its last
    value rather than truncate the portfolio's window.
    """
    from engine.backtest import DEFAULT_CASH, SymbolBacktestResult, portfolio_equity_curve

    long_idx = pd.bdate_range("2022-01-03", periods=200, freq=_MARKET_DAY)
    short_idx = long_idx[:120]
    a = pd.DataFrame({"Equity": np.linspace(10_000, 12_000, len(long_idx))}, index=long_idx)
    b = pd.DataFrame({"Equity": np.linspace(10_000, 9_000, len(short_idx))}, index=short_idx)

    pooled = portfolio_equity_curve({
        "A": SymbolBacktestResult("A", None, pd.DataFrame(), a),
        "B": SymbolBacktestResult("B", None, pd.DataFrame(), b),
    })

    assert len(pooled) == len(long_idx), "shorter sleeve truncated the portfolio window"
    assert pooled.iloc[0] == pytest.approx(2 * DEFAULT_CASH)
    # After B ends it holds its final value; the sum must reflect both sleeves.
    assert pooled.iloc[-1] == pytest.approx(a["Equity"].iloc[-1] + b["Equity"].iloc[-1])
    assert pooled.notna().all()


def test_coverage_guard_uses_the_measured_window():
    """Unmeasurable-by-construction windows are refused, not scored poorly.

    The guard reads the MEASURED window: an intraday run labelled two years
    covers ~50 trading days, so applying it to the requested window would pass
    every time while the data underneath could not support the floor.
    """
    from engine.metrics import MIN_INVESTED_DAYS, coverage_is_measurable

    assert coverage_is_measurable(date(2026, 6, 15), date(2026, 8, 4))    # ~50 days
    assert not coverage_is_measurable(date(2026, 7, 15), date(2026, 8, 4))  # ~20 days
    assert coverage_is_measurable(None, None), "unknown coverage is not bad coverage"
    assert MIN_INVESTED_DAYS == 30
