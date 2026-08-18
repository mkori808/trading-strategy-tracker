"""Refresh full current S&P constituent universes and their local daily data.

These are CURRENT investable rosters for ranking and screening, not PIT
historical ledgers.  Registry metadata and validation gates preserve that
distinction.  All downloaded frames pass engine.sanity.check_window before
they can be recorded.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import io
import json
from pathlib import Path
import tempfile

import pandas as pd
import requests
import yfinance as yf

from engine import data as data_module
from engine.sanity import check_window


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DIR = ROOT / "universes"
FETCH_START = date(2019, 1, 1)
OHLCV = ["Open", "High", "Low", "Close", "Volume"]
SOURCES = {
    "sp500_current": (
        "S&P 500 — all current constituents",
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    ),
    "sp400_current": (
        "S&P MidCap 400 — all current constituents",
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    ),
    "sp600_current": (
        "S&P SmallCap 600 — all current constituents",
        "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
    ),
}
# The maintained table still carried CWEN.A after its Class A shares were
# converted into CWEN and NYSE trading was suspended on 2026-05-01. Keeping
# that dead share class would make a supposedly current roster unfetchable.
INACTIVE_SHARE_CLASSES = {"CWEN-A"}


def _symbols(url: str) -> list[str]:
    response = requests.get(
        url,
        headers={"User-Agent": "TradingStrategyLab/1.0 local research application"},
        timeout=60,
    )
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text))
    table = next(frame for frame in tables if "Symbol" in frame.columns)
    # Yahoo uses '-' where index tables use '.' for share classes.
    symbols = {str(symbol).strip().upper().replace(".", "-") for symbol in table["Symbol"]}
    return sorted(symbols - INACTIVE_SHARE_CLASSES)


def _frame_from_batch(raw: pd.DataFrame, symbol: str, batch_size: int) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=OHLCV)
    try:
        frame = raw[symbol] if batch_size > 1 else raw
    except KeyError:
        return pd.DataFrame(columns=OHLCV)
    if frame.empty or not set(OHLCV).issubset(frame.columns):
        return pd.DataFrame(columns=OHLCV)
    frame = frame[OHLCV].dropna().copy()
    return data_module._localize(frame) if not frame.empty else frame


def _store(symbol: str, frame: pd.DataFrame, requested_end: date) -> pd.DataFrame:
    check_window(frame, FETCH_START, requested_end, label=f"full-universe coverage {symbol}")
    path = data_module.DATA_DIR / f"{symbol}_1d.parquet"
    if path.exists():
        try:
            previous = pd.read_parquet(path)
            frame = pd.concat([previous, frame]).sort_index()
            frame = frame[~frame.index.duplicated(keep="last")]
        except Exception:
            pass
    data_module.DATA_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=data_module.DATA_DIR, prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary)
        data_module._replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return frame


def _download(symbols: list[str], batch_size: int) -> dict[str, pd.DataFrame]:
    requested_end = date.today()
    yahoo_end = requested_end + timedelta(days=1)
    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = data_module.DATA_DIR / f"{symbol}_1d.parquet"
        if not path.exists():
            continue
        try:
            cached = pd.read_parquet(path)
            if (
                not cached.empty
                and cached.index.min().date() <= FETCH_START + timedelta(days=7)
                and cached.index.max().date() >= requested_end - timedelta(days=7)
            ):
                check_window(cached, FETCH_START, requested_end, label=f"cached full-universe coverage {symbol}")
                frames[symbol] = cached
        except Exception:
            continue
    pending = [symbol for symbol in symbols if symbol not in frames]
    print(f"CACHE {len(frames)}/{len(symbols)}; download={len(pending)}", flush=True)
    yf.set_tz_cache_location(str(data_module.DATA_DIR / ".yfinance_cache"))
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        raw = yf.download(
            batch,
            start=FETCH_START,
            end=yahoo_end,
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        for symbol in batch:
            frame = _frame_from_batch(raw, symbol, len(batch))
            if not frame.empty:
                frames[symbol] = _store(symbol, frame, requested_end)
        print(f"DATA {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)

    missing = [symbol for symbol in symbols if symbol not in frames]
    for symbol in missing:
        frame = data_module.get_bars(
            symbol, "1d", FETCH_START, requested_end, force_refresh=True,
        )
        if not frame.empty:
            frames[symbol] = _store(symbol, frame, requested_end)
    unresolved = sorted(set(symbols) - set(frames))
    if unresolved:
        raise RuntimeError(f"No daily price coverage for current constituents: {unresolved}")
    return frames


def _definition(
    universe_id: str,
    label: str,
    source_url: str,
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
) -> dict:
    as_of = date.today().isoformat()
    return {
        "id": universe_id,
        "label": label,
        "category": "S&P indexes",
        "description": (
            f"Full {len(symbols)}-security current constituent roster as of {as_of}. "
            "Appropriate for current screening; historical results remain survivorship-biased without a PIT ledger."
        ),
        "assetClass": "equity",
        "symbols": symbols,
        "membershipLedgerPath": None,
        "membershipMode": "full_current_constituents_static_history",
        "constituentSource": source_url,
        "constituentsAsOf": as_of,
        "sourceAdjustments": {
            "removedInactiveShareClasses": sorted(INACTIVE_SHARE_CLASSES),
            "reason": "CWEN.A trading was suspended on 2026-05-01 when Class A converted to CWEN Class C.",
        },
        "dataCoverage": {
            symbol: {
                "dataSource": "Yahoo Finance adjusted daily local cache",
                "coverageStart": frames[symbol].index.min().date().isoformat(),
                "coverageEnd": frames[symbol].index.max().date().isoformat(),
            }
            for symbol in symbols
        },
        "costModel": {
            "type": "equity_spread",
            "estimator": "engine.execution_calibration.spread_for",
            "commissionBps": 0.0,
            "note": "Per-symbol US-equity spread model.",
        },
        "primaryBenchmark": "SPY",
        "equalWeightBenchmark": "self_equal_weight",
        "applicableGates": {
            "pit_membership": {
                "applicable": True,
                "reason": (
                    "The full current roster is applied statically through history. A date-effective "
                    "membership ledger plus removed/delisted prices is required to pass this gate."
                ),
            },
            "beats_equal_weight": {
                "applicable": True,
                "reason": "This is a multi-instrument equity basket.",
            },
            "beats_spy": {
                "applicable": True,
                "reason": "SPY is the registered benchmark for this US-equity universe.",
            },
        },
        "runnable": True,
        "selectable": True,
        "unavailableReason": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    rosters = {
        universe_id: _symbols(source_url)
        for universe_id, (_label, source_url) in SOURCES.items()
    }
    all_symbols = sorted({symbol for roster in rosters.values() for symbol in roster})
    print(
        "ROSTERS " + " ".join(f"{key}={len(value)}" for key, value in rosters.items())
        + f" unique={len(all_symbols)}",
        flush=True,
    )
    frames = _download(all_symbols, args.batch_size)
    REGISTRY_DIR.mkdir(exist_ok=True)
    for universe_id, symbols in rosters.items():
        label, source_url = SOURCES[universe_id]
        payload = _definition(universe_id, label, source_url, symbols, frames)
        (REGISTRY_DIR / f"{universe_id}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
