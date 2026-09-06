"""Small, isolated client for the Sharadar contract-validation pass.

This module is deliberately not imported by :mod:`engine.data`. Vendor
responses are evidence for a prospective licensed dataset, not backtest input,
and are cached under ``data/sharadar_validation_cache`` as JSON envelopes.
The API key is never included in cache keys, files, logs, or exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from engine.alpaca_client import first_env


BASE_URL = "https://api.sharadar.com/v1.0"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "sharadar_validation_cache"
ALLOWED_TABLES = frozenset({"tickers", "stocks", "actions", "daily", "sp500", "events"})
DEFAULT_REQUESTS_PER_SECOND = 4.0
DEFAULT_RETRIES = 4
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_PAGE_SIZE = 10_000


class SharadarError(RuntimeError):
    """A sanitized Sharadar request failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _RateLimiter:
    def __init__(self, max_per_second: float) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._minimum_interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._minimum_interval:
                time.sleep(self._minimum_interval - elapsed)
            self._last_call = time.monotonic()


@dataclass(frozen=True)
class QueryResult:
    table: str
    rows: list[dict[str, Any]]
    from_cache: bool
    fetched_at: str
    parameters: dict[str, Any]


class SharadarClient:
    """Rate-limited JSON REST client with an isolated, redacted cache."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        cache_dir: Path = CACHE_DIR,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        retries: int = DEFAULT_RETRIES,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = (api_key or first_env("SHARADAR_API_KEY") or "").strip()
        if not self.api_key:
            raise SharadarError(
                "SHARADAR_API_KEY is not configured; add it to .env and rerun the validator"
            )
        self.cache_dir = cache_dir
        self.retries = retries
        self.timeout_seconds = timeout_seconds
        self._limiter = _RateLimiter(requests_per_second)

    @staticmethod
    def _normalized_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        return {
            str(key): value
            for key, value in sorted(parameters.items())
            if key not in {"api_key", "format"} and value is not None
        }

    def _cache_path(self, table_name: str, parameters: dict[str, Any]) -> Path:
        identity = json.dumps(
            {"table": table_name, "parameters": self._normalized_parameters(parameters)},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / table_name / f"{digest}.json"

    def _request_json(self, table_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {**parameters, "format": "json", "api_key": self.api_key}, safe=","
        )
        url = f"{BASE_URL}/data/{urllib.parse.quote(table_name)}?{query}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "Trading-Strategy-Lab/1.0"},
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._limiter.wait()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise SharadarError(f"Sharadar {table_name} returned an unexpected JSON shape")
                return payload
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable:
                    raise SharadarError(
                        f"Sharadar {table_name} request failed with HTTP {exc.code}",
                        status_code=exc.code,
                    ) from exc
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.0 + attempt
                time.sleep(delay + random.uniform(0.0, 0.2))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1) + random.uniform(0.0, 0.2))
        raise SharadarError(
            f"Sharadar {table_name} request failed after {self.retries} attempts: "
            f"{type(last_error).__name__ if last_error else 'unknown error'}"
        ) from last_error

    def query(
        self, table_name: str, *, force_refresh: bool = False, **parameters: Any
    ) -> QueryResult:
        """Return one API page and cache it without the credential."""
        if table_name not in ALLOWED_TABLES:
            raise ValueError(f"table must be one of {sorted(ALLOWED_TABLES)}, got {table_name!r}")
        normalized = self._normalized_parameters(parameters)
        path = self._cache_path(table_name, normalized)
        if path.exists() and not force_refresh:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            return QueryResult(
                table_name, list(envelope["data"]), True, str(envelope["fetchedAt"]), normalized
            )

        payload = self._request_json(table_name, normalized)
        fetched_at = datetime.now(timezone.utc).isoformat()
        envelope = {
            "table": table_name,
            "parameters": normalized,
            "fetchedAt": fetched_at,
            "count": payload.get("count", len(payload["data"])),
            "data": payload["data"],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        temporary.replace(path)
        return QueryResult(table_name, list(payload["data"]), False, fetched_at, normalized)

    def query_all(
        self,
        table_name: str,
        *,
        force_refresh: bool = False,
        page_size: int = DEFAULT_PAGE_SIZE,
        **parameters: Any,
    ) -> QueryResult:
        """Retrieve all pages for a bounded query using Sharadar's skip API."""
        if page_size < 1 or page_size > DEFAULT_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {DEFAULT_PAGE_SIZE}")
        rows: list[dict[str, Any]] = []
        fetched_at: list[str] = []
        any_network = False
        skip = 0
        while True:
            page = self.query(
                table_name, force_refresh=force_refresh, **parameters, limit=page_size, skip=skip
            )
            rows.extend(page.rows)
            fetched_at.append(page.fetched_at)
            any_network = any_network or not page.from_cache
            if len(page.rows) < page_size:
                break
            skip += page_size
        return QueryResult(
            table=table_name,
            rows=rows,
            from_cache=not any_network,
            fetched_at=max(fetched_at),
            parameters=self._normalized_parameters(parameters),
        )
