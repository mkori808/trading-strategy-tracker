"""GET /api/research/shadow-live-marks -- aggregates a live 'right now' mark
for every shadow strategy (Canonical DM/MRM/blends, the hourly shadow, prop
and self-funded shadows) into one payload for the frontend to poll on the
same cadence as the real paper account. Mirrors the established pattern in
test_api_research_status.py: monkeypatch the module-level status/mark
functions the endpoint calls, not the underlying DB/file paths (those are
bound as function-default arguments at import time, so patching the path
attribute after the fact would silently not take effect).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main


@pytest.fixture
def client():
    return TestClient(main.app)


def test_shadow_live_marks_aggregates_every_family_into_one_payload(client, monkeypatch):
    monkeypatch.setattr(main.dm_mrm_forward, "live_marks", lambda: {
        "dm": {"key": "dm", "available": True, "equity": 101.0, "inProgress": True},
        "mrm": {"key": "mrm", "available": True, "equity": 99.0, "inProgress": True},
        "fiftyFifty": {"key": "fiftyFifty", "available": False, "reason": "x", "inProgress": True},
        "volScaled": {"key": "volScaled", "available": False, "reason": "x", "inProgress": True},
    })
    monkeypatch.setattr(main.optimized_dm_hourly_shadow, "live_mark", lambda: {
        "key": "dm_optimized_63d_hourly", "available": True, "equity": 97_500.0, "inProgress": True,
    })
    monkeypatch.setattr(main.prop_forward, "live_marks", lambda: {
        "optimized_dm_020_trailing": {"key": "optimized_dm_020_trailing", "available": True, "equity": 20_100.0, "inProgress": True},
        "optimized_dm_025_static": {"key": "optimized_dm_025_static", "available": True, "equity": 25_050.0, "inProgress": True},
        "optimized_dm_self_funded_020": {"key": "optimized_dm_self_funded_020", "available": True, "equity": 19_500.0, "inProgress": True},
        "optimized_dm_self_funded_025": {"key": "optimized_dm_self_funded_025", "available": True, "equity": 24_200.0, "inProgress": True},
    })

    response = client.get("/api/research/shadow-live-marks")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "dm", "mrm", "fiftyFifty", "volScaled", "dm_optimized_63d_hourly",
        "optimized_dm_020_trailing", "optimized_dm_025_static",
        "optimized_dm_self_funded_020", "optimized_dm_self_funded_025",
    }
    assert payload["dm"]["equity"] == 101.0
    assert payload["dm_optimized_63d_hourly"]["equity"] == 97_500.0
    assert payload["optimized_dm_self_funded_025"]["equity"] == 24_200.0
    assert all(row["inProgress"] is True for row in payload.values())


def test_shadow_live_marks_endpoint_degrades_gracefully_with_nothing_recorded(client, monkeypatch):
    """A cold app (nothing recorded anywhere) must still return 200 with the
    standard unavailable shape for every key -- never a 500."""
    unavailable = lambda key: {"key": key, "available": False, "reason": "Nothing recorded yet.", "inProgress": True}
    monkeypatch.setattr(main.dm_mrm_forward, "live_marks", lambda: {
        key: unavailable(key) for key in ("dm", "mrm", "fiftyFifty", "volScaled")
    })
    monkeypatch.setattr(main.optimized_dm_hourly_shadow, "live_mark", lambda: unavailable("dm_optimized_63d_hourly"))
    monkeypatch.setattr(main.prop_forward, "live_marks", lambda: {})

    response = client.get("/api/research/shadow-live-marks")

    assert response.status_code == 200
    payload = response.json()
    expected_keys = {"dm", "mrm", "fiftyFifty", "volScaled", "dm_optimized_63d_hourly"}
    assert expected_keys <= set(payload)
    for key in expected_keys:
        assert payload[key]["available"] is False
        assert payload[key]["inProgress"] is True
