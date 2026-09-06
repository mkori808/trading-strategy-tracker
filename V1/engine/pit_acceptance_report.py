"""Assemble the current PIT acceptance matrix from existing evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from engine.reorg_identity_screen import GROUND_TRUTH_CASES


ROOT = Path(__file__).resolve().parent.parent
CONTRACT_REPORT = ROOT / "reports" / "sharadar_contract_validation" / "report.json"
PANEL = ROOT / "reports" / "sharadar_contract_validation" / "reconstruction" / "panel.json"
REPORT_DIR = ROOT / "reports" / "pit_acceptance"


def build_report() -> dict[str, Any]:
    contract = json.loads(CONTRACT_REPORT.read_text(encoding="utf-8"))
    panel = json.loads(PANEL.read_text(encoding="utf-8")) if PANEL.exists() else {}
    identity = contract.get("identityContinuity", {})
    cases = [
        {
            "issuer": case["issuer"],
            "preTicker": case["pre"],
            "postTicker": case["post"],
            "shape": case["shape"],
            "expectedRelationship": case["expected_outcome"],
            "identityVerified": False,
            "knownAt": "UNRESOLVED",
            "terminalValue": "UNRESOLVED",
        }
        for case in GROUND_TRUTH_CASES
    ]
    matrix = [
        {"component": "Historical price availability", "status": "PASS", "notes": "Broad historical stocks data measured; HON uses independent reconstruction."},
        {"component": "Active-security coverage", "status": "PARTIAL", "notes": "Paid entitlement measured on Dow probes; all-stock population still requires bundle build."},
        {"component": "Delisted-security coverage", "status": "PARTIAL", "notes": "Actions/tickers document delistings, but population normalization is not complete."},
        {"component": "Corporate-action coverage", "status": "PASS", "notes": "Dated actions available and used for reconstruction."},
        {"component": "Adjusted-return reconstruction", "status": "PARTIAL", "notes": "Five-symbol panel completed; HON requires canonical reconstructed close."},
        {"component": "Ticker-change handling", "status": "PARTIAL", "notes": "Actions exist, but ticker-scoped price joins need broader identity verification."},
        {"component": "Permanent-ID continuity", "status": "PARTIAL", "notes": f"Sharadar permaticker exists; verified continuity examples in current contract audit: {len(identity.get('examples', []))}."},
        {"component": "known_at reliability", "status": "FAIL", "notes": "No public-information timestamp is supplied by the current normalized inputs."},
        {"component": "Terminal delisting value", "status": "FAIL", "notes": "No explicit terminal value or DelistingReturn field has been validated."},
        {"component": "Lookahead protection", "status": "PARTIAL", "notes": "Policy is fail-closed, but cannot publish rows without known_at."},
        {"component": "Survivorship protection", "status": "PARTIAL", "notes": "Delisted records are present in raw inputs; full normalized population is not published."},
    ]
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "PIT_BLOCKED",
        "evidenceOnly": True,
        "backtestsRun": False,
        "holdoutConsumed": False,
        "preregistrationsCreated": False,
        "tenCaseEvidence": cases,
        "identityPanelFlagged": panel.get("flaggedVendorCloseadjDivergesFromReconstruction", []),
        "acceptanceMatrix": matrix,
        "smallestRemainingEvidenceGap": "Externally verified known_at timestamps and sourced terminal delisting values for the pilot cases; permanent-ID continuity must then be expanded beyond the hand-audited sample.",
        "enablement": "PIT_UNIVERSE_DISABLED",
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PIT acceptance report",
        "",
        "> Diagnostic only. No universe was enabled and no strategy, return, or holdout was run.",
        "",
        f"Final status: **{report['status']}**",
        "",
        f"Enablement: `{report['enablement']}`",
        "",
        "## Acceptance matrix",
        "",
        "| Component | Status | Notes |",
        "|---|---|---|",
    ]
    lines.extend(f"| {row['component']} | **{row['status']}** | {row['notes']} |" for row in report["acceptanceMatrix"])
    lines.extend(["", "## Ten-case evidence set", "", "Raw Sharadar action/identity evidence is available for the cases below. `known_at` and terminal value remain unresolved for every case; no value is fabricated.", "", "| Issuer | Pre | Post | Expected relationship | known_at | Terminal value |", "|---|---|---|---|---|---|"])
    lines.extend(f"| {row['issuer']} | {row['preTicker']} | {row['postTicker'] or '—'} | {row['expectedRelationship']} | {row['knownAt']} | {row['terminalValue']} |" for row in report["tenCaseEvidence"])
    lines.extend(["", "## Remaining gap", "", report["smallestRemainingEvidenceGap"], ""])
    return "\n".join(lines)


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "report.json"
    md_path = REPORT_DIR / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return md_path, json_path


if __name__ == "__main__":
    report = build_report()
    paths = write_report(report)
    print(json.dumps({"status": report["status"], "reports": [str(path) for path in paths]}, indent=2))

