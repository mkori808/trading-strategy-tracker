"""Attribution-ladder diagnostic for Market-Residual Momentum.

See research/mrm_attribution_ladder_preregistration.json. This answers one
question: what does market-residualizing the momentum score (rung C -> rung
D) add over plain trailing-return momentum, once concentration (rung B) and
universe return (rung A) are accounted for separately?

Rung C (`PlainMomentumControl`) and rung D (the real, already-canonical
`MarketResidualMomentum`) are run through the identical `dow_pit` universe,
window, cost model, and rebalance mechanics -- the two engine calls below
share every keyword argument except the strategy instance itself, so the
only thing that can move the result is the score formula. Rungs A and B
(PIT equal-weight and the concentration-matched random-top-N null) are
computed exactly once from rung D's fetched bars/membership and reused for
both C and D, so the C-vs-D comparison against them is paired rather than
two independently noisy estimates.

Diagnostic only. This module never writes to `engine/logging_db.py` (same
discipline `engine/compare_filters.py` and `engine/compare_universe.py`
already document: a comparison run must never shadow the canonical
leaderboard result) and never modifies `MarketResidualMomentum` or
`research/frozen_v1_protocol.json`.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.cross_sectional import CrossSectionalResult, run_cross_sectional_backtest
from engine.execution_calibration import spread_for_universe
from engine.pit_analysis import dynamic_random_benchmarks, normalized_benchmark
from engine.universe import SECTOR_BENCHMARK, daily_date_range
from engine.universe_ledger import resolve_schedule
from strategies.cross_sectional import CrossSectionalStrategy
from strategies.params import param_field
from strategies.swing.frozen_research import MarketResidualMomentum

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports" / "mrm_attribution_ladder"
PREREGISTRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "research" / "mrm_attribution_ladder_preregistration.json"
)
COMMISSION_BPS = 0.0
RANDOM_SIMULATIONS = 400
RANDOM_SEED = 1729
BOOTSTRAP_DRAWS = 5000
BOOTSTRAP_SEED = 20260831


@dataclass
class PlainMomentumControl(CrossSectionalStrategy):
    """Rung C: identical mechanics to `MarketResidualMomentum`, scored on raw
    trailing cumulative return instead of the return residualized against
    SPY. Diagnostic only -- deliberately not registered anywhere
    (`ALL_STRATEGY_NAMES`, the leaderboard, the params UI); it exists solely
    to be the "everything the same except the score formula" counterpart
    `engine/mrm_attribution_ladder.py` needs.
    """

    name = "Plain Momentum (Attribution Control)"
    timeframe = "1mo"
    lookback: int = param_field(126, label="Momentum lookback", minimum=63, maximum=189, step=63)
    skip_days: int = param_field(5, label="Recent sessions skipped", minimum=0, maximum=21, step=1)
    top_n: int = param_field(5, label="Positions held", minimum=3, maximum=10, step=1)
    rebalance_frequency: str = param_field("monthly", label="Rebalance frequency", choices=["monthly"])

    def required_history_days(self) -> int:
        # Byte-identical to MarketResidualMomentum.required_history_days so
        # both rungs fetch the same warmup and start trading the same day.
        return self.lookback + self.skip_days + 2

    def rebalance(self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, float]:
        scores: dict[str, float] = {}
        for symbol, bars in universe_bars.items():
            stock = bars.loc[:as_of, "Close"].pct_change()
            if self.skip_days:
                stock = stock.iloc[:-self.skip_days]
            stock = stock.dropna().iloc[-self.lookback:]
            if len(stock) < self.lookback:
                continue
            values = stock.to_numpy(dtype=float)
            if np.any(values <= -1.0):
                continue
            scores[symbol] = float(np.prod(1.0 + values) - 1.0)
        selected = sorted(scores, key=scores.get, reverse=True)[: self.top_n]
        return {symbol: 1.0 / len(selected) for symbol in selected} if selected else {}


def _return_pct(values: pd.Series) -> float | None:
    clean = values.dropna()
    if len(clean) < 2 or float(clean.iloc[0]) <= 0:
        return None
    return float((clean.iloc[-1] / clean.iloc[0] - 1.0) * 100.0)


def _period_returns(equity_curve: pd.Series, rebalance_dates: pd.DatetimeIndex) -> pd.Series:
    """Equity curve's simple return over each rebalance-to-rebalance segment,
    indexed by the segment's start date. The last (possibly partial) segment
    to `end` is included so no traded period is dropped from the bootstrap."""
    boundaries = sorted(set(rebalance_dates) & set(equity_curve.index))
    if not boundaries or boundaries[0] != equity_curve.index[0]:
        boundaries = [equity_curve.index[0]] + boundaries
    boundaries = boundaries + [equity_curve.index[-1]]
    boundaries = sorted(set(boundaries))
    returns = {}
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        start_value = float(equity_curve.loc[start])
        end_value = float(equity_curve.loc[end])
        if start_value > 0:
            returns[start] = end_value / start_value - 1.0
    return pd.Series(returns)


def _paired_bootstrap(diff: pd.Series, *, draws: int, seed: int) -> dict[str, Any]:
    """Resample rebalance-period (D minus C) return differences with
    replacement and compound each draw into a cumulative-return statistic.
    Reported as an interval, not a pass/fail gate -- see the preregistration's
    interpretationRule."""
    values = diff.dropna().to_numpy(dtype=float)
    if len(values) == 0:
        return {"draws": 0, "reason": "no paired rebalance periods"}
    rng = np.random.default_rng(seed)
    n = len(values)
    cumulative = np.empty(draws, dtype=float)
    for i in range(draws):
        sample = rng.choice(values, size=n, replace=True)
        cumulative[i] = float(np.prod(1.0 + sample) - 1.0) * 100.0
    return {
        "draws": draws,
        "periods": n,
        "meanPct": float(cumulative.mean()),
        "medianPct": float(np.median(cumulative)),
        "ci90LowPct": float(np.quantile(cumulative, 0.05)),
        "ci90HighPct": float(np.quantile(cumulative, 0.95)),
        "fractionPositive": float((cumulative > 0).mean()),
        "pointEstimatePct": float(np.prod(1.0 + values) - 1.0) * 100.0,
    }


def compute_rungs(cash: float = 10_000.0) -> dict[str, Any]:
    """Shared setup for rungs A-D: fetch the `dow_pit` schedule/bars once and
    run the plain-momentum control (C) and canonical Market-Residual
    Momentum (D) through byte-identical engine kwargs.

    Returns the raw objects (`CrossSectionalResult`s, the resolved
    `UniverseSchedule`, SPY bars) rather than the JSON-serializable summary
    `run_ladder()` builds from them below, so a later diagnostic that needs
    the actual equity curves and calendar (e.g. a regime/drawdown
    attribution) can reuse this exact periods-and-holdings framework
    instead of re-deriving it -- a pure extraction, not a behavior change;
    `run_ladder()`'s output is unchanged by this split.
    """
    start, end = daily_date_range()
    schedule = resolve_schedule("dow_jones_industrial_average", start, end, require_complete=False)
    if schedule is None:
        raise RuntimeError("Dow point-in-time membership ledger is unavailable")
    symbols = schedule.symbols
    rf = data_module.risk_free_rate(start, end)
    benchmark_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start - timedelta(days=430), end)

    rung_c = PlainMomentumControl()
    rung_d = MarketResidualMomentum(benchmark_bars=benchmark_bars)
    spread_by_symbol = {s: spread_for_universe(s, start, end, None) for s in symbols}

    common_kwargs = dict(
        symbols=symbols, start=start, end=end, cash=cash, risk_free_rate=rf,
        rebalance_frequency="monthly", spread_by_symbol=spread_by_symbol,
        commission_bps=COMMISSION_BPS, membership_at=schedule.membership_at,
        universe_key=schedule.universe_key,
    )
    result_c: CrossSectionalResult = run_cross_sectional_backtest(
        rung_c.name, rung_c, **common_kwargs,
    )
    result_d: CrossSectionalResult = run_cross_sectional_backtest(
        "Market-Residual Momentum", rung_d, **common_kwargs,
    )

    # Rungs A and B: computed ONCE from rung D's fetched bars/membership
    # (identical to rung C's -- same symbols, same window, same warmup) and
    # reused as the shared baseline for both, per the preregistration's
    # pairing requirement.
    ew_curve, random_stats = dynamic_random_benchmarks(
        result_d.validation_bars, schedule.membership_at, result_d.equity_curve.index,
        rebalance_frequency="monthly", top_n=rung_d.top_n,
        initial_equity=cash, simulations=RANDOM_SIMULATIONS, seed=RANDOM_SEED,
    )
    spy_equity = normalized_benchmark(
        benchmark_bars["Close"], result_d.equity_curve.index, cash,
    )
    rebalance_dates = (
        pd.DatetimeIndex(sorted(result_d.rebalances["date"]))
        if not result_d.rebalances.empty else pd.DatetimeIndex([])
    )
    return {
        "start": start, "end": end, "cash": cash, "schedule": schedule,
        "benchmark_bars": benchmark_bars, "rung_c": rung_c, "rung_d": rung_d,
        "result_c": result_c, "result_d": result_d, "ew_curve": ew_curve,
        "random_stats": random_stats, "spy_equity": spy_equity,
        "rebalance_dates": rebalance_dates,
    }


def run_ladder(cash: float = 10_000.0) -> dict[str, Any]:
    computed = compute_rungs(cash)
    start, end = computed["start"], computed["end"]
    result_c: CrossSectionalResult = computed["result_c"]
    result_d: CrossSectionalResult = computed["result_d"]
    rung_d = computed["rung_d"]
    ew_curve = computed["ew_curve"]
    random_stats = computed["random_stats"]
    spy_equity = computed["spy_equity"]
    rebalance_dates = computed["rebalance_dates"]

    ew_return = _return_pct(ew_curve)
    spy_return = _return_pct(spy_equity)

    periods_c = _period_returns(result_c.equity_curve, rebalance_dates)
    periods_d = _period_returns(result_d.equity_curve, rebalance_dates)
    aligned = pd.concat([periods_c.rename("c"), periods_d.rename("d")], axis=1).dropna()
    diff = aligned["d"] - aligned["c"]
    bootstrap = _paired_bootstrap(diff, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED)

    def _row(name: str, result: CrossSectionalResult | None, cumulative_pct: float | None) -> dict[str, Any]:
        return {
            "rung": name,
            "cumulativeReturnPct": cumulative_pct,
            "cagrPct": result.cagr_pct if result else None,
            "sharpe": result.sharpe if result else None,
            "maxDrawdownPct": result.max_drawdown_pct if result else None,
            "totalCosts": result.total_costs if result else None,
        }

    rows = [
        _row("A: PIT Dow equal-weight", None, ew_return),
        {**_row("B: concentration-matched random top-N (mean)", None, random_stats.get("meanReturnPct")),
         "detail": random_stats},
        _row("C: plain momentum (control)", result_c, result_c.return_pct),
        _row("D: canonical Market-Residual Momentum", result_d, result_d.return_pct),
    ]

    incremental = {
        "cumulativeReturnDeltaPct": (
            None if result_c.return_pct is None or result_d.return_pct is None
            else result_d.return_pct - result_c.return_pct
        ),
        "cagrDeltaPct": (
            None if result_c.cagr_pct is None or result_d.cagr_pct is None
            else result_d.cagr_pct - result_c.cagr_pct
        ),
        "sharpeDelta": (
            None if result_c.sharpe is None or result_d.sharpe is None
            else result_d.sharpe - result_c.sharpe
        ),
        "maxDrawdownDeltaPct": result_d.max_drawdown_pct - result_c.max_drawdown_pct,
        "pairedRebalancePeriods": int(len(diff)),
        "pairedBootstrap": bootstrap,
    }

    return {
        "generatedFrom": str(PREREGISTRATION_PATH.name),
        "window": {"start": str(start), "end": str(end)},
        "universe": "dow_pit",
        "canonicalConfig": {
            "lookback": rung_d.lookback, "skipDays": rung_d.skip_days,
            "topN": rung_d.top_n, "rebalanceFrequency": rung_d.rebalance_frequency,
        },
        "benchmarks": {
            "pitEqualWeightReturnPct": ew_return,
            "spyReturnPct": spy_return,
            "randomTopN": random_stats,
        },
        "ladder": rows,
        "incrementalCMinusD": incremental,
        "notApplicable": {
            "E": "No absolute-momentum-filter rung exists for Market-Residual "
                 "Momentum's canonical definition; D and F (the full canonical "
                 "strategy) are the same run. See the preregistration's "
                 "notApplicable.E."
        },
    }


def write_report(payload: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "results.json").write_text(json.dumps(payload, indent=2, default=str))

    incr = payload["incrementalCMinusD"]
    bootstrap = incr["pairedBootstrap"]
    lines = [
        "# Market-Residual Momentum Attribution Ladder",
        "",
        f"Window: {payload['window']['start']} to {payload['window']['end']}. "
        f"Universe: {payload['universe']}. Canonical config: "
        f"{payload['canonicalConfig']}.",
        "",
        "## Ladder",
        "",
        "| Rung | Cumulative return | CAGR | Sharpe | Max DD |",
        "|---|---:|---:|---:|---:|",
    ]
    def _fmt(value: float | None, suffix: str = "%", signed: bool = True) -> str:
        if value is None:
            return ""
        return f"{value:+.2f}{suffix}" if signed else f"{value:.2f}{suffix}"

    for row in payload["ladder"]:
        cumulative = _fmt(row["cumulativeReturnPct"])
        cagr = _fmt(row["cagrPct"])
        sharpe = _fmt(row["sharpe"], suffix="", signed=False)
        drawdown = _fmt(row["maxDrawdownPct"], signed=False)
        lines.append(f"| {row['rung']} | {cumulative} | {cagr} | {sharpe} | {drawdown} |")
    lines += [
        "",
        f"SPY (identical window): {payload['benchmarks']['spyReturnPct']:+.2f}%. "
        f"PIT equal-weight: {payload['benchmarks']['pitEqualWeightReturnPct']:+.2f}%.",
        "",
        "## Incremental effect of residualization (D minus C)",
        "",
        f"- Cumulative return delta: {incr['cumulativeReturnDeltaPct']:+.2f} pp" if incr['cumulativeReturnDeltaPct'] is not None else "- Cumulative return delta: n/a",
        f"- CAGR delta: {incr['cagrDeltaPct']:+.2f} pp" if incr['cagrDeltaPct'] is not None else "- CAGR delta: n/a",
        f"- Sharpe delta: {incr['sharpeDelta']:+.2f}" if incr['sharpeDelta'] is not None else "- Sharpe delta: n/a",
        f"- Max drawdown delta: {incr['maxDrawdownDeltaPct']:+.2f} pp",
        f"- Paired rebalance periods: {incr['pairedRebalancePeriods']}",
        "",
        f"Paired bootstrap ({bootstrap.get('draws', 0)} draws over "
        f"{bootstrap.get('periods', 0)} rebalance periods): point estimate "
        f"{bootstrap.get('pointEstimatePct', float('nan')):+.2f}%, "
        f"90% interval [{bootstrap.get('ci90LowPct', float('nan')):+.2f}%, "
        f"{bootstrap.get('ci90HighPct', float('nan')):+.2f}%], "
        f"{bootstrap.get('fractionPositive', float('nan')) * 100:.1f}% of draws positive.",
        "",
        "Rung E (residual momentum + absolute-momentum filter) does not exist "
        "as a distinct configuration of the canonical strategy -- D and F are "
        "the same run. See the preregistration's notApplicable.E.",
        "",
        "This is an attribution diagnostic, not a validation gate. It does not "
        "by itself change Market-Residual Momentum's 'Interesting, unresolved' "
        "verdict in research/frozen_research_report.md.",
    ]
    (REPORT_DIR / "report.md").write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    payload = run_ladder()
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
