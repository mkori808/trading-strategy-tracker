"""Core rebalance logic, shared across all three tracks. Reuses V1's
existing Alpaca/safety infrastructure (engine/alpaca_trading.py,
engine/kill_switch.py) rather than reimplementing order submission --
see the module docstring in signals.py for what's reused from V2.

SAFETY: order submission goes through V1/engine/alpaca_trading.py, which
hardcodes TradingClient(paper=True) at the SDK level -- there is no
`ALPACA_BASE_URL` to assert on (this codebase doesn't use that pattern);
the equivalent guarantee is structural: the paper flag isn't a toggle
anywhere in that module, so a live key pair would simply fail auth rather
than trade real money. `is_paper_trading_confirmed()` below re-checks this
explicitly before this module will do anything with --submit.

SINGLE-ACCOUNT CAVEAT (see config.json): only one Alpaca paper credential
pair exists. Real order submission for 3 simultaneous tracks sharing one
brokerage account would commingle positions when tickers overlap across
tracks (expected, since IBS dominates 2 of the 3). --submit is therefore
NOT wired to call Alpaca yet -- it raises clearly rather than silently
producing misleading per-track P&L. Dry-run (target portfolio, sanity
checks, cost estimate) is unaffected and fully functional.
"""
from __future__ import annotations
import csv
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

HERE = Path(__file__).resolve().parent
# paper_trading/ lives under EITHER V1 or V2 (copies of this module exist
# in both) -- find repo root by walking up until a directory containing
# both V1/ and V2/ siblings is found, rather than assuming a fixed depth.
def _find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "V1").is_dir() and (candidate / "V2").is_dir():
            return candidate
    raise RuntimeError(f"Could not locate repo root (V1/ and V2/ siblings) above {start}")

REPO_ROOT = _find_repo_root(HERE)
V1_ROOT = REPO_ROOT / "V1"

# IMPORTANT: import signals (and everything it pulls in from V2/strategies,
# V2/utils) BEFORE V1_ROOT ever touches sys.path. V1/strategies has an
# __init__.py (a regular package); V2/strategies does not (a namespace
# package). Python's import system lets a regular package shadow a
# namespace package of the same name anywhere it appears on sys.path,
# regardless of insertion order -- so if V1_ROOT is on sys.path before
# `strategies.signal_library` etc. are first imported, they silently
# resolve to V1/strategies instead and fail with ModuleNotFoundError.
# Importing signals.py first (which only touches V2_ROOT) caches
# sys.modules['strategies'] as the V2 namespace package; V1_ROOT is only
# added afterward, and only where V1's `engine` package is actually needed
# (see is_paper_trading_confirmed below), by which point `strategies` is
# already resolved and won't be re-scanned.
import signals  # noqa: E402 (paper_trading/signals.py)

LEDGER_COLUMNS = [
    "date", "nav", "weekly_return", "vs_spy_weekly", "n_holdings", "turnover_pct",
    "modeled_cost_bps", "realized_cost_bps", "fill_quality_pct", "composite_score_q1_avg",
    "universe_size", "exclusions_nsi", "exclusions_quality",
]
# NOTE: append_ledger_row/LEDGER_COLUMNS are not currently called by the
# live --record/--fill flow (state_engine.py owns fill/cost accounting via
# its own realized_slippage_bps/modeled_cost_bps-as-separate-line-items
# design, recorded in nav_history, not a CSV yet). Left here unused rather
# than deleted since ledger CSV output is still a planned deliverable --
# whoever wires it up should match state_engine's actual field names
# instead of the guessed schema this comment used to carry.


def load_config() -> dict:
    with open(HERE / "config.json") as f:
        cfg = json.load(f)
    # sharadar_db is stored repo-root-relative in config.json; resolve it
    # absolutely here so this works regardless of the caller's cwd.
    cfg["sharadar_db"] = str((REPO_ROOT / cfg["sharadar_db"]).resolve())
    return cfg


def most_recent_friday(today: pd.Timestamp | None = None) -> pd.Timestamp:
    today = today or pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    offset = (today.dayofweek - 4) % 7  # Friday = 4
    return today - pd.Timedelta(days=offset)


def is_paper_trading_confirmed() -> tuple[bool, str]:
    """Re-verify (don't just trust config.json) that Alpaca is reachable
    and that the client V1 builds is the hardcoded-paper one. V1_ROOT is
    added to sys.path here, lazily, and only now -- see the import-order
    comment above `import signals`."""
    if str(V1_ROOT) not in sys.path:
        sys.path.insert(0, str(V1_ROOT))
    from engine import alpaca_trading  # V1/engine
    client, reason = alpaca_trading.trading_client()
    if client is None:
        return False, reason
    # alpaca-py's TradingClient exposes the base url it was built with.
    base_url = str(getattr(client, "_base_url", "")) or str(getattr(client, "base_url", ""))
    if "paper" not in base_url.lower():
        return False, f"ABORT: Alpaca client base URL does not look like paper: {base_url!r}"
    return True, "paper trading confirmed"


def current_month_key(as_of: pd.Timestamp) -> str:
    return f"{as_of.year:04d}-{as_of.month:02d}"


def build_universe_and_score(track_name: str, cfg: dict, as_of: pd.Timestamp, recompute_exclusions: bool = False) -> dict:
    track_cfg = cfg["tracks"][track_name]
    panel = signals.load_live_panel(cfg["sharadar_db"], as_of, cfg["universe"]["exclude_fama_sectors"])
    result = signals.compute_composite(panel, as_of, track_cfg["weights"], track_cfg.get("ibs_earnings_conditioned", False), recompute_exclusions=recompute_exclusions)
    return {"panel": panel, **result}


def construct_portfolio(result: dict, notional_usd: float) -> pd.DataFrame:
    scores = result["scores"].sort_values("composite", ascending=False)
    n = len(scores)
    top_n = max(1, n // 5)
    held = scores.iloc[:top_n].copy()
    held["notional"] = notional_usd / len(held) if len(held) else 0.0
    return held


def current_holdings_from_state(track_name: str, notional: float, as_of: pd.Timestamp) -> set[str]:
    """Current holdings per state_engine.py's state file (the source of
    truth once --fill has run at least once); empty set pre-inception or
    if state hasn't been initialized yet."""
    path = HERE / "state" / f"{track_name}_state.json"
    if not path.exists():
        return set()
    state = json.loads(path.read_text())
    return set(state.get("current_holdings", {}).keys())


def estimate_cost(prior_holdings: set[str], new_holdings: set[str], cost_bps: float) -> dict:
    if not new_holdings:
        return {"turnover_pct": float("nan"), "weekly_cost_bps": float("nan"), "annualized_cost_pct": float("nan")}
    changed = len(prior_holdings.symmetric_difference(new_holdings)) if prior_holdings else len(new_holdings)
    denom = 2 * len(new_holdings) if prior_holdings else len(new_holdings)
    turnover = changed / denom
    weekly_cost = turnover * (cost_bps / 10_000)
    return {"turnover_pct": turnover, "weekly_cost_bps": weekly_cost * 10_000, "annualized_cost_pct": weekly_cost * 52 * 100}


def sanity_checks(result: dict, held: pd.DataFrame) -> list[tuple[str, bool, str]]:
    """Returns (check_name, passed, detail) tuples."""
    checks = []
    uni = result["universe_size"]
    checks.append(("universe_size >= 800", uni >= 800, f"universe_size={uni}"))

    ibs = result["scores"]["ibs"]
    p10, p90 = ibs.quantile(0.10), ibs.quantile(0.90)
    checks.append(("IBS p10 <= 0.30 (discriminating)", p10 <= 0.30, f"p10={p10:.3f}"))
    checks.append(("IBS p90 >= 0.70 (discriminating)", p90 >= 0.70, f"p90={p90:.3f}"))

    checks.append(("top quintile >= 150 names", len(held) >= 150, f"n_held={len(held)}"))

    nsi_in_top = result["excluded_nsi_q5"] & set(held.index)
    qual_in_top = result["excluded_quality_q5"] & set(held.index)
    checks.append(("no NSI Q5 name in top quintile", len(nsi_in_top) == 0, f"found={sorted(nsi_in_top)[:5]}"))
    checks.append(("no Quality Q5 name in top quintile", len(qual_in_top) == 0, f"found={sorted(qual_in_top)[:5]}"))
    return checks


def dry_run_report(track_name: str, cfg: dict, as_of: pd.Timestamp, recompute_exclusions: bool = False) -> dict:
    result = build_universe_and_score(track_name, cfg, as_of, recompute_exclusions=recompute_exclusions)
    held = construct_portfolio(result, cfg["portfolio"]["notional_usd"])
    prior_holdings = current_holdings_from_state(track_name, cfg["portfolio"]["notional_usd"], as_of)
    new_holdings = set(held.index)
    cost = estimate_cost(prior_holdings, new_holdings, cfg["costs"]["model_bps_round_trip"])
    checks = sanity_checks(result, held)
    cost_check = ("annualized cost <= 3%", (pd.isna(cost["annualized_cost_pct"]) or cost["annualized_cost_pct"] <= 3.0), f"{cost['annualized_cost_pct']:.2f}%")
    checks.append(cost_check)

    buys = sorted(new_holdings - prior_holdings)
    sells = sorted(prior_holdings - new_holdings)

    return {
        "track": track_name, "as_of": as_of, "result": result, "held": held,
        "prior_holdings": prior_holdings, "new_holdings": new_holdings,
        "buys": buys, "sells": sells, "cost": cost, "checks": checks,
    }


def append_ledger_row(ledger_dir: Path, track_name: str, row: dict) -> None:
    """Append-only. Never overwrites. Writes the header on first use."""
    path = ledger_dir / f"{track_name}.csv"
    is_new = not path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in LEDGER_COLUMNS})

