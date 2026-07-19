import pandas as pd

from engine.backtest import StrategyBacktestResult, SymbolBacktestResult
from engine.portfolio import run_portfolio_backtest


def _trades(rows):
    """rows: list of (entry_time, exit_time, entry_price, sl, size, pnl)"""
    return pd.DataFrame(rows, columns=["EntryTime", "ExitTime", "EntryPrice", "SL", "Size", "PnL"])


def _result(trades_by_symbol: dict[str, pd.DataFrame]) -> StrategyBacktestResult:
    per_symbol = {
        symbol: SymbolBacktestResult(symbol, stats=None, trades=trades, equity_curve=None)
        for symbol, trades in trades_by_symbol.items()
    }
    return StrategyBacktestResult("S", list(per_symbol), None, None, per_symbol, metrics=None)


def test_non_overlapping_trades_all_execute():
    # Two symbols, sequential (non-overlapping) trades -- nothing should be
    # skipped. cap=1 means open_slots=1 whenever either enters (the other
    # has already exited), so cash rationing doesn't shrink either below a
    # single-slot allocation and the arithmetic stays simple. The replay
    # re-sizes each trade against the *shared* pool's current equity (not
    # the original per-symbol Size), so the original PnL doesn't carry over
    # 1:1 -- that's the point of the module. With these round numbers: SPY
    # risk_per_share=1, pnl_per_unit=20/10=2, size=min(10000*0.01//1,
    # 10000//100)=100 -> +$200, equity 10200; QQQ risk_per_share=2,
    # pnl_per_unit=10/5=2, size=min(10200*0.01//2, 10200//200)=51 -> +$102,
    # equity 10302.
    spy = _trades([[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02"), 100, 99, 10, 20]])
    qqq = _trades([[pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04"), 200, 198, 5, 10]])
    result = _result({"SPY": spy, "QQQ": qqq})

    r = run_portfolio_backtest(result, cash=10_000, risk_pct=0.01, max_concurrent_positions=1)

    assert r.skipped_for_capacity == 0
    assert len(r.trades) == 2
    assert r.final_equity == 10_302.0


def test_max_concurrent_positions_caps_simultaneous_entries():
    # Three symbols all signal at the same instant and stay open past each
    # other's entries -- with a cap of 1, only the first processed should
    # actually open; the other two are skipped for capacity, not silently
    # filled as if capital were unlimited.
    t0 = pd.Timestamp("2024-01-01")
    t1 = pd.Timestamp("2024-01-10")
    a = _trades([[t0, t1, 100, 99, 1, 5]])
    b = _trades([[t0, t1, 100, 99, 1, 5]])
    c = _trades([[t0, t1, 100, 99, 1, 5]])
    result = _result({"A": a, "B": b, "C": c})

    r = run_portfolio_backtest(result, cash=10_000, risk_pct=0.01, max_concurrent_positions=1)

    assert len(r.trades) == 1
    assert r.skipped_for_capacity == 2


def test_position_sized_against_shared_capital_not_a_fresh_pool_per_symbol():
    # Two symbols entering simultaneously with cap=2 (not the constraint
    # here) split the shared $1000 pool roughly evenly, rather than each
    # getting an independent fresh account -- size_by_cash for each is
    # rationed against the *remaining open slots*, so both land on the same
    # size even though they're processed sequentially within the same batch.
    t0 = pd.Timestamp("2024-01-01")
    t1 = pd.Timestamp("2024-01-10")
    a = _trades([[t0, t1, 100, 99, 1, 5]])  # risk_per_share = 1
    b = _trades([[t0, t1, 100, 99, 1, 5]])
    result = _result({"A": a, "B": b})

    r = run_portfolio_backtest(result, cash=1000, risk_pct=1.0, max_concurrent_positions=2)

    assert r.skipped_for_capacity == 0
    assert len(r.trades) == 2
    assert set(r.trades["Size"]) == {5}  # each gets ~half the pool: 5 shares @ $100


def test_cash_rationed_across_slots_can_still_starve_a_later_entrant():
    # A cheap symbol entering first shouldn't be able to claim more than its
    # rationed share and starve a later, pricier symbol that still had a
    # free slot -- but the shared pool genuinely can run out, and that must
    # show up as a skip even though max_concurrent_positions was never hit.
    t0 = pd.Timestamp("2024-01-01")
    t1 = pd.Timestamp("2024-01-10")
    cheap = _trades([[t0, t1, 10, 9, 1, 1]])  # entry_price=10, risk_per_share=1
    pricey = _trades([[t0, t1, 1000, 999, 1, 1]])  # entry_price=1000
    result = _result({"Cheap": cheap, "Pricey": pricey})

    r = run_portfolio_backtest(result, cash=150, risk_pct=1.0, max_concurrent_positions=2)

    # Cheap gets its $75 ration (7 shares @ $10 = $70), leaving $80 -- not
    # enough for even 1 share of Pricey at $1000.
    assert len(r.trades) == 1
    assert r.trades.iloc[0]["Symbol"] == "Cheap"
    assert r.skipped_for_capacity == 1


def test_short_trade_pnl_sign_handled_via_negative_size():
    # backtesting.py encodes shorts as negative Size; PnL/|Size| must still
    # come out positive-per-unit for a winning short (price fell), or the
    # replay would silently turn a winning short into a realized loss.
    # risk_per_share=1, pnl_per_unit=50/10=5, size=min(10000*0.01//1,
    # 10000//100)=100 -> realized PnL = 100*5 = $500.
    t0 = pd.Timestamp("2024-01-01")
    t1 = pd.Timestamp("2024-01-02")
    short = _trades([[t0, t1, 100, 101, -10, 50]])  # short 10 @ 100, wins $50
    result = _result({"SPY": short})

    r = run_portfolio_backtest(result, cash=10_000, risk_pct=0.01, max_concurrent_positions=1)

    assert len(r.trades) == 1
    assert r.trades.iloc[0]["PnL"] == 500.0
    assert r.trades.iloc[0]["Size"] == -100  # negative: direction preserved as short
    assert r.final_equity == 10_500.0


def test_no_trades_produces_flat_curve():
    result = _result({"SPY": pd.DataFrame(columns=["EntryTime", "ExitTime", "EntryPrice", "SL", "Size", "PnL"])})
    r = run_portfolio_backtest(result, cash=10_000)
    assert r.final_equity == 10_000
    assert r.trades.empty
