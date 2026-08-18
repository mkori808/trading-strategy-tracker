import json

import pandas as pd

from engine import logging_db


def test_archived_curves_normalize_mixed_dst_offsets(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    conn = logging_db.get_connection()
    points = [
        ["2025-01-10T16:00:00-05:00", 10_000.0],
        ["2025-07-10T16:00:00-04:00", 10_100.0],
    ]
    with conn:
        conn.execute(
            "INSERT INTO research_equity_curves "
            "(archived_at, strategy_name, experiment_id, run_fingerprint, curve_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-01-01", "peer", None, "fingerprint", json.dumps(points)),
        )
    conn.close()

    peer = logging_db.peer_equity_curves("candidate")["peer"]
    family = logging_db.strategy_equity_curves("peer")["fingerprint"]

    assert isinstance(peer.index, pd.DatetimeIndex)
    assert str(peer.index.tz) == "UTC"
    assert family.index.equals(peer.index)
