# Optimized DM Prospective Prop Program — Launch Record

Launched 2026-08-22 after freezing `research/optimized_dm_prop_forward_v1_preregistration.json`.

## Frozen experiments

| Shadow | Scale | Drawdown rule | Initial state |
|---|---:|---|---|
| Conservative | 0.20x | trailing to breakeven | Challenge; one $500 fee |
| Static reference | 0.25x | static | Challenge; one $500 fee |

Both ledgers consume the same observed Alpaca paper account-equity stream. They do not place orders, change the actual account size, or modify the 63D/Daily strategy.

## Collection

- Five-minute regular-market sampling from the Alpaca account clock.
- Raw account equity, cash, positions, weights, intraday realized/unrealized P&L, exposure, recent orders, fills, references, slippage, partial-fill status, and position contributions are append-only in `logs/prop_forward.db`. After the first mark, position contributions are snapshot-to-snapshot signed market-value changes adjusted for intervening fills; cash, fees, and any unattributed difference remain visible in a reconciliation bucket.
- Shadow marks and lifecycle events are separate append-only tables. A mutable state table is only a restart cache; breaches and outcomes remain immutable.
- Missing/offline intervals are not reconstructed. Gaps over ten minutes are retained as Operational Integrity warnings.
- The first review checkpoints are 20, 60, 126, and 252 completed sessions. Annualized forward claims are withheld before 252.

## Launch status

The strategy fingerprint matches validation Run #33 and the frozen 63D/Daily configuration. The market was closed at launch, so no prospective snapshot was fabricated. Status is **Not started** until the first genuine regular-market observation.

Historical development results, historical Monte Carlo, paper-forward observations, and prospective prop-shadow accounting remain separate evidence classes.

The canonical DM, canonical MRM, fixed 50/50, and frozen volatility-scaled forward NAV ledgers remain unchanged and continue independently.
