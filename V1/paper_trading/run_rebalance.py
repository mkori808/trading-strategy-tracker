"""Entry point for the paper-trading system.

  python run_rebalance.py --dry-run    # dry run only (default)
  python run_rebalance.py --submit     # submit orders (NOT WIRED YET -- see below)
  python run_rebalance.py --report     # generate weekly report from ledgers
  python run_rebalance.py --ledger     # append this week's row to each track's ledger
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from datetime import date
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rebalance_engine as engine

TRACKS = ["ibs_standalone", "composite_v2", "composite_v3_1"]


def print_dry_run(report: dict) -> None:
    r = report["result"]
    held = report["held"]
    ibs = r["scores"]["ibs"]
    comp = r["scores"]["composite"]
    print(f"\n{'='*70}\nTRACK: {report['track']}   as_of={report['as_of'].date()}\n{'='*70}")
    print(f"  Universe size (pre-exclusion): {r['universe_size']}")
    print(f"  NSI Q5 excluded:     {len(r['excluded_nsi_q5'])} names (sample: {sorted(r['excluded_nsi_q5'])[:3]})")
    print(f"  Quality Q5 excluded: {len(r['excluded_quality_q5'])} names (sample: {sorted(r['excluded_quality_q5'])[:3]})")
    print(f"  Post-exclusion universe: {r['post_exclusion_size']}")
    print(f"  IBS score distribution: p10={ibs.quantile(.1):.3f}  p50={ibs.quantile(.5):.3f}  p90={ibs.quantile(.9):.3f}")
    print(f"  Composite score distribution: p10={comp.quantile(.1):.3f}  p50={comp.quantile(.5):.3f}  p90={comp.quantile(.9):.3f}")
    print(f"  Top quintile: {len(held)} names")
    print(f"  Proposed buys:  {len(report['buys'])}" + (f"  (examples: {report['buys'][:5]})" if report['buys'] else ""))
    print(f"  Proposed sells: {len(report['sells'])}" + (f"  (examples: {report['sells'][:5]})" if report['sells'] else ""))
    c = report["cost"]
    print(f"  Estimated weekly turnover: {c['turnover_pct']*100:.2f}%")
    print(f"  Estimated annual cost:     {c['annualized_cost_pct']:.2f}%/yr")
    print("  Sanity checks:")
    all_pass = True
    for name, passed, detail in report["checks"]:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"    [{status}] {name}  ({detail})")
    print(f"  ALL CHECKS: {'PASS' if all_pass else 'FAIL -- review before proceeding'}")


def cmd_dry_run() -> list[dict]:
    ok, reason = engine.is_paper_trading_confirmed()
    print(f"Paper trading credential check: {'OK' if ok else 'FAILED'} -- {reason}")
    cfg = engine.load_config()
    as_of = engine.most_recent_friday()
    reports = []
    for track in TRACKS:
        report = engine.dry_run_report(track, cfg, as_of)
        print_dry_run(report)
        reports.append(report)
    return reports


def cmd_submit() -> None:
    raise NotImplementedError(
        "Order submission is intentionally not wired up. Only one Alpaca "
        "paper credential pair exists (see config.json single_account_caveat) "
        "-- submitting real orders for 3 simultaneous tracks into one shared "
        "brokerage account would commingle positions on overlapping tickers "
        "and make per-track P&L unattributable. Resolve that (separate paper "
        "accounts, or a different attribution scheme) before this is enabled."
    )


def cmd_ledger() -> None:
    cfg = engine.load_config()
    as_of = engine.most_recent_friday()
    ledger_dir = Path(__file__).resolve().parent / "ledger"
    for track in TRACKS:
        report = engine.dry_run_report(track, cfg, as_of)
        held = report["held"]
        row = {
            "date": as_of.date().isoformat(),
            "n_holdings": len(held),
            "turnover_pct": f"{report['cost']['turnover_pct']*100:.4f}" if pd.notna(report['cost']['turnover_pct']) else "",
            "modeled_cost_bps": f"{report['cost']['weekly_cost_bps']:.4f}" if pd.notna(report['cost']['weekly_cost_bps']) else "",
            "composite_score_q1_avg": f"{held['composite'].mean():.4f}" if len(held) else "",
            "universe_size": report["result"]["universe_size"],
            "exclusions_nsi": len(report["result"]["excluded_nsi_q5"]),
            "exclusions_quality": len(report["result"]["excluded_quality_q5"]),
        }
        engine.append_ledger_row(ledger_dir, track, row)
        engine.save_current_holdings(ledger_dir, track, set(held.index))
        print(f"Ledger updated for {track}: {row}")
    print("NOTE: nav / weekly_return / vs_spy_weekly / realized_cost_bps / fill_quality_pct")
    print("      are left blank -- those require actual fills, which require --submit,")
    print("      which is not wired up yet (see cmd_submit).")


def cmd_report() -> None:
    reports_dir = Path(__file__).resolve().parent / "reports"
    ledger_dir = Path(__file__).resolve().parent / "ledger"
    today = date.today().isoformat()
    lines = [f"# Weekly report - {today}\n"]
    for track in TRACKS:
        path = ledger_dir / f"{track}.csv"
        lines.append(f"## {track}\n")
        if not path.exists() or path.stat().st_size < 50:
            lines.append("No ledger rows yet.\n")
            continue
        df = pd.read_csv(path).tail(5)
        header = "| " + " | ".join(df.columns) + " |"
        sep = "|" + "|".join("---" for _ in df.columns) + "|"
        rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
        lines.append("\n".join([header, sep, *rows]) + "\n")
    out_path = reports_dir / f"weekly_{today}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--submit", action="store_true")
    group.add_argument("--report", action="store_true")
    group.add_argument("--ledger", action="store_true")
    args = parser.parse_args()

    if args.submit:
        cmd_submit()
    elif args.report:
        cmd_report()
    elif args.ledger:
        cmd_ledger()
    else:
        cmd_dry_run()
