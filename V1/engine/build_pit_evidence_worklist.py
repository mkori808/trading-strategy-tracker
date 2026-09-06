"""Build the field-specific missing-evidence worklist from existing artifacts."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "data" / "pit_us_all_stocks" / "supplemental_evidence.template.json"
OUTPUT = ROOT / "reports" / "pit_acceptance" / "missing_evidence_worklist.json"


def build_worklist() -> dict:
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    rows = []
    for index, case in enumerate(payload["rows"], start=1):
        case_id = f"case_{index:02d}_{case['ticker'].lower()}"
        for field, evidence_type, source_types in (
            (
                "known_at",
                "public-information timestamp for the security event",
                ["SEC EDGAR filing acceptance timestamp", "issuer press release", "exchange notice", "bankruptcy court filing"],
            ),
            (
                "terminal_value_per_share",
                "shareholder economic outcome at delisting/cancellation",
                ["merger agreement/proxy", "issuer closing announcement", "bankruptcy court/trustee distribution", "exchange notice"],
            ),
        ):
            rows.append({
                "case_id": case_id,
                "issuer": case["issuer"],
                "security_id": None,
                "ticker": case["ticker"],
                "field_needed": field,
                "current_value": None,
                "current_status": "UNRESOLVED",
                "required_evidence_type": evidence_type,
                "acceptable_source_types": source_types,
                "notes": "Do not use the Sharadar action date as known_at; do not use last trade price as terminal value without economic support.",
            })
    return {
        "schemaVersion": 1,
        "evidenceOnly": True,
        "sourceTemplate": str(TEMPLATE.relative_to(ROOT)),
        "rows": rows,
        "unresolvedFieldCount": len(rows),
    }


if __name__ == "__main__":
    report = build_worklist()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT), "unresolvedFieldCount": report["unresolvedFieldCount"]}, indent=2))

