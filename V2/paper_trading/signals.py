"""Live signal computation for paper trading. Reuses V2/strategies code
directly rather than re-deriving formulas -- every function here is a thin
wrapper that calls into the already-verified V2 implementations
(signal_library.py, composite_v2.py, quality.py) and evaluates them AS OF
the most recent Friday rebalance date instead of over historical panels.
"""
from __future__ import annotations
import sys
import json
import os
from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd

STATE_DIR = Path(__file__).resolve().parent / "state"

V2_ROOT = Path(__file__).resolve().parents[2] / "V2"
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from utils.research_utils import (  # noqa: E402
    load_universe_candidates, load_fundamentals_panel, panel_latest_asof,
)
from strategies.signal_library import load_full_ohlc_panel, _wide, build_eligibility, _rsi  # noqa: E402
from strategies.composite_v2 import _nsi_signal, _quality_score, _quintile  # noqa: E402
from strategies.quality import _FUND_COLS  # noqa: E402

LOOKBACK_DAYS = 500  # calendar days of history pulled for warmup (63d sector window + buffer)


def load_live_panel(db_path: str, as_of: pd.Timestamp, exclude_fama_sectors: list[str]):
    """One bulk load of everything needed to score every signal as of
    `as_of` (must be a trading day with data through that day's close)."""
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=exclude_fama_sectors)
    load_start = (as_of - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = as_of.strftime("%Y-%m-%d")
    candidates = candidates[(candidates.firstpricedate <= as_of) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= as_of - pd.Timedelta(days=LOOKBACK_DAYS)))].reset_index(drop=True)
    tickers = candidates.ticker.tolist()

    px = load_full_ohlc_panel(con, tickers, load_start, end)
    shares_panel = load_fundamentals_panel(con, tickers, load_start, end, ["sharesbas"], dimensions=("MRQ",))
    qual_panel = load_fundamentals_panel(con, tickers, load_start, end, _FUND_COLS)
    q = ",".join("?" * len(tickers)) if tickers else ""
    earn = pd.read_sql_query(
        f"SELECT DISTINCT ticker, date AS datekey FROM fundamentals WHERE ticker IN ({q}) AND dimension='ARQ' AND date>=? AND date<=?",
        con, params=[*tickers, load_start, end], parse_dates=["datekey"],
    ) if tickers else pd.DataFrame(columns=["ticker", "datekey"])
    con.close()

    O, H, L, C, V = (_wide(px, c) for c in ("open", "high", "low", "close", "volume"))
    eligible = build_eligibility(O, H, L, C, V, candidates)
    if as_of not in eligible.index:
        raise ValueError(f"{as_of.date()} has no price data in the panel -- not a trading day, or data not yet loaded for today.")

    return {
        "candidates": candidates, "O": O, "H": H, "L": L, "C": C, "V": V,
        "eligible": eligible, "shares_panel": shares_panel, "qual_panel": qual_panel, "earn": earn,
    }


def _exclusion_cache_path(as_of: pd.Timestamp) -> Path:
    return STATE_DIR / f"exclusions_{as_of.year:04d}-{as_of.month:02d}.json"


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)  # atomic on both POSIX and Windows


def excluded_names(panel: dict, as_of: pd.Timestamp, recompute: bool = False) -> dict[str, set[str]]:
    """NSI Q5 and Quality Q5. Computed once per CALENDAR MONTH and persisted
    to state/exclusions_YYYY-MM.json (across separate `--record` process
    invocations, not just within one run) -- every Friday in the same month
    reuses the cached set unless `recompute=True` (the monthly
    --recompute-exclusions flag) or no cache exists yet for this month."""
    cache_path = _exclusion_cache_path(as_of)
    if cache_path.exists() and not recompute:
        cached = json.loads(cache_path.read_text())
        return {"nsi_q5": set(cached["nsi_q5"]), "quality_q5": set(cached["quality_q5"])}

    syms_today = list(panel["eligible"].columns[panel["eligible"].loc[as_of]])
    nsi_excl, qual_excl = set(), set()
    nsi = _nsi_signal(panel["shares_panel"], as_of, syms_today)
    if len(nsi) >= 5:
        q = _quintile(nsi, ascending=True)
        nsi_excl = set(q.index[q == 5])
    qual = _quality_score(panel["qual_panel"], as_of, syms_today)
    if len(qual) >= 5:
        q = _quintile(qual, ascending=False)
        qual_excl = set(q.index[q == 5])
    result = {"nsi_q5": nsi_excl, "quality_q5": qual_excl}
    _atomic_write_json(cache_path, {"as_of": as_of.date().isoformat(), "nsi_q5": sorted(nsi_excl), "quality_q5": sorted(qual_excl)})
    return result


def is_earnings_day(panel: dict, as_of: pd.Timestamp, syms: list[str]) -> pd.Series:
    """True/False per ticker: any ARQ datekey within +/-1 calendar day of as_of."""
    earn = panel["earn"]
    hits = earn[(earn.datekey >= as_of - pd.Timedelta(days=1)) & (earn.datekey <= as_of + pd.Timedelta(days=1))]
    hit_set = set(hits.ticker)
    return pd.Series({s: (s in hit_set) for s in syms})


def ibs_score(panel: dict, as_of: pd.Timestamp, syms: list[str], earnings_conditioned: bool = False) -> pd.Series:
    """5-day trailing average IBS, percentile-ranked. score = 1 - mean(IBS,5).
    If earnings_conditioned: earnings-day names get a neutral 0.50 rank
    instead of their computed value (per composite_v3_1's spec -- Task A's
    own finding was that this conditioning is NOT warranted by the data,
    see config.json note; implemented here because the track config asks
    for it)."""
    C, H, L = panel["C"], panel["H"], panel["L"]
    ibs_raw = (C - L) / (H - L).replace(0, np.nan)
    avg5 = ibs_raw.rolling(5, min_periods=5).mean()
    raw = (1 - avg5).loc[as_of].reindex(syms)
    pct = raw.rank(pct=True, ascending=True)
    if earnings_conditioned:
        earn_flag = is_earnings_day(panel, as_of, syms)
        pct = pct.where(~earn_flag, 0.50)
    return pct


def rsi2_score(panel: dict, as_of: pd.Timestamp, syms: list[str]) -> pd.Series:
    """RSI(close,2), inverted, percentile-ranked. score = 100 - RSI2."""
    rsi2 = _rsi(panel["C"], 2)
    raw = (100 - rsi2).loc[as_of].reindex(syms)
    return raw.rank(pct=True, ascending=True)


def sector_rs_score(panel: dict, as_of: pd.Timestamp, syms: list[str]) -> pd.Series:
    """63-day return, percentile-ranked WITHIN each stock's Fama sector."""
    C = panel["C"]
    ret63 = (C / C.shift(63) - 1).loc[as_of].reindex(syms)
    sector_of = panel["candidates"].set_index("ticker")["sector"].reindex(syms)
    df = pd.DataFrame({"ret63": ret63, "sector": sector_of})
    return df.groupby("sector")["ret63"].rank(pct=True, ascending=True).fillna(0.5)


def turnaround_tue_score(panel: dict, as_of: pd.Timestamp, syms: list[str], threshold: float = -0.01) -> pd.Series:
    """Binary: 1 if this Friday's own daily return < threshold, else 0."""
    C = panel["C"]
    friday_ret = (C / C.shift(1) - 1).loc[as_of].reindex(syms)
    return (friday_ret < threshold).astype(float)


def compute_composite(panel: dict, as_of: pd.Timestamp, weights: dict[str, float], ibs_earnings_conditioned: bool, recompute_exclusions: bool = False) -> pd.DataFrame:
    """Full pipeline: exclude NSI Q5 / Quality Q5, score every active
    signal, weight-combine, return a DataFrame indexed by ticker with each
    component score plus the final composite."""
    excl = excluded_names(panel, as_of, recompute=recompute_exclusions)
    excluded = excl["nsi_q5"] | excl["quality_q5"]
    syms_today = list(panel["eligible"].columns[panel["eligible"].loc[as_of]])
    syms = [s for s in syms_today if s not in excluded]

    scores = pd.DataFrame(index=syms)
    scores["ibs"] = ibs_score(panel, as_of, syms, earnings_conditioned=ibs_earnings_conditioned)
    scores["rsi2"] = rsi2_score(panel, as_of, syms)
    scores["sector_rs"] = sector_rs_score(panel, as_of, syms)
    scores["turnaround_tue"] = turnaround_tue_score(panel, as_of, syms)
    scores = scores.dropna(subset=["ibs", "rsi2", "sector_rs"])  # turnaround_tue is 0/1, never NaN once syms filtered

    scores["composite"] = sum(weights.get(col, 0.0) * scores[col] for col in ("ibs", "rsi2", "sector_rs", "turnaround_tue"))
    return {
        "scores": scores, "excluded_nsi_q5": excl["nsi_q5"], "excluded_quality_q5": excl["quality_q5"],
        "universe_size": len(syms_today), "post_exclusion_size": len(syms),
    }
