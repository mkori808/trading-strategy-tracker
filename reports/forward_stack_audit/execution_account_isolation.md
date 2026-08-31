# Brokerage Execution Isolation

## Governing rule

One brokerage account can support many forward research strategies, but only one strategy should produce authoritative brokerage execution evidence unless position and accounting isolation are independently validated.

The current Alpaca paper account is owned by the exact fingerprint for **Dual Momentum · Optimized 63D/Daily**. Multi-strategy execution is disabled. Canonical DM, MRM, fixed 50/50, and volatility-scaled DM/MRM remain independent synthetic shadow portfolios. The 0.20x and 0.25x prop ledgers remain synthetic wrappers on the fingerprint-locked optimized-DM parent stream.

## Why ownership is account-level

Cash, buying power, equity, margin, positions, realized and unrealized P&L, drawdown, and broker order state are shared at the account. If two strategies request offsetting quantities in one symbol, Alpaca exposes the net position without preserving research ownership. Casual netting would make fills, exits, turnover, slippage, P&L, and prop-equity attribution ambiguous.

The system therefore blocks a second execution owner. It does not rotate strategies, overwrite the current owner, or implement experimental virtual sleeves. Additional brokerage execution requires a separate registered account or a separately validated multi-sleeve architecture.

## Integrity handling

Broker orders are matched to the local execution ledger and owner strategy ID. Unknown/manual orders, unknown fills, unexplained positions, multiple active owners, and owner-fingerprint mismatches are recorded as append-only `action_required` events. Because account equity is the source for prospective prop evidence, the prop collector stops appending observations while account contamination is active; it never silently treats unrelated activity as optimized-DM evidence.
