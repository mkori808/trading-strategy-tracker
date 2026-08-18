"""Synthetic-alpha harness: can this measurement design detect what it seeks?

NOT motivated by suspecting attenuation in the engine. The board is not uniform
-- Scalping at -66.8% CAGR, VWAP Bounce at -45.7%, Turnaround Tuesday moving
+2.26 to -0.52 under pooling. An attenuating engine compresses everything toward
zero; this one produces -37 Sharpes. Correctness is not the open question.

**Power is.** The 4-factor regression of the frozen config returned a minimum
detectable alpha of 12.00%/yr at n=58 months. No plausible real edge is that
large, so that test could never have found one regardless of what was there.
That is a property of the DESIGN -- positions, rebalance frequency, sample
length, cross-correlation of the universe -- and it is knowable BEFORE running
anything.

This harness makes it knowable in advance:

    1. CORRECTNESS -- inject a known alpha into synthetic series and check the
       pipeline recovers it. Guards against the harness itself being wrong,
       which is the failure this project has hit repeatedly.
    2. POWER -- sweep injected alpha x sample length and find where recovery
       becomes detectable at t=2. That grid is the reusable artifact.

Pre-registered before running (recorded here, not after):

    * Recovered alpha should track injected alpha with slope ~1. A slope far
      below 1 means attenuation and would be a correctness finding.
    * Detection should require roughly alpha > 2 * SE, with SE shrinking as
      sqrt(sample length). So a 1%/yr alpha should be undetectable at every
      length tested, and 8%/yr should become detectable somewhere between 2y
      and 10y.
    * If even 8%/yr at 10y is undetectable, the design is unusable for any
      edge worth trading and that is the headline.

Synthetic series carry a factor structure (common market component plus
idiosyncratic noise) so the cross-correlation that destroys breadth is present
rather than assumed away -- independent series would overstate power badly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

from engine.sanity import check_return

TRADING_DAYS = 252


@dataclass
class PowerPoint:
    injected_alpha_pct: float
    years: float
    n_months: int
    recovered_alpha_pct: float
    t_stat: float
    detected: bool
    se_pct: float


def synthetic_panel(
    n_symbols: int,
    years: float,
    injected_alpha_annual: float,
    market_vol: float = 0.16,
    idio_vol: float = 0.22,
    beta: float = 1.0,
    seed: int = 0,
) -> tuple[pd.DataFrame, pd.Series]:
    """Daily price panel with a common market factor plus idiosyncratic noise.

    The alpha is injected into a KNOWN SUBSET (the first n_symbols // 5 names),
    so a ranking rule that finds them earns it and a rule that does not, does
    not. Injecting into every name would make alpha unearnable by selection --
    it would just lift the whole universe and the benchmark with it.

    Market factor included deliberately: cross-correlation is what collapses
    effective breadth, and a panel of independent series would report far more
    statistical power than any real universe provides.
    """
    rng = np.random.default_rng(seed)
    n_days = int(years * TRADING_DAYS)
    dates = pd.bdate_range("2015-01-01", periods=n_days)

    market = rng.normal(0.0004, market_vol / np.sqrt(TRADING_DAYS), n_days)
    alpha_daily = (1 + injected_alpha_annual) ** (1 / TRADING_DAYS) - 1
    n_alpha = max(1, n_symbols // 5)

    cols = {}
    for i in range(n_symbols):
        idio = rng.normal(0.0, idio_vol / np.sqrt(TRADING_DAYS), n_days)
        ret = beta * market + idio + (alpha_daily if i < n_alpha else 0.0)
        cols[f"S{i:02d}"] = 100 * np.cumprod(1 + ret)
    return pd.DataFrame(cols, index=dates), pd.Series(market, index=dates)


def measure(
    prices: pd.DataFrame,
    market: pd.Series,
    top_n: int,
    rebalance: str = "ME",
) -> tuple[float, float, float, int]:
    """Run a momentum rule on the panel and regress its excess return on market.

    Returns (recovered annual alpha %, t-stat, SE %, n months). Deliberately a
    simple single-factor regression: the synthetic panel HAS one factor by
    construction, so adding more would fit noise.
    """
    monthly_px = prices.resample(rebalance).last()
    rets = monthly_px.pct_change()
    # 9-month lookback momentum, matching the frozen config's ~189 trading days.
    signal = monthly_px.pct_change(9)

    port = []
    for i in range(len(monthly_px)):
        if i == 0 or signal.iloc[i - 1].isna().all():
            port.append(np.nan)
            continue
        picks = signal.iloc[i - 1].nlargest(top_n).index
        port.append(float(rets.iloc[i][picks].mean()))
    strat = pd.Series(port, index=monthly_px.index).dropna()

    mkt_monthly = (1 + market).resample(rebalance).prod() - 1
    df = pd.concat([strat.rename("r"), mkt_monthly.rename("mkt")], axis=1).dropna()
    if len(df) < 12:
        return float("nan"), float("nan"), float("nan"), len(df)

    model = sm.OLS(df["r"], sm.add_constant(df[["mkt"]])).fit()
    a_m = float(model.params["const"])
    se_m = float(model.bse["const"])
    return (
        ((1 + a_m) ** 12 - 1) * 100,
        float(model.tvalues["const"]),
        ((1 + se_m) ** 12 - 1) * 100,
        len(df),
    )


def power_curve(
    alphas=(0.01, 0.02, 0.04, 0.08),
    lengths=(2.0, 5.0, 10.0),
    n_symbols: int = 29,
    top_n: int = 5,
    trials: int = 12,
) -> list[PowerPoint]:
    """Sweep injected alpha x sample length. The reusable artifact.

    Averaged over `trials` seeds because a single draw's t-stat is itself noisy
    -- reporting one seed would be measuring a random number and calling it
    power.
    """
    points: list[PowerPoint] = []
    for years in lengths:
        for alpha in alphas:
            recs, ts, ses, ns = [], [], [], []
            for seed in range(trials):
                prices, market = synthetic_panel(n_symbols, years, alpha, seed=seed)
                rec, t, se, n = measure(prices, market, top_n)
                if np.isfinite(rec):
                    recs.append(rec); ts.append(t); ses.append(se); ns.append(n)
            if not recs:
                continue
            mean_t = float(np.mean(ts))
            points.append(PowerPoint(
                injected_alpha_pct=alpha * 100,
                years=years,
                n_months=int(np.mean(ns)),
                recovered_alpha_pct=float(np.mean(recs)),
                t_stat=mean_t,
                detected=mean_t > 2.0,
                se_pct=float(np.mean(ses)),
            ))
    return points


def minimum_detectable_alpha(points: list[PowerPoint], years: float) -> float | None:
    """Smallest injected alpha that reached t=2 at this sample length."""
    hits = [p.injected_alpha_pct for p in points if p.years == years and p.detected]
    return min(hits) if hits else None


def main() -> int:
    points = power_curve()
    print("SYNTHETIC-ALPHA POWER CURVE -- 29 symbols, top 5, monthly")
    print("Panel carries a common market factor, so cross-correlation is present.\n")
    header = f"{'years':>6} {'injected':>9} {'recovered':>10} {'SE':>8} {'t':>7}  detected"
    print(header); print("-" * len(header))
    for p in points:
        flag = "YES" if p.detected else "no"
        print(f"{p.years:6.0f} {p.injected_alpha_pct:8.0f}% {p.recovered_alpha_pct:9.2f}% "
              f"{p.se_pct:7.2f}% {p.t_stat:7.2f}  {flag}")

    print("\n--- correctness: recovered vs injected ---")
    inj = np.array([p.injected_alpha_pct for p in points])
    rec = np.array([p.recovered_alpha_pct for p in points])
    slope = float(np.polyfit(inj, rec, 1)[0])
    print(f"   slope {slope:.2f} (1.0 = unbiased recovery, <<1 would mean attenuation)")

    print("\n--- power: minimum detectable alpha by sample length ---")
    for years in sorted({p.years for p in points}):
        mda = minimum_detectable_alpha(points, years)
        print(f"   {years:.0f}y: " + (f"{mda:.0f}%/yr" if mda else "NOT DETECTABLE at any tested alpha"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- TASK 4: standing pre-run breadth gate ---------------------------------

def independent_bets_per_year(
    positions: int, rebalances_per_year: int, avg_pairwise_corr: float = 0.5
) -> float:
    """Effective independent bets per year, after a cross-correlation haircut.

    Nominal bets (positions x rebalances) massively overstates breadth when the
    universe co-moves. The haircut sqrt(1 / (1 + (n-1) * rho)) is the standard
    correction for averaging correlated signals: at rho=0.5 across 5 mega-cap
    positions it cuts effective breadth by roughly 2/3.

    Dow constituents are highly correlated, so 0.5 is a deliberately generous
    default -- a lower assumed correlation would flatter every design that runs
    through this gate.
    """
    if positions < 1 or rebalances_per_year < 1:
        return 0.0
    haircut = (1.0 / (1.0 + (positions - 1) * avg_pairwise_corr)) ** 0.5
    return positions * rebalances_per_year * haircut


def screen_design(
    label: str,
    positions: int,
    rebalances_per_year: int,
    years: float,
    tradable_alpha_pct: float,
    avg_pairwise_corr: float = 0.5,
) -> dict:
    """Would this design detect an alpha worth trading? Answer BEFORE running.

    `tradable_alpha_pct` is the smallest annual alpha the operator would act on.
    If the minimum detectable alpha exceeds it, the design cannot answer its own
    question and running it produces a number that cannot be interpreted in
    either direction -- exactly what happened to Dual Momentum, whose MDA was
    12.00%/yr against a claimed effect of ~2%/yr.

    MDA is scaled from the measured Dual Momentum anchor (MDA 12.00%/yr at 5
    positions, 12 rebalances/yr, 58 months) rather than assumed: SE falls with
    sqrt(bets x years), so MDA scales inversely with that.
    """
    anchor_bets = independent_bets_per_year(5, 12, avg_pairwise_corr)
    anchor_mda, anchor_years = 12.00, 58 / 12

    bets = independent_bets_per_year(positions, rebalances_per_year, avg_pairwise_corr)
    if bets <= 0 or years <= 0:
        return {"label": label, "viable": False, "mda_pct": float("inf")}
    scale = ((anchor_bets * anchor_years) / (bets * years)) ** 0.5
    mda = anchor_mda * scale
    return {
        "label": label,
        "positions": positions,
        "rebalances_per_year": rebalances_per_year,
        "years": years,
        "independent_bets_per_year": bets,
        "mda_pct": mda,
        "tradable_alpha_pct": tradable_alpha_pct,
        "viable": mda <= tradable_alpha_pct,
    }
