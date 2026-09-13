"""Entry point for the paper-trading system.

  python run_rebalance.py --dry-run    # dry run only (default)
  python run_rebalance.py --submit     # submit orders (NOT WIRED YET -- see below)
  python run_rebalance.py --report     # generate weekly report from ledgers
  python run_rebalance.py --ledger     # append this week's row to each track's ledger

  Virtual-fill workflow (state/, independent of the ledger/ commands above
  and of the real Alpaca account -- see state_engine.py):
  python run_rebalance.py --init-state # one-time: fresh {cash, holdings:{}, nav_history:[]} per track
  python run_rebalance.py --record     # Friday: save this week's target portfolio as pending
  python run_rebalance.py --fill       # Monday after 10am: virtually fill pending at Monday's open
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from datetime import date
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rebalance_engine as engine

TRACKS = ["ibs_standalone", "composite_v2", "composite_v3_1", "composite_v3_2"]
DAILY_TRACKS = ["ibs_daily_overnight_full", "ibs_daily_overnight_liquid"]


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


def cmd_init_state() -> None:
    """Initializes fresh {inception_date, notional, current_holdings: {},
    cash, nav_history: []} state files for all three tracks, OVERWRITING
    whatever is there. Deliberately independent of V1's ledger/holdings
    files (pre-populated with a phantom target before any real fill ever
    happened) and of the real Alpaca account (holds 5 unrelated legacy
    positions from an earlier system) -- both are ignored, not migrated."""
    import state_engine
    cfg = engine.load_config()
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    for track in TRACKS:
        state = state_engine.init_fresh_state(track, cfg["portfolio"]["notional_usd"], today)
        print(f"Initialized {state_engine.state_path(track)}: {state}")


def cmd_record() -> None:
    """Friday: computes each track's target portfolio and saves it as
    `pending` in that track's state file, to be virtually filled Monday by
    --fill. Does not touch cash/current_holdings."""
    import state_engine
    cfg = engine.load_config()
    as_of = engine.most_recent_friday()
    for track in TRACKS:
        rec = state_engine.record_target(track, cfg, as_of)
        held = rec["held"]
        print(f"\n{'='*70}\nRECORD: {track}   as_of={rec['as_of'].date()}\n{'='*70}")
        print(f"  Universe size (pre-exclusion): {rec['result']['universe_size']}")
        print(f"  Post-exclusion universe: {rec['result']['post_exclusion_size']}")
        print(f"  Target holdings: {len(held)} names, ${cfg['portfolio']['notional_usd']:,.0f} notional")
        print(f"  Missing Friday close price: {len(rec['missing_price'])}" + (f" {rec['missing_price']}" if rec['missing_price'] else ""))
        all_pass = True
        for name, passed, detail in rec["checks"]:
            status = "PASS" if passed else "FAIL"
            if not passed:
                all_pass = False
            print(f"    [{status}] {name}  ({detail})")
        print(f"  ALL CHECKS: {'PASS' if all_pass else 'FAIL -- review before Monday'}")
        print(f"  Saved to: {state_engine.state_path(track)}")


def cmd_fill() -> None:
    """Monday after 10am: virtually fills each track's pending target at
    Monday's actual open price (first 1-min bar >= 9:30 ET), Friday-close
    fallback + DATA_UNAVAILABLE flag on missing data. Never submits a real
    order -- market data endpoint only."""
    import state_engine
    ok, reason = engine.is_paper_trading_confirmed()
    print(f"Alpaca market-data credential check: {'OK' if ok else 'FAILED'} -- {reason}")
    if not ok:
        return
    sys.path.insert(0, str(engine.V1_ROOT))
    from engine import alpaca_trading
    client, _ = alpaca_trading.trading_client()

    monday = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    for track in TRACKS:
        result = state_engine.apply_fill(track, client, monday)
        print(f"\n{'='*70}\nFILL: {track}   monday={result['monday'].date()}\n{'='*70}")
        for f in result["fills"]:
            print(f"  {f['ticker']:<6} {f['status']:<17} fill={f['fill_price']}  "
                  f"friday_close={f['friday_close']}  delta_shares={f['delta_shares']:.4f}  "
                  f"realized_slippage_bps={f['realized_slippage_bps']}  modeled_cost_bps={f['modeled_cost_bps']}")
        print(f"  NAV: {result['nav']:.2f}   Cash: {result['cash']:.2f}")


def print_daily_dry_run(report: dict) -> None:
    result, held = report["result"], report["held"]
    scores = result["scores"]
    print(f"\n{'='*70}\nDAILY OVERNIGHT TRACK: {report['track']}   as_of={report['as_of'].date()}\n{'='*70}")
    print(f"  Universe size (before exclusions): {result['universe_size']}")
    print(f"  NSI Q5 excluded:     {len(result['excluded_nsi_q5'])}")
    print(f"  Quality Q5 excluded: {len(result['excluded_quality_q5'])}")
    print(f"  Post-exclusion universe: {result['post_exclusion_size']}")
    print(f"  Earnings neutralized: {result['earnings_neutral_count']}")
    print(f"  IBS score distribution: p10={scores.ibs.quantile(.1):.3f}  p50={scores.ibs.quantile(.5):.3f}  p90={scores.ibs.quantile(.9):.3f}")
    print(f"  Top quintile: {len(held)} names")
    print(f"  Mean trailing ADV: ${held.adv20.mean():,.0f}" if len(held) else "  Mean trailing ADV: N/A")
    print("  Sanity checks:")
    all_pass = True
    for name, passed, detail in report["checks"]:
        print(f"    [{'PASS' if passed else 'FAIL'}] {name}  ({detail})")
        all_pass &= passed
    print(f"  ALL CHECKS: {'PASS' if all_pass else 'FAIL -- do not authorize launch'}")
    print("  Registered cost model: 10bps round trip/night = 25.20% annualized at daily turnover [COST_DOMINATES].")


def print_daily_execution_audit(cfg: dict) -> None:
    import daily_overnight_engine as daily
    audit = daily.execution_audit(cfg)
    print("\nTRACK 4 EXECUTION TIMELINE")
    print(f"  Market-data cutoff: {audit['market_data_cutoff']}")
    print(f"  IBS calculation:    {audit['signal_calculation_time']}")
    print(f"  Selection:          {audit['portfolio_selection_time']}")
    print(f"  Entry:              {audit['entry_fill']}")
    print(f"  Exit:               {audit['exit_fill']}")
    print(f"  STATUS: {audit['status']} -- {audit['reason']}")
    print("  PIT-safe pre-close / closing-auction implementation: NONE EXISTS.")
    print("  The portfolio cannot be known before the day-t official closing entry price.")


def print_daily_cost_diagnostics(cfg: dict) -> None:
    import daily_overnight_engine as daily
    print("\nTRACK 4 PREDECLARED COST SENSITIVITIES (diagnostic; 10bp is primary)")
    for row in daily.cost_sensitivities(cfg):
        flag = " [PRIMARY]" if row["round_trip_bps"] == 10 else ""
        print(f"  {row['round_trip_bps']:g} bps/night -> {row['annualized_cost_pct']:.2f}% annualized{flag}")
    weekly_full_turnover_cost = 10 / 10_000 * 52 * 100
    daily_primary_cost = 10 / 10_000 * 252 * 100
    incremental = daily_primary_cost - weekly_full_turnover_cost
    print(f"  Daily 10bp vs weekly Track 1 10bp at 52 rebalances: incremental {incremental:.2f}%/yr.")
    print(f"  Daily IBS must exceed Track 1 gross annual return by at least {incremental:.2f} percentage points to overcome frequency cost; the preregistered +2% net hurdle implies 22.00 points of gross advantage.")


def cmd_dry_run_daily() -> list[dict]:
    import daily_overnight_engine as daily
    cfg = engine.load_config()
    print_daily_execution_audit(cfg)
    print_daily_cost_diagnostics(cfg)
    reports = []
    for track in DAILY_TRACKS:
        report = daily.dry_run(track, cfg)
        print_daily_dry_run(report)
        reports.append(report)
    return reports


def _next_friday_after(today: pd.Timestamp) -> pd.Timestamp:
    offset = (4 - today.dayofweek) % 7
    if offset == 0:
        offset = 7
    return today + pd.Timedelta(days=offset)


def cmd_init_daily_state() -> None:
    """Create inactive Track 4 state files; no target or trade is recorded."""
    import daily_overnight_engine as daily
    cfg = engine.load_config()
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    inception = _next_friday_after(today)
    for track in DAILY_TRACKS:
        state = daily.init_state(track, cfg["portfolio"]["notional_usd"], inception)
        print(f"Initialized inactive daily state: {daily.state_path(track)} (inception={state['inception_date']})")
    print("Launch remains blocked by config.json daily_overnight.launch_authorized=false.")


def cmd_record_daily() -> None:
    """Close-entry virtual record for Track 4 only; blocked until approval."""
    import daily_overnight_engine as daily
    cfg = engine.load_config()
    if not daily.launch_authorized(cfg):
        raise RuntimeError("Daily launch is blocked pending explicit dry-run approval; no record was created.")
    as_of = daily.latest_available_session(cfg)
    for track in DAILY_TRACKS:
        result = daily.record_close_target(track, cfg, as_of)
        print(f"Recorded close-entry target for {track}: {len(result['held'])} holdings; modeled cost=${result['modeled_cost_usd']:.2f}")


def cmd_fill_daily() -> None:
    """Next-open mark/exit for Track 4 only; never submits orders."""
    import daily_overnight_engine as daily
    cfg = engine.load_config()
    if not daily.launch_authorized(cfg):
        raise RuntimeError("Daily launch is blocked pending explicit dry-run approval; no fill was attempted.")
    ok, reason = engine.is_paper_trading_confirmed()
    print(f"Alpaca market-data credential check: {'OK' if ok else 'FAILED'} -- {reason}")
    if not ok:
        return
    sys.path.insert(0, str(engine.V1_ROOT))
    from engine import alpaca_trading
    client, _ = alpaca_trading.trading_client()
    session = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    for track in DAILY_TRACKS:
        result = daily.apply_open_mark(track, client, session)
        row = result["row"]
        print(f"Daily overnight fill {track}: net={row['net_return']:.3%} NAV=${result['nav']:.2f} fill_quality={row['fill_quality_avg']:.5f}")


def _aligned_spy_overnight_return(df: pd.DataFrame) -> float | None:
    """Compound SPY's matching close-to-next-open returns, if retrievable."""
    try:
        import yfinance as yf
        history = yf.download(
            "SPY", start=(df.record_date.min() - pd.Timedelta(days=5)).date(),
            end=(df.fill_date.max() + pd.Timedelta(days=5)).date(),
            auto_adjust=False, progress=False,
        )
        if isinstance(history.columns, pd.MultiIndex):
            history.columns = history.columns.get_level_values(0)
        values = []
        for row in df.itertuples(index=False):
            close = history.loc[pd.Timestamp(row.record_date), "Close"]
            opening = history.loc[pd.Timestamp(row.fill_date), "Open"]
            values.append(float(opening / close - 1))
        return float(pd.Series(values).add(1).prod() - 1) if values else None
    except Exception:
        return None


def _track1_return_since(start: pd.Timestamp) -> float | None:
    """Return of the existing weekly IBS virtual NAV over the same calendar span.

    This is a secondary, non-aligned comparison: Track 1 executes weekly at
    Monday's open, while Track 4 measures close-to-open daily only.
    """
    try:
        import state_engine
        history = state_engine.load_state("ibs_standalone").get("nav_history", [])
        nav = pd.DataFrame(history)
        if len(nav) < 2:
            return None
        nav["date"] = pd.to_datetime(nav["date"])
        nav = nav[nav.date >= start]
        if len(nav) < 2:
            return None
        return float(nav.nav.iloc[-1] / nav.nav.iloc[0] - 1)
    except Exception:
        return None


def _paper_scorecard(track: str, cfg: dict) -> dict:
    """Return the required forward fields without inventing immature metrics."""
    import json
    import math

    path = Path(__file__).resolve().parent / "state" / f"{track}_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    history = pd.DataFrame(state.get("nav_history", []))
    fills = state.get("fill_history", [])
    initial = float(state.get("notional", cfg["portfolio"]["notional_usd"]))
    cumulative = None
    annualized = None
    sharpe = None
    max_dd = None
    end_date = None
    if not history.empty and "nav" in history:
        history["date"] = pd.to_datetime(history["date"])
        history = history.sort_values("date")
        nav = history["nav"].astype(float)
        end_date = history["date"].iloc[-1].date().isoformat()
        cumulative = float(nav.iloc[-1] / initial - 1)
        elapsed_days = (history["date"].iloc[-1] - pd.Timestamp(state["inception_date"])).days
        if elapsed_days >= 63 and nav.iloc[-1] > 0:
            annualized = float((nav.iloc[-1] / initial) ** (365.2425 / elapsed_days) - 1)
        series = pd.concat([pd.Series([initial]), nav], ignore_index=True)
        returns = series.pct_change().dropna()
        if len(returns) >= 12 and returns.std(ddof=1) > 0:
            sharpe = float((returns.mean() / returns.std(ddof=1)) * math.sqrt(52))
        wealth = series / initial
        max_dd = float((wealth / wealth.cummax() - 1).min())
    modeled_cost = float(sum(float(row.get("modeled_transaction_cost_usd", 0)) for row in fills))
    turnover_notional = float(sum(float(row.get("turnover_notional", 0)) for row in fills))
    trades = int(sum(int(row.get("trade_count", 0)) for row in fills))
    slippage = [row.get("estimated_slippage_bps_mean") for row in fills if row.get("estimated_slippage_bps_mean") is not None]
    failures = state.get("implementation_failures", [])
    track_cfg = cfg["tracks"][track]
    return {
        "inception_date": state.get("inception_date"),
        "end_date": end_date,
        "specification_version": state.get("specification_version", track_cfg.get("specification_version", track)),
        "specification_sha256": state.get("specification_sha256", track_cfg.get("specification_sha256")),
        "cumulative_return": cumulative,
        "cagr": annualized,
        "spy_return": None,
        "excess_return_vs_spy": None,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "cumulative_turnover": turnover_notional / initial if initial else None,
        "modeled_transaction_cost_usd": modeled_cost,
        "estimated_slippage_bps_mean": (sum(slippage) / len(slippage) if slippage else None),
        "trade_count": trades,
        "implementation_failure_count": len(failures),
        "benchmark_unavailable_reason": "No completed aligned SPY live-period series is persisted yet.",
    }


def _fmt_metric(value, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "NOT AVAILABLE"
    return f"{value:.2%}" if pct else f"{value:.3f}"


def cmd_report() -> None:
    reports_dir = Path(__file__).resolve().parent / "reports"
    ledger_dir = Path(__file__).resolve().parent / "ledger"
    today = date.today().isoformat()
    lines = [f"# Weekly report - {today}\n"]
    cfg = engine.load_config()
    for track in TRACKS:
        path = ledger_dir / f"{track}.csv"
        lines.append(f"## {track}\n")
        score = _paper_scorecard(track, cfg)
        lines.append(
            f"Frozen spec: {score['specification_version']} / `{score['specification_sha256'] or 'NOT AVAILABLE'}`; "
            f"inception: {score['inception_date']}; through: {score['end_date'] or 'NOT AVAILABLE'}.\n"
        )
        lines.append(
            f"Cumulative return: {_fmt_metric(score['cumulative_return'], True)}; "
            f"CAGR: {_fmt_metric(score['cagr'], True)}; SPY same-period return: NOT AVAILABLE; "
            f"excess return vs SPY: NOT AVAILABLE; Sharpe: {_fmt_metric(score['sharpe'])}; "
            f"max drawdown: {_fmt_metric(score['max_drawdown'], True)}.\n"
        )
        lines.append(
            f"Cumulative turnover: {_fmt_metric(score['cumulative_turnover'], True)}; "
            f"modeled transaction costs: ${score['modeled_transaction_cost_usd']:,.2f}; "
            f"estimated slippage: {_fmt_metric(score['estimated_slippage_bps_mean'])} bps; "
            f"trades: {score['trade_count']}; implementation failures: {score['implementation_failure_count']}.\n"
        )
        if score["spy_return"] is None:
            lines.append(f"Benchmark note: {score['benchmark_unavailable_reason']}\n")
        if not path.exists() or path.stat().st_size < 50:
            lines.append("No ledger rows yet.\n")
            continue
        df = pd.read_csv(path).tail(5)
        header = "| " + " | ".join(df.columns) + " |"
        sep = "|" + "|".join("---" for _ in df.columns) + "|"
        rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
        lines.append("\n".join([header, sep, *rows]) + "\n")
    lines.append("# Daily overnight experiment\n")
    for track in DAILY_TRACKS:
        path = ledger_dir / f"{track}.csv"
        lines.append(f"## {track}\n")
        if not path.exists() or path.stat().st_size < 50:
            lines.append("No completed overnight ledger rows yet.\n")
            continue
        df = pd.read_csv(path, parse_dates=["record_date", "fill_date"])
        cumulative = (1 + df.net_return).prod() - 1
        weekly = (1 + df.tail(5).net_return).prod() - 1
        lines.append(f"Cumulative NAV return: {cumulative:.2%}; latest five completed overnights: {weekly:.2%}.\n")
        spy = _aligned_spy_overnight_return(df)
        track1 = _track1_return_since(df.record_date.min())
        spy_text = "unavailable" if spy is None else f"{spy:.2%} (strategy excess {cumulative - spy:.2%})"
        track1_text = "unavailable" if track1 is None else f"{track1:.2%} (not execution-aligned)"
        lines.append(f"Aligned SPY close-to-open cumulative return: {spy_text}; Track 1 weekly IBS return since Track 4 inception: {track1_text}.\n")
        lines.append(f"Average daily target replacement: {df.target_replacement_pct.mean():.2%}; annualized modeled cost: {df.annualized_cost_estimate_pct.iloc[-1]:.2f}%; latest cost flag: {df.cost_flag.iloc[-1]}.\n")
        dow = df.groupby("day_of_week").net_return.agg(["count", "mean"])
        lines.append("| Day (close) | Observations | Mean overnight return |\n|---|---:|---:|")
        for day, row in dow.iterrows():
            lines.append(f"| {['Mon','Tue','Wed','Thu','Fri'][int(day)]} | {int(row['count'])} | {row['mean']:.3%} |")
        lines.append("")
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
    group.add_argument("--init-state", action="store_true")
    group.add_argument("--record", action="store_true")
    group.add_argument("--fill", action="store_true")
    group.add_argument("--dry-run-daily", action="store_true")
    group.add_argument("--init-daily-state", action="store_true")
    group.add_argument("--record-daily", action="store_true")
    group.add_argument("--fill-daily", action="store_true")
    group.add_argument("--audit-daily-timing", action="store_true")
    args = parser.parse_args()

    if args.submit:
        cmd_submit()
    elif args.report:
        cmd_report()
    elif args.ledger:
        cmd_ledger()
    elif args.init_state:
        cmd_init_state()
    elif args.record:
        cmd_record()
    elif args.fill:
        cmd_fill()
    elif args.dry_run_daily:
        cmd_dry_run_daily()
    elif args.init_daily_state:
        cmd_init_daily_state()
    elif args.record_daily:
        cmd_record_daily()
    elif args.fill_daily:
        cmd_fill_daily()
    elif args.audit_daily_timing:
        cfg = engine.load_config()
        print_daily_execution_audit(cfg)
        print_daily_cost_diagnostics(cfg)
    else:
        cmd_dry_run()
