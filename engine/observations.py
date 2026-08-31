"""Turn an already-computed backtest into an observation dataset.

One row per historical decision the strategy actually made, carrying (a) the
outcome the existing engine already computed for it and (b) the point-in-time
market state at the moment that decision became actionable.

**This module never re-runs a strategy and never changes one.** It reads a
`StrategyBacktestResult` or `CrossSectionalResult` that some other part of
the app produced and describes the conditions around signals that already
exist. Conditional Edge Discovery is therefore incapable of retroactively
altering a strategy's historical signal generation -- the only thing it can
do downstream is *drop* observations (see
`engine/conditional_validation.py`), never add or move one.

**The decision bar.**  Both engines separate the bar a decision is made on
from the bar it is filled on, and the feature snapshot must come from the
former:

- Standard per-symbol engine: `engine/event_timing.py` pins execution to
  NEXT_OPEN, so a fill stamped `EntryTime` was decided on the close of the
  session strictly before it. Rather than re-deriving `EntryBar - 1` from a
  re-fetched bar frame (which would have to align positionally with whatever
  the backtest saw), the decision bar is looked up as "the last feature row
  strictly before `EntryTime`". That is the same bar, derived from the
  trade's own timestamp.
- Cross-sectional engine: `engine/cross_sectional.py` ranks on
  `bars.index < rebalance_day` and fills at that day's open, so the decision
  bar is again the last session strictly before the stamped date.

**Outcomes are taken as computed, including costs.**  Realized R comes from
`engine/metrics.py:r_multiples` on the same trade rows the leaderboard uses,
and those fills already have the per-symbol spread applied by
`engine/backtest.py`. There is no gross/net asymmetry to introduce later: a
conditioned subset of these rows carries exactly the cost model the
unconditioned set does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from engine import data as data_module
from engine import pit_features
from engine.metrics import r_multiples
from engine.pit_features import MarketContext

#: Column carrying the primary prediction target for each engine shape. The
#: target is always the STRATEGY'S OWN OUTCOME, never a raw future security
#: return -- the research question is "will this signal work", not "will this
#: stock go up".
PRIMARY_OUTCOME: dict[str, str] = {
    "standard": "realized_r",
    "cross_sectional": "contribution_pct",
}

PRIMARY_OUTCOME_LABEL: dict[str, str] = {
    "standard": "Expectancy (R)",
    "cross_sectional": "Mean holding-period contribution (%)",
}

#: Metadata columns on the observation frame. Everything else is either a
#: feature (see engine/pit_features.py:FEATURES) or an outcome.
META_COLUMNS = (
    "symbol", "decision_time", "entry_time", "exit_time", "direction",
)

OUTCOME_COLUMNS = (
    "realized_r", "net_return_pct", "pnl", "modeled_cost", "win",
    "mfe_r", "mae_r", "exit_efficiency_pct", "holding_days", "holding_bars",
    "exit_reason", "weight", "contribution_pct", "position_return_pct",
)


@dataclass
class ObservationSet:
    """The observation dataset for one strategy, plus its provenance.

    `frame` is one row per decision, sorted by `decision_time`. Sorting is
    load-bearing: every chronological split downstream (holdout,
    walk-forward, purge/embargo) assumes it, and a randomly-ordered frame
    would silently produce a random split.
    """

    strategy_name: str
    engine: str
    frame: pd.DataFrame
    outcome_column: str
    outcome_label: str
    start: date
    end: date
    symbols: list[str]
    feature_keys: list[str]
    warnings: list[str] = field(default_factory=list)
    #: Per-feature count of non-missing values -- the coverage report a
    #: reader needs to tell "this feature says nothing" from "this feature
    #: was never computed here".
    coverage: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def outcomes(self) -> pd.Series:
        return self.frame[self.outcome_column]

    def slice_between(self, start: pd.Timestamp | None, end: pd.Timestamp | None) -> "ObservationSet":
        """A chronological sub-range, keeping all provenance.

        Bounds are on `decision_time` and are half-open at the top
        (`start <= t < end`), so adjacent windows can never share a row.
        """
        frame = self.frame
        if start is not None:
            frame = frame[frame["decision_time"] >= start]
        if end is not None:
            frame = frame[frame["decision_time"] < end]
        return ObservationSet(
            strategy_name=self.strategy_name, engine=self.engine,
            frame=frame.reset_index(drop=True), outcome_column=self.outcome_column,
            outcome_label=self.outcome_label, start=self.start, end=self.end,
            symbols=self.symbols, feature_keys=self.feature_keys,
            warnings=list(self.warnings), coverage=dict(self.coverage),
        )

    def coverage_report(self) -> list[dict[str, Any]]:
        total = len(self.frame)
        rows = []
        for key in self.feature_keys:
            present = int(self.coverage.get(key, 0))
            definition = pit_features.FEATURES[key]
            rows.append({
                "key": key, "label": definition.label, "group": definition.group,
                "present": present, "total": total,
                "coveragePct": (present / total * 100.0) if total else 0.0,
                "pitSafe": definition.pit_safe,
                "discoveryEligible": definition.discovery_eligible,
                "missingPolicy": definition.missing_policy,
            })
        return sorted(rows, key=lambda r: (-r["coveragePct"], r["key"]))


def _exit_reason(trade: pd.Series) -> str:
    """Classify how a trade ended, from the bracket levels it carried.

    backtesting.py does not record which order closed a position, so this is
    inferred by comparing the exit price to the stop and target the trade was
    opened with. A 0.1% tolerance absorbs the fill adjustment; anything that
    matches neither is a signal exit or the end of the data window.
    """
    exit_price = trade.get("ExitPrice")
    if exit_price is None or pd.isna(exit_price):
        return "unknown"
    for level, label in (("SL", "stop"), ("TP", "target")):
        value = trade.get(level)
        if value is not None and not pd.isna(value) and value != 0:
            if abs(float(exit_price) / float(value) - 1.0) <= 0.001:
                return label
    return "signal"


def _standard_rows(
    result: Any, context: MarketContext, start: date, end: date, warnings: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    feature_keys = [k for k in pit_features.FEATURES if pit_features.FEATURES[k].available]
    for symbol, per_symbol in sorted(result.per_symbol.items()):
        trades = per_symbol.trades
        if trades is None or trades.empty:
            continue
        frame = pit_features.symbol_features(symbol, context, start, end)
        if frame.empty:
            warnings.append(f"{symbol}: no feature frame; its {len(trades)} trades are excluded.")
            continue
        realized = r_multiples(trades)
        excursions = per_symbol.excursions
        excursion_by_entry: dict[Any, pd.Series] = {}
        if excursions is not None and not excursions.empty:
            for _, row in excursions.iterrows():
                excursion_by_entry[pd.Timestamp(row["EntryTime"])] = row
        for position, (_, trade) in enumerate(trades.iterrows()):
            entry_time = pd.Timestamp(trade["EntryTime"])
            decision_time = _last_index_before(frame.index, entry_time)
            if decision_time is None:
                warnings.append(
                    f"{symbol}: trade entered {entry_time} has no prior feature bar "
                    "(window starts at the first fill); excluded."
                )
                continue
            snapshot = pit_features.snapshot_at(frame, decision_time)
            excursion = excursion_by_entry.get(entry_time)
            exit_time = trade.get("ExitTime")
            exit_time = pd.Timestamp(exit_time) if exit_time is not None and not pd.isna(exit_time) else None
            pnl = float(trade["PnL"])
            row: dict[str, Any] = {
                "symbol": symbol,
                "decision_time": decision_time,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "direction": "long" if float(trade["Size"]) > 0 else "short",
                "realized_r": float(realized.iloc[position]) if pd.notna(realized.iloc[position]) else np.nan,
                "net_return_pct": float(trade.get("ReturnPct", np.nan)) * 100.0,
                "pnl": pnl,
                "modeled_cost": float(trade.get("ModeledCost", np.nan)),
                "win": bool(pnl > 0),
                "mfe_r": float(excursion["MFE_R"]) if excursion is not None else np.nan,
                "mae_r": float(excursion["MAE_R"]) if excursion is not None else np.nan,
                "exit_efficiency_pct": (
                    float(excursion["ExitEfficiencyPct"]) if excursion is not None else np.nan
                ),
                "holding_bars": (
                    float(trade["ExitBar"] - trade["EntryBar"])
                    if pd.notna(trade.get("ExitBar")) else np.nan
                ),
                "holding_days": (
                    float((exit_time - entry_time).days) if exit_time is not None else np.nan
                ),
                "exit_reason": _exit_reason(trade),
                "weight": np.nan,
                "contribution_pct": np.nan,
                "position_return_pct": float(trade.get("ReturnPct", np.nan)) * 100.0,
            }
            row.update(snapshot)
            rows.append(row)
    return rows, feature_keys


def _last_index_before(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> pd.Timestamp | None:
    """The last index entry STRICTLY before `timestamp`.

    Strictly-before, not at-or-before, is the whole point: the fill bar's own
    close is not information the decision could have used. `side="left"`
    places an exact match at that match's own position, so subtracting one
    lands on the previous bar even when the timestamps line up exactly.
    """
    if len(index) == 0:
        return None
    if index.tz is not None and timestamp.tz is None:
        timestamp = timestamp.tz_localize(index.tz)
    elif index.tz is None and timestamp.tz is not None:
        timestamp = timestamp.tz_localize(None)
    position = index.searchsorted(timestamp, side="left") - 1
    if position < 0:
        return None
    return index[position]


def _cross_sectional_rows(
    result: Any, context: MarketContext, start: date, end: date, warnings: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per (rebalance, holding).

    The outcome is that position's own return over the holding period it was
    actually held for -- open of this rebalance to open of the next -- and
    its portfolio contribution (weight x return). There is no R-multiple
    here because there is no stop: nothing in this engine defines a per-trade
    risk unit, and inventing one would make the number look comparable to a
    bracketed strategy's expectancy when it is not.
    """
    rows: list[dict[str, Any]] = []
    feature_keys = [k for k in pit_features.FEATURES if pit_features.FEATURES[k].available]
    rebalances = getattr(result, "rebalances", None)
    if rebalances is None or rebalances.empty:
        warnings.append("Result carries no rebalance log; no observations could be built.")
        return rows, feature_keys

    opens = _open_panel(result, start, end)
    dates = [pd.Timestamp(d) for d in rebalances["date"]]
    for position, rebalance_date in enumerate(dates):
        holdings: dict[str, float] = rebalances.iloc[position]["holdings"] or {}
        next_date = dates[position + 1] if position + 1 < len(dates) else None
        for symbol, weight in sorted(holdings.items()):
            frame = pit_features.symbol_features(symbol, context, start, end)
            if frame.empty:
                continue
            decision_time = _last_index_before(frame.index, rebalance_date)
            if decision_time is None:
                continue
            entry_price = _price_at(opens, symbol, rebalance_date)
            exit_price = (
                _price_at(opens, symbol, next_date) if next_date is not None
                else _last_price(opens, symbol)
            )
            if entry_price is None or exit_price is None or entry_price <= 0:
                continue
            position_return = (exit_price / entry_price - 1.0) * 100.0
            snapshot = pit_features.snapshot_at(frame, decision_time)
            exit_time = next_date if next_date is not None else _last_timestamp(opens, symbol)
            row: dict[str, Any] = {
                "symbol": symbol,
                "decision_time": decision_time,
                "entry_time": rebalance_date,
                "exit_time": exit_time,
                "direction": "long" if weight >= 0 else "short",
                "realized_r": np.nan,
                "net_return_pct": position_return,
                "pnl": np.nan,
                "modeled_cost": np.nan,
                "win": bool(position_return > 0),
                "mfe_r": np.nan,
                "mae_r": np.nan,
                "exit_efficiency_pct": np.nan,
                "holding_bars": np.nan,
                "holding_days": (
                    float((exit_time - rebalance_date).days) if exit_time is not None else np.nan
                ),
                "exit_reason": "rebalance",
                "weight": float(weight),
                "contribution_pct": float(weight) * position_return,
                "position_return_pct": position_return,
            }
            row.update(snapshot)
            rows.append(row)
    return rows, feature_keys


def _open_panel(result: Any, start: date, end: date) -> pd.DataFrame:
    """Opening prices for every symbol the result traded.

    Prefers the bar cache the cross-sectional run itself attached
    (`validation_bars`), so the prices used here are byte-identical to the
    ones the fills used. Falls back to a fresh fetch over the same window.
    """
    cached = getattr(result, "validation_bars", None)
    if isinstance(cached, dict) and cached:
        opens = {s: b["Open"] for s, b in cached.items() if not b.empty}
        if opens:
            return pd.DataFrame(opens).sort_index()
    opens = {}
    for symbol in getattr(result, "symbols", []):
        try:
            bars = data_module.get_bars(symbol, "1d", start, end)
        except Exception:  # noqa: BLE001
            continue
        if not bars.empty:
            opens[symbol] = bars["Open"]
    return pd.DataFrame(opens).sort_index() if opens else pd.DataFrame()


def _price_at(opens: pd.DataFrame, symbol: str, when: pd.Timestamp | None) -> float | None:
    if opens.empty or symbol not in opens.columns or when is None:
        return None
    series = opens[symbol].dropna()
    if series.empty:
        return None
    index = series.index
    if index.tz is not None and when.tz is None:
        when = when.tz_localize(index.tz)
    elif index.tz is None and when.tz is not None:
        when = when.tz_localize(None)
    position = index.searchsorted(when, side="left")
    if position >= len(index):
        return None
    return float(series.iloc[position])


def _last_price(opens: pd.DataFrame, symbol: str) -> float | None:
    if opens.empty or symbol not in opens.columns:
        return None
    series = opens[symbol].dropna()
    return float(series.iloc[-1]) if not series.empty else None


def _last_timestamp(opens: pd.DataFrame, symbol: str) -> pd.Timestamp | None:
    if opens.empty or symbol not in opens.columns:
        return None
    series = opens[symbol].dropna()
    return pd.Timestamp(series.index[-1]) if not series.empty else None


def build_observations(
    result: Any,
    *,
    engine: str,
    universe: Sequence[str] | None = None,
    context: MarketContext | None = None,
) -> ObservationSet:
    """Build the observation dataset for an already-computed backtest result.

    `universe` is the symbol list breadth and cross-sectional percentile
    features rank against. It defaults to the result's own traded universe,
    which is the honest choice: breadth measured over a different set than
    the strategy could trade would describe a market the strategy was not in.
    """
    strategy_name = getattr(result, "strategy_name", "unknown")
    start = getattr(result, "start")
    end = getattr(result, "end")
    symbols = list(getattr(result, "symbols", []))
    universe = list(universe) if universe else symbols
    warnings: list[str] = []
    context = context or pit_features.build_market_context(universe, start, end)
    warnings.extend(context.warnings)

    if engine == "standard":
        rows, feature_keys = _standard_rows(result, context, start, end, warnings)
    elif engine == "cross_sectional":
        rows, feature_keys = _cross_sectional_rows(result, context, start, end, warnings)
    else:
        raise ValueError(
            f"Unsupported engine {engine!r}. Conditional Edge Discovery supports the "
            "per-symbol ('standard') and rebalance ('cross_sectional') engines; a new "
            "engine shape needs its own observation builder rather than a coerced one."
        )

    columns = list(META_COLUMNS) + list(OUTCOME_COLUMNS) + feature_keys
    frame = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    if not frame.empty:
        frame = frame.sort_values("decision_time").reset_index(drop=True)
    coverage = {key: int(frame[key].notna().sum()) for key in feature_keys} if not frame.empty else {
        key: 0 for key in feature_keys
    }
    outcome_column = PRIMARY_OUTCOME[engine]
    if not frame.empty:
        before = len(frame)
        frame = frame[frame[outcome_column].notna()].reset_index(drop=True)
        dropped = before - len(frame)
        if dropped:
            warnings.append(
                f"{dropped} observation(s) had no computable {outcome_column} and were dropped."
            )
    return ObservationSet(
        strategy_name=strategy_name, engine=engine, frame=frame,
        outcome_column=outcome_column, outcome_label=PRIMARY_OUTCOME_LABEL[engine],
        start=start, end=end, symbols=symbols, feature_keys=feature_keys,
        warnings=warnings, coverage=coverage,
    )


def concurrency_profile(frame: pd.DataFrame) -> dict[str, Any]:
    """Simultaneous-position statistics for a set of observations.

    A conditioned strategy that keeps 30% of the signals has not necessarily
    kept 30% of the *capital demand*: filters that select on a market-wide
    state (regime, breadth, market volatility) keep or drop whole days at a
    time, so the survivors cluster. Reported alongside every conditioned-vs-
    original comparison for exactly that reason.
    """
    if frame.empty:
        return {
            "observations": 0, "averageConcurrent": 0.0, "maxConcurrent": 0,
            "capitalUtilizationPct": 0.0, "distinctEntryDays": 0,
            "maxEntriesPerDay": 0, "clusteringRatio": None,
        }
    events: list[tuple[pd.Timestamp, int]] = []
    for _, row in frame.iterrows():
        entry = row["entry_time"]
        exit_time = row["exit_time"]
        if entry is None or pd.isna(entry):
            continue
        events.append((pd.Timestamp(entry), 1))
        if exit_time is not None and not pd.isna(exit_time):
            events.append((pd.Timestamp(exit_time), -1))
    if not events:
        return {
            "observations": len(frame), "averageConcurrent": 0.0, "maxConcurrent": 0,
            "capitalUtilizationPct": 0.0, "distinctEntryDays": 0,
            "maxEntriesPerDay": 0, "clusteringRatio": None,
        }
    events.sort(key=lambda e: (e[0], -e[1]))
    open_count = 0
    peak = 0
    weighted = 0.0
    previous = events[0][0]
    span_days = 0.0
    for timestamp, delta in events:
        elapsed = (timestamp - previous).total_seconds() / 86400.0
        if elapsed > 0:
            weighted += open_count * elapsed
            span_days += elapsed
        open_count += delta
        peak = max(peak, open_count)
        previous = timestamp
    entry_days = pd.DatetimeIndex(
        [pd.Timestamp(t).normalize() for t in frame["entry_time"] if pd.notna(t)]
    )
    per_day = pd.Series(1, index=entry_days).groupby(level=0).sum() if len(entry_days) else pd.Series(dtype=int)
    average = weighted / span_days if span_days > 0 else 0.0
    return {
        "observations": len(frame),
        "averageConcurrent": float(average),
        "maxConcurrent": int(peak),
        # Utilisation against a 10-position book -- the same concurrency cap
        # engine/portfolio.py applies -- so a filtered strategy's demand on
        # shared capital is comparable to the unfiltered one's.
        "capitalUtilizationPct": float(min(average / 10.0, 1.0) * 100.0),
        "distinctEntryDays": int(len(per_day)),
        "maxEntriesPerDay": int(per_day.max()) if len(per_day) else 0,
        "clusteringRatio": (
            float(len(frame) / len(per_day)) if len(per_day) else None
        ),
    }
