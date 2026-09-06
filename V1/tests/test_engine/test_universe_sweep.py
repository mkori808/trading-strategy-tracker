from datetime import date

from engine import run_universe_sweep as sweep


def test_sanity_floor_becomes_failed_cell_instead_of_stranding_family(monkeypatch):
    recorded = []
    completed = []

    def fake_validation(job_id, engine, strategy, overrides, context):
        sweep.api_module._validation_jobs[job_id].update({
            "status": "completed",
            "result": {
                "portfolio": {"returnPct": 1_000_000.0, "sharpe": 1.0},
                "metrics": {"benchmarkGapPct": 1_000_000.0},
                "validation": {"dimensions": [], "verdict": {"headline": "invalid"}},
            },
        })

    monkeypatch.setattr(sweep.api_module, "_execute_validation_job", fake_validation)
    monkeypatch.setattr(
        sweep.logging_db, "record_universe_sweep_cell",
        lambda **kwargs: recorded.append(kwargs),
    )
    monkeypatch.setattr(
        sweep.logging_db, "complete_experiment",
        lambda *args: completed.append(args),
    )

    plan = {
        "strategy": "Opening Range Breakout (ORB)",
        "engine": "standard",
        "universeId": "dow_pit",
        "experimentId": 123,
        "familySearchNumber": 1,
        "familySearchCount": 4,
        "symbols": ["AAPL"],
        "interval": "5m",
        "start": date(2025, 1, 1),
        "end": date(2026, 1, 1),
        "preResultPower": {"mdaPct": 20.0},
        "mda": 20.0,
    }

    _, _, status = sweep._execute(plan, "test-sweep")

    assert status == "failed"
    assert completed[-1][1] == "failed"
    assert recorded[-1]["status"] == "failed"
    assert "sanity floor" in recorded[-1]["error"].lower()

