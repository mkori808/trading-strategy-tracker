"""SEC EDGAR Form 4 (insider open-market purchase) data pipeline.

The entire "insider buying predicts returns" hypothesis stands or falls on
using only information that was PUBLIC at the time of trade entry. Form 4 has
a 2-business-day filing deadline from the transaction date -- an insider can
buy Monday and not file until Wednesday -- so the tradeable signal date is the
filing's FILED-AT timestamp, never the transaction date. Using the transaction
date is the single most common bug in insider-trading backtests: it makes the
strategy react to information before it existed. See `compute_signal_date`
and `Check 1 / Check 5` in `run_validation` below, which exist specifically to
catch this class of bug rather than assume the parser got it right.

Two more look-ahead-adjacent details this pipeline gets from the raw filing
rather than approximating:
  - The SEC's atom filing-history feed's `<updated>` timestamp already carries
    the correct America/New_York UTC offset (SEC's own server-side
    acceptance time, DST-aware) -- that IS the filed-at timestamp, not a
    derived value. No timezone math is applied to it.
  - A filing accepted after the 4pm ET close (or on a non-trading day --
    EDGAR accepts filings any day of the week) can't move price until the
    next session, so `compute_signal_date` rolls it forward using the same
    cached SPY trading-day calendar every other engine module uses
    (`engine/data.py`), not a hardcoded weekday check that would silently
    mishandle market holidays.

Caching: every filing's accession number is recorded in `fetched_accessions`
the moment it's successfully parsed (even if it yielded zero qualifying P
transactions), so re-running this module never re-fetches a filing it has
already resolved. A network/parse failure does NOT get marked fetched, so a
transient error is retried on the next run rather than silently and
permanently dropping that filing.

Rate limiting: SEC's fair-access policy caps automated access at 10
requests/second and requires a descriptive User-Agent with a contact email
(see SEC_EDGAR_USER_AGENT below / the .env var of the same name). This module
self-throttles to 8/second -- under the limit, not at it -- via a single
shared rate limiter that every HTTP call funnels through (`_fetch_url`).

Only P (open-market/private purchase) transactions from the non-derivative
transaction table are stored -- option exercises, awards/grants, gifts, and
dispositions are excluded at parse time, not filtered later.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import pandas as pd

from engine import data as data_module
from engine.alpaca_client import first_env

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "edgar_form4.db"
TICKER_CIK_CACHE_PATH = DATA_DIR / "sec_company_tickers.json"

SEC_EDGAR_USER_AGENT = first_env("SEC_EDGAR_USER_AGENT") or (
    "Trading Strategy Lab (research/backtesting; contact michaelkori16@gmail.com)"
)

FULL_TEXT_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
BROWSE_EDGAR_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ATOM_NS = "{http://www.w3.org/2005/Atom}"

PAGE_SIZE = 100  # browse-edgar's observed page size; detected, not assumed, per page
QUALIFYING_TRANSACTION_CODE = "P"  # open-market/private purchase only

# Commit cadence within one ticker's filing loop -- see fetch_form4_for_universe.
COMMIT_EVERY_N_FILINGS = 200

# SEC's fair-access ceiling is 10 req/s -- stay under it, not at it.
_TARGET_REQUESTS_PER_SECOND = 8.0
_HTTP_TIMEOUT_SECONDS = 20
_HTTP_RETRIES = 3

MARKET_CLOSE_ET = dtime(16, 0)


# --------------------------------------------------------------------------- #
# Rate-limited HTTP
# --------------------------------------------------------------------------- #


class _RateLimiter:
    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second
        self._last_call = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


_rate_limiter = _RateLimiter(_TARGET_REQUESTS_PER_SECOND)


def _fetch_url(url: str) -> bytes:
    """GET `url` with SEC's required User-Agent, rate-limited and retried.
    Raises on repeated failure -- callers decide whether that's fatal for the
    whole run (universe/ticker map) or skippable-and-retry-later (one filing).
    """
    req = urllib.request.Request(url, headers={"User-Agent": SEC_EDGAR_USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(_HTTP_RETRIES):
        _rate_limiter.wait()
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429 or 500 <= exc.code < 600:
                time.sleep(1.0 + attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:  # noqa: PERF203
            last_exc = exc
            time.sleep(0.5 + attempt)
    raise RuntimeError(f"Failed to fetch {url} after {_HTTP_RETRIES} attempts: {last_exc}")


# --------------------------------------------------------------------------- #
# Ticker -> CIK
# --------------------------------------------------------------------------- #

_ticker_cik_map: dict[str, str] | None = None


def _load_ticker_cik_map(force_refresh: bool = False) -> dict[str, str]:
    global _ticker_cik_map
    if _ticker_cik_map is not None and not force_refresh:
        return _ticker_cik_map

    if TICKER_CIK_CACHE_PATH.exists() and not force_refresh:
        raw = json.loads(TICKER_CIK_CACHE_PATH.read_text())
    else:
        raw = json.loads(_fetch_url(COMPANY_TICKERS_URL).decode("utf-8"))
        DATA_DIR.mkdir(exist_ok=True)
        TICKER_CIK_CACHE_PATH.write_text(json.dumps(raw))

    _ticker_cik_map = {
        entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in raw.values()
    }
    return _ticker_cik_map


def cik_for_ticker(ticker: str) -> str | None:
    """10-digit zero-padded CIK for `ticker`, or None if SEC's bulk mapping
    (company_tickers.json, cached locally) has no match -- callers must
    exclude the symbol rather than guess a CIK."""
    return _load_ticker_cik_map().get(ticker.upper())


# --------------------------------------------------------------------------- #
# Trading-day calendar (reused for the after-hours / weekend roll-forward)
# --------------------------------------------------------------------------- #


def trading_days_index(start: date, end: date) -> pd.DatetimeIndex:
    """Trading days in [start, end], from the same cached SPY pipeline every
    other backtest module reads (engine/data.py) -- so the calendar this
    module rolls forward against is the same one the rest of the project
    already trusts, not a second, independently-maintained one."""
    bars = data_module.get_bars("SPY", "1d", start, end)
    if bars.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(sorted({ts.normalize() for ts in bars.index}))


def compute_signal_date(filed_at: pd.Timestamp, trading_days: frozenset[date]) -> date:
    """The first date a Form 4 filing's information could actually be traded
    on: the filed date itself if filed on a trading day before the 4pm ET
    close, otherwise rolled forward to the next trading day (after-hours
    filings, and filings made on non-trading days -- EDGAR accepts filings
    any day of the week, including weekends)."""
    filed_date = filed_at.date()
    after_hours = filed_at.time() >= MARKET_CLOSE_ET
    if not after_hours and filed_date in trading_days:
        return filed_date
    later = sorted(d for d in trading_days if d > filed_date)
    if later:
        return later[0]
    # No trading-day data this far forward (edge of the cached SPY window) --
    # fall back to the raw filed date rather than raising. Should not happen
    # inside a real backtest window; the caller fetches a generous horizon.
    return filed_date


# --------------------------------------------------------------------------- #
# Atom filing-history feed (per-issuer Form 4 listing)
# --------------------------------------------------------------------------- #


def _atom_find(elem: ET.Element | None, tag: str) -> ET.Element | None:
    return None if elem is None else elem.find(f"{ATOM_NS}{tag}")


def _atom_text(elem: ET.Element | None, tag: str) -> str | None:
    found = _atom_find(elem, tag)
    return found.text.strip() if found is not None and found.text else None


def _parse_atom_page(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    entries = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        content = _atom_find(entry, "content")
        acc = _atom_text(content, "accession-number")
        filing_date = _atom_text(content, "filing-date")
        updated = _atom_text(entry, "updated")
        if not acc or not filing_date or not updated:
            continue
        entries.append({"accession_no": acc, "filing_date": filing_date, "updated": updated})
    return entries


# browse-edgar's `start=` offset 503s past roughly 5100 for high-volume
# issuers (large financials -- GS and JPM's Form 4 volume alone hit this in
# well under a year, driven by a large Section-16 officer roster). Reset well
# before that via the `dateb` cursor (filings on/before a date, offset back to
# 0) rather than paging `start` indefinitely -- verified directly against
# EDGAR that `dateb` resets the window without losing entries (duplicates
# across the reset boundary are deduped via `seen`, which costs a few
# harmless re-fetched pages, not data).
_MAX_PAGE_OFFSET = 4000


def list_form4_filings(issuer_cik: str, start: date, end: date) -> list[dict]:
    """Every Form 4 filing where `issuer_cik` is the ISSUER, filing-date in
    [start, end], paginated until either a page comes back short (end of
    history) or the oldest entry on a page predates `start`.

    `owner=exclude` (not `include`) is deliberate: `include` also returns
    filings where this CIK merely appears as a REPORTING OWNER on someone
    else's Form 4 -- real for GS specifically, a large broker-dealer that
    itself files as a >=10% holder of small unrelated issuers (observed:
    GS's CIK pulled in Form 4s for BACQ, SG, QVCGP -- none of them Dow
    names -- under `owner=include`). `exclude` restricts to filings where
    this CIK is the issuer being reported ON, which is the only case this
    universe-driven fetch (call site: `fetch_form4_for_universe`) wants.
    `parse_form4_xml`'s caller also double-checks issuer_cik against the
    ticker's own CIK before storing, as a second, independent guard against
    this same class of contamination."""
    out: list[dict] = []
    seen: set[str] = set()
    dateb: date | None = end + timedelta(days=1)
    while True:
        offset = 0
        page_had_older = False
        ran_out = False
        oldest_seen_this_window: date | None = None
        while True:
            dateb_str = dateb.strftime("%Y%m%d") if dateb else ""
            url = (
                f"{BROWSE_EDGAR_URL}?action=getcompany&CIK={issuer_cik}&type=4"
                f"&dateb={dateb_str}&owner=exclude&count={PAGE_SIZE}&start={offset}&output=atom"
            )
            entries = _parse_atom_page(_fetch_url(url))
            if not entries:
                ran_out = True
                break
            for e in entries:
                fdate = datetime.strptime(e["filing_date"], "%Y-%m-%d").date()
                oldest_seen_this_window = (
                    fdate if oldest_seen_this_window is None else min(oldest_seen_this_window, fdate)
                )
                if e["accession_no"] in seen:
                    continue
                seen.add(e["accession_no"])
                if fdate < start:
                    page_had_older = True
                    continue
                if fdate > end:
                    continue
                out.append(e)
            if page_had_older or len(entries) < PAGE_SIZE:
                ran_out = True
                break
            offset += PAGE_SIZE
            if offset >= _MAX_PAGE_OFFSET:
                break  # reset the dateb cursor instead of paging deeper
        if ran_out:
            return out
        if oldest_seen_this_window is None or (dateb is not None and oldest_seen_this_window >= dateb):
            return out  # safety: cursor didn't move, stop rather than loop forever
        dateb = oldest_seen_this_window


def _filing_xml_url(issuer_cik: str, accession_no: str) -> str | None:
    """The primary Form 4 XML document's URL for one filing, resolved from
    the filing's own directory listing rather than guessed by naming
    convention (observed filenames vary: wk-form4_*.xml, doc4.xml,
    ownership.xml, primary_doc.xml, ...)."""
    acc_nodash = accession_no.replace("-", "")
    cik_int = str(int(issuer_cik))
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
    payload = json.loads(_fetch_url(index_url))
    items = payload.get("directory", {}).get("item", [])
    # The primary ownership document is the .xml item with a real byte size;
    # index/txt siblings in the same listing report an empty size field.
    xml_items = [it for it in items if it.get("name", "").endswith(".xml") and it.get("size")]
    if not xml_items:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{xml_items[0]['name']}"


# --------------------------------------------------------------------------- #
# Form 4 XML parsing
# --------------------------------------------------------------------------- #


@dataclass
class Form4Transaction:
    accession_no: str
    transaction_index: int
    issuer_cik: str
    issuer_ticker: str
    issuer_name: str
    filer_cik: str
    filer_name: str
    filed_at: str  # ISO8601 with UTC offset -- the true filed-at timestamp
    signal_date: str  # ISO date -- see compute_signal_date
    transaction_date: str  # ISO date, from the filing itself
    transaction_code: str
    shares_transacted: float
    price_per_share: float
    transaction_value: float
    shares_owned_following: float | None
    shares_owned_before: float | None  # derived: following -/+ shares by A/D code
    pct_change_holdings: float | None
    ownership_nature: str | None
    security_title: str | None
    form_url: str
    fetched_at: str


def _text(elem: ET.Element | None, path: str) -> str | None:
    if elem is None:
        return None
    found = elem.find(path)
    if found is None or found.text is None:
        return None
    stripped = found.text.strip()
    return stripped or None


def _float(elem: ET.Element | None, path: str) -> float | None:
    raw = _text(elem, path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_form4_xml(
    xml_bytes: bytes,
    *,
    accession_no: str,
    filed_at: pd.Timestamp,
    form_url: str,
    trading_days: frozenset[date],
) -> list[Form4Transaction]:
    """Qualifying (code == 'P', shares > 0) purchase transactions from one
    Form 4 XML document. Only the non-derivative transaction table is read --
    open-market common-stock purchases live there; the derivative table (option
    exercises/grants) is out of scope by construction, not filtered after the
    fact. Joint filings with multiple <reportingOwner> blocks use the first
    owner as the filer of record -- a disclosed simplification, not a silent
    one; joint Form 4s are uncommon for single-purchase signals."""
    root = ET.fromstring(xml_bytes)

    issuer = root.find("issuer")
    issuer_cik = _text(issuer, "issuerCik") or ""
    issuer_name = _text(issuer, "issuerName") or ""
    issuer_ticker = _text(issuer, "issuerTradingSymbol") or ""

    owner = root.find("reportingOwner")
    owner_id = owner.find("reportingOwnerId") if owner is not None else None
    filer_cik = _text(owner_id, "rptOwnerCik") or ""
    filer_name = _text(owner_id, "rptOwnerName") or ""

    signal_date = compute_signal_date(filed_at, trading_days)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    out: list[Form4Transaction] = []
    table = root.find("nonDerivativeTable")
    if table is None:
        return out

    for idx, txn in enumerate(table.findall("nonDerivativeTransaction")):
        code = _text(txn, "transactionCoding/transactionCode")
        if code != QUALIFYING_TRANSACTION_CODE:
            continue

        txn_date = _text(txn, "transactionDate/value")
        shares = _float(txn, "transactionAmounts/transactionShares/value")
        price = _float(txn, "transactionAmounts/transactionPricePerShare/value")
        if not txn_date or shares is None or price is None or shares <= 0:
            continue  # incomplete/malformed row -- excluded, not guessed

        ad_code = _text(txn, "transactionAmounts/transactionAcquiredDisposedCode/value")
        following = _float(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        before = None
        if following is not None:
            before = following - shares if ad_code != "D" else following + shares
        pct_change = (shares / before * 100) if before and before > 0 else None

        out.append(
            Form4Transaction(
                accession_no=accession_no,
                transaction_index=idx,
                issuer_cik=issuer_cik,
                issuer_ticker=issuer_ticker,
                issuer_name=issuer_name,
                filer_cik=filer_cik,
                filer_name=filer_name,
                filed_at=filed_at.isoformat(),
                signal_date=signal_date.isoformat(),
                transaction_date=txn_date,
                transaction_code=code,
                shares_transacted=shares,
                price_per_share=price,
                transaction_value=shares * price,
                shares_owned_following=following,
                shares_owned_before=before,
                pct_change_holdings=pct_change,
                ownership_nature=_text(txn, "ownershipNature/directOrIndirectOwnership/value"),
                security_title=_text(txn, "securityTitle/value"),
                form_url=form_url,
                fetched_at=fetched_at,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# SQLite storage
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS form4_transactions (
    accession_no TEXT NOT NULL,
    transaction_index INTEGER NOT NULL,
    issuer_cik TEXT NOT NULL,
    issuer_ticker TEXT,
    issuer_name TEXT,
    filer_cik TEXT NOT NULL,
    filer_name TEXT,
    filed_at TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    transaction_code TEXT NOT NULL,
    shares_transacted REAL,
    price_per_share REAL,
    transaction_value REAL,
    shares_owned_following REAL,
    shares_owned_before REAL,
    pct_change_holdings REAL,
    ownership_nature TEXT,
    security_title TEXT,
    form_url TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (accession_no, transaction_index)
);
CREATE INDEX IF NOT EXISTS idx_form4_filed_at ON form4_transactions(filed_at);
CREATE INDEX IF NOT EXISTS idx_form4_signal_date ON form4_transactions(signal_date);
CREATE INDEX IF NOT EXISTS idx_form4_issuer_ticker ON form4_transactions(issuer_ticker);
CREATE INDEX IF NOT EXISTS idx_form4_filer_cik ON form4_transactions(filer_cik);

CREATE TABLE IF NOT EXISTS fetched_accessions (
    accession_no TEXT PRIMARY KEY,
    issuer_cik TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    n_qualifying_transactions INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

_INSERT_SQL = """
INSERT OR REPLACE INTO form4_transactions (
    accession_no, transaction_index, issuer_cik, issuer_ticker, issuer_name,
    filer_cik, filer_name, filed_at, signal_date, transaction_date,
    transaction_code, shares_transacted, price_per_share, transaction_value,
    shares_owned_following, shares_owned_before, pct_change_holdings,
    ownership_nature, security_title, form_url, fetched_at
) VALUES (
    :accession_no, :transaction_index, :issuer_cik, :issuer_ticker, :issuer_name,
    :filer_cik, :filer_name, :filed_at, :signal_date, :transaction_date,
    :transaction_code, :shares_transacted, :price_per_share, :transaction_value,
    :shares_owned_following, :shares_owned_before, :pct_change_holdings,
    :ownership_nature, :security_title, :form_url, :fetched_at
)
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # SQLite's default busy behavior is to fail immediately ("database is
    # locked") rather than wait -- a real collision risk now that a read
    # query (recent_purchases, used by the live insider-buying feed) can run
    # while a fetch (fetch_form4_for_universe, potentially minutes long) is
    # mid-write. Wait up to 5s for the writer instead of erroring outright.
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_SCHEMA)
    return conn


def _already_fetched(conn: sqlite3.Connection, accession_no: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fetched_accessions WHERE accession_no = ?", (accession_no,)
    ).fetchone()
    return row is not None


def _mark_fetched(
    conn: sqlite3.Connection, accession_no: str, issuer_cik: str, filing_date: str, n_qualifying: int
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fetched_accessions "
        "(accession_no, issuer_cik, filing_date, n_qualifying_transactions, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (accession_no, issuer_cik, filing_date, n_qualifying, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def _insert_transactions(conn: sqlite3.Connection, txns: list[Form4Transaction]) -> None:
    if txns:
        conn.executemany(_INSERT_SQL, [dataclasses.asdict(t) for t in txns])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def fetch_form4_for_universe(
    tickers: list[str],
    start: date,
    end: date,
    *,
    force_refresh: bool = False,
    progress: bool = True,
) -> dict[str, int]:
    """Fetch + cache Form 4 purchase transactions for every ticker in
    `tickers` over filing dates in [start, end]. Returns
    {ticker: n_new_qualifying_transactions_stored}, -1 for a ticker with no
    SEC CIK match (excluded, not guessed), or -2 for a ticker that failed
    this run (e.g. a transient network timeout) -- re-run to retry it, same
    as any individual failed filing.

    Idempotent: filings already recorded in `fetched_accessions` are skipped,
    so re-running this after an interruption only fetches what's new.
    """
    conn = connect()
    # Wide margin: the after-hours/weekend roll-forward can look up to ~2
    # calendar weeks past the last filing date, and a few days of warmup
    # before `start` costs nothing.
    trading_days = frozenset(
        d.date() for d in trading_days_index(start - timedelta(days=5), end + timedelta(days=14))
    )
    stats: dict[str, int] = {}
    try:
        for ticker in tickers:
            cik = cik_for_ticker(ticker)
            if cik is None:
                stats[ticker] = -1
                if progress:
                    print(f"{ticker}: no SEC CIK match -- excluded")
                continue

            try:
                # A transient network failure (e.g. an SEC-side timeout mid-
                # pagination for a heavy issuer like GS/JPM, which pages
                # through dozens of dateb-cursor resets) must cost this ONE
                # ticker's progress this run, not crash the whole multi-hour
                # universe fetch -- measured directly: an uncaught timeout
                # here killed a real 5-year run partway through GS a second
                # time. Idempotent caching means a later re-run picks this
                # ticker back up from wherever it left off; the per-filing
                # try/except below already protects individual filings the
                # same way, this is the same discipline one level up.
                filings = list_form4_filings(cik, start, end)
                new_count = 0
                for i, f in enumerate(filings):
                    acc = f["accession_no"]
                    if not force_refresh and _already_fetched(conn, acc):
                        continue
                    try:
                        xml_url = _filing_xml_url(cik, acc)
                        if xml_url is None:
                            _mark_fetched(conn, acc, cik, f["filing_date"], 0)
                            continue
                        xml_bytes = _fetch_url(xml_url)
                        filed_at = pd.Timestamp(f["updated"])
                        txns = parse_form4_xml(
                            xml_bytes,
                            accession_no=acc,
                            filed_at=filed_at,
                            form_url=xml_url,
                            trading_days=trading_days,
                        )
                        # Second, independent guard against the owner=include-style
                        # contamination `list_form4_filings` already excludes at the
                        # source: only store a transaction if the filing's OWN
                        # issuer really is the ticker we searched for.
                        txns = [t for t in txns if t.issuer_cik.lstrip("0") == cik.lstrip("0")]
                        for t in txns:
                            if not t.issuer_ticker:
                                t.issuer_ticker = ticker
                        _insert_transactions(conn, txns)
                        n_qualifying = len(txns)
                        new_count += n_qualifying
                        _mark_fetched(conn, acc, cik, f["filing_date"], n_qualifying)
                    except Exception as exc:  # noqa: BLE001 -- one bad filing must not kill the run
                        print(f"WARN: {ticker} {acc} failed ({exc}) -- not cached, will retry next run")
                        continue

                    # Commit every COMMIT_EVERY_N_FILINGS filings, not just once
                    # per ticker -- a single heavy issuer (GS/JPM) can take hours,
                    # and a mid-ticker crash must lose at most a few hundred
                    # filings' worth of re-fetching, not the whole ticker. See
                    # LESSONS.md: a silent process death during the 1-year fetch
                    # already cost a full re-fetch of one ticker's progress once.
                    if (i + 1) % COMMIT_EVERY_N_FILINGS == 0:
                        conn.commit()

                conn.commit()
                stats[ticker] = new_count
                if progress:
                    print(f"{ticker}: {len(filings)} Form 4 filings scanned, {new_count} new qualifying purchases stored")
            except Exception as exc:  # noqa: BLE001 -- one bad ticker must not kill the whole universe fetch
                conn.commit()  # keep whatever partial progress this ticker already made
                stats[ticker] = -2
                print(f"WARN: {ticker} failed ({exc}) -- skipping for this run, will retry next run")
                continue
    finally:
        conn.close()
    return stats


# --------------------------------------------------------------------------- #
# Read queries (for the live insider-buying feed -- api/main.py:/api/insider)
# --------------------------------------------------------------------------- #


def recent_purchases(
    tickers: list[str] | None = None, since: date | None = None, limit: int = 50
) -> list[dict]:
    """Form 4 open-market purchases for `tickers` (all tracked issuers if
    None) with signal_date >= `since`, largest transaction value first --
    a read-only query against the existing form4_transactions table, no
    fetch/network involved. Returns [] rather than raising if the DB
    doesn't exist yet or has no matching rows, same degrade-gracefully
    convention as engine/quotes.py."""
    if not DB_PATH.exists():
        return []
    # A plain connect() rather than the shared connect() helper above --
    # that one always re-runs executescript(_SCHEMA), which (unlike a
    # true no-op CREATE TABLE IF NOT EXISTS check) still needs SQLite to
    # briefly escalate to a write lock, so a read colliding with a
    # long-running fetch_form4_for_universe write would itself block or
    # fail even though this query only ever reads.
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM form4_transactions WHERE 1=1"
        params: list[str | int] = []
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            query += f" AND issuer_ticker IN ({placeholders})"
            params.extend(t.upper() for t in tickers)
        if since is not None:
            query += " AND signal_date >= ?"
            params.append(since.isoformat())
        query += " ORDER BY transaction_value DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Validation checks (run BEFORE any strategy code, per CLAUDE.md / task spec)
# --------------------------------------------------------------------------- #


def check_no_lookahead(conn: sqlite3.Connection, sample_size: int = 20) -> dict:
    """Check 1: transaction_date must never be after filed_at's date. A
    violation means the parser grabbed the wrong field."""
    rows = conn.execute(
        "SELECT accession_no, transaction_date, filed_at FROM form4_transactions "
        "ORDER BY RANDOM() LIMIT ?",
        (sample_size,),
    ).fetchall()
    violations = []
    for acc, txn_date, filed_at in rows:
        filed_date = pd.Timestamp(filed_at).date()
        t_date = datetime.strptime(txn_date, "%Y-%m-%d").date()
        if t_date > filed_date:
            violations.append({"accession_no": acc, "transaction_date": txn_date, "filed_at": filed_at})
    status = "inconclusive" if not rows else ("pass" if not violations else "fail")
    return {"status": status, "sample_size": len(rows), "violations": violations}


def check_after_hours_handling(conn: sqlite3.Connection, sample_size: int = 20) -> dict:
    """Check 2: a filing accepted after 4pm ET must get a signal_date strictly
    after its own filed date (rolled to the next trading day), never the
    filing date itself."""
    rows = conn.execute("SELECT accession_no, filed_at, signal_date FROM form4_transactions").fetchall()
    after_hours = [(a, f, s) for a, f, s in rows if pd.Timestamp(f).time() >= MARKET_CLOSE_ET]
    if not after_hours:
        return {"status": "inconclusive", "reason": "no after-hours filings in the sample", "n_after_hours": 0}
    sample = after_hours[:sample_size]
    violations = [
        {"accession_no": a, "filed_at": f, "signal_date": s}
        for a, f, s in sample
        if pd.Timestamp(s).date() <= pd.Timestamp(f).date()
    ]
    return {
        "status": "pass" if not violations else "fail",
        "n_after_hours": len(after_hours),
        "sample_checked": len(sample),
        "violations": violations,
    }


def check_transaction_type_filter(conn: sqlite3.Connection, sample_size: int = 10) -> dict:
    """Check 3: every stored row must be code 'P' with positive shares. The
    parser filters at insert time, so this is a regression guard on the DB
    itself, not just on the parser's logic."""
    rows = conn.execute(
        "SELECT accession_no, transaction_code, shares_transacted FROM form4_transactions "
        "ORDER BY RANDOM() LIMIT ?",
        (sample_size,),
    ).fetchall()
    violations = [
        {"accession_no": a, "transaction_code": c, "shares_transacted": s}
        for a, c, s in rows
        if c != QUALIFYING_TRANSACTION_CODE or (s or 0) <= 0
    ]
    non_p_total = conn.execute(
        "SELECT COUNT(*) FROM form4_transactions WHERE transaction_code != ? OR shares_transacted <= 0",
        (QUALIFYING_TRANSACTION_CODE,),
    ).fetchone()[0]
    status = "inconclusive" if not rows else ("pass" if not violations and non_p_total == 0 else "fail")
    return {
        "status": status,
        "sample_size": len(rows),
        "violations": violations,
        "non_qualifying_rows_in_db": non_p_total,
    }


def check_coverage(
    conn: sqlite3.Connection, start: date | None = None, end: date | None = None, min_expected: int = 50
) -> dict:
    """Check 4: distinct issuer coverage for the window. Spec: a 5-year
    Dow-29 window should show hundreds of filings; under 50 means the fetch
    or ticker matching is broken."""
    q = "SELECT COUNT(DISTINCT issuer_ticker), COUNT(*) FROM form4_transactions"
    params: list[str] = []
    if start and end:
        q += " WHERE signal_date BETWEEN ? AND ?"
        params = [start.isoformat(), end.isoformat()]
    distinct_tickers, total = conn.execute(q, params).fetchone()
    return {
        "status": "pass" if total >= min_expected else "fail",
        "distinct_tickers": distinct_tickers,
        "total_filings": total,
        "min_expected": min_expected,
    }


def check_filing_lag_distribution(conn: sqlite3.Connection) -> dict:
    """Check 5: distribution of (filed_at date - transaction_date) in
    calendar days. A median of exactly 0 across the whole dataset is the
    spec's stated red flag for "transaction date used instead of filed-at
    date" -- reported as a fail so it gets investigated, not waved through."""
    rows = conn.execute("SELECT filed_at, transaction_date FROM form4_transactions").fetchall()
    if not rows:
        return {"status": "inconclusive", "reason": "no data"}
    lags = []
    for filed_at, txn_date in rows:
        fd = pd.Timestamp(filed_at).date()
        td = datetime.strptime(txn_date, "%Y-%m-%d").date()
        lags.append((fd - td).days)
    s = pd.Series(lags, dtype=float)
    median = float(s.median())
    return {
        "status": "fail" if median <= 0 else "pass",
        "n": len(lags),
        "median_days": median,
        "p90_days": float(s.quantile(0.90)),
        "mean_days": float(s.mean()),
        "min_days": int(s.min()),
        "max_days": int(s.max()),
    }


def run_validation(
    conn: sqlite3.Connection, start: date | None = None, end: date | None = None
) -> dict:
    return {
        "1_no_lookahead": check_no_lookahead(conn),
        "2_after_hours_handling": check_after_hours_handling(conn),
        "3_transaction_type_filter": check_transaction_type_filter(conn),
        "4_coverage": check_coverage(conn, start, end),
        "5_filing_lag_distribution": check_filing_lag_distribution(conn),
    }


def all_checks_passed(report: dict) -> bool:
    return all(check["status"] == "pass" for check in report.values())


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    from engine.universe import EQUITY_UNIVERSE, daily_date_range

    parser = argparse.ArgumentParser(description="Fetch + validate SEC EDGAR Form 4 purchase data.")
    parser.add_argument("--tickers", nargs="*", default=None, help="Defaults to EQUITY_UNIVERSE (Dow-29).")
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD, defaults to daily_date_range() start.")
    parser.add_argument("--end", type=str, default=None, help="YYYY-MM-DD, defaults to daily_date_range() end.")
    parser.add_argument("--validate-only", action="store_true", help="Skip fetching; just run the 5 checks.")
    args = parser.parse_args()

    default_start, default_end = daily_date_range()
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else default_start
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else default_end
    tickers = args.tickers or EQUITY_UNIVERSE

    if not args.validate_only:
        print(f"Fetching Form 4 purchases for {len(tickers)} tickers, {start} to {end} ...")
        fetch_form4_for_universe(tickers, start, end)

    conn = connect()
    report = run_validation(conn, start, end)
    conn.close()
    print(json.dumps(report, indent=2, default=str))
    print("ALL CHECKS PASSED" if all_checks_passed(report) else "AT LEAST ONE CHECK FAILED -- do not proceed to strategy code")


if __name__ == "__main__":
    main()
