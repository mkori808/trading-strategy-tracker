"""Point-in-time, long-only short-term mean-reversion research.

Signals formed at a close enter at the next session's open.  A one-day hold
therefore exits at that session's close.  Gap signals are known at the open,
enter at that open, and a one-day hold exits at the same close.  Holding
periods longer than one day are represented by equal-capital overlapping
daily cohorts rather than by pretending every cohort owns the whole account.

The module deliberately writes each run to a new timestamped directory.  It
never overwrites a prior canonical result.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


TRADING_DAYS = 252
SIGNAL_LABELS = {
    "raw_1d": "Raw 1-day reversal",
    "raw_3d": "Raw 3-day reversal",
    "idio_1d": "Market-adjusted 1-day reversal",
    "beta_idio_1d": "Beta-adjusted idiosyncratic 1-day reversal",
    "volnorm_20d": "Volatility-normalized reversal (20d)",
    "volnorm_60d": "Volatility-normalized reversal (60d)",
    "gap_down": "Gap-down reversal",
    "momentum_pullback": "Medium-term winners with a short-term pullback",
}
DEFAULT_SIGNAL = "beta_idio_1d"
DEFAULT_HOLD_DAYS = 1
DEFAULT_SELECTION = "pct_2"
DEFAULT_MIN_PRICE = 5.0
DEFAULT_MIN_ADV = 5_000_000.0
DEFAULT_MAX_VOL20 = 0.10


@dataclass(frozen=True)
class ResearchConfig:
    signal: str
    hold_days: int
    selection: str = "pct_2"
    min_price: float = 1.0
    min_adv: float = 500_000.0
    max_vol20: float | None = None
    min_momentum_percentile: float = 0.70
    max_fundamental_red_flags: int = 0
    primary: bool = False

    def __post_init__(self) -> None:
        if self.signal not in SIGNAL_LABELS:
            raise ValueError(f"unknown signal: {self.signal}")
        if not 1 <= self.hold_days <= 5:
            raise ValueError("hold_days must be between 1 and 5")
        _selection_count(self.selection, 100)
        if self.min_price < 0:
            raise ValueError("min_price cannot be negative")
        if self.min_adv < 0:
            raise ValueError("min_adv cannot be negative")
        if self.max_vol20 is not None and self.max_vol20 <= 0:
            raise ValueError("max_vol20 must be positive or None")
        if not 0 <= self.min_momentum_percentile <= 1:
            raise ValueError("min_momentum_percentile must be between 0 and 1")
        if self.max_fundamental_red_flags < 0:
            raise ValueError("max_fundamental_red_flags cannot be negative")

    @property
    def slug(self) -> str:
        cap = "none" if self.max_vol20 is None else f"{self.max_vol20:.2f}"
        slug = (
            f"{self.signal}__h{self.hold_days}__{self.selection}"
            f"__p{self.min_price:g}__adv{self.min_adv:g}__vcap{cap}"
        )
        if self.signal == "momentum_pullback":
            slug += (f"__mom{self.min_momentum_percentile:.2f}"
                     f"__flags{self.max_fundamental_red_flags}")
        return slug


def research_grid() -> list[ResearchConfig]:
    """Fixed primary plus a compact, recorded exploratory grid."""
    configs: list[ResearchConfig] = [
        ResearchConfig("beta_idio_1d", 1, min_price=5, min_adv=5_000_000,
                       max_vol20=.10, primary=True)
    ]
    for signal in (s for s in SIGNAL_LABELS if s != "momentum_pullback"):
        holds = (1, 2, 3, 5) if signal in {"raw_1d", "raw_3d"} else (1, 2, 3, 4, 5)
        configs.extend(ResearchConfig(signal, h) for h in holds)
    configs.extend(ResearchConfig("raw_1d", 1, selection=s) for s in
                   ("pct_1", "pct_5", "pct_10", "top_20"))
    configs.extend([
        ResearchConfig("raw_1d", 1, min_adv=5_000_000),
        ResearchConfig("raw_1d", 1, min_adv=25_000_000),
        ResearchConfig("raw_1d", 1, max_vol20=.10),
        ResearchConfig("gap_down", 1, min_price=5, min_adv=5_000_000, max_vol20=.10),
    ])
    # Preserve order while removing an accidentally duplicated specification.
    return list(dict.fromkeys(configs))


def _selection_count(selection: str, universe_size: int) -> int:
    """Validate a selection rule and return its requested position count."""
    prefix, separator, raw_value = selection.partition("_")
    if separator != "_" or prefix not in {"pct", "top"}:
        raise ValueError("selection must have the form pct_N or top_N")
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("selection N must be a whole number") from exc
    if value <= 0 or (prefix == "pct" and value > 100):
        raise ValueError("selection must use top_N with N > 0 or pct_N with 1 <= N <= 100")
    if prefix == "pct":
        return max(1, int(math.ceil(universe_size * value / 100)))
    return min(universe_size, value)


def side_cost_rate(adv: pd.Series | np.ndarray) -> np.ndarray:
    """Spread + fees + slippage estimate on each side, conditional on ADV."""
    a = np.asarray(adv, dtype=float)
    return np.select(
        [a < 5e6, a < 25e6, a < 100e6],
        [35e-4, 20e-4, 12.5e-4],
        default=7.5e-4,
    )


def adjusted_open(frame: pd.DataFrame) -> pd.Series:
    """Put raw open on closeadj's scale without using a future price."""
    raw_close = pd.to_numeric(frame["close"], errors="coerce")
    adj_close = pd.to_numeric(frame["closeadj"], errors="coerce")
    factor = adj_close.div(raw_close.where(raw_close.ne(0)))
    return pd.to_numeric(frame["open"], errors="coerce") * factor


def select_losers(frame: pd.DataFrame, signal: str, selection: str) -> pd.DataFrame:
    d = frame.dropna(subset=[signal]).sort_values([signal, "ticker"], kind="mergesort")
    if d.empty:
        return d
    n = _selection_count(selection, len(d))
    return d.head(n)


def _candidate_metadata(con: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    exchanges = ("NASDAQ", "NYSE", "NYSEMKT", "NYSEARCA", "BATS")
    ph = ",".join("?" * len(exchanges))
    q = f"""SELECT ticker,firstpricedate,lastpricedate
            FROM tickers
            WHERE category='Domestic Common Stock' AND exchange IN ({ph})
              AND firstpricedate<=?
              AND (lastpricedate IS NULL OR lastpricedate>=?)"""
    out = pd.read_sql_query(q, con, params=[*exchanges, end, start],
                            parse_dates=["firstpricedate", "lastpricedate"])
    return out.drop_duplicates("ticker").reset_index(drop=True)


def _load_window(con: sqlite3.Connection, metadata: pd.DataFrame,
                 core_start: pd.Timestamp, core_end: pd.Timestamp,
                 include_fundamentals: bool = True) -> pd.DataFrame:
    # A 400-calendar-day buffer supplies at least 252 trading observations
    # for the longest medium-term momentum feature.
    lo = core_start - pd.Timedelta(days=400 if include_fundamentals else 130)
    hi = core_end + pd.Timedelta(days=12)
    live = metadata[
        (metadata.firstpricedate <= hi)
        & (metadata.lastpricedate.isna() | (metadata.lastpricedate >= lo))
    ]
    tickers = live.ticker.tolist()
    if not tickers:
        return pd.DataFrame()
    ph = ",".join("?" * len(tickers))
    q = f"""SELECT s.ticker,s.date,s.open,s.close,s.closeadj,s.volume,d.marketcap
            FROM stocks s LEFT JOIN daily d
              ON d.ticker=s.ticker AND d.date=s.date
            WHERE s.ticker IN ({ph}) AND s.date>=? AND s.date<=?
            ORDER BY s.ticker,s.date"""
    chunks = pd.read_sql_query(
        q, con, params=[*tickers, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")],
        parse_dates=["date"], chunksize=400_000,
    )
    parts = []
    for x in chunks:
        for col in ("open", "close", "closeadj", "volume", "marketcap"):
            x[col] = pd.to_numeric(x[col], errors="coerce")
        parts.append(x)
    if not parts:
        return pd.DataFrame()
    panel = pd.concat(parts, ignore_index=True)
    panel = panel.merge(live, on="ticker", how="left", validate="many_to_one")
    if not include_fundamentals:
        return panel

    # This database's fundamentals table identifies the fiscal period but
    # does not retain a precise filing timestamp.  Delay every MRT record by
    # 90 calendar days before making it visible to the strategy.  This is a
    # conservative approximation to public availability and prevents the
    # quarter-end values from being used at quarter end.
    fq = f"""SELECT ticker,date,equity,assets,debt,netinc,fcf
             FROM fundamentals
             WHERE ticker IN ({ph}) AND dimension='MRT'
               AND date>=? AND date<=?"""
    fundamentals = pd.read_sql_query(
        fq, con,
        params=[*tickers, (lo - pd.Timedelta(days=460)).strftime("%Y-%m-%d"),
                core_end.strftime("%Y-%m-%d")],
        parse_dates=["date"],
    )
    return attach_fundamentals(panel, fundamentals)


def attach_fundamentals(prices: pd.DataFrame, fundamentals: pd.DataFrame,
                        reporting_lag_days: int = 90) -> pd.DataFrame:
    """Attach the latest conservatively available trailing fundamentals."""
    renamed = {
        "equity": "fund_equity", "assets": "fund_assets", "debt": "fund_debt",
        "netinc": "fund_netinc", "fcf": "fund_fcf",
    }
    if fundamentals.empty:
        out = prices.copy()
        for column in renamed.values():
            out[column] = np.nan
        out["fundamental_red_flags"] = 0
        out["fundamental_data_available"] = False
        return out

    f = fundamentals.copy()
    for column in renamed:
        f[column] = pd.to_numeric(f[column], errors="coerce")
    f["fundamental_period_date"] = pd.to_datetime(f["date"])
    f["fundamental_available_date"] = (
        f.fundamental_period_date + pd.Timedelta(days=reporting_lag_days)
    )
    f = f.rename(columns=renamed).drop(columns="date")
    out = pd.merge_asof(
        prices.sort_values(["date", "ticker"]),
        f.sort_values(["fundamental_available_date", "ticker"]),
        left_on="date", right_on="fundamental_available_date", by="ticker",
        direction="backward", allow_exact_matches=True,
    ).sort_values(["ticker", "date"]).reset_index(drop=True)

    negative_equity = out.fund_equity.le(0) & out.fund_equity.notna()
    extreme_leverage = (out.fund_debt / out.fund_assets).ge(0.80)
    cash_burn = out.fund_netinc.lt(0) & out.fund_fcf.lt(0)
    out["fundamental_red_flags"] = (
        negative_equity.astype(int) + extreme_leverage.fillna(False).astype(int)
        + cash_burn.astype(int)
    )
    out["fundamental_data_available"] = out[list(renamed.values())].notna().any(axis=1)
    return out


def build_features(panel: pd.DataFrame, market_returns: pd.Series,
                   core_start: pd.Timestamp, core_end: pd.Timestamp) -> pd.DataFrame:
    """Create only contemporaneous signals and forward fields used for scoring."""
    p = panel.sort_values(["ticker", "date"]).copy()
    p["adj_close"] = p.closeadj.fillna(p.close)
    p["adj_open"] = adjusted_open(p).fillna(p.open)
    g = p.groupby("ticker", sort=False)
    p["ret1"] = g.adj_close.pct_change(fill_method=None)
    p["ret3"] = g.adj_close.pct_change(3, fill_method=None)
    p["ret5"] = g.adj_close.pct_change(5, fill_method=None)
    p["gap"] = p.adj_open / g.adj_close.shift(1) - 1
    p["dollar_volume"] = p.close * p.volume
    p["adv20"] = g.dollar_volume.transform(lambda s: s.rolling(20, min_periods=15).median())
    lagged = g.ret1.shift(1)
    p["vol20"] = lagged.groupby(p.ticker).transform(lambda s: s.rolling(20, min_periods=15).std())
    p["vol60"] = lagged.groupby(p.ticker).transform(lambda s: s.rolling(60, min_periods=40).std())
    supplied_market = p.date.map(market_returns)
    lagged_cap = g.marketcap.shift(1)
    valid_market = p.ret1.notna() & lagged_cap.gt(0)
    weighted_numerator = (p.ret1.where(valid_market) * lagged_cap.where(valid_market)).groupby(p.date).sum(min_count=1)
    weighted_denominator = lagged_cap.where(valid_market).groupby(p.date).sum(min_count=1)
    internal_market = weighted_numerator / weighted_denominator
    # SEP contains common stocks but no SPY/ETF history.  The point-in-time,
    # lagged-cap-weighted eligible equity market is therefore both the
    # benchmark and the market return used in residual signals.
    p["mkt_ret"] = supplied_market.fillna(p.date.map(internal_market))

    # Rolling beta uses t-60..t-1 only; today's loss is never allowed to
    # influence its own normal-return estimate.
    y = p.groupby("ticker", sort=False).mkt_ret.shift(1)
    xy = lagged * y
    y2 = y * y
    keys = p.ticker
    roll = lambda s: s.groupby(keys).transform(lambda z: z.rolling(60, min_periods=40).mean())
    mx, my, mxy, my2 = roll(lagged), roll(y), roll(xy), roll(y2)
    denom = my2 - my * my
    p["beta60"] = (mxy - mx * my) / denom.where(denom.abs() > 1e-12)

    p["raw_1d"] = p.ret1
    p["raw_3d"] = p.ret3
    p["idio_1d"] = p.ret1 - p.mkt_ret
    p["beta_idio_1d"] = p.ret1 - p.beta60 * p.mkt_ret
    p["volnorm_20d"] = p.ret1 / p.vol20.where(p.vol20 > 0)
    p["volnorm_60d"] = p.ret1 / p.vol60.where(p.vol60 > 0)
    p["gap_down"] = p.gap
    # Medium-term strength is measured through t-5, so the pullback being
    # bought cannot help a stock qualify as a prior winner.  Requiring all
    # three horizons to be positive captures persistent rather than
    # one-window momentum.
    lag5_close = g.adj_close.shift(5)
    p["mom_3m_skip5"] = lag5_close / g.adj_close.shift(63) - 1
    p["mom_6m_skip5"] = lag5_close / g.adj_close.shift(126) - 1
    p["mom_12m_skip5"] = lag5_close / g.adj_close.shift(252) - 1
    p["momentum_strength"] = p[
        ["mom_3m_skip5", "mom_6m_skip5", "mom_12m_skip5"]
    ].mean(axis=1, skipna=False)
    normalized_pullbacks = pd.concat([
        p.ret1 / p.vol20,
        p.ret3 / (p.vol20 * np.sqrt(3)),
        p.ret5 / (p.vol20 * np.sqrt(5)),
    ], axis=1)
    p["momentum_pullback"] = normalized_pullbacks.min(axis=1, skipna=False)

    for k in range(1, 7):
        p[f"fwd_close_{k}"] = g.adj_close.shift(-k)
        p[f"fwd_open_{k}"] = g.adj_open.shift(-k)
        p[f"fwd_date_{k}"] = g.date.shift(-k)

    age_ok = p.firstpricedate <= p.date - pd.Timedelta(days=365)
    listing_ok = p.lastpricedate.isna() | (p.lastpricedate >= p.date)
    sane = p.ret1.between(-.80, 3.0) & p.adj_close.gt(0) & p.adj_open.gt(0)
    core = p.date.between(core_start, core_end)
    return p[core & age_ok & listing_ok & sane].copy()


def filter_eligible(day: pd.DataFrame, config: ResearchConfig) -> pd.DataFrame:
    """Apply liquidity, risk, and signal-specific eligibility constraints."""
    eligible = day[
        (day.adj_close >= config.min_price) & (day.adv20 >= config.min_adv)
    ]
    if config.max_vol20 is not None:
        eligible = eligible[eligible.vol20 <= config.max_vol20]
    if config.signal == "gap_down":
        eligible = eligible[eligible.gap.between(-.80, 3.0)]
    if config.signal == "momentum_pullback":
        persistent_strength = eligible[
            ["mom_3m_skip5", "mom_6m_skip5", "mom_12m_skip5"]
        ].gt(0).all(axis=1)
        clean = eligible.fundamental_red_flags.le(config.max_fundamental_red_flags)
        eligible = eligible[persistent_strength & clean]
        momentum_rank = eligible.momentum_strength.rank(method="average", pct=True)
        eligible = eligible[momentum_rank >= config.min_momentum_percentile]
    return eligible


def _daily_trade_paths(sel: pd.DataFrame, signal: str, hold: int):
    """Return gross daily trade paths, dates, and delayed-close returns."""
    if signal == "gap_down":
        entry = sel.adj_open.to_numpy(float)
        closes = [sel[f"fwd_close_{k}"].to_numpy(float) if k else sel.adj_close.to_numpy(float)
                  for k in range(hold)]
        dates = [sel[f"fwd_date_{k}"].to_numpy() if k else sel.date.to_numpy()
                 for k in range(hold)]
        delayed_entry = sel.fwd_close_1.to_numpy(float)
        delayed_exit = sel[f"fwd_close_{hold + 1}"].to_numpy(float)
    else:
        # Entry at the signal day's own close (the same close used to form
        # the signal), not the next day's open. This is the one-parameter
        # change under test: it includes the overnight session (signal
        # close -> next open) in the holding period instead of discarding
        # it, isolating whether reversal for extreme single-day losers
        # lives overnight rather than intraday.
        entry = sel.adj_close.to_numpy(float)
        closes = [sel[f"fwd_close_{k}"].to_numpy(float) for k in range(1, hold + 1)]
        dates = [sel[f"fwd_date_{k}"].to_numpy() for k in range(1, hold + 1)]
        delayed_entry = sel.fwd_close_1.to_numpy(float)
        delayed_exit = sel[f"fwd_close_{hold + 1}"].to_numpy(float)
    paths = []
    prev = entry
    for close in closes:
        paths.append(close / prev - 1)
        prev = close
    return paths, dates, delayed_exit / delayed_entry - 1


class Accumulator:
    def __init__(self, config: ResearchConfig):
        self.config = config
        self.net_components = defaultdict(list)
        self.gross_components = defaultdict(list)
        self.n = self.wins = 0
        self.sum_gross = self.sum_net = self.sum_win = self.sum_loss = 0.0
        self.n_win = self.n_loss = 0
        self.sum_cost = self.sum_delayed = 0.0
        self.n_delayed = 0
        self.positive_pnl = 0.0
        self.top_winners: list[float] = []
        self.bucket = defaultdict(lambda: [0, 0.0, 0])
        self.capacity_sum = self.capacity_n = 0

    def add(self, selected: pd.DataFrame) -> None:
        paths, dates, delayed = _daily_trade_paths(selected, self.config.signal,
                                                   self.config.hold_days)
        valid = np.ones(len(selected), dtype=bool)
        for arr in paths:
            valid &= np.isfinite(arr) & (arr >= -.80) & (arr <= 3.0)
        costs = side_cost_rate(selected.adv20.to_numpy(float))
        valid &= np.isfinite(costs)
        if not valid.any():
            return
        paths = [x[valid] for x in paths]
        dates = [x[valid] for x in dates]
        costs = costs[valid]
        chosen = selected.iloc[np.flatnonzero(valid)]
        gross_total = np.prod(1 + np.column_stack(paths), axis=1) - 1
        net_total = (1 + gross_total) * (1 - costs) ** 2 - 1
        self.n += len(net_total)
        self.sum_gross += float(gross_total.sum())
        self.sum_net += float(net_total.sum())
        self.sum_cost += float((gross_total - net_total).sum())
        win = net_total > 0
        self.wins += int(win.sum())
        self.n_win += int(win.sum()); self.sum_win += float(net_total[win].sum())
        self.n_loss += int((~win).sum()); self.sum_loss += float(net_total[~win].sum())
        self.positive_pnl += float(net_total[win].sum())
        for value in net_total[win]:
            if len(self.top_winners) < 20:
                heapq.heappush(self.top_winners, float(value))
            elif value > self.top_winners[0]:
                heapq.heapreplace(self.top_winners, float(value))
        good_delay = np.isfinite(delayed[valid])
        if good_delay.any():
            delayed_net = (1 + delayed[valid][good_delay]) * (1 - costs[good_delay]) ** 2 - 1
            self.sum_delayed += float(delayed_net.sum()); self.n_delayed += len(delayed_net)

        for i, (gross, day) in enumerate(zip(paths, dates)):
            net = gross.copy()
            if i == 0: net -= costs
            if i == len(paths) - 1: net -= costs
            for d, x, y in zip(day, gross, net):
                ts = pd.Timestamp(d)
                self.gross_components[ts].append(float(x))
                self.net_components[ts].append(float(y))

        for label, values in (
            ("liquidity", pd.cut(chosen.adv20, [-np.inf, 5e6, 25e6, 100e6, np.inf],
                                 labels=["<5m", "5-25m", "25-100m", ">=100m"])),
            ("size", chosen.size_bucket),
        ):
            for bucket_name in pd.Series(values).dropna().unique():
                mask = np.asarray(values == bucket_name)
                key = (label, str(bucket_name))
                self.bucket[key][0] += int(mask.sum())
                self.bucket[key][1] += float(net_total[mask].sum())
                self.bucket[key][2] += int((net_total[mask] > 0).sum())
        # At 0.5% ADV per order, account capital can be this large before
        # the average new cohort breaches the participation constraint.
        capacity = .005 * chosen.adv20.to_numpy(float) * len(chosen) * self.config.hold_days
        self.capacity_sum += float(np.nanmean(capacity)); self.capacity_n += 1


def _annualized(series: pd.Series) -> float:
    s = series.dropna()
    if s.empty or (1 + s).le(0).any(): return float("nan")
    return float(np.exp(np.log1p(s).sum() * TRADING_DAYS / len(s)) - 1)


def _sharpe(series: pd.Series) -> float:
    s = series.dropna()
    return float(np.sqrt(TRADING_DAYS) * s.mean() / s.std(ddof=1)) if len(s) > 1 and s.std() else float("nan")


def _max_drawdown(series: pd.Series) -> float:
    wealth = (1 + series.fillna(0)).cumprod()
    return float((wealth / wealth.cummax() - 1).min()) if len(wealth) else float("nan")


def _factor_alpha(strategy: pd.Series, factors: pd.DataFrame) -> tuple[float, float, dict]:
    import statsmodels.api as sm
    columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
    d = pd.concat([strategy.rename("strategy"), factors[[*columns, "RF"]]],
                  axis=1, sort=False).dropna()
    if len(d) < 30: return np.nan, np.nan, {}
    model = sm.OLS(d.strategy - d.RF, sm.add_constant(d[columns])).fit(
        cov_type="HAC", cov_kwds={"maxlags": 5})
    return (float(model.params["const"] * TRADING_DAYS),
            float(model.tvalues["const"]), model.params[columns].to_dict())


def summarize(acc: Accumulator, factors: pd.DataFrame, start: str, end: str) -> tuple[dict, list[dict]]:
    net = pd.Series({d: np.mean(v) for d, v in acc.net_components.items()}).sort_index()
    gross = pd.Series({d: np.mean(v) for d, v in acc.gross_components.items()}).sort_index()
    market = (factors["Mkt-RF"] + factors["RF"]).rename("market")
    bench = market.reindex(net.index).dropna()
    aligned = pd.concat([net, bench], axis=1).dropna()
    alpha, tstat, betas = _factor_alpha(net, factors)
    gross_ann, net_ann, bench_ann = _annualized(gross), _annualized(net), _annualized(bench)
    crash = aligned[aligned.iloc[:, 1] <= -.03].iloc[:, 0]
    cfg = acc.config
    row = {
        "config_id": cfg.slug, "strategy_name": SIGNAL_LABELS[cfg.signal],
        "signal": cfg.signal, "holding_days": cfg.hold_days,
        "selection": cfg.selection, "primary": cfg.primary,
        "universe": "Sharadar domestic common stocks, PIT listings, incl. delisted",
        "sample_start": str(net.index.min().date()) if len(net) else start,
        "sample_end": str(net.index.max().date()) if len(net) else end,
        "number_of_trades": acc.n, "average_holding_period": cfg.hold_days,
        "annualized_return_gross": gross_ann, "benchmark_annualized_return": bench_ann,
        "excess_return_vs_benchmark_net": net_ann - bench_ann,
        "five_factor_alpha_annual": alpha, "alpha_tstat_hac": tstat,
        "market_beta": betas.get("Mkt-RF", np.nan), "smb_beta": betas.get("SMB", np.nan),
        "hml_beta": betas.get("HML", np.nan), "rmw_beta": betas.get("RMW", np.nan),
        "cma_beta": betas.get("CMA", np.nan),
        "sharpe_net": _sharpe(net), "win_rate": acc.wins / acc.n if acc.n else np.nan,
        "average_winner": acc.sum_win / acc.n_win if acc.n_win else np.nan,
        "average_loser": acc.sum_loss / acc.n_loss if acc.n_loss else np.nan,
        "max_drawdown_net": _max_drawdown(net),
        "annual_two_way_turnover": 2 * TRADING_DAYS / cfg.hold_days,
        "estimated_transaction_cost_annual": gross_ann - net_ann,
        "net_return_after_costs": net_ann,
        "capacity_estimate_usd_at_half_pct_adv": acc.capacity_sum / acc.capacity_n if acc.capacity_n else np.nan,
        "long_leg_alpha_annual": alpha,
        "long_leg_positive_meaningful": bool(alpha > 0 and tstat >= 2) if np.isfinite(tstat) else False,
        "next_close_average_trade_net": acc.sum_delayed / acc.n_delayed if acc.n_delayed else np.nan,
        "crash_day_average_net": float(crash.mean()) if len(crash) else np.nan,
        "crash_day_count": int(len(crash)),
        "top_20_winner_share_of_positive_pnl": sum(acc.top_winners) / acc.positive_pnl if acc.positive_pnl else np.nan,
        "minimum_price": cfg.min_price, "minimum_adv": cfg.min_adv,
        "maximum_vol20": cfg.max_vol20,
        "minimum_momentum_percentile": cfg.min_momentum_percentile,
        "maximum_fundamental_red_flags": cfg.max_fundamental_red_flags,
    }
    for label, lo, hi in (("2000_2007", "2000-01-01", "2007-12-31"),
                          ("2008_2015", "2008-01-01", "2015-12-31"),
                          ("2016_2023", "2016-01-01", "2023-12-31"),
                          ("2024_plus", "2024-01-01", "2100-01-01")):
        row[f"net_return_{label}"] = _annualized(net.loc[lo:hi])
    buckets = []
    for (kind, name), (n, total, wins) in sorted(acc.bucket.items()):
        buckets.append({"config_id": cfg.slug, "bucket_type": kind, "bucket": name,
                        "number_of_trades": n, "average_trade_net": total / n,
                        "win_rate": wins / n})
    return row, buckets


def _assign_size_buckets(day: pd.DataFrame) -> pd.Series:
    out = pd.Series(index=day.index, dtype="object")
    good = day.marketcap.dropna().sort_values()
    if len(good) < 30: return out
    ranks = good.rank(method="first", pct=True)
    out.loc[good.index] = pd.cut(ranks, [0, 1/3, 2/3, 1],
                                 labels=["Small", "Mid", "Large"], include_lowest=True).astype(str)
    return out


def net_return_series(acc: "Accumulator") -> pd.Series:
    """Raw daily net-return series underlying one config's summary row --
    the equal-capital-blended average across overlapping cohorts on each
    date. Needed for metrics (Sharpe, Sortino, IC, etc.) that require the
    full series, not just the summary statistics in `summarize()`."""
    return pd.Series({d: np.mean(v) for d, v in acc.net_components.items()}).sort_index()


def run_research(db_path: str | Path, start: str, end: str,
                 configs: list[ResearchConfig] | None = None,
                 accumulators_out: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """`accumulators_out`, if given an empty dict, is populated with
    {config_slug: Accumulator} as a side effect -- lets a caller recover raw
    per-date series (see net_return_series) without changing this
    function's return signature or duplicating the backtest."""
    configs = configs or research_grid()
    accs = {c.slug: Accumulator(c) for c in configs}
    if accumulators_out is not None:
        accumulators_out.update(accs)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    con = sqlite3.connect(str(db_path))
    metadata = _candidate_metadata(con, start, end)
    import pandas_datareader.data as web
    factors = web.DataReader("F-F_Research_Data_5_Factors_2x3_daily", "famafrench",
                             start=start, end=end)[0] / 100
    factors.index = (factors.index.to_timestamp() if isinstance(factors.index, pd.PeriodIndex)
                     else pd.to_datetime(factors.index))
    if factors.index.max() < end_ts:
        raise ValueError(f"factor data ends {factors.index.max().date()}, before requested end {end}")
    market = (factors["Mkt-RF"] + factors["RF"]).rename("market")
    needs_fundamentals = any(c.signal == "momentum_pullback" for c in configs)

    for year in range(start_ts.year, end_ts.year + 1):
        lo = max(start_ts, pd.Timestamp(year=year, month=1, day=1))
        hi = min(end_ts, pd.Timestamp(year=year, month=12, day=31))
        panel = _load_window(con, metadata, lo, hi, include_fundamentals=needs_fundamentals)
        if panel.empty: continue
        features = build_features(panel, market, lo, hi)
        for _, day in features.groupby("date", sort=True):
            day = day.copy()
            day["size_bucket"] = _assign_size_buckets(day)
            selection_cache = {}
            for cfg in configs:
                cache_key = (cfg.signal, cfg.selection, cfg.min_price,
                             cfg.min_adv, cfg.max_vol20,
                             cfg.min_momentum_percentile,
                             cfg.max_fundamental_red_flags)
                selected = selection_cache.get(cache_key)
                if selected is None:
                    eligible = filter_eligible(day, cfg)
                    selected = select_losers(eligible, cfg.signal, cfg.selection)
                    selection_cache[cache_key] = selected
                if len(selected): accs[cfg.slug].add(selected)
    con.close()

    rows, buckets = [], []
    for acc in accs.values():
        row, b = summarize(acc, factors, start, end); rows.append(row); buckets.extend(b)
    return pd.DataFrame(rows), pd.DataFrame(buckets), factors


def classify(primary: pd.Series) -> tuple[str, list[str]]:
    reasons = []
    gates = {
        "net annual return is positive": primary.net_return_after_costs > 0,
        "five-factor alpha is positive": primary.five_factor_alpha_annual > 0,
        "HAC alpha t-stat is at least 2.0": primary.alpha_tstat_hac >= 2,
        "at least 1,000 trades": primary.number_of_trades >= 1000,
        "maximum drawdown is no worse than -35%": primary.max_drawdown_net >= -.35,
        "liquid-universe constraint is active": primary.minimum_adv >= 5e6,
    }
    reasons.extend(("PASS: " if ok else "FAIL: ") + label for label, ok in gates.items())
    if all(gates.values()): return "ACCEPTED / IMPLEMENTABLE", reasons
    if (primary.annualized_return_gross > 0 or primary.five_factor_alpha_annual > 0) and primary.number_of_trades >= 200:
        return "UNRESOLVED", reasons
    return "REJECTED / FALSIFIED", reasons


def write_outputs(summary: pd.DataFrame, buckets: pd.DataFrame, factors: pd.DataFrame,
                  output_root: str | Path, start: str, end: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(summary.to_csv(index=False).encode()).hexdigest()[:10]
    out = Path(output_root) / f"{stamp}_{digest}"
    out.mkdir(parents=True, exist_ok=False)
    summary.to_csv(out / "config_results.csv", index=False)
    buckets.to_csv(out / "bucket_results.csv", index=False)
    factors.to_csv(out / "fama_french_daily_factors.csv")
    primary = summary[summary.primary].iloc[0]
    verdict, gates = classify(primary)
    best = summary.sort_values("net_return_after_costs", ascending=False).head(10)
    registered_id = ResearchConfig(
        DEFAULT_SIGNAL, DEFAULT_HOLD_DAYS, selection=DEFAULT_SELECTION,
        min_price=DEFAULT_MIN_PRICE, min_adv=DEFAULT_MIN_ADV,
        max_vol20=DEFAULT_MAX_VOL20, primary=True,
    ).slug
    result_heading = ("Primary preregistered result"
                      if primary.config_id == registered_id else "Configured result")
    selection_text = (f"worst {int(primary.selection.split('_')[1])}%"
                      if primary.selection.startswith("pct_")
                      else f"worst {int(primary.selection.split('_')[1])} stocks")
    entry_text = "same-open entry" if primary.signal == "gap_down" else "next-open entry"
    vol_text = ("no volatility cap" if pd.isna(primary.maximum_vol20)
                else f"vol cap {primary.maximum_vol20:.1%}")
    momentum_text = (
        f"; positive 3/6/12-month momentum through t-5; top "
        f"{(1 - primary.minimum_momentum_percentile):.0%} momentum; at most "
        f"{int(primary.maximum_fundamental_red_flags)} fundamental red flags"
        if primary.signal == "momentum_pullback" else ""
    )
    if len(summary) > 1:
        grid_note = (
            "The broad grid is exploratory and cannot replace the preregistered primary after results are seen. "
            if primary.config_id == registered_id
            else "The other configurations are parameter-sensitivity checks around the configured primary. "
        )
    else:
        grid_note = ""
    lines = [
        "# Short-Term Mean Reversion Research Report", "",
        f"**Classification: {verdict}**", "",
        f"Run: {stamp}; requested sample {start} through {end}.", "",
        f"## {result_heading}", "",
        f"Signal: {primary.strategy_name}; {selection_text}; {int(primary.holding_days)}-day hold; "
        f"{entry_text}; price >= ${primary.minimum_price:g}; ADV >= ${primary.minimum_adv:,.0f}; "
        f"{vol_text}{momentum_text}.", "",
        f"Trades: {int(primary.number_of_trades):,}; gross annual return: {primary.annualized_return_gross:.2%}; "
        f"net annual return: {primary.net_return_after_costs:.2%}; Fama-French market: {primary.benchmark_annualized_return:.2%}.",
        f"Fama-French five-factor alpha: {primary.five_factor_alpha_annual:.2%} (HAC t={primary.alpha_tstat_hac:.2f}); "
        f"beta: {primary.market_beta:.2f}; Sharpe: {primary.sharpe_net:.2f}; max drawdown: {primary.max_drawdown_net:.2%}.",
        f"Win rate: {primary.win_rate:.2%}; average winner: {primary.average_winner:.2%}; "
        f"average loser: {primary.average_loser:.2%}; estimated annual cost drag: {primary.estimated_transaction_cost_annual:.2%}.",
        f"Capacity estimate (0.5% ADV/order): ${primary.capacity_estimate_usd_at_half_pct_adv:,.0f}; "
        f"next-close average net trade: {primary.next_close_average_trade_net:.3%}.", "",
        "Acceptance gates:", "", *[f"- {x}" for x in gates], "",
        "## Interpretation", "",
        "The reported alpha is the long leg's daily Fama-French five-factor alpha; no short-book spread is used. Costs are charged on entry and exit using ADV tiers. "
        + grid_note
        + "Size/liquidity results are in `bucket_results.csv`.", "",
        "Crash-day performance, delayed next-close execution, winner concentration, turnover, capacity, and all tested configurations are recorded in `config_results.csv`.", "",
        "## Best exploratory configurations by net annual return", "",
        "| Signal | Hold | Selection | Net | Alpha | t-stat | Max DD | Trades |", "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for _, r in best.iterrows():
        lines.append(f"| {r.strategy_name} | {int(r.holding_days)} | {r.selection} | {r.net_return_after_costs:.2%} | {r.five_factor_alpha_annual:.2%} | {r.alpha_tstat_hac:.2f} | {r.max_drawdown_net:.2%} | {int(r.number_of_trades):,} |")
    lines.extend(["", "## Data and limitations", "",
                  "Universe membership is reconstructed from each security's listing interval and includes delisted issues. This is broader than an index universe but relies on Sharadar's ticker history rather than CRSP PERMNO continuity. Raw opens are placed on the adjusted-close scale with the same-day adjustment factor. Market-cap buckets require same-date Sharadar DAILY coverage. Daily Fama-French five-factor returns are saved with the run for reproducibility."])
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    metadata = {"created_utc": stamp, "classification": verdict, "requested_start": start,
                "requested_end": end, "primary_config_id": primary.config_id,
                "primary_parameters": {
                    "signal": primary.signal,
                    "holding_days": int(primary.holding_days),
                    "selection": primary.selection,
                    "minimum_price": float(primary.minimum_price),
                    "minimum_adv": float(primary.minimum_adv),
                    "maximum_vol20": (None if pd.isna(primary.maximum_vol20)
                                      else float(primary.maximum_vol20)),
                    "minimum_momentum_percentile": float(primary.minimum_momentum_percentile),
                    "maximum_fundamental_red_flags": int(primary.maximum_fundamental_red_flags),
                },
                "result_sha256": hashlib.sha256((out / "config_results.csv").read_bytes()).hexdigest()}
    (out / "run_manifest.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return out


def _optional_float(value: str) -> float | None:
    if value.lower() in {"none", "null", "off"}:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number or 'none'") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a configurable long-only short-term mean-reversion backtest."
    )
    parser.add_argument("--db", default="V2/data/sharadar.db")
    parser.add_argument("--start", default="2000-03-01")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--output-root", default="V2/reports/short_term_mean_reversion")
    parser.add_argument("--signal", choices=tuple(SIGNAL_LABELS), default=DEFAULT_SIGNAL)
    parser.add_argument("--hold-days", type=int, choices=range(1, 6), default=DEFAULT_HOLD_DAYS)
    parser.add_argument("--selection", default=DEFAULT_SELECTION,
                        help="Cross-sectional losers to buy: pct_N or top_N (default: pct_2).")
    parser.add_argument("--min-price", type=float, default=DEFAULT_MIN_PRICE)
    parser.add_argument("--min-adv", type=float, default=DEFAULT_MIN_ADV,
                        help="Minimum trailing 20-day median dollar volume.")
    parser.add_argument("--max-vol20", type=_optional_float, default=DEFAULT_MAX_VOL20,
                        help="Maximum prior 20-day daily volatility; use 'none' to disable.")
    parser.add_argument("--min-momentum-percentile", type=float, default=0.70,
                        help="Minimum cross-sectional momentum percentile for momentum_pullback.")
    parser.add_argument("--max-fundamental-red-flags", type=int, default=0,
                        help="Allowed distress flags for momentum_pullback (default: 0).")
    parser.add_argument("--grid", action="store_true",
                        help="Run the original preregistered plus exploratory grid instead of one configuration.")
    return parser


def config_from_args(args: argparse.Namespace) -> ResearchConfig:
    try:
        return ResearchConfig(
            signal=args.signal,
            hold_days=args.hold_days,
            selection=args.selection,
            min_price=args.min_price,
            min_adv=args.min_adv,
            max_vol20=args.max_vol20,
            min_momentum_percentile=args.min_momentum_percentile,
            max_fundamental_red_flags=args.max_fundamental_red_flags,
            primary=True,
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        configs = research_grid() if args.grid else [config_from_args(args)]
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    summary, buckets, factors = run_research(args.db, args.start, args.end, configs)
    out = write_outputs(summary, buckets, factors, args.output_root, args.start, args.end)
    print(out)


if __name__ == "__main__":
    main()
