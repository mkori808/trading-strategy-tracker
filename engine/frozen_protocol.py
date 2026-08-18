"""Read-only access to the pre-result frozen research registration."""

from __future__ import annotations

import json
from pathlib import Path

PROTOCOL_PATH = Path(__file__).resolve().parent.parent / "research" / "frozen_v1_protocol.json"


def load_protocol() -> dict:
    with PROTOCOL_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def family_record(strategy_name: str) -> dict | None:
    protocol = load_protocol()
    families = protocol.get("families", {})
    if strategy_name in families:
        return families[strategy_name]
    if strategy_name.startswith("Volume-Shock Continuation"):
        return families.get("Volume-Shock Continuation")
    if strategy_name.startswith("Overnight Idiosyncratic Shock Reversal"):
        return families.get("Overnight Idiosyncratic Shock Reversal")
    if strategy_name.startswith("MAX Lottery-Return Reversal"):
        return families.get("MAX Lottery-Return Reversal")
    return None
