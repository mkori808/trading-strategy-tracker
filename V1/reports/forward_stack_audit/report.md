# Forward-Testing Stack Operational Audit

Audited 2026-08-22T20:13:02-04:00.

## Outcome

The research shadow stack is now wired to the existing hourly app scheduler and advances every newly completed session exactly once. It is append-only, restart-safe, completed-session-only, and incapable of placing Alpaca orders.

**Operational blocker:** Alpaca automation is enabled and placed a completed paper rebalance on 2026-08-21, but its persisted configuration is 63-session/daily over a 26-name custom universe—not canonical 189-session/monthly DM. Per the instruction to leave Alpaca automation unchanged, this audit did not alter it.

Alpaca paper account equity at audit time: $102,749.96. This is not normalized research NAV.

## Current forward evidence

The cutoff is 2026-08-21. The latest completed session is also 2026-08-21, so all five normalized series correctly have zero forward observations. No development NAV was spliced or relabeled.

| Series | Type | Sessions | NAV | Status |
|---|---|---:|---:|---|
| Canonical DM | Normalized canonical shadow; Alpaca configuration mismatch | 0 | — | Frozen — Forward Testing / No Forward Observations Yet |
| Canonical MRM | Research shadow | 0 | — | Frozen — Forward Testing / No Forward Observations Yet |
| Fixed 50/50 DM/MRM | Research shadow | 0 | — | Frozen — Forward Testing / No Forward Observations Yet |
| Vol-Scaled DM/MRM | Research shadow | 0 | — | Frozen — Forward Testing / No Forward Observations Yet |
| SPY | Benchmark shadow | 0 | — | Frozen — Forward Testing / No Forward Observations Yet |

## Frozen vol-scaled state

Actual drifted sleeve weights: DM 38.00%, MRM 62.00%. Targets: DM 38.10%, MRM 61.90%. Last reset 2026-08-03; next scheduled reset 2026-09-01.

Combined holdings aggregate any overlap into one security exposure: JNJ 12.99%, KO 12.62%, JPM 12.18%, PG 12.16%, MCD 12.05%, MRK 8.68%, CAT 7.53%, INTC 7.52%, CSCO 7.16%, TRV 7.12%.

## Controls verified

- Decisions and NAV reject dates on or before the cutoff.
- Volatility windows contain 60 common sessions and end strictly before reset dates.
- Existing decisions are skipped on rerun; outcomes are stored separately; source revisions raise an implementation alert instead of rewriting history.
- NAV uses a common 100 baseline on the first accepted forward session, preserves sleeve costs, and charges the frozen zero overlay cost.
- Restarts catch up missing sessions chronologically; no-new-data reruns are byte-identical.
- Checkpoints are 20, 63, 126, and 252 sessions. Annualized metrics remain withheld before 252.
- Alpaca equity remains separately labeled from normalized research NAV.

## Scheduling limitation

Automatic advancement uses the repository's existing scheduler and therefore runs only while the API process is alive. A restart catches up missed sessions; no external scheduler was created.
