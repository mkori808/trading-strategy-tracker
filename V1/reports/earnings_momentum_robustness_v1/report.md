# Earnings Momentum / Gap-Hold Robustness Audit v1

## Classification

**DATA BLOCKED**

The registered implementation detects a gap and volume surge but explicitly does not verify an earnings announcement. No PIT earnings ledger is installed, so the audit cannot establish BMO/AMC timing, public availability, or the earliest executable session.

The prior ~42 trades remain price/volume-proxy candidate evidence. They are not promoted to earnings evidence, and contribution/leave-out/bootstrap results were intentionally withheld.

## Missing authoritative fields

- `symbol`
- `announcement_timestamp`
- `timezone`
- `release_session`
- `known_at`
- `source`

## Unlock requirement

Install an immutable historical announcement ledger with every required field, original/revision provenance, and event-by-event known_at timestamps; then rerun this frozen audit without changing its test grid.

No signal, parameter, universe, execution rule, or Alpaca configuration was changed.
