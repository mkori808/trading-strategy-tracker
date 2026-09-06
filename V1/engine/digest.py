"""Daily digest content composition: today's regime, top movers, recent
insider buys, and the market-signals breadth score, folded into one
structured payload plus a plain-text rendering.

Preview-only by design: no scheduler, no SMTP, no real send.
api/main.py's GET /api/digest/preview just calls build_digest() and returns
it. Wiring up a real send is a separate, deliberate decision (new .env
credentials, a scheduled job) -- deferred rather than half-built.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from engine import data_edgar, market_overview
from engine import market_signals as market_signals_module
from engine import movers as movers_module
from engine.universe import RESEARCH_UNIVERSE

INSIDER_LOOKBACK_DAYS = 5
TOP_N = 5

DISCLAIMER = (
    "This digest is descriptive only -- statistics about tracked symbols, "
    "not a recommendation to buy or sell any security."
)


def _render_text(regime: dict, signals: dict, mv: dict, insider: list[dict]) -> str:
    lines = [f"Trading Strategy Lab -- Daily Digest ({date.today().isoformat()})", ""]
    if signals["score"] is not None:
        lines.append(f"SPY regime: {regime['current']} (breadth score {signals['score']:.0f}/100)")
    else:
        lines.append(f"SPY regime: {regime['current']}")
    lines.append("")

    lines.append("Top gainers:")
    for r in mv["gainers"]:
        lines.append(f"  {r['symbol']:<6} {r['changePct']:+.2f}%")
    lines.append("")

    lines.append("Top losers:")
    for r in mv["losers"]:
        lines.append(f"  {r['symbol']:<6} {r['changePct']:+.2f}%")
    lines.append("")

    lines.append(f"Insider buying (last {INSIDER_LOOKBACK_DAYS} days):")
    if insider:
        for t in insider:
            lines.append(
                f"  {t['issuer_ticker']:<6} {t['filer_name']} bought "
                f"${t['transaction_value']:,.0f} ({t['signal_date']})"
            )
    else:
        lines.append("  No qualifying purchases filed in this window.")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_digest() -> dict[str, Any]:
    regime = market_overview.current_regime()
    signals = market_signals_module.market_signals()
    mv = movers_module.build_movers(top_n=TOP_N)
    insider = data_edgar.recent_purchases(
        tickers=RESEARCH_UNIVERSE,
        since=date.today() - timedelta(days=INSIDER_LOOKBACK_DAYS),
        limit=TOP_N,
    )
    return {
        "asOf": date.today().isoformat(),
        "regime": regime,
        "marketSignals": signals,
        "movers": mv,
        "insiderPurchases": insider,
        "disclaimer": DISCLAIMER,
        "text": _render_text(regime, signals, mv, insider),
    }
