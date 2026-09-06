"""Create a blank, provenance-preserving supplement template for pilot cases."""

from __future__ import annotations

import json
from pathlib import Path

from engine.reorg_identity_screen import GROUND_TRUTH_CASES


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "pit_us_all_stocks" / "supplemental_evidence.template.json"


def build_template() -> dict:
    rows = []
    for case in GROUND_TRUTH_CASES:
        rows.append({
            "security_id": "",
            "ticker": case["pre"],
            "issuer": case["issuer"],
            "known_at": "",
            "terminal_value_per_share": None,
            "terminal_value_type": "",
            "terminal_value_source": "",
            "source_type": "",
            "source_title": "",
            "source_organization": "",
            "source_date": "",
            "retrieval_date": "",
            "source_identifier": "",
            "evidence_section": "",
            "confidence": "",
            "notes": "Populate from an independently dated public record; do not use the Sharadar action date as known_at.",
        })
    return {
        "schemaVersion": 1,
        "evidenceOnly": True,
        "purpose": "Template only; blank values are intentionally non-publishable.",
        "rows": rows,
    }


if __name__ == "__main__":
    payload = build_template()
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT), "rows": len(payload["rows"])}, indent=2))
