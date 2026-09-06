from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.ex_dividend_feasibility_screen import (
    summarize_symbol_quotes,
    quoted_spread_bps,
)


def quote(bid: float, ask: float, bid_size: float = 1, ask_size: float = 1) -> SimpleNamespace:
    return SimpleNamespace(
        bid_price=bid,
        ask_price=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


def test_quoted_spread_uses_midpoint_and_basis_points() -> None:
    assert quoted_spread_bps(quote(99, 101)) == pytest.approx(200)


@pytest.mark.parametrize(
    "bad_quote",
    [
        quote(0, 101),
        quote(101, 99),
        quote(99, 101, bid_size=0),
        quote(99, 101, ask_size=0),
    ],
)
def test_quoted_spread_rejects_invalid_or_crossed_books(bad_quote: SimpleNamespace) -> None:
    assert quoted_spread_bps(bad_quote) is None


def test_quote_summary_keeps_invalid_observations_out_of_distribution() -> None:
    summary = summarize_symbol_quotes(
        "TEST",
        [quote(99.9, 100.1), quote(99.8, 100.2), quote(101, 99)],
    )
    assert summary.valid_quotes == 2
    assert summary.rejected_quotes == 1
    assert summary.median_spread_bps == pytest.approx(30)
    assert summary.p90_spread_bps == pytest.approx(40)

