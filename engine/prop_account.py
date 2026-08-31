"""Prop-firm / funded-account suitability analysis.

A SECOND evaluation framework alongside the existing edge analysis, not a
replacement for it. Nothing here touches strategy signals, validation gates,
PIT safeguards, execution-timing contracts, MDA, or holdout logic -- this
module only consumes return series the existing engine already produced.

The question it answers is different from the one `engine/validation.py`
answers. That one asks "is there a real edge here". This one asks: given a
firm's loss limits, payout split and rules, can the strategy be SIZED so it
earns attractive payouts while rarely breaching those limits?

Those come apart. A strategy can trail SPY badly and still be an excellent
prop strategy (low tail risk, scaled against someone else's capital), and a
strategy can beat SPY handsomely and be a terrible one (normal drawdowns
exceeding the firm's entire loss budget). So **benchmark gap is recorded but
never scored here** -- see `prop_verdict`, which does not read it.

Two framings this module insists on:

- **Nominal size is not capital.** A $100k account with a 5% max loss gives
  the trader a $5,000 RISK BUDGET, not $100k of capital. The binding
  resource is the loss budget; `PropAccountConfig.risk_budget` names it and
  the UI leads with it.
- **Strategy return is not trader return.** The account's gross P&L is not
  what the trader receives: a payout split, evaluation fees, and the chance
  of losing the account all sit in between. `PropSimulationResult` reports
  both and never conflates them.

Look-ahead: none is introduced. Sizing is applied as a CONSTANT multiplier
over a whole path, chosen out-of-band; it is never varied using information
from later in the path. Sizing exploration is capital allocation, not
strategy-parameter optimization -- the strategy's own parameters are frozen
inputs here and this module cannot change them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Sequence

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252

DrawdownType = Literal["static", "trailing", "trailing_to_breakeven"]
AccountMode = Literal["evaluation", "funded"]


@dataclass(frozen=True)
class PropAccountConfig:
    """One firm's rules, treated as an EXTERNAL CONSTRAINT.

    Deliberately not tuned per strategy: picking the rule set that flatters
    a given strategy would be the prop-config equivalent of overfitting, and
    would answer a question nobody asked. Use the named scenarios below, or
    a real firm's published rules.

    Percentages are fractions of `account_size` (0.05 == 5%).
    """

    account_size: float = 100_000.0
    max_total_loss_pct: float = 0.05
    daily_loss_limit_pct: float = 0.02
    payout_split: float = 0.80
    evaluation_fee: float = 500.0
    profit_target_pct: float = 0.08
    mode: AccountMode = "evaluation"
    drawdown_type: DrawdownType = "static"
    min_trading_days: int = 0
    #: Cap on gross notional as a multiple of account size. None == no cap.
    max_notional_multiple: float | None = None
    #: Payouts below this are not paid out (they roll forward).
    min_payout: float = 0.0
    name: str = "Custom"

    def __post_init__(self) -> None:
        if self.account_size <= 0:
            raise ValueError("account_size must be positive")
        if not 0 < self.max_total_loss_pct < 1:
            raise ValueError("max_total_loss_pct must be a fraction in (0, 1)")
        if not 0 < self.daily_loss_limit_pct <= 1:
            raise ValueError("daily_loss_limit_pct must be a fraction in (0, 1]")
        if not 0 <= self.payout_split <= 1:
            raise ValueError("payout_split must be a fraction in [0, 1]")
        if self.daily_loss_limit_pct > self.max_total_loss_pct:
            # Not an error -- some firms really do set it this way -- but the
            # daily limit is then unreachable without also breaching total
            # loss, which makes the daily-limit probability meaningless.
            pass

    @property
    def risk_budget(self) -> float:
        """Dollars of loss the account permits before it is terminated.

        THE binding resource, and the number the UI leads with. A $100k
        account at 5% is a $5,000 risk budget -- not $100k of capital.
        """
        return self.account_size * self.max_total_loss_pct

    @property
    def daily_risk_budget(self) -> float:
        return self.account_size * self.daily_loss_limit_pct

    @property
    def profit_target(self) -> float:
        return self.account_size * self.profit_target_pct


#: Generic scenarios spanning the range of real firms' rules. Named so a
#: result can say WHICH constraint set produced it. Never auto-selected by
#: strategy performance -- that choice is the user's.
SCENARIOS: dict[str, PropAccountConfig] = {
    "conservative": PropAccountConfig(
        name="Conservative", max_total_loss_pct=0.04, daily_loss_limit_pct=0.02
    ),
    "moderate": PropAccountConfig(
        name="Moderate", max_total_loss_pct=0.06, daily_loss_limit_pct=0.03
    ),
    "loose": PropAccountConfig(
        name="Loose", max_total_loss_pct=0.10, daily_loss_limit_pct=0.05
    ),
}


@dataclass(frozen=True)
class PropSimulationConfig:
    """How the Monte Carlo is run. Seeded, so results are reproducible."""

    n_paths: int = 5_000
    horizon_days: int = TRADING_DAYS_PER_YEAR
    #: Block length for the stationary/moving block bootstrap. IID sampling
    #: destroys the short-horizon autocorrelation that decides whether
    #: losses ARRIVE CONSECUTIVELY -- which is exactly what a daily limit and
    #: a trailing drawdown react to. ~1 month of sessions is a defensible
    #: default: long enough to carry a losing streak, short enough that
    #: paths are not just replays of history.
    block_size: int = 21
    seed: int = 20260822
    method: Literal["daily_block", "daily_iid", "trade"] = "daily_block"

    def __post_init__(self) -> None:
        if self.n_paths < 1:
            raise ValueError("n_paths must be >= 1")
        if self.block_size < 1:
            raise ValueError("block_size must be >= 1")


@dataclass(frozen=True)
class PathOutcome:
    """What happened to ONE simulated account."""

    failed: bool
    failure_reason: str | None
    failure_day: int | None
    passed_evaluation: bool
    pass_day: int | None
    days_survived: int
    gross_profit: float
    max_drawdown: float
    worst_day: float


def _empty_outcome(days: int) -> PathOutcome:
    return PathOutcome(False, None, None, False, None, days, 0.0, 0.0, 0.0)


def simulate_path(
    daily_pnl: Sequence[float], config: PropAccountConfig
) -> PathOutcome:
    """Apply the firm's rules to one path of daily account P&L, in dollars.

    The rules are applied in the order a firm applies them, and the account
    STOPS at the first breach -- a terminated account cannot recover on a
    later day, so anything after the breach must not be counted. Getting
    that wrong inflates both survival and payout.

    Drawdown types:

    - ``static``   -- floor fixed at ``start - risk_budget`` for the life of
      the account.
    - ``trailing`` -- floor follows the equity high-water mark up forever.
    - ``trailing_to_breakeven`` -- floor follows the HWM but stops once it
      reaches the starting balance, which is what most firms actually do;
      after that the account can never be closed for less than break-even.

    The daily limit is checked on the day's P&L, the drawdown on the running
    equity, because that is how the two rules genuinely differ: a slow bleed
    can breach total loss without ever breaching a daily limit, and one bad
    session can breach the daily limit while total equity is still up.
    """
    equity = config.account_size
    peak = equity
    static_floor = config.account_size - config.risk_budget
    trough = equity
    worst_day = 0.0
    trading_days = 0

    for day, pnl in enumerate(daily_pnl, start=1):
        equity += pnl
        trading_days += 1
        worst_day = min(worst_day, pnl)
        trough = min(trough, equity)

        # Daily limit first: it is evaluated on the session's own P&L.
        if pnl <= -config.daily_risk_budget:
            return PathOutcome(
                True, "daily_loss_limit", day, False, None, day,
                equity - config.account_size, config.account_size - trough, worst_day,
            )

        if config.drawdown_type == "static":
            floor = static_floor
        else:
            peak = max(peak, equity)
            floor = peak - config.risk_budget
            if config.drawdown_type == "trailing_to_breakeven":
                floor = min(floor, config.account_size)

        if equity <= floor:
            return PathOutcome(
                True, "max_total_loss", day, False, None, day,
                equity - config.account_size, config.account_size - trough, worst_day,
            )

        if (
            config.mode == "evaluation"
            and equity - config.account_size >= config.profit_target
            and trading_days >= config.min_trading_days
        ):
            return PathOutcome(
                False, None, None, True, day, day,
                equity - config.account_size, config.account_size - trough, worst_day,
            )

    return PathOutcome(
        False, None, None, False, None, len(daily_pnl),
        equity - config.account_size, config.account_size - trough, worst_day,
    )


def _block_bootstrap(
    returns: np.ndarray, n_paths: int, horizon: int, block: int, rng: np.random.Generator
) -> np.ndarray:
    """Moving-block bootstrap: sample contiguous runs, not single days.

    Preserves short-horizon dependence (streaks, volatility clustering),
    which IID sampling destroys -- and streaks are precisely what a daily
    limit and a trailing drawdown react to. Blocks start anywhere including
    positions that wrap, so every observation is equally likely to appear.
    """
    n = len(returns)
    if n == 0:
        return np.zeros((n_paths, horizon))
    block = min(block, n)
    n_blocks = int(np.ceil(horizon / block))
    starts = rng.integers(0, n, size=(n_paths, n_blocks))
    offsets = np.arange(block)
    idx = (starts[:, :, None] + offsets[None, None, :]) % n
    return returns[idx.reshape(n_paths, -1)[:, :horizon]]


def _iid_bootstrap(
    returns: np.ndarray, n_paths: int, horizon: int, rng: np.random.Generator
) -> np.ndarray:
    if len(returns) == 0:
        return np.zeros((n_paths, horizon))
    return rng.choice(returns, size=(n_paths, horizon), replace=True)


@dataclass
class PropSimulationResult:
    """Outcome distribution for one strategy at ONE risk multiplier.

    `gross_*` figures are the ACCOUNT's P&L. `payout_*` figures are what
    reaches the TRADER after the split and fees. They are reported
    separately and never summed -- conflating them is the single easiest way
    to make a prop account look better than it is.
    """

    risk_multiplier: float
    n_paths: int
    horizon_days: int
    failure_prob: float
    daily_limit_failure_prob: float
    total_loss_failure_prob: float
    pass_prob: float
    median_days_to_failure: float | None
    median_days_to_pass: float | None
    survival_1m: float
    survival_3m: float
    survival_6m: float
    survival_12m: float
    expected_gross_profit: float
    expected_payout: float
    expected_payout_given_survival: float
    expected_net_payout: float
    median_max_drawdown: float
    p95_max_drawdown: float
    worst_day: float
    config_name: str = "Custom"

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def simulate(
    daily_returns: pd.Series | np.ndarray,
    account: PropAccountConfig,
    sim: PropSimulationConfig | None = None,
    risk_multiplier: float = 1.0,
) -> PropSimulationResult:
    """Bootstrap `sim.n_paths` accounts and apply `account`'s rules to each.

    `daily_returns` are the STRATEGY's own fractional daily returns. Sizing
    convention, stated plainly because everything downstream depends on it:
    1.0x means the strategy's native return series is applied to the
    account's NOMINAL size, so a -1% strategy day is -$1,000 on a $100k
    account. The multiplier scales that linearly. This is a capital-
    allocation choice, applied as a constant over each whole path -- it
    never reacts to what the path did, which would be look-ahead.
    """
    sim = sim or PropSimulationConfig()
    values = np.asarray(
        daily_returns.dropna() if isinstance(daily_returns, pd.Series) else daily_returns,
        dtype=float,
    )
    values = values[np.isfinite(values)]
    rng = np.random.default_rng(sim.seed)

    if sim.method == "daily_iid":
        paths = _iid_bootstrap(values, sim.n_paths, sim.horizon_days, rng)
    else:
        paths = _block_bootstrap(
            values, sim.n_paths, sim.horizon_days, sim.block_size, rng
        )

    pnl_paths = paths * account.account_size * risk_multiplier
    outcomes = [simulate_path(path, account) for path in pnl_paths]

    failed = np.array([o.failed for o in outcomes])
    reasons = [o.failure_reason for o in outcomes]
    passed = np.array([o.passed_evaluation for o in outcomes])
    survived_to = np.array([o.days_survived for o in outcomes])
    gross = np.array([o.gross_profit for o in outcomes])
    drawdowns = np.array([o.max_drawdown for o in outcomes])
    worst = np.array([o.worst_day for o in outcomes])

    fail_days = [o.failure_day for o in outcomes if o.failure_day is not None]
    pass_days = [o.pass_day for o in outcomes if o.pass_day is not None]

    # A failed account keeps no profit: the firm closes it at the breach.
    keepable = np.where(failed, 0.0, np.maximum(gross, 0.0))
    payout = keepable * account.payout_split
    payout = np.where(payout >= account.min_payout, payout, 0.0)

    survivors = ~failed
    n = max(len(outcomes), 1)

    def _survival(days: int) -> float:
        # Survived `days` means: not failed, or failed only after them.
        return float(np.mean(~failed | (survived_to > days)))

    return PropSimulationResult(
        risk_multiplier=risk_multiplier,
        n_paths=len(outcomes),
        horizon_days=sim.horizon_days,
        failure_prob=float(failed.mean()),
        daily_limit_failure_prob=float(
            np.mean([r == "daily_loss_limit" for r in reasons])
        ),
        total_loss_failure_prob=float(np.mean([r == "max_total_loss" for r in reasons])),
        pass_prob=float(passed.mean()),
        median_days_to_failure=float(np.median(fail_days)) if fail_days else None,
        median_days_to_pass=float(np.median(pass_days)) if pass_days else None,
        survival_1m=_survival(21),
        survival_3m=_survival(63),
        survival_6m=_survival(126),
        survival_12m=_survival(TRADING_DAYS_PER_YEAR),
        expected_gross_profit=float(gross.mean()),
        expected_payout=float(payout.mean()),
        expected_payout_given_survival=(
            float(payout[survivors].mean()) if survivors.any() else 0.0
        ),
        # The fee is sunk on every attempt, won or lost -- that is the
        # trader's actual personally-committed capital at risk, so the net
        # figure subtracts it unconditionally rather than only on failure.
        expected_net_payout=float(payout.mean() - account.evaluation_fee),
        median_max_drawdown=float(np.median(drawdowns)) if len(drawdowns) else 0.0,
        p95_max_drawdown=float(np.percentile(drawdowns, 95)) if len(drawdowns) else 0.0,
        worst_day=float(worst.min()) if len(worst) else 0.0,
        config_name=account.name,
    )


#: Sizing grid. Coarse at the top because the interesting region for a
#: strategy whose drawdowns exceed the loss budget is BELOW 1x, and a
#: strategy that survives 2x is not sizing-constrained at all.
DEFAULT_RISK_MULTIPLIERS: tuple[float, ...] = (
    0.10, 0.20, 0.25, 0.33, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00,
)


@dataclass
class SizingAnalysis:
    """The sizing sweep plus the named operating points it identifies."""

    rows: list[PropSimulationResult]
    max_survival: PropSimulationResult | None
    conservative: PropSimulationResult | None
    max_payout: PropSimulationResult | None
    max_eval_economics: PropSimulationResult | None
    #: True when the search hit MAX_SEARCH_MULTIPLIER without the failure
    #: probability ever becoming material -- the optimum is NOT resolved.
    boundary_reached: bool = False
    #: Named points that landed on the largest tested multiplier. Each is a
    #: boundary artifact, not a recommendation.
    unresolved_at_boundary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "maxSurvival": self.max_survival.to_dict() if self.max_survival else None,
            "conservative": self.conservative.to_dict() if self.conservative else None,
            "maxPayout": self.max_payout.to_dict() if self.max_payout else None,
            "maxEvalEconomics": (
                self.max_eval_economics.to_dict() if self.max_eval_economics else None
            ),
            "boundaryReached": self.boundary_reached,
            "unresolvedAtBoundary": self.unresolved_at_boundary,
        }


#: Hard ceiling on the search. Not a belief about what is prudent -- it is
#: the point past which a normalized strategy would be levered so far beyond
#: its historical notional that the bootstrap is extrapolating rather than
#: resampling. Reaching it is reported, never silently treated as an answer.
MAX_SEARCH_MULTIPLIER = 64.0

#: A failure probability at or above this counts as "clearly material", so
#: the search has bracketed the interesting region and can stop.
MATERIAL_FAILURE_PROB = 0.25


def _extend_multipliers(
    existing: Sequence[float], cap: float = MAX_SEARCH_MULTIPLIER
) -> list[float]:
    """Next doubling above the current grid, bounded by the hard ceiling."""
    top = max(existing)
    out: list[float] = []
    nxt = top * 2.0
    while nxt <= cap and len(out) < 2:
        out.append(round(nxt, 4))
        nxt *= 2.0
    return out


def sweep_sizing(
    daily_returns: pd.Series | np.ndarray,
    account: PropAccountConfig,
    sim: PropSimulationConfig | None = None,
    multipliers: Sequence[float] = DEFAULT_RISK_MULTIPLIERS,
    survival_threshold: float = 0.05,
    conservative_threshold: float = 0.02,
    extend: bool = True,
) -> SizingAnalysis:
    """Evaluate the SAME strategy at several sizings and name the useful points.

    Deliberately does NOT recommend the sizing with the largest raw expected
    return. Past a point, extra size buys expected profit by buying failure
    probability, and a failed account keeps nothing -- so the largest gross
    number is routinely the worst choice for the trader.

    **The grid extends itself.** A search that stops at its own boundary and
    then reports that boundary as "optimal" has reported the boundary, not
    an optimum -- which is exactly what happened to two low-exposure
    strategies before normalization: every sizing up to 2.0x showed 0%
    failure and monotonically rising payout, so 2.0x "won" for no reason
    other than being last. The sweep now keeps doubling until failure
    probability becomes material, expected payout peaks and turns down, or
    the hard ceiling is reached -- and `boundary_reached` records the last
    case so the caller cannot mistake it for a resolved optimum.
    """
    tested = list(multipliers)
    rows = [simulate(daily_returns, account, sim, m) for m in tested]

    def _resolved() -> bool:
        """Has the search bracketed the interesting region?"""
        top = max(rows, key=lambda r: r.risk_multiplier)
        if top.failure_prob >= MATERIAL_FAILURE_PROB:
            return True
        # Payout peaked and then fell away from the peak by a clear margin.
        best = max(rows, key=lambda r: r.expected_net_payout)
        if best.risk_multiplier < top.risk_multiplier and best.expected_net_payout > 0:
            return top.expected_net_payout < best.expected_net_payout * 0.9
        return False

    boundary_reached = False
    if extend:
        while not _resolved():
            more = _extend_multipliers(tested)
            if not more:
                boundary_reached = True
                break
            for m in more:
                tested.append(m)
                rows.append(simulate(daily_returns, account, sim, m))
    rows.sort(key=lambda r: r.risk_multiplier)

    def _largest_within(threshold: float) -> PropSimulationResult | None:
        eligible = [r for r in rows if r.failure_prob <= threshold]
        return max(eligible, key=lambda r: r.risk_multiplier) if eligible else None

    max_payout = max(rows, key=lambda r: r.expected_net_payout) if rows else None
    eval_rows = [r for r in rows if r.pass_prob > 0]
    max_eval = (
        max(eval_rows, key=lambda r: r.pass_prob * max(r.expected_net_payout, 0.0))
        if eval_rows
        else None
    )

    top_multiplier = max(r.risk_multiplier for r in rows) if rows else 0.0
    unresolved = [
        label
        for label, point in (
            ("maxSurvival", _largest_within(survival_threshold)),
            ("conservative", _largest_within(conservative_threshold)),
            ("maxPayout", max_payout),
        )
        if point is not None and point.risk_multiplier >= top_multiplier
    ]

    return SizingAnalysis(
        rows=rows,
        max_survival=_largest_within(survival_threshold),
        conservative=_largest_within(conservative_threshold),
        max_payout=max_payout,
        max_eval_economics=max_eval,
        boundary_reached=boundary_reached,
        unresolved_at_boundary=unresolved,
    )
