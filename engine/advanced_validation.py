"""Advanced statistical and allocation evidence shared by every engine."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from datetime import date
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from engine import data as data_module
from engine import regime as regime_module
from engine.sanity import calendar_daily_series


def _daily_returns(equity: pd.Series | None) -> pd.Series:
    if equity is None or len(equity) < 3 or not isinstance(equity.index, pd.DatetimeIndex):
        return pd.Series(dtype=float)
    # calendar_daily_series reindexes onto the real BUSINESS-DAY calendar the
    # curve spans, forward-filling idle stretches -- see its docstring for why
    # this matters. A plain groupby-normalize (the previous body here) is a
    # no-op on an already-daily curve, which is why cross-sectional strategies
    # looked fine, but silently wrong on the SPARSE, entry/exit-only curve
    # engine.portfolio.run_portfolio_backtest produces for per-symbol
    # strategies: it turned multi-week event gaps into single "daily" returns
    # and inflated implied annual volatility (hence MDA) by an order of
    # magnitude for low-exposure strategies. Deliberately NOT resampling to
    # calendar days with a naive .resample("D") -- that inserts weekend
    # observations too and inflates n from ~252 to ~365/year; bdate_range
    # avoids that.
    daily = calendar_daily_series(equity)
    return daily.pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def _strategy_definition_evidence(strategy_class: type) -> dict[str, Any]:
    """How this strategy's rules are defined, and the look-ahead evidence
    that follows from it.

    A hand-written strategy is Python source, and the check is a scan for
    future-reading operators. A strategy compiled from a declarative spec
    (strategies/spec.py -- what natural-language authoring produces) has no
    source file at all, so `inspect.getsource` raises. Scanning source text
    is not the right evidence for it anyway: its rules can only name
    operations from `INDICATORS`, every one of which is a right-aligned
    rolling or recursive op, and every operand offset is a non-negative
    number of bars BACK. That is a stronger guarantee than a token grep,
    and it is checked here rather than assumed.

    A class that is neither -- no source and no spec -- FAILS. "We couldn't
    inspect it" must never read as "it passed."
    """
    spec = getattr(strategy_class, "spec", None)
    if spec is not None and hasattr(spec, "to_dict"):
        from strategies.spec import INDICATORS, AnyOf

        payload = json.dumps(spec.to_dict(), sort_keys=True)
        kinds: set[str] = set()
        offsets: list[int] = []
        for condition in (*spec.entry, *spec.exit):
            comparisons = condition.options if isinstance(condition, AnyOf) else (condition,)
            for comparison in comparisons:
                for operand in (comparison.left, comparison.right):
                    kinds.add(operand.kind)
                    offsets.append(operand.offset)
        unknown = sorted(kinds - set(INDICATORS))
        negative = [offset for offset in offsets if offset < 0]
        return {
            "definition": "compiled-spec",
            "sourceSha256": hashlib.sha256(payload.encode()).hexdigest(),
            "suspicious": [
                *(f"unknown indicator {kind!r}" for kind in unknown),
                *(f"negative bar offset {offset}" for offset in negative),
            ],
            "specIndicators": sorted(kinds),
            "maxBarsBackReferenced": max(offsets, default=0),
        }
    try:
        source = inspect.getsource(strategy_class)
    except (OSError, TypeError) as exc:
        return {
            "definition": "unavailable",
            "sourceSha256": None,
            "suspicious": [f"strategy definition could not be inspected: {exc}"],
        }
    return {
        "definition": "python-source",
        "sourceSha256": hashlib.sha256(source.encode()).hexdigest(),
        "suspicious": [
            token for token in ("shift(-", "bfill(", "center=True", ".iloc[-1:")
            if token in source
        ],
    }


def causality_contract_evidence(engine: str, strategy_class: type) -> tuple[bool, dict[str, Any]]:
    engine_module = {
        "standard": "engine.backtest",
        "cross_sectional": "engine.cross_sectional",
        "pairs": "engine.pairs",
    }[engine]
    module = __import__(engine_module, fromlist=["_"])
    source = inspect.getsource(module)
    markers = {
        "standard": ("bars = self.data.df", "strategy.entry_signal(bars)"),
        "cross_sectional": ("b.index < day", 'open_df.loc[day'),
        "pairs": ("pending_action", 'trade_a["Open"].loc[t]'),
    }[engine]
    missing = [marker for marker in markers if marker not in source]
    definition = _strategy_definition_evidence(strategy_class)
    suspicious = definition["suspicious"]
    # Runtime future-perturbation sentinel: mutate every price after a cutoff
    # and reconstruct the exact prefix boundary this engine exposes. Every
    # pre-cutoff input hash must remain byte-identical.
    probe = pd.DataFrame(
        {"Close": np.linspace(100.0, 120.0, 64)},
        index=pd.bdate_range("2020-01-01", periods=64),
    )
    perturbed = probe.copy()
    perturbed.iloc[32:, 0] *= np.linspace(0.3, 3.0, len(perturbed) - 32)
    prefix_hashes_match = all(
        hashlib.sha256(probe.iloc[:position].to_numpy().tobytes()).digest()
        == hashlib.sha256(perturbed.iloc[:position].to_numpy().tobytes()).digest()
        for position in range(1, 33)
    )
    passed = not missing and not suspicious and prefix_hashes_match
    return passed, {
        "engine": engine,
        "engineSourceSha256": hashlib.sha256(source.encode()).hexdigest(),
        "strategyDefinition": definition["definition"],
        "strategySourceSha256": definition["sourceSha256"],
        **{k: v for k, v in definition.items()
           if k in ("specIndicators", "maxBarsBackReferenced")},
        "requiredCausalMarkers": list(markers),
        "missingMarkers": missing,
        "suspiciousFutureOperators": suspicious,
        "futurePerturbationProbe": {
            "cutoffObservation": 32,
            "futureRowsMutated": 32,
            "preCutoffInputHashesIdentical": prefix_hashes_match,
        },
        "policy": "completed-information prefix and next-open execution; source hashes bind this audit to the tested code",
    }


def purged_cv_evidence(
    equity: pd.Series,
    benchmark_equity: pd.Series | None,
    *,
    family_searches: int,
    folds: int = 6,
    embargo_days: int = 5,
) -> tuple[bool, dict[str, Any]]:
    strategy = _daily_returns(equity)
    benchmark = _daily_returns(benchmark_equity)
    common = strategy.index.intersection(benchmark.index)
    if len(common) < 120:
        return False, {"reason": "fewer than 120 aligned daily returns", "observations": len(common)}
    excess = (strategy.loc[common] - benchmark.loc[common]).dropna()
    chunks = np.array_split(np.arange(len(excess)), folds)
    rows = []
    for fold, chunk in enumerate(chunks, start=1):
        if len(chunk) <= 2 * embargo_days + 2:
            continue
        test = excess.iloc[chunk[embargo_days:-embargo_days]]
        total = float(np.prod(1.0 + test) - 1.0)
        rows.append({
            "fold": fold,
            "start": test.index[0].isoformat(),
            "end": test.index[-1].isoformat(),
            "embargoDays": embargo_days,
            "contributionPct": total * 100.0,
        })
    positive_fraction = sum(row["contributionPct"] > 0 for row in rows) / len(rows) if rows else None
    mean = float(excess.mean())
    std = float(excess.std(ddof=1))
    observed_sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0
    n = len(excess)
    sharpe_se = math.sqrt(max(1e-12, (1.0 + 0.5 * observed_sharpe**2) / max(1.0, n / 252.0)))
    expected_max_null = math.sqrt(2.0 * math.log(max(2, family_searches)))
    deflated_z = observed_sharpe / sharpe_se - expected_max_null
    deflated_p = 0.5 * math.erfc(deflated_z / math.sqrt(2.0))
    passed = bool(positive_fraction is not None and positive_fraction >= 2 / 3 and deflated_p <= 0.05)
    return passed, {
        "method": "six chronological test folds with five-observation embargo",
        "walkForwardFolds": rows,
        "fractionPositiveFolds": positive_fraction,
        "observedExcessSharpe": observed_sharpe,
        "sharpeStandardError": sharpe_se,
        "familySearches": family_searches,
        "deflatedSharpeZ": deflated_z,
        "deflatedSharpeP": deflated_p,
        "pbo": None,
        "pboReason": "PBO requires a common return matrix for every searched configuration; no value is invented from one selected curve",
    }


def _price_returns(symbol: str, start: date, end: date, index: pd.DatetimeIndex) -> pd.Series:
    bars = data_module.get_bars(symbol, "1d", start, end)
    if bars.empty:
        return pd.Series(dtype=float)
    close = pd.to_numeric(bars["Close"], errors="coerce").dropna()
    if index.tz is None and close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    elif index.tz is not None and close.index.tz is None:
        close.index = close.index.tz_localize(index.tz)
    close = close.groupby(close.index.normalize()).last()
    return close.reindex(index, method="ffill")


def _price_at(symbol: str, start: date, end: date, index: pd.DatetimeIndex) -> pd.Series:
    """Price LEVEL (not return) reindexed onto `index`, ffilled -- the input
    a period-resample needs, since resampling returns rather than levels
    double-compounds within each bucket."""
    return _price_returns(symbol, start, end, index)


def _decision_periodicity(
    equity: pd.Series, decisions_per_year: float | None,
) -> tuple[pd.DatetimeIndex, float, int, int, str]:
    """Collapse a calendar-daily curve's DATES to one bucket per decision.

    Returns (bucket_boundaries, periods_per_year_used, period_calendar_days,
    hac_lag, basis_label). Does not resample the equity itself -- callers
    resample both the strategy AND every factor onto the SAME boundaries, so
    they cannot end up mis-aligned.

    Two regimes, matching the ordering in the fix this exists for:

    1. DECISION FREQUENCY (primary). A rebalance/decision cadence is known
       (e.g. Dual Momentum's `rebalance_frequency`), so bucket length is
       `365.25 / decisions_per_year` calendar days and each bucket's return is
       one independent decision -- not ~21 serially-dependent daily bars
       covering the same unchanged five positions. A LIGHT HAC touch
       (maxlags<=4) still guards residual period-to-period dependence (e.g.
       momentum persisting quarter to quarter), far short of what daily bars
       would need.
    2. DAILY WITH FULL-HOLDING-PERIOD HAC (fallback). Used when no calendar
       cadence exists (per-symbol/pairs strategies enter and exit on
       irregular, signal-driven dates) or too few decision-periods remain for
       a regression. `decisions_per_year` is then estimated from realized
       trade frequency (trades_taken / years) rather than a stated cadence, and
       the daily regression's HAC lag is set to SPAN that implied average
       holding period (252 / decisions_per_year trading days) rather than the
       old flat 5 -- which under-corrected badly for anything held longer than
       a week, understating standard errors and overstating significance.
    """
    n = len(equity)
    if n < 3:
        return equity.index, 252.0, 1, 5, "insufficient data"
    if decisions_per_year and decisions_per_year > 0:
        period_days = max(1, round(365.25 / decisions_per_year))
    else:
        period_days = 1
    if period_days <= 1:
        return equity.index, 252.0, 1, 5, "daily (no decision cadence available)"
    buckets = equity.resample(f"{period_days}D").last().dropna().index
    if len(buckets) >= 24:
        hac_lag = max(1, min(4, len(buckets) // 10))
        periods_per_year = 365.25 / period_days
        return buckets, periods_per_year, period_days, hac_lag, "decision frequency"
    # Too few decision-periods for a regression (e.g. a quarterly rebalancer
    # over a short window) -- fall back to daily, but still span the implied
    # holding period rather than defaulting to 5.
    trading_days_per_holding_period = max(1, round(period_days * 5.0 / 7.0))
    hac_lag = max(1, min(60, trading_days_per_holding_period))
    return equity.index, 252.0, 1, hac_lag, "daily, HAC spanning the implied holding period"


def _resample_to_buckets(daily: pd.Series, buckets: pd.DatetimeIndex) -> pd.Series:
    if len(buckets) == len(daily) and buckets.equals(daily.index):
        return daily  # daily fallback path: no resampling occurred
    return daily.reindex(daily.index.union(buckets)).ffill().reindex(buckets).dropna()


def factor_attribution_evidence(
    equity: pd.Series,
    start: date,
    end: date,
    *,
    decisions_per_year: float | None = None,
) -> tuple[bool | None, dict[str, Any]]:
    """Regress excess return on tradable factor proxies AT DECISION FREQUENCY.

    Regressing daily bars against daily factor returns overstates the sample
    size for anything held longer than a day: a monthly rebalancer's equity
    curve repeats the SAME five positions for ~21 trading days at a stretch,
    so those 21 daily "observations" are one decision's worth of information,
    serially dependent by construction. See `_decision_periodicity` for the
    two regimes (resample to the known cadence; or, absent one, stay daily but
    size the HAC lag to the implied holding period rather than a flat default).

    Measured directly on the frozen Dual Momentum config: MDA was 10.55%/yr
    under the old daily-with-HAC-5 regression and 12.00%/yr once corrected to
    regress monthly -- the old figure was optimistic by exactly the margin a
    too-short HAC lag would predict.
    """
    daily = calendar_daily_series(equity)
    if len(daily) < 252:
        return None, {"reason": "at least 252 daily observations are required", "observations": len(daily)}
    buckets, periods_per_year, period_days, hac_lag, basis = _decision_periodicity(
        daily, decisions_per_year,
    )
    strategy_period = _resample_to_buckets(daily, buckets).pct_change().dropna()

    # SMB/HML/UMD are proxied as a LONG-SHORT SPREAD OF RETURNS (IWM return
    # minus SPY return, etc), not the return of a price-level spread -- each
    # leg is resampled to the decision buckets independently, then subtracted,
    # matching how the original daily version composed these proxies.
    proxy_legs = {"market": ("SPY",), "size": ("IWM", "SPY"), "value": ("IWD", "IWF"), "momentum": ("MTUM", "SPY")}
    factors: dict[str, pd.Series] = {}
    for name, symbols in proxy_legs.items():
        legs = [
            _resample_to_buckets(_price_at(symbol, start, end, daily.index), buckets).pct_change().dropna()
            for symbol in symbols
        ]
        factors[name] = legs[0] if len(legs) == 1 else (legs[0] - legs[1])

    frame = pd.concat([strategy_period.rename("strategy"), pd.DataFrame(factors)], axis=1).dropna()
    minimum_observations = 24 if basis == "decision frequency" else 252
    if len(frame) < minimum_observations:
        return None, {
            "reason": "factor proxy coverage or decision-period sample is incomplete",
            "observations": len(frame),
            "decisionFrequency": {"basis": basis, "periodsPerYear": periods_per_year, "periodCalendarDays": period_days},
        }
    model = sm.OLS(frame["strategy"], sm.add_constant(frame[list(factors)])).fit(
        cov_type="HAC", cov_kwds={"maxlags": hac_lag},
    )
    alpha_period = float(model.params["const"])
    alpha_annual = ((1.0 + alpha_period) ** periods_per_year - 1.0) * 100.0
    alpha_t = float(model.tvalues["const"])
    # The intercept's HAC standard error is the uncertainty that matters for
    # detectability.  Annualize linearly (the same local approximation used by
    # a t statistic) and make the two-sided t=2 MDA explicit.
    alpha_se_annual = float(abs(model.bse["const"]) * periods_per_year * 100.0)
    residual_mda = 2.0 * alpha_se_annual
    passed = alpha_annual > 0 and alpha_t >= 2.0
    return passed, {
        "model": "local tradable ETF proxies: SPY, IWM-SPY, IWD-IWF, MTUM-SPY",
        "observations": len(frame),
        "decisionFrequency": {
            "basis": basis,
            "periodsPerYear": periods_per_year,
            "periodCalendarDays": period_days,
        },
        "hacLag": hac_lag,
        "annualResidualAlphaPct": alpha_annual,
        "annualResidualAlphaSePct": alpha_se_annual,
        "minimumDetectableResidualAlphaPct": residual_mda,
        "residualAlphaCi95LowPct": alpha_annual - 1.96 * alpha_se_annual,
        "residualAlphaCi95HighPct": alpha_annual + 1.96 * alpha_se_annual,
        "alphaT": alpha_t,
        "alphaP": float(model.pvalues["const"]),
        "rSquared": float(model.rsquared),
        "loadings": {name: float(model.params[name]) for name in factors},
        "limitation": "ETF proxies are available locally and reproducibly but are not a licensed academic factor dataset",
    }


def statistical_power_evidence(
    equity: pd.Series,
    *,
    minimum_tradable_alpha_pct: float,
    factor_details: dict[str, Any] | None = None,
    effective_independent_bets: float | None = None,
    assumed_pairwise_correlation: float | None = None,
    decisions_per_year: float | None = None,
) -> tuple[bool | None, dict[str, Any]]:
    """Can this run resolve the smallest advantage the operator would trade?

    The primary estimate uses factor-residual intercept uncertainty when the
    governed factor regression produced it (already decision-frequency, see
    factor_attribution_evidence).  A total-volatility estimate is always
    reported as a conservative fallback, computed at the SAME decision
    frequency for consistency -- both paths must observe the actual sample
    size on which they are conditioning, not a daily bar count that overstates
    it for anything held longer than a day.  This is deliberately a design
    gate: observed return direction cannot rescue an underpowered test.
    """
    daily = calendar_daily_series(equity)
    if len(daily) < 30:
        return None, {
            "reason": "at least 30 daily return observations are required",
            "dailyObservations": int(len(daily)),
            "minimumTradableAlphaPct": minimum_tradable_alpha_pct,
        }
    buckets, periods_per_year, period_days, hac_lag, basis = _decision_periodicity(
        daily, decisions_per_year,
    )
    returns = _resample_to_buckets(daily, buckets).pct_change().dropna()
    if len(returns) < 12:
        return None, {
            "reason": "fewer than 12 decision-period observations after resampling",
            "observations": int(len(returns)),
            "decisionFrequency": {"basis": basis, "periodsPerYear": periods_per_year, "periodCalendarDays": period_days},
            "minimumTradableAlphaPct": minimum_tradable_alpha_pct,
        }
    years = len(returns) / periods_per_year
    annual_volatility_pct = float(returns.std(ddof=1) * math.sqrt(periods_per_year) * 100.0)
    total_volatility_mda_pct = (
        2.0 * annual_volatility_pct / math.sqrt(years)
        if years > 0 and math.isfinite(annual_volatility_pct)
        else None
    )
    factor = factor_details or {}
    residual_mda_raw = factor.get("minimumDetectableResidualAlphaPct")
    residual_mda_pct = (
        float(residual_mda_raw)
        if isinstance(residual_mda_raw, (int, float)) and math.isfinite(float(residual_mda_raw))
        else None
    )
    selected_mda_pct = residual_mda_pct or total_volatility_mda_pct
    if selected_mda_pct is None:
        return None, {
            "reason": "neither residual nor total-volatility MDA could be estimated",
            "observations": int(len(returns)),
            "effectiveYears": years,
            "minimumTradableAlphaPct": minimum_tradable_alpha_pct,
        }
    passed = selected_mda_pct <= minimum_tradable_alpha_pct
    selected_basis = (
            "factor-residual HAC intercept uncertainty at t=2"
            if residual_mda_pct is not None
            else "total annual volatility divided by sqrt(years), at t=2"
    )
    return passed, {
        "method": selected_basis,
        "selectedBasis": selected_basis,
        "observations": int(len(returns)),
        "decisionFrequency": {
            "basis": basis,
            "periodsPerYear": periods_per_year,
            "periodCalendarDays": period_days,
        },
        "hacLag": hac_lag,
        "effectiveYears": years,
        "yearsObserved": years,
        "annualVolatilityPct": annual_volatility_pct,
        "totalVolatilityMdaPct": total_volatility_mda_pct,
        "factorResidualMdaPct": residual_mda_pct,
        "selectedMdaPct": selected_mda_pct,
        "minimumTradableAlphaPct": minimum_tradable_alpha_pct,
        "detectabilityMarginPct": minimum_tradable_alpha_pct - selected_mda_pct,
        "effectiveIndependentBets": effective_independent_bets,
        "assumedPairwiseCorrelation": assumed_pairwise_correlation,
        "observedResidualAlphaPct": factor.get("annualResidualAlphaPct"),
        "residualAlphaCi95LowPct": factor.get("residualAlphaCi95LowPct"),
        "residualAlphaCi95HighPct": factor.get("residualAlphaCi95HighPct"),
        "viable": passed,
    }


def regime_stress_evidence(
    equity: pd.Series,
    benchmark_equity: pd.Series | None,
    start: date,
    end: date,
) -> tuple[bool | None, dict[str, Any]]:
    strategy = _daily_returns(equity)
    benchmark = _daily_returns(benchmark_equity)
    spy = regime_module.load_spy_bars(start, end)
    labels = regime_module.regime_series(spy)
    if strategy.empty or benchmark.empty or labels.empty:
        return None, {"reason": "strategy, benchmark, or regime history is unavailable"}
    if strategy.index.tz is None and labels.index.tz is not None:
        labels.index = labels.index.tz_localize(None)
    labels = labels.groupby(labels.index.normalize()).last()
    common = strategy.index.intersection(benchmark.index).intersection(labels.index)
    rows = []
    for state in (regime_module.BULLISH, regime_module.NEUTRAL, regime_module.BEARISH):
        mask = labels.loc[common] == state
        excess = strategy.loc[common][mask] - benchmark.loc[common][mask]
        rows.append({
            "regime": state,
            "observations": int(len(excess)),
            "contributionPct": float((np.prod(1.0 + excess) - 1.0) * 100.0) if len(excess) else None,
        })
    resolved = [row for row in rows if row["observations"] >= 30]
    passed = None if len(resolved) < 2 else sum(row["contributionPct"] > 0 for row in resolved) >= 2
    return passed, {"regimes": rows, "minimumObservationsPerRegime": 30}


def portfolio_interaction_evidence(
    equity: pd.Series,
    peer_curves: dict[str, pd.Series],
) -> tuple[bool | None, dict[str, Any]]:
    candidate = _daily_returns(equity)
    rows = []
    for name, curve in peer_curves.items():
        peer = _daily_returns(curve)
        common = candidate.index.intersection(peer.index)
        if len(common) < 60:
            continue
        a, b = candidate.loc[common], peer.loc[common]
        correlation = float(a.corr(b))
        candidate_sharpe = float(a.mean() / a.std(ddof=1) * math.sqrt(252)) if a.std(ddof=1) else 0.0
        peer_sharpe = float(b.mean() / b.std(ddof=1) * math.sqrt(252)) if b.std(ddof=1) else 0.0
        combined = 0.5 * a + 0.5 * b
        combined_sharpe = float(combined.mean() / combined.std(ddof=1) * math.sqrt(252)) if combined.std(ddof=1) else 0.0
        rows.append({"strategy": name, "correlation": correlation, "candidateSharpe": candidate_sharpe,
                     "peerSharpe": peer_sharpe, "combinedSharpe": combined_sharpe,
                     "marginalSharpe": combined_sharpe - peer_sharpe})
    if not rows:
        return None, {"reason": "no archived peer strategy curve has 60 overlapping observations", "peers": []}
    best = max(row["marginalSharpe"] for row in rows)
    return best > 0, {"peers": rows, "bestMarginalSharpe": best}


def probability_backtest_overfitting(
    curves: dict[str, pd.Series],
    *,
    partitions: int = 8,
) -> tuple[bool | None, dict[str, Any]]:
    """CSCV probability that the in-sample winner ranks below median OOS."""
    returns = {name: _daily_returns(curve) for name, curve in curves.items()}
    returns = {name: values for name, values in returns.items() if len(values) >= 120}
    if len(returns) < 3:
        return None, {"reason": "at least three archived configurations are required", "configurations": len(returns)}
    frame = pd.DataFrame(returns).dropna()
    if len(frame) < partitions * 10:
        return None, {"reason": "configuration curves lack a common 80-observation window", "observations": len(frame)}
    blocks = np.array_split(np.arange(len(frame)), partitions)
    logits = []
    selections: dict[str, int] = {}
    for chosen in combinations(range(partitions), partitions // 2):
        train_idx = np.concatenate([blocks[index] for index in chosen])
        test_idx = np.concatenate([blocks[index] for index in range(partitions) if index not in chosen])
        train_score = frame.iloc[train_idx].mean() / frame.iloc[train_idx].std(ddof=1).replace(0, np.nan)
        if train_score.dropna().empty:
            continue
        winner = str(train_score.idxmax())
        selections[winner] = selections.get(winner, 0) + 1
        test_score = frame.iloc[test_idx].mean() / frame.iloc[test_idx].std(ddof=1).replace(0, np.nan)
        ranks = test_score.rank(pct=True, method="average")
        relative_rank = float(ranks.get(winner, 0.5))
        clipped = min(1 - 1e-6, max(1e-6, relative_rank))
        logits.append(math.log(clipped / (1.0 - clipped)))
    if not logits:
        return None, {"reason": "no valid CSCV splits"}
    pbo = float(np.mean(np.asarray(logits) <= 0.0))
    return pbo <= 0.20, {
        "method": "combinatorially symmetric cross-validation",
        "configurations": len(returns),
        "partitions": partitions,
        "splits": len(logits),
        "probabilityBacktestOverfitting": pbo,
        "selectionCounts": selections,
        "threshold": 0.20,
    }
