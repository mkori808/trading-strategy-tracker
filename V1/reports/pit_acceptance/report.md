# PIT acceptance report

> Diagnostic only. No universe was enabled and no strategy, return, or holdout was run.

Final status: **PIT_BLOCKED**

Enablement: `PIT_UNIVERSE_DISABLED`

## Acceptance matrix

| Component | Status | Notes |
|---|---|---|
| Historical price availability | **PASS** | Broad historical stocks data measured; HON uses independent reconstruction. |
| Active-security coverage | **PARTIAL** | Paid entitlement measured on Dow probes; all-stock population still requires bundle build. |
| Delisted-security coverage | **PARTIAL** | Actions/tickers document delistings, but population normalization is not complete. |
| Corporate-action coverage | **PASS** | Dated actions available and used for reconstruction. |
| Adjusted-return reconstruction | **PARTIAL** | Five-symbol panel completed; HON requires canonical reconstructed close. |
| Ticker-change handling | **PARTIAL** | Actions exist, but ticker-scoped price joins need broader identity verification. |
| Permanent-ID continuity | **PARTIAL** | Sharadar permaticker exists; verified continuity examples in current contract audit: 0. |
| known_at reliability | **FAIL** | No public-information timestamp is supplied by the current normalized inputs. |
| Terminal delisting value | **FAIL** | No explicit terminal value or DelistingReturn field has been validated. |
| Lookahead protection | **PARTIAL** | Policy is fail-closed, but cannot publish rows without known_at. |
| Survivorship protection | **PARTIAL** | Delisted records are present in raw inputs; full normalized population is not published. |

## Ten-case evidence set

Raw Sharadar action/identity evidence is available for the cases below. `known_at` and terminal value remain unresolved for every case; no value is fabricated.

| Issuer | Pre | Post | Expected relationship | known_at | Terminal value |
|---|---|---|---|---|---|
| Hertz Global Holdings | HTZGQ | HTZ | REORGANIZED | UNRESOLVED | UNRESOLVED |
| Frontier Communications | FTRCQ | FYBR | REORGANIZED | UNRESOLVED | UNRESOLVED |
| Chesapeake Energy | CHKAQ | EXE | REORGANIZED | UNRESOLVED | UNRESOLVED |
| General Motors Corp (old) | MTLQQ | GM | UNKNOWN | UNRESOLVED | UNRESOLVED |
| Washington Mutual | WAMUQ | WM | UNKNOWN | UNRESOLVED | UNRESOLVED |
| Sears Holdings | SHLDQ | — | LIQUIDATED | UNRESOLVED | UNRESOLVED |
| SVB Financial Group | SIVBQ | — | LIQUIDATED | UNRESOLVED | UNRESOLVED |
| Party City Holdco | PRTYQ | — | LIQUIDATED | UNRESOLVED | UNRESOLVED |
| Rite Aid Corp | RADCQ | — | LIQUIDATED | UNRESOLVED | UNRESOLVED |
| Bed Bath & Beyond Inc | BBBYQ | BBBY | UNKNOWN | UNRESOLVED | UNRESOLVED |

## Remaining gap

Externally verified known_at timestamps and sourced terminal delisting values for the pilot cases; permanent-ID continuity must then be expanded beyond the hand-audited sample.
