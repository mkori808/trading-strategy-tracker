"""Fail-closed Earnings Momentum / Gap-Hold robustness audit.

The registered implementation is a price/volume gap proxy.  Robustness
statistics are forbidden until an immutable PIT announcement ledger exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PREREG = Path("research/earnings_momentum_robustness_v1_preregistration.json")
OUTPUT_DIR = Path("reports/earnings_momentum_robustness_v1")
REQUIRED_FIELDS = ("symbol", "announcement_timestamp", "timezone", "release_session", "known_at", "source")


def audit_input_gate(ledger: Path | None = None) -> dict[str, Any]:
    missing = list(REQUIRED_FIELDS)
    issues = [
        "The registered strategy declares itself a price/volume proxy and does not confirm that a gap follows earnings.",
        "No historical earnings-announcement ledger exists under data/, research/, or logs/.",
        "BMO versus AMC, original timezone, public known_at time, revisions, and source provenance cannot be audited.",
        "Therefore earliest tradable session and same-bar lookahead cannot be established event by event.",
    ]
    if ledger is not None and ledger.exists():
        rows = json.loads(ledger.read_text(encoding="utf-8"))
        if isinstance(rows, list) and rows:
            missing = sorted({field for field in REQUIRED_FIELDS if any(row.get(field) in (None, "") for row in rows)})
            if not missing:
                return {"passed": True, "ledger": str(ledger), "rows": len(rows), "missingFields": [], "issues": []}
    return {"passed": False, "ledger": None if ledger is None else str(ledger), "rows": 0,
            "missingFields": missing, "issues": issues}


def run_audit(output_dir: Path = OUTPUT_DIR, ledger: Path | None = None) -> dict[str, Any]:
    gate = audit_input_gate(ledger)
    if gate["passed"]:
        raise NotImplementedError("Input gate passed; implement the already-preregistered robustness estimators before examining outcomes.")
    output_dir.mkdir(parents=True, exist_ok=True)
    prereg_hash = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    result = {
        "study": "Earnings Momentum / Gap-Hold Robustness Audit v1",
        "classification": "DATA BLOCKED",
        "preregistration": str(PREREG),
        "preregistrationSha256": prereg_hash,
        "inputGate": gate,
        "priorCandidateEvidence": {"tradesApprox": 42, "winRatePctApprox": 54.8, "expectancyRApprox": 0.38, "profitFactorApprox": 2.65,
                                   "classification": "price/volume-proxy candidate evidence; not reclassified as earnings evidence"},
        "testsRun": [],
        "testsWithheld": ["contribution concentration", "remove-best-trades", "leave-one-security-out", "leave-one-year-out",
                              "cluster-aware effective N/bootstrap", "BMO/AMC timing", "slippage/gap sensitivity", "prop path"],
        "reasonTestsWithheld": "Running robustness statistics on events that cannot be identified as PIT earnings announcements would answer a different question and create misleading precision.",
        "unlockRequirement": "Install an immutable historical announcement ledger with every required field, original/revision provenance, and event-by-event known_at timestamps; then rerun this frozen audit without changing its test grid.",
    }
    (output_dir / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# Earnings Momentum / Gap-Hold Robustness Audit v1", "", "## Classification", "", "**DATA BLOCKED**", "",
             "The registered implementation detects a gap and volume surge but explicitly does not verify an earnings announcement. No PIT earnings ledger is installed, so the audit cannot establish BMO/AMC timing, public availability, or the earliest executable session.", "",
             "The prior ~42 trades remain price/volume-proxy candidate evidence. They are not promoted to earnings evidence, and contribution/leave-out/bootstrap results were intentionally withheld.", "",
             "## Missing authoritative fields", "", *[f"- `{field}`" for field in gate["missingFields"]], "",
             "## Unlock requirement", "", result["unlockRequirement"], "",
             "No signal, parameter, universe, execution rule, or Alpaca configuration was changed."]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run_audit(), indent=2))
