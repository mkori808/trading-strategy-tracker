"""Fail-closed data contract for a survivorship-free U.S. all-stocks universe.

Yahoo and today's index constituents cannot satisfy this contract.  A bundle
must use permanent security identifiers and contain date-effective identity
records plus total-return daily histories, including delisting returns.  CRSP
is the reference source, but the normalized contract is vendor-neutral so a
licensed equivalent can be used without changing the backtest engine.

The module deliberately separates *dataset readiness* from strategy results.
If any provenance flag or required file/column is missing, the registered
universe remains visible in the UI but is not runnable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pyarrow.parquet as pq


BUNDLE_DIR = Path(__file__).resolve().parent.parent / "data" / "pit_us_all_stocks"
MANIFEST_FILE = "manifest.json"
SECURITY_HISTORY_FILE = "security_history.parquet"
DAILY_PATH = "daily"

REQUIRED_MANIFEST_TRUE = (
    "survivorshipFree",
    "delistedSecuritiesIncluded",
    "delistingReturnsIncluded",
    "tickerHistoryIncluded",
    "corporateActionsIncluded",
    "pointInTimeSecurityTypes",
    "historicalVolumeIncluded",
)
REQUIRED_SECURITY_COLUMNS = {
    "security_id", "ticker", "effective_start", "effective_end", "known_at",
    "is_us_listed", "is_common_stock", "security_type", "exchange",
    "is_acquired", "delisting_reason",
}
REQUIRED_DAILY_COLUMNS = {
    "security_id", "date", "Open", "High", "Low", "Close", "RawClose", "Volume",
    "DelistingReturn",
}


@dataclass(frozen=True)
class PitDatasetStatus:
    ready: bool
    summary: str
    bundle_path: str
    missing_artifacts: tuple[str, ...]
    invalid_reasons: tuple[str, ...]
    source: str | None = None
    snapshot_id: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    security_count: int | None = None
    delisted_count: int | None = None
    acquired_count: int | None = None
    ticker_change_count: int | None = None
    market_cap_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "ready": payload["ready"],
            "summary": payload["summary"],
            "bundlePath": payload["bundle_path"],
            "missingArtifacts": list(payload["missing_artifacts"]),
            "invalidReasons": list(payload["invalid_reasons"]),
            "source": payload["source"],
            "snapshotId": payload["snapshot_id"],
            "coverageStart": payload["coverage_start"],
            "coverageEnd": payload["coverage_end"],
            "securityCount": payload["security_count"],
            "delistedCount": payload["delisted_count"],
            "acquiredCount": payload["acquired_count"],
            "tickerChangeCount": payload["ticker_change_count"],
            "marketCapAvailable": payload["market_cap_available"],
        }


def _daily_files(bundle_dir: Path) -> list[Path]:
    path = bundle_dir / DAILY_PATH
    if path.is_file() and path.suffix == ".parquet":
        return [path]
    if path.is_dir():
        return sorted(path.rglob("*.parquet"))
    alternate = bundle_dir / f"{DAILY_PATH}.parquet"
    return [alternate] if alternate.exists() else []


def inspect_dataset(bundle_dir: Path = BUNDLE_DIR) -> PitDatasetStatus:
    manifest_path = bundle_dir / MANIFEST_FILE
    history_path = bundle_dir / SECURITY_HISTORY_FILE
    missing: list[str] = []
    if not manifest_path.exists():
        missing.append(MANIFEST_FILE)
    if not history_path.exists():
        missing.append(SECURITY_HISTORY_FILE)
    if not _daily_files(bundle_dir):
        missing.append("daily/**/*.parquet (or daily.parquet)")
    if missing:
        return PitDatasetStatus(
            False,
            "Licensed point-in-time security-master and delisted-price data are not installed",
            str(bundle_dir), tuple(missing), (),
        )

    invalid: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PitDatasetStatus(
            False, "The PIT dataset manifest is unreadable", str(bundle_dir), (), (str(exc),),
        )
    if manifest.get("schemaVersion") != 1:
        invalid.append("manifest.schemaVersion must equal 1")
    for key in REQUIRED_MANIFEST_TRUE:
        if manifest.get(key) is not True:
            invalid.append(f"manifest.{key} must be true")
    if manifest.get("priceBasis") != "total_return_adjusted_ohlcv":
        invalid.append("manifest.priceBasis must be 'total_return_adjusted_ohlcv'")
    for key in ("source", "snapshotId", "coverageStart", "coverageEnd"):
        if not manifest.get(key):
            invalid.append(f"manifest.{key} is required")
    try:
        coverage_start = date.fromisoformat(str(manifest.get("coverageStart")))
        coverage_end = date.fromisoformat(str(manifest.get("coverageEnd")))
        if coverage_end <= coverage_start:
            invalid.append("manifest coverageEnd must be after coverageStart")
    except ValueError:
        coverage_start = coverage_end = None
        invalid.append("manifest coverage dates must use YYYY-MM-DD")

    try:
        security_history = pd.read_parquet(history_path)
    except Exception as exc:  # noqa: BLE001 - status must report corrupt bundles
        security_history = pd.DataFrame()
        invalid.append(f"cannot read {SECURITY_HISTORY_FILE}: {exc}")
    missing_security_columns = sorted(REQUIRED_SECURITY_COLUMNS - set(security_history.columns))
    if missing_security_columns:
        invalid.append("security_history missing columns: " + ", ".join(missing_security_columns))

    daily_columns: set[str] = set()
    for daily_path in _daily_files(bundle_dir):
        try:
            # Inspect metadata only. A real all-stocks daily partition can
            # contain millions of rows and registry/UI reads must never load it.
            fragment_columns = set(pq.ParquetFile(daily_path).schema.names)
            daily_columns |= fragment_columns
            missing_daily_columns = sorted(REQUIRED_DAILY_COLUMNS - fragment_columns)
            if missing_daily_columns:
                invalid.append(
                    f"{daily_path.name} missing columns: " + ", ".join(missing_daily_columns)
                )
        except Exception as exc:  # noqa: BLE001
            invalid.append(f"cannot read daily price parquet {daily_path.name}: {exc}")

    security_count = delisted_count = acquired_count = ticker_change_count = None
    if not security_history.empty and "security_id" in security_history:
        ids = security_history["security_id"].astype(str)
        security_count = int(ids.nunique())
        if security_count < 2:
            invalid.append("security history must contain at least two permanent security IDs")
        if "delisting_reason" in security_history:
            delisted_count = int(
                security_history.loc[security_history["delisting_reason"].notna(), "security_id"]
                .astype(str).nunique()
            )
        if "is_acquired" in security_history:
            acquired_count = int(
                security_history.loc[security_history["is_acquired"].fillna(False).astype(bool), "security_id"]
                .astype(str).nunique()
            )
        if "ticker" in security_history:
            ticker_counts = security_history.assign(_id=ids).groupby("_id")["ticker"].nunique()
            ticker_change_count = int((ticker_counts > 1).sum())
        if not ids.str.strip().all():
            invalid.append("security_id values must be non-empty")
    if delisted_count == 0:
        invalid.append("security history contains no delisted securities")
    if manifest.get("delistedSecuritiesIncluded") is True and delisted_count is None:
        invalid.append("delisted-security inclusion cannot be verified")

    ready = not invalid
    summary = (
        "PIT security master, historical identities, and delisted total-return prices are validated"
        if ready else "PIT dataset is installed but failed its integrity contract"
    )
    return PitDatasetStatus(
        ready, summary, str(bundle_dir), (), tuple(invalid),
        source=manifest.get("source"), snapshot_id=manifest.get("snapshotId"),
        coverage_start=coverage_start.isoformat() if coverage_start else None,
        coverage_end=coverage_end.isoformat() if coverage_end else None,
        security_count=security_count, delisted_count=delisted_count,
        acquired_count=acquired_count, ticker_change_count=ticker_change_count,
        market_cap_available="MarketCap" in daily_columns,
    )


def require_dataset(bundle_dir: Path = BUNDLE_DIR) -> PitDatasetStatus:
    status = inspect_dataset(bundle_dir)
    if not status.ready:
        detail = [*status.missing_artifacts, *status.invalid_reasons]
        suffix = "; missing/invalid: " + "; ".join(detail) if detail else ""
        raise ValueError(status.summary + suffix)
    return status


def security_ids(bundle_dir: Path = BUNDLE_DIR) -> list[str]:
    require_dataset(bundle_dir)
    history = pd.read_parquet(bundle_dir / SECURITY_HISTORY_FILE, columns=["security_id"])
    return sorted(history["security_id"].astype(str).unique())


@dataclass
class PitEligibilityUniverse:
    """Loaded normalized histories and a cached date-effective eligibility rule."""

    status: PitDatasetStatus
    security_history: pd.DataFrame
    bars_by_security: dict[str, pd.DataFrame]
    raw_close_by_security: dict[str, pd.Series]
    market_cap_by_security: dict[str, pd.Series]
    membership_at: Callable[[date], set[str]]
    eligibility_counts: dict[str, int]
    exclusion_counts: dict[str, dict[str, int]]
    eligible_members_by_date: dict[str, set[str]]

    @property
    def security_ids(self) -> list[str]:
        return sorted(self.bars_by_security)

    def ticker_at(self, security_id: str, as_of: date) -> str:
        rows = self.security_history[
            (self.security_history["security_id"] == security_id)
            & (self.security_history["effective_start"] <= pd.Timestamp(as_of))
            & (self.security_history["effective_end"] >= pd.Timestamp(as_of))
            & (self.security_history["known_at"] <= pd.Timestamp(as_of))
        ]
        if rows.empty:
            rows = self.security_history[
                (self.security_history["security_id"] == security_id)
                & (self.security_history["effective_start"] <= pd.Timestamp(as_of))
            ].sort_values("effective_end")
        ticker = str(rows.iloc[-1]["ticker"]) if not rows.empty else "UNKNOWN"
        return f"{ticker} [{security_id}]"

    def market_cap_classifications_at(self, as_of: date) -> dict[str, dict[str, float | str]]:
        """Historical tiers from the last cap observation before `as_of`.

        An absent MarketCap column produces an empty mapping rather than a
        current-snapshot substitution. See engine.pit_market_cap.
        """
        from engine.pit_market_cap import classifications_at

        return classifications_at(self.market_cap_by_security, as_of)

    def integrity_diagnostics(self, top_n: int) -> dict[str, Any]:
        used = set().union(*self.eligible_members_by_date.values()) if self.eligible_members_by_date else set()
        used_rows = self.security_history[self.security_history["security_id"].isin(used)]
        delisted = set(
            used_rows.loc[used_rows["delisting_reason"].notna(), "security_id"].astype(str)
        )
        acquired = set(
            used_rows.loc[used_rows["is_acquired"].fillna(False).astype(bool), "security_id"].astype(str)
        )
        ticker_counts = used_rows.groupby("security_id")["ticker"].nunique() if not used_rows.empty else pd.Series(dtype=int)
        counts = list(self.eligibility_counts.values())
        complete_dates = sum(
            reasons.get("missingPriceHistory", 0) == 0
            for reasons in self.exclusion_counts.values()
        )
        current = set(
            self.security_history.loc[
                self.security_history["effective_end"] >= pd.Timestamp(self.status.coverage_end),
                "security_id",
            ].astype(str)
        ) if self.status.coverage_end else set()
        return {
            "dataset": self.status.to_dict(),
            "currentlyActiveSecuritiesUsed": len(used & current),
            "historicallyDelistedSecuritiesUsed": len(delisted),
            "acquiredSecuritiesUsed": len(acquired),
            "tickerChangesResolved": int((ticker_counts > 1).sum()),
            "securitiesExcludedForMissingHistoricalData": int(sum(
                reasons.get("missingPriceHistory", 0)
                for reasons in self.exclusion_counts.values()
            )),
            "completePitCoveragePct": (
                complete_dates / len(self.exclusion_counts) * 100.0
                if self.exclusion_counts else 0.0
            ),
            "averageEligibleSecurities": float(sum(counts) / len(counts)) if counts else 0.0,
            "minimumEligibleSecurities": min(counts, default=0),
            "maximumEligibleSecurities": max(counts, default=0),
            "periodsBelowTargetPositionsPct": (
                sum(count < top_n for count in counts) / len(counts) * 100.0 if counts else 100.0
            ),
            "eligibilityByDate": dict(self.eligibility_counts),
            "exclusionsByDate": dict(self.exclusion_counts),
        }

    def capacity_diagnostics(
        self,
        rebalances: pd.DataFrame,
        equity_curve: pd.Series,
        *,
        maximum_adv_participation_pct: float,
        liquidity_lookback_days: int,
    ) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        for _, row in rebalances.iterrows():
            timestamp = pd.Timestamp(row["date"])
            prior_equity = equity_curve.loc[equity_curve.index <= timestamp]
            portfolio_value = float(prior_equity.iloc[-1]) if not prior_equity.empty else float(equity_curve.iloc[0])
            for security_id, weight in row["holdings"].items():
                raw = self.raw_close_by_security.get(str(security_id))
                bars = self.bars_by_security.get(str(security_id))
                if raw is None or bars is None:
                    continue
                prior_raw = raw.loc[raw.index < timestamp].tail(liquidity_lookback_days)
                prior_volume = bars.loc[bars.index < timestamp, "Volume"].tail(liquidity_lookback_days)
                adv = float((prior_raw * prior_volume.reindex(prior_raw.index)).mean())
                target = float(weight) * portfolio_value
                participation = target / adv * 100.0 if adv > 0 else float("inf")
                observations.append({
                    "date": timestamp.isoformat(), "securityId": str(security_id),
                    "targetNotional": target, "averageDailyDollarVolume": adv,
                    "participationPct": participation,
                    "breach": participation > maximum_adv_participation_pct,
                })
        breaches = [row for row in observations if row["breach"]]
        return {
            "maximumAllowedAdvParticipationPct": maximum_adv_participation_pct,
            "maximumObservedAdvParticipationPct": max(
                (row["participationPct"] for row in observations), default=None,
            ),
            "breachCount": len(breaches),
            "observations": len(observations),
            "largestBreaches": sorted(
                breaches, key=lambda row: row["participationPct"], reverse=True,
            )[:25],
        }


def _read_daily(bundle_dir: Path, start: date, end: date) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    columns = list(REQUIRED_DAILY_COLUMNS | {"MarketCap"})
    for path in _daily_files(bundle_dir):
        available_columns = set(pq.ParquetFile(path).schema.names)
        selected_columns = [column for column in columns if column in available_columns]
        try:
            frame = pd.read_parquet(
                path, columns=selected_columns,
                filters=[("date", ">=", pd.Timestamp(start)), ("date", "<=", pd.Timestamp(end))],
            )
        except Exception:  # noqa: BLE001 - optional columns vary by licensed bundle
            frame = pd.read_parquet(path, columns=selected_columns)
        frames.append(frame)
    daily = pd.concat(frames, ignore_index=True)
    daily["date"] = pd.to_datetime(daily["date"]).dt.tz_localize(None)
    return daily[(daily["date"].dt.date >= start) & (daily["date"].dt.date <= end)]


def load_eligibility_universe(
    start: date,
    end: date,
    *,
    lookback_days: int = 189,
    minimum_price: float = 5.0,
    minimum_history_days: int = 252,
    liquidity_lookback_days: int = 60,
    minimum_average_dollar_volume: float = 1_000_000.0,
    minimum_market_cap: float | None = None,
    bundle_dir: Path = BUNDLE_DIR,
) -> PitEligibilityUniverse:
    status = require_dataset(bundle_dir)
    coverage_start = date.fromisoformat(status.coverage_start or "9999-12-31")
    coverage_end = date.fromisoformat(status.coverage_end or "0001-01-01")
    if start < coverage_start or end > coverage_end:
        raise ValueError(
            f"Requested {start} to {end}, but validated PIT coverage is "
            f"{coverage_start} to {coverage_end}"
        )
    if minimum_market_cap and not status.market_cap_available:
        raise ValueError("Historical market-cap filter requested, but MarketCap is absent from the PIT bundle")

    # Preload the full preregistered 252-day neighboring arm too. Eligibility
    # still uses the selected rule below, but robustness must not fetch a
    # different dataset after seeing the primary result.
    required_history = max(minimum_history_days, lookback_days + 1, 253, liquidity_lookback_days)
    warmup_start = start - timedelta(days=int(required_history * 1.55) + 14)
    history = pd.read_parquet(bundle_dir / SECURITY_HISTORY_FILE).copy()
    for column in ("effective_start", "effective_end", "known_at"):
        history[column] = pd.to_datetime(history[column]).dt.tz_localize(None)
    history["security_id"] = history["security_id"].astype(str)
    daily = _read_daily(bundle_dir, warmup_start, end)
    daily["security_id"] = daily["security_id"].astype(str)
    daily = daily.sort_values(["security_id", "date"])

    bars: dict[str, pd.DataFrame] = {}
    raw_close: dict[str, pd.Series] = {}
    market_caps: dict[str, pd.Series] = {}
    for security_id, rows in daily.groupby("security_id", sort=False):
        indexed = rows.set_index("date").sort_index()
        bars[security_id] = indexed[["Open", "High", "Low", "Close", "Volume"]].copy()
        raw_close[security_id] = indexed["RawClose"].astype(float)
        if "MarketCap" in indexed:
            market_caps[security_id] = indexed["MarketCap"].astype(float)

    eligibility_counts: dict[str, int] = {}
    exclusion_counts: dict[str, dict[str, int]] = {}
    cache: dict[date, set[str]] = {}
    eligible_members_by_date: dict[str, set[str]] = {}

    def membership_at(as_of: date) -> set[str]:
        if as_of in cache:
            return set(cache[as_of])
        signal_time = pd.Timestamp(as_of)
        active_rows = history[
            (history["effective_start"] < signal_time)
            & (history["effective_end"] >= signal_time)
            & (history["known_at"] < signal_time)
            & history["is_us_listed"].fillna(False).astype(bool)
            & history["is_common_stock"].fillna(False).astype(bool)
        ]
        eligible: set[str] = set()
        reasons = {
            "missingPriceHistory": 0, "insufficientHistory": 0, "belowPrice": 0,
            "belowLiquidity": 0, "belowMarketCap": 0,
        }
        for security_id in active_rows["security_id"].unique():
            security_bars = bars.get(security_id)
            raw = raw_close.get(security_id)
            if security_bars is None or raw is None:
                reasons["missingPriceHistory"] += 1
                continue
            prior = security_bars.index < signal_time
            count = int(prior.sum())
            if count < required_history:
                reasons["insufficientHistory"] += 1
                continue
            raw_prior = raw.loc[raw.index < signal_time]
            if raw_prior.empty or float(raw_prior.iloc[-1]) < minimum_price:
                reasons["belowPrice"] += 1
                continue
            volume = security_bars.loc[prior, "Volume"].tail(liquidity_lookback_days).astype(float)
            trailing_raw = raw_prior.tail(liquidity_lookback_days)
            adv = float((trailing_raw * volume.reindex(trailing_raw.index)).mean())
            if pd.isna(adv) or adv < minimum_average_dollar_volume:
                reasons["belowLiquidity"] += 1
                continue
            if minimum_market_cap:
                cap = market_caps.get(security_id)
                cap_prior = cap.loc[cap.index < signal_time] if cap is not None else pd.Series(dtype=float)
                if cap_prior.empty or float(cap_prior.iloc[-1]) < minimum_market_cap:
                    reasons["belowMarketCap"] += 1
                    continue
            eligible.add(security_id)
        cache[as_of] = eligible
        eligibility_counts[as_of.isoformat()] = len(eligible)
        exclusion_counts[as_of.isoformat()] = reasons
        eligible_members_by_date[as_of.isoformat()] = set(eligible)
        return set(eligible)

    return PitEligibilityUniverse(
        status, history, bars, raw_close, market_caps, membership_at,
        eligibility_counts, exclusion_counts, eligible_members_by_date,
    )


if __name__ == "__main__":
    print(json.dumps(inspect_dataset().to_dict(), indent=2))
