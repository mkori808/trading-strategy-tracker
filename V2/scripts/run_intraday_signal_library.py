"""Orchestrates the intraday signal library characterization exercise and
prints the report. See V2/strategies/intraday_signal_library.py for the
computation; this script only runs it end-to-end and formats output."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from strategies.intraday_signal_library import (
    run, earnings_subgroup, vwap_convention_compare, overnight_intraday_check,
    correlation_with_daily_library,
)

MECHANISM = {
    'S1_orb_30': 'Opening-range breakout (30min) -- close relative to the first-30-minute high/low, in ATR units.',
    'S1b_orb_60': 'Opening-range breakout (60min) -- close relative to the first-60-minute high/low, in ATR units.',
    'S2_vwap_position': 'Close relative to session VWAP -- proxy for whether the close is rich/cheap vs the day\'s volume-weighted average trade price.',
    'S3_gap_fade': 'Overnight downward gap size -- bets the gap partially reverses intraday/next-day.',
    'S4_gap_and_go': 'Overnight upward gap size -- bets the gap continues (information-driven momentum).',
    'S5_pivot_reversal': 'Open price relative to the prior-day classic pivot support level.',
    'S6_intraday_reversal': 'First-30-minute return -- bets morning losers recover into the afternoon.',
}
TRADABILITY = {
    'S1_orb_30': 'INTRADAY', 'S1b_orb_60': 'INTRADAY', 'S2_vwap_position': 'INTRADAY',
    'S3_gap_fade': 'END-OF-DAY', 'S4_gap_and_go': 'END-OF-DAY', 'S5_pivot_reversal': 'END-OF-DAY',
    'S6_intraday_reversal': 'INTRADAY',
}
HLABEL = {'intraday': 'Intraday', 1: '1-day', 5: '5-day', 20: '20-day'}


TAUTOLOGICAL_H1 = {'S1_orb_30', 'S1b_orb_60', 'S2_vwap_position', 'S6_intraday_reversal'}


def fmt_pct(x):
    return f'{x * 100:6.1f}%/yr' if pd.notna(x) else '   n/a    '


def fmt_daily_bp(x):
    return f'{x * 1e4:7.1f}bp/day' if pd.notna(x) else '     n/a    '


def fmt_t(x):
    return f'{x:6.2f}' if pd.notna(x) else '  n/a '


def primary_factor(betas: dict) -> str:
    if not betas:
        return 'n/a'
    name = max(betas, key=lambda k: abs(betas[k]))
    return f'{name} ({betas[name]:+.2f})'


def print_signal_report(sname: str, res: dict):
    print(f'\nSIGNAL: {sname}')
    print(f'Tradability: {TRADABILITY.get(sname, "?")}')
    print(f'Mechanism: {MECHANISM.get(sname, "")}')
    print('-' * 74)
    print(f'{"Horizon":<12}{"Q1-Q5 Spread":<14}{"t-stat":<9}{"Primary factor":<20}')
    h = res['horizons']
    row = h['intraday']
    if sname in TAUTOLOGICAL_H1:
        print(f'{"Intraday":<12}{fmt_daily_bp(row["spread_daily_mean"]):<14}{fmt_t(row["spread_tstat"]):<9}{"n/a (concurrent, not fwd)":<20}')
    else:
        print(f'{"Intraday":<12}{fmt_pct(row["spread_ann"]):<14}{fmt_t(row["spread_tstat"]):<9}{"n/a (no factor reg)":<20}')
    for hz in (1, 5, 20):
        row = h[hz]
        betas = row.get('spread_betas', {})
        print(f'{HLABEL[hz]:<12}{fmt_pct(row["spread_ann"]):<14}{fmt_t(row["spread_tstat"]):<9}{primary_factor(betas):<20}')

    reg = res['ff5_regression']
    print('\nFactor regression (FF5, daily 1-day spread series):')
    alpha_ann = (1 + reg['alpha_daily']) ** 252 - 1 if pd.notna(reg['alpha_daily']) else np.nan
    print(f'  Alpha(ann)={fmt_pct(alpha_ann)}  t={fmt_t(reg["tstat"])}  R2={reg["r_squared"]:.3f}' if pd.notna(reg['r_squared']) else '  n/a (insufficient overlap)')
    if reg['betas']:
        betas_str = '  '.join(f'{k}={v:+.2f}' for k, v in reg['betas'].items())
        print(f'  {betas_str}')
    print('  Note: factor regression not applicable to horizon H1 (intraday) -- raw spread only.')
    if sname in TAUTOLOGICAL_H1:
        print('  CAVEAT: this signal\'s score is partly defined from the SAME day\'s close/VWAP/'
              '\n          morning-return, so the "Intraday" row above measures a concurrent'
              '\n          relationship, not a forward-predictive one (it is ~always same-sign'
              '\n          across days -- see report notes). Only H2-H4 are genuine forward tests'
              '\n          for this signal; H1 is not tradable as scored (score needs the close).')

    print(f'\n  Avg eligible names/day: {res["avg_names_per_day"]:.0f}' if pd.notna(res['avg_names_per_day']) else '')


def main():
    print('Building daily summary, joining Sharadar, applying filters...')
    out = run('data/alpaca_intraday.db', 'data/sharadar.db')

    fr = out['filter_report']
    print('\n' + '=' * 74)
    print('A. FILTER APPLICATION SUMMARY')
    print('=' * 74)
    print(f'  Total stock-days (pre-filter): {fr["total"]:,}')
    for k in ('is_earnings_day', 'is_exdiv_day', 'is_split_day', 'prior_close_null', 'dollar_volume_lt_1m', 'total_volume_zero'):
        print(f'    excluded by {k:<22}: {fr[k]:>10,}')
    print(f'  Remaining: {fr["remaining"]:,} ({fr["pct_remaining"]:.1f}%)')

    print('\n' + '=' * 74)
    print('B. SIGNAL CHARACTERIZATION (6 signals x 4 horizons)')
    print('=' * 74)
    for sname, res in out['results'].items():
        print_signal_report(sname, res)

    print('\n' + '=' * 74)
    print('C. EARNINGS DAY SUB-GROUP (gap signals)')
    print('=' * 74)
    es = earnings_subgroup(out['panel_unfiltered'], '2020-07-27', '2026-09-05')
    for sname, row in es.items():
        print(f'\n{sname}:')
        for lbl in ('intraday', '1day'):
            e = row[f'{lbl}_earnings']
            ne = row[f'{lbl}_non_earnings']
            print(f'  {lbl:<10} spread: earnings {fmt_pct(e["spread_ann"])} (t={fmt_t(e["tstat"])}, n={e["n"]})'
                  f'  vs  non-earnings {fmt_pct(ne["spread_ann"])} (t={fmt_t(ne["tstat"])}, n={ne["n"]})')

    print('\n' + '=' * 74)
    print('VWAP CONVENTION COMPARISON')
    print('=' * 74)
    vc = vwap_convention_compare(out['panel_filtered'], out['fwd_cc'])
    for k, v in vc.items():
        print(f'  {k:<20} spread={fmt_pct(v["spread_ann"])}  t={fmt_t(v["tstat"])}  n={v["n"]}')

    print('\n' + '=' * 74)
    print('OVERNIGHT / INTRADAY RETURN ASYMMETRY (this intraday universe, 2020-2026)')
    print('=' * 74)
    oc = overnight_intraday_check(out['panel_filtered'])
    print(f'  Overnight (prior close->open):  ann={oc["overnight_ann"]*100:6.2f}%  daily_mean={oc["overnight_mean_daily"]*1e4:.2f}bp  n={oc["n"]}')
    print(f'  Intraday  (open->close):        ann={oc["intraday_ann"]*100:6.2f}%  daily_mean={oc["intraday_mean_daily"]*1e4:.2f}bp')
    print(f'  Close-to-close (actual):        ann={oc["close_to_close_ann"]*100:6.2f}%  daily_mean={oc["close_to_close_mean_daily"]*1e4:.2f}bp')
    print(f'  Sum check (overnight+intraday daily mean): {oc["sum_check_daily"]*1e4:.2f}bp vs actual close-to-close {oc["close_to_close_mean_daily"]*1e4:.2f}bp')

    print('\n' + '=' * 74)
    print('D. CROSS-SIGNAL CORRELATION MATRIX (overlapping universe/dates)')
    print('=' * 74)
    tickers = out['panel_filtered'].ticker.unique().tolist()
    spread_series = {k: v['spread_series_1d'] for k, v in out['results'].items()}
    corr = correlation_with_daily_library('data/sharadar.db', tickers, '2020-07-27', '2026-09-05', spread_series)
    with pd.option_context('display.width', 200, 'display.max_columns', 20, 'display.float_format', '{:.2f}'.format):
        print(corr)

    return out, es, vc, oc, corr


if __name__ == '__main__':
    main()
