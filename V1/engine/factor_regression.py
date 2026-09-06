"""Fama-French 3-factor + momentum (UMD) regression of the frozen strategy.

THE DECISIVE TEST. Every prior control in this research asked "is the ranking
contribution real?" and the answer converged on yes-but-small. This asks a
different and more damaging question: **is it anything other than a documented
risk premium already purchasable in ETF form?**

Cross-sectional momentum (UMD) is one of the most published factors in finance.
If the strategy's alpha collapses once UMD is included, the Dow result is not an
edge -- it is momentum exposure, obtainable with more capacity, more
diversification and less single-name risk than a 5-name book. It would also
explain the cross-universe failure without any new hypothesis: UMD loading
differs by universe, so a rule tuned on one universe's momentum exposure need
not transfer.

Pre-registered before running (see FROZEN_DUAL_MOMENTUM.md):

    alpha survives, t > 2 after UMD  -> claim strengthens materially
    alpha collapses                  -> research complete; "this is momentum,
                                        buy the factor"
    alpha positive but t < 2         -> consistent with the small effective
                                        sample already documented. NOT a rescue.

Also reports the Sharpe standard error, which is a cleaner statement of the
sample limitation than anything else in the file: at S=0.81 over 5 years,
SE ~ sqrt((1 + S^2/2)/T_years) ~ 0.52, so 0.81 is not statistically
distinguishable from SPY's ~0.55.

Factors come from Ken French's data library (monthly, percent units).
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from datetime import date

import numpy as np
import pandas as pd
import statsmodels.api as sm

from engine.sanity import check_return, check_sharpe

FF3_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_CSV.zip"
)
MOM_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Momentum_Factor_CSV.zip"
)


def _french_monthly(url: str) -> pd.DataFrame:
    """Monthly factor table from a French-library zip, in DECIMAL units.

    The files carry an annual section appended after the monthly one, separated
    by a blank line, plus a multi-line header. Parsed by locating the first row
    whose index is a 6-digit YYYYMM and stopping at the first that is not --
    reading the whole file would silently splice annual rows into a monthly
    series, which would look like data rather than an error.
    """
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read()
    name = zipfile.ZipFile(io.BytesIO(raw)).namelist()[0]
    text = zipfile.ZipFile(io.BytesIO(raw)).read(name).decode("latin-1")

    rows, header = [], None
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if header is None:
            if len(parts) > 1 and parts[0] == "" and any(parts[1:]):
                header = [p for p in parts[1:] if p]
            continue
        if not parts or not parts[0].isdigit() or len(parts[0]) != 6:
            if rows:
                break  # monthly section ended; annual section follows
            continue
        rows.append([parts[0]] + [float(x) for x in parts[1 : len(header) + 1]])

    frame = pd.DataFrame(rows, columns=["ym"] + header)
    frame["date"] = pd.to_datetime(frame["ym"], format="%Y%m") + pd.offsets.MonthEnd(0)
    frame = frame.set_index("date").drop(columns=["ym"])
    return frame / 100.0  # French publishes percent


def load_factors() -> pd.DataFrame:
    """MKT-RF, SMB, HML, RF and UMD aligned on month end."""
    ff3 = _french_monthly(FF3_URL)
    mom = _french_monthly(MOM_URL)
    mom.columns = ["UMD"] * len(mom.columns) if len(mom.columns) == 1 else mom.columns
    joined = ff3.join(mom, how="inner")
    joined.columns = [c.replace("Mkt-RF", "MKT").strip() for c in joined.columns]
    return joined


def monthly_returns(equity: pd.Series) -> pd.Series:
    """Month-end returns from a daily equity curve."""
    monthly = equity.groupby(equity.index.normalize()).last()
    monthly.index = pd.DatetimeIndex(monthly.index).tz_localize(None)
    return monthly.resample("ME").last().pct_change().dropna()


def sharpe_standard_error(sharpe: float, years: float) -> float:
    """SE of an ANNUALIZED Sharpe (Lo 2002), iid approximation.

        SE(S) ~ sqrt((1 + S^2 / 2) / T_years)

    Reported because a point estimate without it invites treating 0.81 as
    meaningfully above 0.55 when the interval covering both is wider than the
    difference.
    """
    return float(np.sqrt((1.0 + 0.5 * sharpe**2) / years))


def regress(strategy_monthly: pd.Series, factors: pd.DataFrame) -> dict:
    """OLS of strategy EXCESS return on MKT, SMB, HML, UMD.

    Excess over the risk-free leg of the same factor dataset, not a separately
    sourced rate -- mixing rf sources would put the intercept on a different
    basis from the factors it is regressed against.
    """
    df = pd.concat([strategy_monthly.rename("ret"), factors], axis=1).dropna()
    y = df["ret"] - df["RF"]
    x = sm.add_constant(df[["MKT", "SMB", "HML", "UMD"]])
    model = sm.OLS(y, x).fit()

    alpha_monthly = float(model.params["const"])
    return {
        "n_months": int(len(df)),
        "alpha_monthly_pct": alpha_monthly * 100,
        "alpha_annual_pct": ((1 + alpha_monthly) ** 12 - 1) * 100,
        "alpha_t": float(model.tvalues["const"]),
        "alpha_p": float(model.pvalues["const"]),
        "loadings": {k: float(model.params[k]) for k in ("MKT", "SMB", "HML", "UMD")},
        "t_stats": {k: float(model.tvalues[k]) for k in ("MKT", "SMB", "HML", "UMD")},
        "r_squared": float(model.rsquared),
        "model": model,
    }


def main() -> int:
    from engine import data as data_module
    from engine.cross_sectional import run_cross_sectional_backtest
    from engine.universe import EQUITY_UNIVERSE, SECTOR_BENCHMARK, daily_date_range
    from strategies.swing.dual_momentum import DualMomentum

    start, end = daily_date_range()
    rf = data_module.risk_free_rate(start, end)
    spreads = {s: data_module.estimate_spread(s, start, end) for s in EQUITY_UNIVERSE}

    strategy = DualMomentum(risk_free_rate=rf)  # FROZEN config, untouched
    result = run_cross_sectional_backtest(
        "Dual Momentum", strategy, EQUITY_UNIVERSE, start, end,
        risk_free_rate=rf, rebalance_frequency="monthly",
        spread_by_symbol=spreads, commission_bps=0.0,
    )
    check_return(result.return_pct, label="frozen strategy window return")
    check_sharpe(result.sharpe, label="frozen strategy Sharpe")

    factors = load_factors()
    out = regress(monthly_returns(result.equity_curve), factors)

    years = (result.end - result.start).days / 365.25
    se = sharpe_standard_error(result.sharpe, years)

    print("FROZEN CONFIG -- top_n=5, lookback=189, monthly. Unchanged.\n")
    print(f"window {result.start} -> {result.end}   return {result.return_pct:.1f}%   "
          f"Sharpe {result.sharpe:.3f}")
    print(f"\nSharpe standard error (Lo 2002, {years:.1f}y): +/- {se:.3f}")
    print(f"   95% CI: [{result.sharpe - 1.96*se:+.2f}, {result.sharpe + 1.96*se:+.2f}]")
    print(f"   SPY's ~0.55 is {'INSIDE' if abs(0.55 - result.sharpe) < 1.96*se else 'OUTSIDE'} that interval")

    print(f"\n--- 4-factor regression (MKT, SMB, HML, UMD), n={out['n_months']} months ---")
    print(f"   alpha  {out['alpha_monthly_pct']:+.3f}%/mo   "
          f"({out['alpha_annual_pct']:+.2f}%/yr)")
    print(f"   t-stat {out['alpha_t']:+.3f}    p = {out['alpha_p']:.4f}")
    print(f"   R^2    {out['r_squared']:.3f}")
    print("\n   loadings:")
    for k in ("MKT", "SMB", "HML", "UMD"):
        print(f"      {k:4} {out['loadings'][k]:+.3f}   (t {out['t_stats'][k]:+.2f})")

    print("\n--- PRE-REGISTERED VERDICT ---")
    if out["alpha_t"] > 2:
        print("   alpha SURVIVES with t > 2 -> the claim strengthens materially.")
    elif out["alpha_annual_pct"] <= 0 or out["alpha_t"] <= 0:
        print("   alpha COLLAPSES -> research complete. This is momentum; buy the factor.")
    else:
        print("   alpha positive but t < 2 -> consistent with the small effective")
        print("   sample already documented. NOT a rescue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
