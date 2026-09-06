import sqlite3
from datetime import date

from engine import dm_regime_forward as forward


def _source(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE session_outcomes (
      session_date TEXT PRIMARY KEY, opening_equity REAL, closing_equity REAL, total_pl REAL
    );
    CREATE TABLE raw_equity_snapshots (
      id INTEGER PRIMARY KEY, strategy_fingerprint TEXT
    );
    """)
    conn.execute("INSERT INTO raw_equity_snapshots VALUES (1, 'locked-parent')")
    conn.execute("INSERT INTO session_outcomes VALUES ('2026-08-26',100000,101000,1000)")
    conn.execute("INSERT INTO session_outcomes VALUES ('2026-08-27',101000,102000,1000)")
    conn.commit(); conn.close()


def test_forward_regime_labels_start_after_preregistration_and_are_idempotent(tmp_path):
    source, output = tmp_path / "prop.db", tmp_path / "regime.db"
    _source(source)
    labels = lambda dates: {
        day.isoformat(): {"spy_trend": True, "market_volatility": False, "cross_sectional_dispersion": True}
        for day in dates
    }
    spy_returns = lambda dates: {day.isoformat(): 0.015 for day in dates}
    first = forward.record_completed_sessions(
        source_path=source, output_path=output, label_builder=labels,
        spy_return_builder=spy_returns,
    )
    second = forward.record_completed_sessions(
        source_path=source, output_path=output, label_builder=labels,
        spy_return_builder=spy_returns,
    )
    assert first == {"status": "appended", "appendedSessions": 1, "lastSession": "2026-08-27"}
    assert second == {"status": "up_to_date", "appendedSessions": 0}
    conn = forward.get_connection(output)
    row = conn.execute("SELECT * FROM daily_observations").fetchone()
    assert row["session_date"] == "2026-08-27"
    assert row["parent_strategy_fingerprint"] == "locked-parent"
    assert row["spy_trend"] == 1 and row["market_volatility"] == 0
    assert row["account_return"] == 1000 / 101000
    assert row["spy_return"] == 0.015
    assert row["active_return"] == 1000 / 101000 - 0.015
    assert row["absolute_loss"] == 0
    assert row["underperformed_spy"] == 1
    assert row["joint_damaging"] == 0
    status = forward.status(output)
    assert status["ordersAllowed"] is False
    assert status["relativeOutcomePreregistrationLocked"] is True


def test_no_completed_post_cutoff_session_does_not_call_label_builder(tmp_path):
    source, output = tmp_path / "prop.db", tmp_path / "regime.db"
    _source(source)
    conn = sqlite3.connect(source)
    conn.execute("DELETE FROM session_outcomes WHERE session_date > '2026-08-26'")
    conn.commit(); conn.close()
    result = forward.record_completed_sessions(
        source_path=source, output_path=output,
        label_builder=lambda _: (_ for _ in ()).throw(AssertionError("must not fetch labels")),
        spy_return_builder=lambda _: (_ for _ in ()).throw(AssertionError("must not fetch SPY")),
    )
    assert result == {"status": "up_to_date", "appendedSessions": 0}


def test_existing_forward_database_is_migrated_without_rewriting_rows(tmp_path):
    output = tmp_path / "old_regime.db"
    conn = sqlite3.connect(output)
    conn.executescript(forward._SCHEMA)
    conn.execute(
        "INSERT INTO daily_observations VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-27", "now", "locked-parent", 0.01, "test", 1, 0, 1, "{}", "prospective"),
    )
    conn.commit(); conn.close()
    migrated = forward.get_connection(output)
    row = migrated.execute("SELECT * FROM daily_observations").fetchone()
    assert row["account_return"] == 0.01
    assert row["spy_return"] is None and row["underperformed_spy"] is None
    migrated.close()
