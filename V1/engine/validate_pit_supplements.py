"""Validate externally sourced PIT supplements without inventing evidence."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SUPPLEMENT_FILE = ROOT / "data" / "pit_us_all_stocks" / "supplemental_evidence.json"
REQUIRED = {
    "security_id", "known_at", "terminal_value_per_share", "terminal_value_source",
    "terminal_value_type", "source_type", "source_title", "source_organization",
    "source_date", "retrieval_date", "source_identifier", "evidence_section", "confidence",
}


def validate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    seen: set[str] = set()
    valid: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED - set(row))
        if missing:
            errors.append(f"row {index}: missing {', '.join(missing)}")
            continue
        security_id = str(row["security_id"]).strip()
        if not security_id or security_id in seen:
            errors.append(f"row {index}: security_id must be non-empty and unique")
            continue
        try:
            date.fromisoformat(str(row["known_at"])[:10])
            value = float(row["terminal_value_per_share"])
            if value < 0:
                raise ValueError("negative terminal value")
        except (TypeError, ValueError) as exc:
            errors.append(f"row {index}: invalid known_at or terminal value ({exc})")
            continue
        if not str(row["terminal_value_source"]).strip():
            errors.append(f"row {index}: terminal_value_source is required")
            continue
        for field in ("terminal_value_type", "source_type", "source_title", "source_organization", "source_date", "retrieval_date", "source_identifier", "evidence_section", "confidence"):
            if not str(row.get(field) or "").strip():
                errors.append(f"row {index}: {field} is required")
                break
        else:
            seen.add(security_id)
            valid.append(row)
    return {
        "schemaVersion": 1,
        "evidenceOnly": True,
        "publishable": bool(valid) and not errors,
        "rows": len(rows),
        "validRows": len(valid),
        "errors": errors,
        "requiredFields": sorted(REQUIRED),
    }


def validate_file(path: Path = SUPPLEMENT_FILE) -> dict[str, Any]:
    if not path.exists():
        return {
            "schemaVersion": 1,
            "evidenceOnly": True,
            "publishable": False,
            "rows": 0,
            "validRows": 0,
            "errors": [f"supplemental evidence file not found: {path}"],
            "requiredFields": sorted(REQUIRED),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"schemaVersion": 1, "evidenceOnly": True, "publishable": False, "rows": 0, "validRows": 0, "errors": [str(exc)], "requiredFields": sorted(REQUIRED)}
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {"schemaVersion": 1, "evidenceOnly": True, "publishable": False, "rows": 0, "validRows": 0, "errors": ["file must contain a list or an object with rows"], "requiredFields": sorted(REQUIRED)}
    return validate_rows(rows)


if __name__ == "__main__":
    print(json.dumps(validate_file(), indent=2))
