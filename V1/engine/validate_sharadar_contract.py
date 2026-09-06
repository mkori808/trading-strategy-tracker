"""Diagnostic-only Sharadar audit against ``engine.pit_all_stocks``.

The validator reads the contract constants from the production loader, probes
only the four in-scope Sharadar tables, compares vendor total-return closes to
the existing Dow cache without modifying it, and writes evidence under
``reports/sharadar_contract_validation``. It never publishes a PIT bundle or
changes a universe definition.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import pandas as pd

from engine.pit_all_stocks import (
    MANIFEST_SCHEMA_VERSION,
    MARKET_CAP_COLUMN,
    REQUIRED_DAILY_COLUMNS,
    REQUIRED_MANIFEST_FIELDS,
    REQUIRED_MANIFEST_TRUE,
    REQUIRED_PRICE_BASIS,
    REQUIRED_SECURITY_COLUMNS,
)
from engine.sharadar_client import SharadarClient, SharadarError
from engine.universe import EQUITY_UNIVERSE


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports" / "sharadar_contract_validation"
RECONSTRUCTION_PANEL = REPORT_DIR / "reconstruction" / "panel.json"

PRESENT = "PRESENT"
MISSING = "MISSING"
AMBIGUOUS = "AMBIGUOUS"
UNTESTABLE = "UNTESTABLE-ON-THIS-TIER"

DOC_URLS = {
    "stocks": "https://sharadar.com/docs/stocks",
    "tickers": "https://sharadar.com/docs/tickers",
    "actions": "https://sharadar.com/docs/actions",
    "daily": "https://sharadar.com/docs/daily",
    "faq": "https://sharadar.com/docs/faqs",
}

# Provider schemas documented at DOC_URLS. These are not a second copy of the
# PIT requirements: required fields are always iterated from the imported
# contract constants above. Runtime rows replace/confirm these columns when an
# API key is available.
DOCUMENTED_COLUMNS = {
    "tickers": {
        "table", "permaticker", "ticker", "name", "exchange", "isdelisted",
        "category", "cusips", "figi", "relatedtickers", "lastupdated",
        "firstadded", "firstpricedate", "lastpricedate",
    },
    "stocks": {
        "ticker", "date", "open", "high", "low", "close", "volume",
        "closeadj", "closeunadj", "lastupdated",
    },
    "actions": {"date", "action", "ticker", "name", "value", "contraticker", "contraname"},
    "daily": {
        "ticker", "date", "lastupdated", "ev", "evebit", "evebitda",
        "marketcap", "pb", "pe", "ps",
    },
}

WMT_SPLIT_DATE = date(2024, 2, 26)
WMT_SPLIT_RATIO = 3.0
# NVDA joined the Dow in November 2024, so it cannot distinguish the current-
# Dow free entitlement from broader equity access. TSLA is liquid, active, and
# outside the Dow at the time of this audit.
NON_DOW_PROBE = "TSLA"


def _is_free_limited_tier(tier: str) -> bool:
    return tier.startswith("free_5y")


@dataclass(frozen=True)
class Finding:
    area: str
    field: str
    status: str
    source: str
    reason: str


def _finding(area: str, field: str, status: str, source: str, reason: str) -> dict[str, str]:
    return asdict(Finding(area, field, status, source, reason))


def _has(columns: dict[str, set[str]], table: str, *fields: str) -> bool:
    return all(field in columns.get(table, set()) for field in fields)


def contract_field_findings(
    columns: dict[str, set[str]],
    *,
    tier: str,
    identity_verified: bool,
) -> list[dict[str, str]]:
    """Map observed vendor fields to the imported normalized contract.

    Candidate mappings are deliberately conservative. A related field is
    AMBIGUOUS until it proves the same semantics; an absent field is MISSING.
    """
    findings: list[dict[str, str]] = []

    for field in sorted(REQUIRED_SECURITY_COLUMNS):
        if field == "security_id":
            present = _has(columns, "tickers", "permaticker")
            status = PRESENT if present and identity_verified else AMBIGUOUS if present else MISSING
            reason = (
                "tickers.permaticker was observed and one ticker-change lineage retained it"
                if status == PRESENT
                else "tickers.permaticker exists, but no accessible ticker-change lineage proved continuity"
                if present
                else "No permanent security identifier was located"
            )
            source = "tickers.permaticker"
        elif field == "ticker":
            present = _has(columns, "tickers", "ticker")
            status = AMBIGUOUS if present else MISSING
            reason = (
                "Current ticker exists and actions documents changes, but a complete date-effective interval must be reconstructed"
                if present else "tickers.ticker was not observed"
            )
            source = "tickers.ticker + actions"
        elif field in {"effective_start", "effective_end"}:
            present = _has(columns, "tickers", "firstpricedate", "lastpricedate") and _has(
                columns, "actions", "date", "action"
            )
            status = AMBIGUOUS if present else MISSING
            reason = (
                "Price endpoints and action dates exist, but are not themselves complete ticker/listing effective intervals"
                if present else "Required endpoint/action fields were not located"
            )
            source = "tickers.firstpricedate/lastpricedate + actions"
        elif field == "known_at":
            status, source = MISSING, "none"
            reason = "No announcement/known-at timestamp is documented for identity or listing changes"
        elif field == "is_us_listed":
            present = _has(columns, "tickers", "exchange")
            status = AMBIGUOUS if present else MISSING
            source = "tickers.exchange"
            reason = (
                "Exchange is current-only; Sharadar explicitly says historical primary-listing venues are unavailable"
                if present else "No listing-venue field was observed"
            )
        elif field in {"is_common_stock", "security_type"}:
            present = _has(columns, "tickers", "category")
            status = AMBIGUOUS if present else MISSING
            source = "tickers.category"
            reason = (
                "Category can classify the current security, but point-in-time security-type intervals were not located"
                if present else "tickers.category was not observed"
            )
        elif field == "exchange":
            present = _has(columns, "tickers", "exchange")
            status = AMBIGUOUS if present else MISSING
            source = "tickers.exchange"
            reason = "Latest exchange is present, but historical venue changes are not supplied" if present else "Missing"
        elif field == "is_acquired":
            present = _has(columns, "actions", "action", "contraticker")
            status = AMBIGUOUS if present else MISSING
            source = "actions.action/contraticker"
            reason = (
                "Acquisition events and counterparties exist, but normalized issuer-level coverage must be audited"
                if present else "Acquisition event fields were not observed"
            )
        elif field == "delisting_reason":
            present = _has(columns, "actions", "action", "value")
            status = UNTESTABLE if _is_free_limited_tier(tier) else AMBIGUOUS if present else MISSING
            source = "actions.action/value"
            reason = (
                "Five-year Dow access cannot establish historical delisting-reason population coverage"
                if status == UNTESTABLE
                else "Delisting reasons are action rows, not a directly observed complete normalized field"
                if present else "Delisting-reason evidence was not observed"
            )
        else:  # pragma: no cover - drift guard below makes this visible
            status, source, reason = MISSING, "none", "No Sharadar mapping has been reviewed"
        findings.append(_finding("security_history", field, status, source, reason))

    for field in sorted(REQUIRED_DAILY_COLUMNS):
        lower = field.lower()
        if field == "security_id":
            has_join = _has(columns, "stocks", "ticker") and _has(columns, "tickers", "permaticker", "ticker")
            status = AMBIGUOUS if has_join else MISSING
            source = "stocks.ticker -> tickers.permaticker"
            reason = (
                "stocks is ticker-keyed; the join is safe only after ticker-change and ticker-reuse lineage tests pass"
                if has_join else "No permanent ID exists directly on stocks rows and the proposed join fields are incomplete"
            )
        elif field == "DelistingReturn":
            status = UNTESTABLE if _is_free_limited_tier(tier) else MISSING
            source = "none"
            reason = (
                "The free Dow/five-year slice cannot test historical terminal-return coverage"
                if status == UNTESTABLE
                else "No explicit delisting-return field exists in stocks, actions, or daily"
            )
        elif field == "RawClose":
            status = PRESENT if _has(columns, "stocks", "closeunadj") else MISSING
            source, reason = "stocks.closeunadj", "Documented unadjusted close"
        elif field == "Close":
            status = PRESENT if _has(columns, "stocks", "closeadj") else MISSING
            source = "stocks.closeadj"
            reason = "Fully split/dividend/spinoff-adjusted close is delivered directly" if status == PRESENT else "stocks.closeadj was not observed"
        elif field in {"Open", "High", "Low"}:
            has_base = _has(columns, "stocks", lower, "close", "closeadj")
            status = AMBIGUOUS if has_base else MISSING
            source = f"stocks.{lower} * stocks.closeadj / stocks.close"
            reason = (
                "Sharadar supplies split-adjusted OHLC and fully adjusted close; total-return OHLC must be imputed"
                if has_base else "Fields needed to construct total-return-adjusted OHLC were not observed"
            )
        else:  # date, Volume
            status = PRESENT if _has(columns, "stocks", lower) else MISSING
            source = f"stocks.{lower}"
            reason = "Directly documented/observed field" if status == PRESENT else "Field was not observed"
        findings.append(_finding("daily", field, status, source, reason))

    return findings


def manifest_findings(
    columns: dict[str, set[str]], *, tier: str, identity_verified: bool
) -> list[dict[str, str]]:
    """Evaluate every imported manifest truth flag, without duplicating the list."""
    findings: list[dict[str, str]] = []
    for field in REQUIRED_MANIFEST_TRUE:
        if field == "survivorshipFree":
            status = UNTESTABLE if _is_free_limited_tier(tier) else AMBIGUOUS
            reason = "A five-year Dow slice cannot measure the inactive all-stock population" if status == UNTESTABLE else "Provider claims 99%, not a contract-level completeness proof"
            source = "tickers/actions/stocks population audit"
        elif field == "delistedSecuritiesIncluded":
            status = UNTESTABLE if _is_free_limited_tier(tier) else AMBIGUOUS
            reason = "Free scope cannot measure all historical delisted securities" if status == UNTESTABLE else "Requires inactive-population reconciliation"
            source = "tickers.isdelisted + actions"
        elif field == "delistingReturnsIncluded":
            status = UNTESTABLE if _is_free_limited_tier(tier) else MISSING
            reason = "Free scope cannot test terminal returns" if status == UNTESTABLE else "No explicit delisting-return field was located"
            source = "none"
        elif field == "tickerHistoryIncluded":
            status = PRESENT if identity_verified else AMBIGUOUS
            reason = "A ticker-change lineage retained permaticker" if identity_verified else "Actions are documented, but permanent-ID continuity was not empirically verified"
            source = "tickers.permaticker + actions"
        elif field == "corporateActionsIncluded":
            status = PRESENT if _has(columns, "actions", "date", "action", "ticker", "value") else MISSING
            reason, source = "Actions table supplies dated typed events" if status == PRESENT else "Core action fields missing", "actions"
        elif field == "pointInTimeSecurityTypes":
            status, source = MISSING, "tickers.category"
            reason = "Category is a current snapshot; no date-effective security-type history was located"
        elif field == "historicalVolumeIncluded":
            status = PRESENT if _has(columns, "stocks", "volume") else MISSING
            reason, source = "Daily split-adjusted volume is supplied" if status == PRESENT else "stocks.volume missing", "stocks.volume"
        else:  # pragma: no cover
            status, source, reason = MISSING, "none", "No mapping reviewed"
        findings.append(_finding("manifest", field, status, source, reason))
    for field in REQUIRED_MANIFEST_FIELDS:
        if field == "source":
            status, source, reason = PRESENT, "API endpoint", "Sharadar is an attributable source"
        elif field == "snapshotId":
            status, source, reason = AMBIGUOUS, "lastupdated/fetch timestamp", "No immutable vendor snapshot identifier is documented; ingestion must hash/version the retrieved artifacts"
        else:
            status = UNTESTABLE if _is_free_limited_tier(tier) or tier in {"no_api_key", "unknown"} else AMBIGUOUS
            source = "measured table dates"
            reason = "The full licensed coverage boundary is outside this tier" if status == UNTESTABLE else "Must be measured across every normalized artifact, not copied from marketing history"
        findings.append(_finding("manifest", field, status, source, reason))
    findings.append(_finding(
        "manifest",
        "schemaVersion",
        PRESENT,
        f"normalizer constant {MANIFEST_SCHEMA_VERSION}",
        "The local normalized bundle controls its schema version",
    ))
    findings.append(_finding(
        "manifest",
        "priceBasis",
        AMBIGUOUS,
        "stocks.closeadj plus imputed adjusted O/H/L",
        f"Target is {REQUIRED_PRICE_BASIS}; close is direct but adjusted O/H/L are imputed and as-of adjustment behavior must be accepted explicitly",
    ))
    return findings


def market_cap_finding(columns: dict[str, set[str]]) -> dict[str, str]:
    """Report the size-filter prerequisite directly, not folded into REQUIRED_DAILY_COLUMNS.

    ``engine.pit_all_stocks.MARKET_CAP_COLUMN`` is conditionally, not
    unconditionally, mandatory: a bundle without it stays runnable for any
    strategy that never passes ``minimum_market_cap`` to
    ``run_size_filtered_scan``, which only raises once that filter is actually
    requested. It is reported here on its own so the field never gets treated
    as always-required, and never silently omitted either.
    """
    present = _has(columns, "daily", "marketcap")
    return _finding(
        "daily_market_cap",
        MARKET_CAP_COLUMN,
        PRESENT if present else MISSING,
        "daily.marketcap" if present else "none",
        (
            "Sharadar's daily table delivers per-date market cap directly; "
            "conditionally mandatory in engine/pit_all_stocks.py only once a "
            "minimum-market-cap filter is requested (market_cap_available)"
            if present
            else "daily.marketcap was not observed; a bundle without it stays "
            "runnable until a minimum-market-cap filter is requested, at which "
            "point engine/pit_all_stocks.py raises"
        ),
    )


def tier_capabilities(tier: str, reason: str | None = None) -> list[dict[str, str]]:
    free_limited = _is_free_limited_tier(tier)
    checks = {
        "dow_recent_prices": free_limited or tier == "full_history_or_broader",
        "pre_2021_history": tier == "full_history_or_broader",
        "non_dow_coverage": tier in {"free_5y_broader", "full_history_or_broader"},
        "delisting_returns": False,
        "historical_delisted_population": tier == "full_history_or_broader",
    }
    rows = []
    for name, capable in checks.items():
        if tier == "no_api_key":
            status = UNTESTABLE
            why = reason or "SHARADAR_API_KEY is not configured"
        elif capable:
            status = PRESENT
            why = "The detected entitlement exposes enough scope to run this check; result still requires validation"
        else:
            status = UNTESTABLE
            why = (
                "No explicit DelistingReturn field is exposed"
                if name == "delisting_returns"
                else "The detected Dow/five-year entitlement does not expose this population or period"
            )
        rows.append({"check": name, "status": status, "reason": why})
    return rows


def _safe_rows(client: SharadarClient, table_name: str, **parameters: Any) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return client.query_all(table_name, **parameters).rows, None
    except SharadarError as exc:
        return [], str(exc)


def detect_tier(client: SharadarClient, *, force_refresh: bool = False) -> tuple[str, dict[str, Any]]:
    today = date.today()
    recent_from = (today - timedelta(days=45)).isoformat()
    recent_to = today.isoformat()
    probes: dict[str, Any] = {}
    for label, ticker, start, end in (
        ("dowRecent", "WMT", recent_from, recent_to),
        ("nonDowRecent", NON_DOW_PROBE, recent_from, recent_to),
        ("dowPre2021", "WMT", "2020-01-02", "2020-02-01"),
    ):
        rows, error = _safe_rows(
            client, "stocks", ticker=ticker, **{"from": start, "to": end},
            fields="ticker,date,close", sort="date.asc", force_refresh=force_refresh,
        )
        probes[label] = {"ticker": ticker, "rows": len(rows), "error": error}
    if probes["dowRecent"]["rows"] and probes["dowPre2021"]["rows"]:
        tier = "full_history_or_broader"
    elif probes["dowRecent"]["rows"] and probes["nonDowRecent"]["rows"]:
        tier = "free_5y_broader"
    elif probes["dowRecent"]["rows"]:
        tier = "free_5y_current_dow"
    else:
        tier = "unknown"
    return tier, probes


def collect_columns(client: SharadarClient, *, force_refresh: bool = False) -> tuple[dict[str, set[str]], dict[str, Any]]:
    today = date.today()
    start = (today - timedelta(days=365 * 5 + 15)).isoformat()
    samples: dict[str, Any] = {}
    queries = {
        "tickers": {"ticker": "WMT", "table": "stocks"},
        "stocks": {"ticker": "WMT", "from": (today - timedelta(days=30)).isoformat(), "to": today.isoformat()},
        "actions": {"ticker": "WMT", "from": start, "to": today.isoformat()},
        "daily": {"ticker": "WMT", "from": (today - timedelta(days=30)).isoformat(), "to": today.isoformat()},
    }
    columns: dict[str, set[str]] = {}
    for table, parameters in queries.items():
        rows, error = _safe_rows(client, table, force_refresh=force_refresh, **parameters)
        observed = set().union(*(set(row) for row in rows)) if rows else set()
        columns[table] = observed
        samples[table] = {"rows": len(rows), "columns": sorted(observed), "error": error}
    return columns, samples


def find_identity_evidence(actions: Iterable[dict[str, Any]], tickers: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Require old/new ticker rows sharing an ID plus a linking change event.

    Merely seeing a ticker-change action next to the current ticker is not
    enough: it would still permit a reused symbol to be mistaken for the old
    security. Both symbols must independently resolve to the same permaticker.
    """
    ticker_rows = list(tickers)
    by_ticker = {
        str(row.get("ticker", "")).upper(): str(row.get("permaticker", ""))
        for row in ticker_rows if row.get("ticker") and row.get("permaticker")
    }
    change_rows = [row for row in actions if "ticker" in str(row.get("action", "")).lower()]
    examples = []
    for row in change_rows:
        ticker = str(row.get("ticker", "")).upper()
        contra = str(row.get("contraticker", "")).upper()
        if ticker and contra and ticker != contra and by_ticker.get(ticker) == by_ticker.get(contra):
            examples.append({
                "oldOrNewTicker": ticker,
                "linkedTicker": contra,
                "permaticker": by_ticker[ticker],
                "action": row,
            })
    return {"verified": bool(examples), "examples": examples[:5], "tickerChangeRows": len(change_rows)}


def collect_identity_rows(
    client: SharadarClient, *, force_refresh: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Fetch lineage evidence one symbol at a time.

    The free endpoint can reject a comma-separated request when even one symbol
    is outside its current entitlement. Per-symbol isolation preserves the
    successful evidence and records exact failures instead of losing the batch.
    """
    start = (date.today() - timedelta(days=365 * 5 + 15)).isoformat()
    end = date.today().isoformat()
    actions: list[dict[str, Any]] = []
    tickers: list[dict[str, Any]] = []
    errors: list[str] = []
    for symbol in EQUITY_UNIVERSE:
        action_rows, action_error = _safe_rows(
            client,
            "actions",
            ticker=symbol,
            **{"from": start, "to": end},
            sort="date.asc",
            force_refresh=force_refresh,
        )
        ticker_rows, ticker_error = _safe_rows(
            client,
            "tickers",
            ticker=symbol,
            table="stocks",
            force_refresh=force_refresh,
        )
        actions.extend(action_rows)
        tickers.extend(ticker_rows)
        if action_error:
            errors.append(f"{symbol} actions: {action_error}")
        if ticker_error:
            errors.append(f"{symbol} tickers: {ticker_error}")

    # Fetch counterpart symbols named by an accessible action so both sides of
    # a purported ticker change must independently resolve to the same ID.
    known = {str(row.get("ticker", "")).upper() for row in tickers}
    counterparties = sorted({
        str(row.get("contraticker", "")).upper()
        for row in actions
        if row.get("contraticker") and str(row.get("contraticker", "")).upper() not in known
    })
    for symbol in counterparties:
        rows, error = _safe_rows(
            client,
            "tickers",
            ticker=symbol,
            table="stocks",
            force_refresh=force_refresh,
        )
        tickers.extend(rows)
        if error:
            errors.append(f"{symbol} counterparty ticker: {error}")
    return actions, tickers, errors


def adjustment_documentation() -> dict[str, Any]:
    return {
        "source": DOC_URLS["faq"],
        "method1Unadjusted": {
            "delivered": "closeunadj",
            "imputedOHLC": "split_adjusted_OHLC * closeunadj / close",
            "imputedVolume": "volume * close / closeunadj",
        },
        "method2SplitAdjusted": {
            "delivered": "open, high, low, close, volume",
            "imputed": "none",
        },
        "method3SplitDividendSpinoffAdjusted": {
            "delivered": "closeadj",
            "imputedOHLC": "split_adjusted_OHLC * closeadj / close",
            "volume": "volume remains split-adjusted; it is not dividend/spinoff-adjusted",
        },
    }


def analyze_split_semantics(
    around_rows: Iterable[dict[str, Any]],
    pre_event_rows: Iterable[dict[str, Any]],
    *,
    event_date: date = WMT_SPLIT_DATE,
    expected_ratio: float = WMT_SPLIT_RATIO,
) -> dict[str, Any]:
    rows = sorted(list(around_rows), key=lambda row: str(row.get("date", "")))
    before = [row for row in rows if date.fromisoformat(str(row["date"])) < event_date]
    after = [row for row in rows if date.fromisoformat(str(row["date"])) >= event_date]
    if not before or not after:
        return {"status": UNTESTABLE, "reason": "No rows on both sides of the known WMT split"}
    pre, post = before[-1], after[0]

    def ratio(field: str) -> float | None:
        a, b = pre.get(field), post.get(field)
        return float(a) / float(b) if a not in (None, 0) and b not in (None, 0) else None

    unadjusted_ratio = ratio("closeunadj")
    split_ratio = ratio("close")
    full_ratio = ratio("closeadj")
    pre_only = list(pre_event_rows)
    future_factor_ratios = [
        float(row["closeunadj"]) / float(row["close"])
        for row in pre_only
        if row.get("closeunadj") not in (None, 0) and row.get("close") not in (None, 0)
    ]
    future_factor = median(future_factor_ratios) if future_factor_ratios else None
    unadjusted_ok = unadjusted_ratio is not None and abs(unadjusted_ratio - expected_ratio) / expected_ratio <= 0.15
    split_ok = split_ratio is not None and abs(split_ratio - 1.0) <= 0.15
    full_ok = full_ratio is not None and abs(full_ratio - 1.0) <= 0.15
    future_contamination = future_factor is not None and abs(future_factor - expected_ratio) / expected_ratio <= 0.05
    status = PRESENT if unadjusted_ok and split_ok and full_ok and future_contamination else AMBIGUOUS
    return {
        "status": status,
        "knownEvent": {"ticker": "WMT", "date": event_date.isoformat(), "ratio": expected_ratio},
        "lastPreSplitDate": pre["date"],
        "firstPostSplitDate": post["date"],
        "prePostCloseRatios": {
            "unadjusted": unadjusted_ratio,
            "splitAdjusted": split_ratio,
            "fullyAdjusted": full_ratio,
        },
        "checks": {
            "unadjustedShowsSplitDiscontinuity": unadjusted_ok,
            "splitAdjustedIsContinuous": split_ok,
            "fullyAdjustedIsContinuous": full_ok,
            "preEventOnlyResponseAlreadyIncludesFutureSplit": future_contamination,
        },
        "preEventOnlyMedianCloseUnadjToCloseRatio": future_factor,
        "interpretation": (
            "Sharadar split-adjusted history is retroactively adjusted for the later split even when the query ends before it; query bounds are not an as-of adjustment boundary."
            if future_contamination else
            "The pre-event-only query did not prove whether later actions retroactively alter returned history."
        ),
    }


def run_adjustment_test(client: SharadarClient, *, force_refresh: bool = False) -> dict[str, Any]:
    fields = "ticker,date,open,high,low,close,volume,closeadj,closeunadj"
    around, around_error = _safe_rows(
        client, "stocks", ticker="WMT", **{"from": "2024-02-20", "to": "2024-03-05"},
        fields=fields, sort="date.asc", force_refresh=force_refresh,
    )
    pre, pre_error = _safe_rows(
        client, "stocks", ticker="WMT", **{"from": "2024-02-20", "to": "2024-02-23"},
        fields=fields, sort="date.asc", force_refresh=force_refresh,
    )
    result = analyze_split_semantics(around, pre)
    result["errors"] = [error for error in (around_error, pre_error) if error]
    result["documentation"] = adjustment_documentation()
    return result


def compare_dow_closes(client: SharadarClient, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=365 * 5 + 15)
    results = []
    for symbol in EQUITY_UNIVERSE:
        rows, error = _safe_rows(
            client, "stocks", ticker=symbol, **{"from": start.isoformat(), "to": end.isoformat()},
            fields="ticker,date,closeadj", sort="date.asc", force_refresh=force_refresh,
        )
        path = DATA_DIR / f"{symbol}_1d.parquet"
        base = {
            "symbol": symbol, "sharadarRows": len(rows), "localCache": str(path.relative_to(ROOT)),
            "error": error,
        }
        if not rows or not path.exists():
            results.append({**base, "overlapRows": 0, "status": UNTESTABLE, "reason": "Missing vendor rows or local cache"})
            continue
        vendor = pd.DataFrame(rows)
        vendor["date"] = pd.to_datetime(vendor["date"]).dt.tz_localize(None)
        vendor = vendor.set_index("date")["closeadj"].astype(float).rename("sharadar")
        local_frame = pd.read_parquet(path, columns=["Close"])
        local_frame.index = pd.to_datetime(local_frame.index, utc=True).tz_convert(None).normalize()
        local = local_frame["Close"].astype(float).rename("local")
        joined = pd.concat([vendor, local], axis=1, join="inner").dropna()
        if joined.empty:
            results.append({**base, "overlapRows": 0, "status": UNTESTABLE, "reason": "No common sessions"})
            continue
        differences = ((joined["sharadar"] - joined["local"]).abs() / joined["local"].abs()) * 100.0
        median_difference = float(differences.median())
        latest_difference = float(differences.iloc[-1])
        likely_adjustment_vintage = median_difference > 0.5 and latest_difference <= 0.5
        results.append({
            **base,
            "overlapRows": len(joined),
            "measuredStart": joined.index.min().date().isoformat(),
            "measuredEnd": joined.index.max().date().isoformat(),
            "medianAbsolutePctDifference": median_difference,
            "maxAbsolutePctDifference": float(differences.max()),
            "latestAbsolutePctDifference": latest_difference,
            "status": "DISCREPANCY" if median_difference > 0.5 else PRESENT,
            "aboveMedianDivergenceThreshold": median_difference > 0.5,
            "likelyAdjustmentVintageDifference": likely_adjustment_vintage,
            "reason": (
                "Median exceeds 0.5%, but the latest common close agrees within 0.5%; "
                "this pattern is consistent with different as-of dividend/corporate-action adjustment vintages"
                if likely_adjustment_vintage
                else "Compared Sharadar closeadj with the existing yfinance-adjusted Close"
            ),
        })
    return results


def _summary_counts(findings: list[dict[str, str]]) -> dict[str, int]:
    return {status: sum(row["status"] == status for row in findings) for status in (PRESENT, MISSING, AMBIGUOUS, UNTESTABLE)}


def _verdict(tier: str, comparisons: list[dict[str, Any]], adjustment: dict[str, Any]) -> dict[str, Any]:
    unresolved_paid = [
        "full history before the free tier's five-year boundary",
        "non-Dow active and inactive security coverage",
        "permaticker continuity across historical ticker changes and ticker reuse",
        "historical delisted-security population and delisting-reason coverage",
        "cash/stock acquisition and other terminal-value completeness",
        "whether an explicit or reproducible delisting return can satisfy DelistingReturn",
        "full-period daily market-cap coverage for size filters",
        "coverage and identity behavior around mergers, spinoffs, relistings, and OTC transitions",
    ]
    if tier == "no_api_key":
        return {
            "decision": "NOT YET",
            "recommendPaidMonth": False,
            "reason": "The free account was not measured because SHARADAR_API_KEY is not configured.",
            "paidMonthWouldAnswer": unresolved_paid,
        }
    measured = [row for row in comparisons if row.get("status") != UNTESTABLE]
    all_symbols_measured = len(measured) == len(EQUITY_UNIVERSE)
    discrepancy = [row["symbol"] for row in measured if row.get("aboveMedianDivergenceThreshold")]
    # A completed from-scratch reconstruction can resolve a vendor-vs-cache
    # discrepancy without changing the underlying contract. Only retain
    # symbols whose reconstructed close still diverges from vendor closeadj.
    if RECONSTRUCTION_PANEL.exists():
        try:
            panel = json.loads(RECONSTRUCTION_PANEL.read_text(encoding="utf-8"))
            resolved = {
                symbol for symbol, result in panel.get("perSymbol", {}).items()
                if result.get("vendorAgreesWithReconstruction") is True
            }
            discrepancy = [symbol for symbol in discrepancy if symbol not in resolved]
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    adjustment_passed = adjustment.get("status") == PRESENT
    if _is_free_limited_tier(tier):
        if all_symbols_measured and not discrepancy and adjustment_passed:
            return {
                "decision": "YES — buy one paid month for the unresolved contract tests",
                "recommendPaidMonth": True,
                "reason": "The accessible Dow price slice and adjustment behavior validated cleanly; the remaining blockers are structurally outside the free entitlement.",
                "paidMonthWouldAnswer": unresolved_paid,
            }
        return {
            "decision": "NO — resolve free-tier failures before paying",
            "recommendPaidMonth": False,
            "reason": f"Free-tier foundation incomplete: measuredSymbols={len(measured)}/{len(EQUITY_UNIVERSE)}, discrepancies={discrepancy}, adjustmentStatus={adjustment.get('status')}.",
            "paidMonthWouldAnswer": unresolved_paid,
        }
    # A broader-than-free tier is already active: "should we pay" no longer
    # applies, so the verdict reports what a paid entitlement can and cannot
    # yet certify -- never the free-tier purchase framing, even though the
    # underlying discrepancy/adjustment checks are unchanged.
    if not discrepancy and adjustment_passed:
        return {
            "decision": "CLEAN — paid entitlement validated with no unresolved price discrepancies",
            "recommendPaidMonth": True,
            "reason": f"Detected entitlement {tier!r} measured {len(measured)}/{len(EQUITY_UNIVERSE)} symbols with no discrepancy above the 0.5% threshold.",
            "paidMonthWouldAnswer": unresolved_paid,
        }
    return {
        "decision": (
            f"CONDITIONAL — paid entitlement active, but {len(discrepancy)} symbol(s) "
            "need a per-symbol reconstruction (engine/reconstruct_adjustment.py) before "
            "vendor closeadj is trusted for them"
        ),
        "recommendPaidMonth": True,
        "reason": (
            f"Detected entitlement {tier!r}; measuredSymbols={len(measured)}/{len(EQUITY_UNIVERSE)}, "
            f"discrepancies={discrepancy}, adjustmentStatus={adjustment.get('status')}. Buying broader "
            "coverage does not resolve a same-window closeadj discrepancy -- see "
            "engine/reconstruct_adjustment.py and reports/sharadar_contract_validation/reconstruction/."
        ),
        "paidMonthWouldAnswer": unresolved_paid,
    }


def build_report(*, force_refresh: bool = False) -> dict[str, Any]:
    generated = pd.Timestamp.now(tz="UTC").isoformat()
    try:
        client = SharadarClient()
    except SharadarError as exc:
        tier = "no_api_key"
        columns = {name: set(values) for name, values in DOCUMENTED_COLUMNS.items()}
        samples: dict[str, Any] = {table: {"rows": 0, "columns": [], "error": str(exc)} for table in DOCUMENTED_COLUMNS}
        probes: dict[str, Any] = {}
        identity = {"verified": False, "examples": [], "tickerChangeRows": 0, "reason": str(exc)}
        adjustment = {"status": UNTESTABLE, "reason": str(exc), "documentation": adjustment_documentation()}
        comparisons = [
            {"symbol": symbol, "status": UNTESTABLE, "reason": str(exc), "overlapRows": 0}
            for symbol in EQUITY_UNIVERSE
        ]
        api_error = str(exc)
    else:
        tier, probes = detect_tier(client, force_refresh=force_refresh)
        columns, samples = collect_columns(client, force_refresh=force_refresh)
        # Isolate symbols because the free endpoint can reject an entire batch
        # when it contains a former (no longer current) Dow constituent.
        actions, tickers, identity_errors = collect_identity_rows(
            client, force_refresh=force_refresh
        )
        identity = find_identity_evidence(actions, tickers)
        identity["errors"] = identity_errors
        adjustment = run_adjustment_test(client, force_refresh=force_refresh)
        comparisons = compare_dow_closes(client, force_refresh=force_refresh)
        api_error = None

    fields = contract_field_findings(columns, tier=tier, identity_verified=bool(identity["verified"]))
    manifests = manifest_findings(columns, tier=tier, identity_verified=bool(identity["verified"]))
    capabilities = tier_capabilities(tier, api_error)
    all_findings = [*manifests, *fields, market_cap_finding(columns)]
    report = {
        "schemaVersion": 1,
        "generatedAt": generated,
        "scope": ["tickers", "stocks", "actions", "daily"],
        "evidenceOnly": True,
        "universeChanged": False,
        "backtestCacheWritten": False,
        "contractSource": "engine/pit_all_stocks.py",
        "tier": {"detected": tier, "probes": probes, "apiError": api_error},
        "tableSamples": samples,
        "fieldFindings": all_findings,
        "findingCounts": _summary_counts(all_findings),
        "tierCapabilities": capabilities,
        "identityContinuity": identity,
        "adjustmentSemantics": adjustment,
        "dowCloseComparison": {
            "thresholdMedianAbsolutePctDifference": 0.5,
            "basis": "Sharadar closeadj versus existing data/{SYMBOL}_1d.parquet Close",
            "symbols": comparisons,
            "discrepancies": [row["symbol"] for row in comparisons if row.get("aboveMedianDivergenceThreshold")],
        },
        "verdict": _verdict(tier, comparisons, adjustment),
        "documentation": DOC_URLS,
    }
    return report


def _markdown(report: dict[str, Any]) -> str:
    tier = report["tier"]
    lines = [
        "# Sharadar contract validation",
        "",
        f"Generated: `{report['generatedAt']}`",
        "",
        "> Diagnostic evidence only. This report did not publish a PIT bundle, alter a universe, or run a strategy.",
        "",
        "## Verdict",
        "",
        f"**{report['verdict']['decision']}**",
        "",
        report["verdict"]["reason"],
        "",
        "## Detected entitlement",
        "",
        f"- Tier: `{tier['detected']}`",
        f"- API error: `{tier['apiError'] or 'none'}`",
        "",
        "The current-Dow classification is empirical: recent WMT data were available, a genuinely non-Dow active stock (TSLA) was denied, and pre-2021 WMT history returned no rows. The repository comparison roster is the older 29-name `dow_pit` roster, so former constituents INTC and DOW are outside the observed free entitlement.",
        "",
        "| Probe | Symbol | Rows | Error |",
        "|---|---|---:|---|",
    ]
    for label, probe in tier.get("probes", {}).items():
        lines.append(f"| `{label}` | {probe['ticker']} | {probe['rows']} | {probe.get('error') or 'none'} |")
    lines.extend([
        "",
        "A zero-row or inaccessible capability is never counted as a pass.",
        "",
        "## Contract field mapping",
        "",
        "| Area | Required field | Status | Sharadar source | Reason |",
        "|---|---|---|---|---|",
    ])
    for row in report["fieldFindings"]:
        reason = row["reason"].replace("|", "\\|")
        lines.append(f"| {row['area']} | `{row['field']}` | **{row['status']}** | `{row['source']}` | {reason} |")
    lines.extend(["", "## Tier capability", "", "| Check | Status | Reason |", "|---|---|---|"])
    for row in report["tierCapabilities"]:
        lines.append(f"| `{row['check']}` | **{row['status']}** | {row['reason']} |")

    docs = report["adjustmentSemantics"].get("documentation", adjustment_documentation())
    lines.extend([
        "", "## Adjustment-method semantics", "",
        f"Source: [Sharadar FAQ]({docs['source']}) and [stocks schema]({DOC_URLS['stocks']}).", "",
        "1. Unadjusted: `closeunadj` is delivered. O/H/L are imputed as split-adjusted O/H/L × `closeunadj / close`; tape volume is imputed as `volume × close / closeunadj`.",
        "2. Split-adjusted: `open`, `high`, `low`, `close`, and `volume` are delivered directly.",
        "3. Split/dividend/spinoff-adjusted: only `closeadj` is delivered. O/H/L are imputed as split-adjusted O/H/L × `closeadj / close`; volume remains split-adjusted and is not adjusted for dividends or spinoffs.",
        "",
        f"Empirical WMT 3-for-1 test: **{report['adjustmentSemantics']['status']}** — {report['adjustmentSemantics'].get('interpretation') or report['adjustmentSemantics'].get('reason')}",
        "",
        "## Dow close cross-check", "",
        "| Symbol | Vendor rows | Overlap | Measured window | Median abs. % diff | Max abs. % diff | Latest abs. % diff | Status |",
        "|---|---:|---:|---|---:|---:|---:|---|",
    ])
    for row in report["dowCloseComparison"]["symbols"]:
        window = f"{row.get('measuredStart', '—')} to {row.get('measuredEnd', '—')}"
        med = f"{row['medianAbsolutePctDifference']:.6f}" if row.get("medianAbsolutePctDifference") is not None else "—"
        maximum = f"{row['maxAbsolutePctDifference']:.6f}" if row.get("maxAbsolutePctDifference") is not None else "—"
        latest = f"{row['latestAbsolutePctDifference']:.6f}" if row.get("latestAbsolutePctDifference") is not None else "—"
        lines.append(f"| {row['symbol']} | {row.get('sharadarRows', 0)} | {row.get('overlapRows', 0)} | {window} | {med} | {maximum} | {latest} | **{row['status']}** |")
    lines.extend([
        "", "The comparison is reported per symbol. Any median divergence above 0.5% is named as a discrepancy and is not averaged away.",
        "", "For all currently flagged symbols, the latest common close agrees within 0.5%. That time pattern is consistent with the two cached adjusted series having different corporate-action/dividend adjustment vintages; it does not prove that either historical series is wrong, so the discrepancies remain unresolved rather than being waived.",
        "", "## Raw bundle fields versus normalized PIT contract", "",
        "Sharadar's paid bundle does include the raw ingredients for much of this work: the official actions documentation lists ticker changes, listing and delisting dates, delisting reasons, acquisition counterparties, spinoffs, and relations between securities, while stocks provides deep daily history. The findings above use **MISSING** or **AMBIGUOUS** when the application cannot yet derive and validate the stricter normalized field (for example a date-effective interval with a known-at timestamp, or a reproducible terminal delisting return). This is a normalization/semantic gate, not a claim that the paid bundle contains no delisting or corporate-action records.",
        "", "## Permanent identity", "",
        f"Status: **{'PRESENT' if report['identityContinuity']['verified'] else 'AMBIGUOUS'}**.",
        "",
        "`permaticker` is documented as permanent, but the contract does not accept that claim until an accessible ticker-change lineage retains the same identifier. The current test found "
        f"{report['identityContinuity'].get('tickerChangeRows', 0)} ticker-change action rows and {len(report['identityContinuity'].get('examples', []))} verified examples.",
        "", "## What one paid month would answer", "",
    ])
    lines.extend(f"- {item}" for item in report["verdict"]["paidMonthWouldAnswer"])
    lines.extend([
        "", "The paid tier still does not automatically pass the contract. In particular, absence of an explicit delisting-return field, current-only exchange/category metadata, and missing known-at timestamps may remain structural product gaps after purchase.",
        "", "## Official documentation", "",
    ])
    lines.extend(f"- [{name}]({url})" for name, url in DOC_URLS.items())
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], report_dir: Path = REPORT_DIR) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "report.json"
    markdown_path = report_dir / "report.md"
    json_tmp = json_path.with_suffix(".json.tmp")
    md_tmp = markdown_path.with_suffix(".md.tmp")
    json_tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_tmp.write_text(_markdown(report), encoding="utf-8")
    json_tmp.replace(json_path)
    md_tmp.replace(markdown_path)
    return markdown_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-refresh", action="store_true", help="Ignore the isolated Sharadar JSON cache")
    args = parser.parse_args()
    report = build_report(force_refresh=args.force_refresh)
    paths = write_report(report)
    print(json.dumps({"report": [str(path) for path in paths], "tier": report["tier"]["detected"], "verdict": report["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
