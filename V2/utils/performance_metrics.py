"""Strategy-agnostic performance metrics.

Every function takes a return series (one observation per rebalance/trading
period, NaN-safe) plus an explicit `periods_per_year` -- 12 for the monthly
quintile strategies (accruals, net_share_issuance, quality), 252 for the
daily short_term_mean_reversion strategy. No function assumes a frequency
implicitly, so the same code applies to any strategy in this project without
silently mixing monthly and daily conventions.

`full_report()` is the single entry point: pass a strategy's return series
plus its factor-return frame and get every metric back in one dict.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def cagr(returns: pd.Series, periods_per_year: int) -> float:
    """Compound annual growth rate -- how much money the strategy made,
    annualized, assuming profits are reinvested."""
    r = returns.dropna()
    if r.empty or (1 + r).le(0).any():
        return float('nan')
    growth = float((1 + r).prod())
    n_years = len(r) / periods_per_year
    if n_years <= 0 or growth <= 0:
        return float('nan')
    return growth ** (1 / n_years) - 1


def sharpe_ratio(returns: pd.Series, periods_per_year: int, rf: pd.Series | float = 0.0) -> float:
    """Return earned per unit of total volatility (upside and downside both
    penalized equally)."""
    r = returns.dropna()
    if isinstance(rf, pd.Series):
        excess = (r - rf.reindex(r.index)).dropna()
    else:
        excess = r - rf
    if len(excess) < 2 or excess.std(ddof=1) == 0 or pd.isna(excess.std(ddof=1)):
        return float('nan')
    return float(np.sqrt(periods_per_year) * excess.mean() / excess.std(ddof=1))


def sortino_ratio(returns: pd.Series, periods_per_year: int, mar: float = 0.0) -> float:
    """Like Sharpe, but only downside deviations below a minimum acceptable
    return (mar) count against the strategy -- upside volatility isn't
    penalized."""
    r = returns.dropna()
    downside = (r[r < mar] - mar)
    if downside.empty:
        return float('inf') if len(r) else float('nan')
    downside_dev = np.sqrt((downside ** 2).mean())
    if downside_dev == 0 or pd.isna(downside_dev):
        return float('nan')
    return float(np.sqrt(periods_per_year) * (r.mean() - mar) / downside_dev)


def max_drawdown(returns: pd.Series) -> float:
    """Worst peak-to-trough decline in cumulative wealth."""
    r = returns.dropna()
    if r.empty:
        return float('nan')
    wealth = (1 + r.fillna(0)).cumprod()
    dd = wealth / wealth.cummax() - 1
    return float(dd.min())


def calmar_ratio(returns: pd.Series, periods_per_year: int) -> float:
    """CAGR divided by the worst drawdown -- risk-adjusted return using
    drawdown, not volatility, as the risk measure."""
    c = cagr(returns, periods_per_year)
    mdd = max_drawdown(returns)
    if pd.isna(c) or pd.isna(mdd) or mdd == 0:
        return float('nan')
    return c / abs(mdd)


def omega_ratio(returns: pd.Series, mar: float = 0.0) -> float:
    """Ratio of total gains above a minimum acceptable return to total
    losses below it -- how much upside per unit of downside."""
    r = returns.dropna()
    if r.empty:
        return float('nan')
    gains = float((r[r > mar] - mar).sum())
    losses = float((mar - r[r <= mar]).sum())
    if losses == 0:
        return float('inf') if gains > 0 else float('nan')
    return gains / losses


def win_rate(returns: pd.Series) -> float:
    """Fraction of periods with a positive return."""
    r = returns.dropna()
    return float((r > 0).mean()) if len(r) else float('nan')


def skewness(returns: pd.Series) -> float:
    """Which tail is longer -- positive means big upside outliers dominate,
    negative means big downside outliers dominate."""
    r = returns.dropna()
    return float(r.skew()) if len(r) >= 3 else float('nan')


def excess_kurtosis(returns: pd.Series) -> float:
    """How fat the tails are relative to a normal distribution (0 = normal;
    pandas' .kurt() is already excess, not raw, kurtosis)."""
    r = returns.dropna()
    return float(r.kurt()) if len(r) >= 4 else float('nan')


def recovery_factor(returns: pd.Series) -> float:
    """Total return produced relative to the worst drawdown suffered along
    the way."""
    r = returns.dropna()
    if r.empty or (1 + r).le(0).any():
        return float('nan')
    total_return = float((1 + r).prod() - 1)
    mdd = max_drawdown(returns)
    if pd.isna(mdd) or mdd == 0:
        return float('nan')
    return total_return / abs(mdd)


def alpha_tstat_ir(returns: pd.Series, factors: pd.DataFrame, periods_per_year: int, is_long_short: bool = False) -> dict:
    """Fama-French five-factor alpha, its t-stat, and the Information Ratio.

    Alpha: the return left over after controlling for Mkt-RF/SMB/HML/RMW/CMA
    -- the part that isn't just riding a known risk factor.
    t-stat: alpha's size relative to its own estimation uncertainty (>=2 is
    the conventional bar for "probably not noise").
    IR here = annualized alpha / annualized residual volatility from the
    same regression -- how consistently the strategy beat what the factor
    model would have predicted, distinct from Sharpe (which uses total
    return volatility, not residual-to-the-model volatility).

    A long-only leg regresses excess return (r - RF); a zero-cost long-short
    spread regresses the raw return, since it ties up no capital and has no
    risk-free hurdle to net out (matches the convention already used in
    research_utils.factor_regression for the quintile-spread signals).
    """
    import statsmodels.api as sm
    cols = ['Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA']
    aligned = pd.concat([returns.rename('r'), factors[[*cols, 'RF']]], axis=1, join='inner').dropna()
    if len(aligned) < 10:
        return {'alpha_annual': float('nan'), 'tstat': float('nan'), 'ir': float('nan'), 'r_squared': float('nan'), 'betas': {}}
    dependent = aligned.r if is_long_short else aligned.r - aligned.RF
    model = sm.OLS(dependent, sm.add_constant(aligned[cols])).fit()
    resid_std = float(model.resid.std(ddof=1))
    alpha_period = float(model.params['const'])
    alpha_annual = alpha_period * periods_per_year if is_long_short else (1 + alpha_period) ** periods_per_year - 1
    ir = (alpha_period / resid_std) * np.sqrt(periods_per_year) if resid_std > 0 else float('nan')
    return {
        'alpha_annual': alpha_annual, 'tstat': float(model.tvalues['const']), 'ir': float(ir),
        'r_squared': float(model.rsquared), 'betas': model.params[cols].to_dict(),
    }


def information_coefficient(signal: pd.Series, forward_return: pd.Series, method: str = 'spearman') -> float:
    """Rank correlation between a signal and the forward return it's meant
    to predict -- whether the signal itself is predictive, independent of
    how it later gets turned into a portfolio. Pass pooled (all
    security-months stacked) or per-period series; for a per-period IC time
    series, call this once per period and aggregate separately."""
    aligned = pd.concat([signal.rename('signal'), forward_return.rename('fwd')], axis=1).dropna()
    if len(aligned) < 5:
        return float('nan')
    return float(aligned['signal'].corr(aligned['fwd'], method=method))


def full_report(returns: pd.Series, factors: pd.DataFrame, periods_per_year: int, is_long_short: bool = False,
                signal: pd.Series | None = None, forward_return: pd.Series | None = None, mar: float = 0.0) -> dict:
    """Every metric in one call. `signal`/`forward_return` are optional --
    pass pooled per-security-period series to also get the Information
    Coefficient; omit them to get everything else."""
    out = {
        'cagr': cagr(returns, periods_per_year),
        'sharpe': sharpe_ratio(returns, periods_per_year),
        'sortino': sortino_ratio(returns, periods_per_year, mar=mar),
        'max_drawdown': max_drawdown(returns),
        'calmar': calmar_ratio(returns, periods_per_year),
        'omega': omega_ratio(returns, mar=mar),
        'win_rate': win_rate(returns),
        'skew': skewness(returns),
        'excess_kurtosis': excess_kurtosis(returns),
        'recovery_factor': recovery_factor(returns),
    }
    af = alpha_tstat_ir(returns, factors, periods_per_year, is_long_short=is_long_short)
    out['alpha_annual'] = af['alpha_annual']
    out['alpha_tstat'] = af['tstat']
    out['information_ratio'] = af['ir']
    out['r_squared'] = af['r_squared']
    out['factor_betas'] = af['betas']
    out['information_coefficient'] = (
        information_coefficient(signal, forward_return) if signal is not None and forward_return is not None
        else float('nan')
    )
    return out


def format_report(name: str, report: dict, periods_per_year: int) -> str:
    """Render a full_report() dict in a consistent, readable block."""
    def pct(x): return f"{x:.2%}" if pd.notna(x) and np.isfinite(x) else "N/A"
    def num(x): return f"{x:.2f}" if pd.notna(x) and np.isfinite(x) else "N/A"
    freq = "monthly" if periods_per_year == 12 else ("daily" if periods_per_year == 252 else f"{periods_per_year}/yr")
    L = [f"{name} -- Performance Metrics ({freq})", '=' * 60,
         f"CAGR:                    {pct(report['cagr'])}",
         f"Alpha (annualized):      {pct(report['alpha_annual'])}",
         f"Alpha t-stat:            {num(report['alpha_tstat'])}",
         f"Information Ratio:       {num(report['information_ratio'])}",
         f"Information Coefficient: {num(report['information_coefficient'])}",
         f"R-squared:               {num(report['r_squared'])}",
         "",
         f"Sharpe:                  {num(report['sharpe'])}",
         f"Sortino:                 {num(report['sortino'])}",
         f"Calmar:                  {num(report['calmar'])}",
         f"Omega:                   {num(report['omega'])}",
         "",
         f"Max drawdown:            {pct(report['max_drawdown'])}",
         f"Recovery factor:         {num(report['recovery_factor'])}",
         f"Win rate:                {pct(report['win_rate'])}",
         f"Skew:                    {num(report['skew'])}",
         f"Excess kurtosis:         {num(report['excess_kurtosis'])}",
         ]
    if report.get('factor_betas'):
        L.append("")
        L.append("Factor betas:")
        for k, v in report['factor_betas'].items():
            L.append(f"  {k}: {v:.2f}")
    return '\n'.join(L)
