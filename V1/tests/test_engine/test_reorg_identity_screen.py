from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from engine.reorg_identity_screen import check_case, extract_cik


@dataclass
class _FakeResult:
    rows: list[dict[str, Any]]


class _FakeClient:
    """Deterministic, offline stand-in for SharadarClient -- no live network
    calls in the test suite, matching this repo's convention
    (tests/test_engine/test_sharadar_contract.py mocks the same way).
    Responses below are recorded from real 2026-09-06 Sharadar queries
    (see engine/reorg_identity_screen.py's module docstring), not invented.
    """

    def __init__(self, tickers: dict[str, dict[str, Any]], bankruptcy_dates: dict[str, str]):
        self._tickers = tickers
        self._bankruptcy_dates = bankruptcy_dates

    def query_all(self, table_name: str, **kwargs: Any) -> _FakeResult:
        ticker = kwargs.get("ticker")
        if table_name == "tickers":
            record = self._tickers.get(ticker)
            return _FakeResult([record] if record else [])
        if table_name == "actions":
            bdate = self._bankruptcy_dates.get(ticker)
            if not bdate:
                return _FakeResult([])
            return _FakeResult([{"date": bdate, "action": "bankruptcyliquidation", "ticker": ticker}])
        raise AssertionError(f"unexpected table {table_name!r} in test fixture")


def _client_for(*, old_ticker: str, old_cik: str | None, old_permaticker: str, old_delisted: str,
                 old_bankruptcy_date: str, new_ticker: str | None = None, new_cik: str | None = None,
                 new_permaticker: str | None = None, new_first_trade: str | None = None) -> _FakeClient:
    tickers = {
        old_ticker: {
            "permaticker": old_permaticker, "isdelisted": old_delisted,
            "secfilings": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={old_cik}" if old_cik else None,
        }
    }
    bankruptcy_dates = {old_ticker: old_bankruptcy_date}
    if new_ticker:
        tickers[new_ticker] = {
            "permaticker": new_permaticker, "isdelisted": "N",
            "firstpricedate": new_first_trade,
            "secfilings": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={new_cik}" if new_cik else None,
        }
    return _FakeClient(tickers, bankruptcy_dates)


def test_extract_cik_parses_the_sec_url() -> None:
    assert extract_cik("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001657853") == "0001657853"
    assert extract_cik(None) is None
    assert extract_cik("") is None
    assert extract_cik("not a url") is None


def test_reorganized_case_same_cik_different_permaticker_is_high_confidence() -> None:
    # Pinned to the real Hertz case: old CIK 0001657853 == new CIK 0001657853,
    # but permaticker changes (124354 -> 636661), reflecting that the SECURITY
    # was cancelled and reissued even though the legal registrant persisted.
    client = _client_for(
        old_ticker="HTZGQ", old_cik="0001657853", old_permaticker="124354", old_delisted="Y",
        old_bankruptcy_date="2020-10-29",
        new_ticker="HTZ", new_cik="0001657853", new_permaticker="636661", new_first_trade="2021-11-09",
    )
    event = check_case(client, "HTZGQ", "HTZ")
    assert event.outcome == "REORGANIZED"
    assert event.linkage_confidence == "HIGH"
    assert event.linkage_method == "same_sec_cik_different_permaticker"
    assert event.post_reorg_first_trade_date == date(2021, 11, 9)
    # PIT invariant: a strategy can never enter before the security it is
    # buying actually exists.
    assert event.post_reorg_first_trade_date > event.bankruptcy_filing_or_delisting_date


def test_ticker_reuse_by_unrelated_company_fails_closed() -> None:
    # Pinned to the real Washington Mutual / Waste Management false-link
    # trap: relatedtickers/tickerchangefrom surface "WM" as if related, but
    # the CIKs prove they are unrelated companies.
    client = _client_for(
        old_ticker="WAMUQ", old_cik="0000933136", old_permaticker="197327", old_delisted="Y",
        old_bankruptcy_date="2008-10-30",
        new_ticker="WM", new_cik="0000823768", new_permaticker="198260", new_first_trade="1991-09-30",
    )
    event = check_case(client, "WAMUQ", "WM")
    assert event.outcome == "UNKNOWN"
    assert event.linkage_confidence == "LOW"
    assert event.linkage_method == "different_cik_candidate_is_likely_unrelated"


def test_liquidation_with_no_successor_is_high_confidence_liquidated() -> None:
    # Pinned to Sears Holdings: acquired via credit bid, no new public equity
    # was ever issued -- no candidate ticker to check at all.
    client = _client_for(
        old_ticker="SHLDQ", old_cik="0000909100", old_permaticker="194967", old_delisted="Y",
        old_bankruptcy_date="2018-10-23",
    )
    event = check_case(client, "SHLDQ", None)
    assert event.outcome == "LIQUIDATED"
    assert event.linkage_confidence == "HIGH"
    assert event.post_reorg_ticker is None


def test_ambiguous_candidate_not_found_fails_closed_to_unresolved() -> None:
    # A candidate ticker that does not resolve to any current record must
    # never be silently treated as "no successor" (that would be LIQUIDATED,
    # a false claim) -- it must be reported UNRESOLVED, distinct from a
    # verified liquidation.
    client = _client_for(
        old_ticker="BBBYQ", old_cik="0000886158", old_permaticker="197799", old_delisted="Y",
        old_bankruptcy_date="2023-05-02",
    )
    event = check_case(client, "BBBYQ", "BBBY")
    assert event.outcome == "UNKNOWN"
    assert event.linkage_confidence == "UNRESOLVED"
    assert event.linkage_method == "candidate_ticker_not_found"


def test_repeated_restructuring_chain_stays_same_cik_through_a_later_rename() -> None:
    # Pinned to Chesapeake -> Expand Energy: the post-reorg entity later
    # ticker-changed again via an unrelated 2024 merger. The CIK-matching
    # method must still classify the REORGANIZATION leg correctly regardless
    # of what happens to the ticker afterward.
    client = _client_for(
        old_ticker="CHKAQ", old_cik="0000895126", old_permaticker="197696", old_delisted="Y",
        old_bankruptcy_date="2021-02-10",
        new_ticker="EXE", new_cik="0000895126", new_permaticker="634009", new_first_trade="2021-02-10",
    )
    event = check_case(client, "CHKAQ", "EXE")
    assert event.outcome == "REORGANIZED"
    assert event.linkage_confidence == "HIGH"


def test_no_candidate_and_still_listed_is_unresolved_not_liquidated() -> None:
    # Fail-closed edge case distinct from the Sears/SVB pattern: if no
    # candidate is supplied AND the old ticker is not even marked delisted,
    # asserting LIQUIDATED would be an unearned claim.
    client = _client_for(
        old_ticker="XYZQ", old_cik="0000000001", old_permaticker="1", old_delisted="N",
        old_bankruptcy_date="2024-01-01",
    )
    event = check_case(client, "XYZQ", None)
    assert event.outcome == "UNKNOWN"
    assert event.linkage_confidence == "UNRESOLVED"


def test_cik_unresolvable_on_either_side_is_unresolved_not_a_guess() -> None:
    client = _client_for(
        old_ticker="AAAQ", old_cik=None, old_permaticker="1", old_delisted="Y",
        old_bankruptcy_date="2020-01-01",
        new_ticker="BBB", new_cik="0000000002", new_permaticker="2", new_first_trade="2020-06-01",
    )
    event = check_case(client, "AAAQ", "BBB")
    assert event.linkage_confidence == "UNRESOLVED"
    assert event.outcome == "UNKNOWN"
