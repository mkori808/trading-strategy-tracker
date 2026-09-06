"""engine/logging_db.py: the is_canonical migration/backfill and the
canonical-only leaderboard query.

Uses a real temporary SQLite file (via monkeypatching DB_PATH) rather than
mocking sqlite3 -- the migration/backfill logic is exactly the kind of thing
that looks right and silently isn't; only running real DDL against a real
file catches that.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from engine import logging_db
from engine.metrics import compute_metrics

import pandas as pd


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    return logging_db


def _metrics(name="S", sharpe=None, alpha_pct=None):
    # The query tests need a real stored Sharpe. A zero-trade result correctly
    # withholds Sharpe in current metrics semantics, so use one minimal trade.
    trades = pd.DataFrame([{"EntryPrice": 100.0, "SL": 99.0, "Size": 1, "PnL": 1.0}])
    return compute_metrics(
        name, "ALL", trades,
        sharpe=sharpe, alpha_pct=alpha_pct,
    )


def test_log_run_defaults_to_canonical(db):
    db.log_run(_metrics(), ["AAPL"])
    row = db.run_history("S")[0]
    assert row["is_canonical"] == 1


def test_log_run_records_non_canonical_explicitly(db):
    db.log_run(_metrics(), ["AAPL"], params={"x": 1}, is_canonical=False)
    row = db.run_history("S")[0]
    assert row["is_canonical"] == 0
    assert row["params"] == '{"x": 1}'


def test_standard_benchmark_uses_explicit_shared_capital_fields(db):
    run_id = db.log_run(_metrics(alpha_pct=-53.0), ["AAPL"])
    db.attach_standard_benchmark(
        run_id,
        strategy_return_pct=6.0,
        benchmark_return_pct=80.0,
    )

    row = db.run_history("S")[0]
    assert row["alpha_pct"] == -53.0  # legacy diagnostic remains distinct
    assert row["strategy_return_pct"] == 6.0
    assert row["benchmark_return_pct"] == 80.0
    assert row["benchmark_gap_pct"] == -74.0
    assert row["benchmark_name"] == "SPY"
    # No window passed -- must record absence, not a guessed window.
    assert row["benchmark_window_start"] is None
    assert row["benchmark_window_end"] is None


def test_standard_benchmark_pins_the_exact_measured_window(db):
    # Two canonical re-runs of the SAME strategy on different calendar days
    # (i.e. different measured_end, since a default request window ends
    # "today") must record their OWN window on their OWN row, so a reader
    # can see the gap moved because the window moved -- not guess it.
    run_id = db.log_run(_metrics(), ["AAPL"])
    db.attach_standard_benchmark(
        run_id,
        strategy_return_pct=6.0,
        benchmark_return_pct=58.6,
        benchmark_window_start=date(2021, 1, 1),
        benchmark_window_end=date(2026, 1, 1),
    )
    row = db.run_history("S")[0]
    assert row["benchmark_window_start"] == "2021-01-01"
    assert row["benchmark_window_end"] == "2026-01-01"
    assert row["benchmark_gap_pct"] == pytest.approx(6.0 - 58.6)

    run_id_2 = db.log_run(_metrics(), ["AAPL"])
    db.attach_standard_benchmark(
        run_id_2,
        strategy_return_pct=6.0,
        benchmark_return_pct=92.1,
        benchmark_window_start=date(2021, 1, 1),
        benchmark_window_end=date(2026, 8, 12),
    )
    rows = db.run_history("S")
    latest = next(r for r in rows if r["id"] == run_id_2)
    assert latest["benchmark_window_end"] == "2026-08-12"
    assert latest["benchmark_gap_pct"] != row["benchmark_gap_pct"]


def test_latest_run_per_strategy_ignores_non_canonical_rows(db):
    db.log_run(_metrics(), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(), ["MSFT"], params={"tweaked": True}, is_canonical=False)
    latest = db.latest_run_per_strategy()
    assert latest["S"]["is_canonical"] == 1


def test_latest_run_per_strategy_absent_when_only_experiments_exist(db):
    """A strategy that has only ever been run with overrides must not show
    up in the canonical leaderboard at all -- there is no default result to
    show yet."""
    db.log_run(_metrics(), ["AAPL"], is_canonical=False)
    assert "S" not in db.latest_run_per_strategy()


def test_latest_run_per_strategy_picks_the_most_recent_canonical(db):
    db.log_run(_metrics(), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(), ["AAPL"], params={"x": 1}, is_canonical=False)
    db.log_run(_metrics(), ["MSFT"], is_canonical=True)  # most recent canonical
    latest = db.latest_run_per_strategy()
    assert latest["S"]["symbols"] == '["MSFT"]'


def test_best_run_per_strategy_ignores_non_canonical_rows(db):
    db.log_run(_metrics(sharpe=0.1), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=2.9), ["MSFT"], params={"tweaked": True}, is_canonical=False)
    best = db.best_run_per_strategy()
    assert best["S"]["is_canonical"] == 1
    assert best["S"]["sharpe"] == 0.1


def test_best_run_per_strategy_picks_the_highest_sharpe_not_the_latest(db):
    """The whole point of best_run_per_strategy() vs. latest_run_per_strategy():
    an earlier canonical run with a better Sharpe must win over a more recent
    one with a worse Sharpe -- e.g. a re-run against updated data that happens
    to score worse must not bury a strategy's best honest canonical result."""
    db.log_run(_metrics(sharpe=1.5), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=0.2), ["MSFT"], is_canonical=True)  # most recent, worse
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["AAPL"]'
    assert best["S"]["sharpe"] == 1.5


def test_best_run_per_strategy_ranks_null_sharpe_last(db):
    """A run with no computed Sharpe must never outrank one with a real,
    even negative, Sharpe -- there's nothing to verify a NULL is good."""
    db.log_run(_metrics(sharpe=None), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=-2.0), ["MSFT"], is_canonical=True)
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'


def test_best_run_per_strategy_breaks_ties_on_most_recent(db):
    db.log_run(_metrics(sharpe=0.5), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=0.5), ["MSFT"], is_canonical=True)
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'


def test_best_run_prefers_validated_row_over_unvalidated_higher_sharpe(db):
    db.log_run(_metrics(sharpe=2.0, alpha_pct=5.0), ["AAPL"], is_canonical=True)
    validated_id = db.log_run(_metrics(sharpe=0.4, alpha_pct=1.0), ["MSFT"], is_canonical=True)
    db.attach_validation("runs", validated_id, _validation_report(forward_test_worthy=True))

    best = db.best_run_per_strategy()

    assert best["S"]["symbols"] == '["MSFT"]'
    assert best["S"]["edge_verdict"] == "Identified edge"


def test_best_run_per_strategy_prefers_runs_with_alpha_over_higher_sharpe_without(db):
    """A NULL alpha_pct means this run predates the benchmark-relative
    migration (see engine/logging_db.py's module docstring), not that the
    strategy has nothing to beat -- it must never outrank a more complete,
    even worse-Sharpe, later run. Regression test for the real bug this
    caught: several strategies' best-Sharpe pick was a pre-migration row,
    silently showing alpha as unavailable on the Compare tab even though
    every current run of that strategy computes it."""
    db.log_run(_metrics(sharpe=2.0, alpha_pct=None), ["AAPL"], is_canonical=True)  # pre-migration, better Sharpe
    db.log_run(_metrics(sharpe=0.3, alpha_pct=-5.0), ["MSFT"], is_canonical=True)  # post-migration, worse Sharpe
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'
    assert best["S"]["alpha_pct"] == -5.0


def test_best_run_per_strategy_alpha_completeness_is_a_noop_when_no_run_has_it(db):
    """A strategy whose engine never computes alpha at all (e.g. Overnight
    Hold) must still rank purely by Sharpe -- every row ties on
    alpha-completeness, so it falls through unaffected."""
    db.log_run(_metrics(sharpe=-1.0, alpha_pct=None), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(sharpe=0.5, alpha_pct=None), ["MSFT"], is_canonical=True)
    best = db.best_run_per_strategy()
    assert best["S"]["symbols"] == '["MSFT"]'


def test_run_history_returns_both_canonical_and_experiment_rows(db):
    db.log_run(_metrics(), ["AAPL"], is_canonical=True)
    db.log_run(_metrics(), ["AAPL"], params={"x": 1}, is_canonical=False)
    assert len(db.run_history("S")) == 2


def test_migration_backfills_pre_existing_rows_as_canonical(db, tmp_path):
    """Rows written before is_canonical existed (simulated here by inserting
    directly against the old schema) must read back as canonical=1, not
    NULL -- NULL would silently vanish from latest_run_per_strategy's
    WHERE is_canonical = 1 clause."""
    old_schema_conn = sqlite3.connect(tmp_path / "runs.db")
    old_schema_conn.execute(logging_db._SCHEMA)  # pre-migration columns only
    old_schema_conn.execute(
        "INSERT INTO runs (run_at, strategy_name, symbols, trades_taken) "
        "VALUES ('2020-01-01T00:00:00', 'Legacy', '[]', 5)"
    )
    old_schema_conn.commit()
    old_schema_conn.close()

    # get_connection() runs _migrate() as a side effect of opening -- this
    # is the real migration path, not a re-implementation of it.
    #
    # Asserted against the ROW, not via latest_run_per_strategy(): that now
    # filters to the current metrics_version, and this legacy row is correctly
    # stamped version 0, so it is deliberately excluded from the leaderboard.
    # Observing it through a view that filters it out would test the filter
    # rather than the migration.
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs WHERE strategy_name = 'Legacy'").fetchone()
    conn.close()
    assert row["is_canonical"] == 1
    assert row["metrics_version"] == 0, "a pre-migration row must read as legacy, not current"


def test_migration_is_idempotent(db):
    """Opening the connection twice (ALTER TABLE ADD COLUMN on an already-
    migrated DB) must not raise."""
    db.log_run(_metrics(), ["AAPL"])
    db.get_connection().close()
    db.get_connection().close()
    assert len(db.run_history("S")) == 1


# --- portfolio_runs (cross-sectional / pairs) -----------------------------------


def _portfolio_kwargs(**overrides):
    kwargs = dict(
        strategy_name="Dual Momentum",
        symbols=["AAPL", "MSFT"],
        start=None,
        end=None,
        final_equity=11000.0,
        return_pct=10.0,
        cagr_pct=2.0,
        max_drawdown_pct=5.0,
        sharpe=0.5,
        sortino=0.6,
        risk_free_rate=0.03,
    )
    kwargs.update(overrides)
    return kwargs


def test_log_portfolio_run_refuses_implausible_cumulative_return(db):
    # Regression test for the exact run this check was added for: Dual
    # Momentum x sp500_current, unscaled top_n=5 on a 503-symbol universe,
    # reported 92.27%/yr CAGR (inside PLAUSIBLE_CAGR_PCT alone) and a
    # mutually-consistent +2477.8% cumulative return over ~5 years. The flat
    # CAGR check alone let it through; the cumulative check must not.
    with pytest.raises(logging_db.ImplausibleMetrics):
        db.log_portfolio_run(**_portfolio_kwargs(
            start=date(2021, 8, 14), end=date(2026, 8, 13),
            return_pct=2477.8, cagr_pct=92.27, sharpe=1.77,
        ))


def test_log_portfolio_run_allows_a_genuinely_strong_multi_year_return(db):
    # A real, if excellent, multi-year run (roughly doubling every ~2 years)
    # must not be caught by the same guard that rejects the case above.
    run_id = db.log_portfolio_run(**_portfolio_kwargs(
        start=date(2021, 8, 14), end=date(2026, 8, 13),
        return_pct=400.0, cagr_pct=37.0, sharpe=1.2,
    ))
    assert run_id is not None


def test_log_portfolio_run_defaults_to_canonical(db):
    db.log_portfolio_run(**_portfolio_kwargs())
    row = db.portfolio_run_history("Dual Momentum")[0]
    assert row["is_canonical"] == 1
    assert row["final_equity"] == 11000.0
    assert row["pair_symbol_a"] is None


def test_log_portfolio_run_records_pair_fields(db):
    db.log_portfolio_run(
        **_portfolio_kwargs(
            strategy_name="Pairs / Stat Arb", pair=("AAPL", "MSFT", 0.01),
        )
    )
    row = db.portfolio_run_history("Pairs / Stat Arb")[0]
    assert row["pair_symbol_a"] == "AAPL"
    assert row["pair_symbol_b"] == "MSFT"
    assert row["pair_p_value"] == 0.01


def test_log_portfolio_run_records_no_pair_found(db):
    """A Pairs run that found no cointegrated pair is still worth logging
    -- "ran, found nothing" is different from "never ran"."""
    db.log_portfolio_run(
        **_portfolio_kwargs(strategy_name="Pairs / Stat Arb", pair=None)
    )
    row = db.portfolio_run_history("Pairs / Stat Arb")[0]
    assert row["pair_symbol_a"] is None


def test_latest_portfolio_run_per_strategy_ignores_non_canonical(db):
    db.log_portfolio_run(**_portfolio_kwargs(is_canonical=True))
    db.log_portfolio_run(**_portfolio_kwargs(final_equity=99999.0, is_canonical=False))
    latest = db.latest_portfolio_run_per_strategy()
    assert latest["Dual Momentum"]["final_equity"] == 11000.0


def test_latest_portfolio_run_per_strategy_absent_when_only_experiments_exist(db):
    db.log_portfolio_run(**_portfolio_kwargs(is_canonical=False))
    assert "Dual Momentum" not in db.latest_portfolio_run_per_strategy()


def test_latest_portfolio_run_breaks_same_second_tie_by_id(db):
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=2.0, final_equity=15000.0))
    latest_id = db.log_portfolio_run(
        **_portfolio_kwargs(sharpe=0.4, final_equity=11000.0)
    )

    latest = db.latest_portfolio_run_per_strategy()["Dual Momentum"]

    assert latest["id"] == latest_id
    assert latest["final_equity"] == 11000.0


def test_best_portfolio_run_per_strategy_picks_highest_sharpe(db):
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=0.3, final_equity=10500.0))
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=1.2, final_equity=10800.0))  # older, better
    best = db.best_portfolio_run_per_strategy()
    assert best["Dual Momentum"]["final_equity"] == 10800.0


def test_best_portfolio_run_per_strategy_ignores_non_canonical(db):
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=0.1, is_canonical=True))
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=2.9, is_canonical=False))
    best = db.best_portfolio_run_per_strategy()
    assert best["Dual Momentum"]["sharpe"] == 0.1


def test_best_portfolio_run_prefers_validated_evidence_row(db):
    db.log_portfolio_run(**_portfolio_kwargs(sharpe=2.0, final_equity=15000.0))
    validated_id = db.log_portfolio_run(
        **_portfolio_kwargs(sharpe=0.4, final_equity=11000.0)
    )
    db.attach_validation(
        "portfolio_runs", validated_id, _validation_report(forward_test_worthy=True),
    )

    best = db.best_portfolio_run_per_strategy()

    assert best["Dual Momentum"]["final_equity"] == 11000.0
    assert best["Dual Momentum"]["edge_verdict"] == "Identified edge"


def test_portfolio_run_history_returns_all_runs_newest_first(db):
    db.log_portfolio_run(**_portfolio_kwargs(final_equity=10500.0))
    db.log_portfolio_run(**_portfolio_kwargs(final_equity=11000.0))
    rows = db.portfolio_run_history("Dual Momentum")
    assert len(rows) == 2
    assert rows[0]["final_equity"] == 11000.0  # most recent first


def _validation_report(*, forward_test_worthy: bool) -> dict:
    return {
        "version": 5,
        "generatedAt": "2026-08-12T00:00:00+00:00",
        "dimensions": [],
        "verdict": {
            "headline": "Identified edge" if forward_test_worthy else "Edge not established",
            "forwardTestWorthy": forward_test_worthy,
            "lifecycleStage": "paper_eligible" if forward_test_worthy else "edge_not_established",
            "blockers": [] if forward_test_worthy else ["Historical stability"],
        },
    }


def test_attach_validation_persists_full_report_and_short_verdict(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    report = _validation_report(forward_test_worthy=True)

    db.attach_validation("portfolio_runs", run_id, report)

    row = db.portfolio_run_history("Dual Momentum")[0]
    assert row["edge_verdict"] == "Identified edge"
    assert db.json.loads(row["validation_json"]) == report


def test_paper_execution_requires_forward_worthy_current_canonical_report(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    db.attach_validation(
        "portfolio_runs", run_id, _validation_report(forward_test_worthy=False),
    )

    eligible, reason, selected_id = db.paper_execution_eligibility(
        "Dual Momentum", run_id,
    )

    assert eligible is False
    assert "Historical stability" in reason
    assert selected_id == run_id


def test_paper_execution_accepts_exact_passing_experiment_report(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs(is_canonical=False))
    db.attach_validation(
        "portfolio_runs", run_id, _validation_report(forward_test_worthy=True),
    )

    eligible, reason, selected_id = db.paper_execution_eligibility(
        "Dual Momentum", run_id,
    )

    assert eligible is True
    assert reason == "Forward-test gate passed"
    assert selected_id == run_id


def test_paper_execution_accepts_exact_passing_canonical_report(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    db.attach_validation(
        "portfolio_runs", run_id, _validation_report(forward_test_worthy=True),
    )

    eligible, reason, selected_id = db.paper_execution_eligibility(
        "Dual Momentum", run_id,
    )

    assert eligible is True
    assert reason == "Forward-test gate passed"
    assert selected_id == run_id


def test_paper_execution_rejects_stale_validation_contract(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    report = _validation_report(forward_test_worthy=True)
    report["version"] = 2
    db.attach_validation("portfolio_runs", run_id, report)

    eligible, reason, selected_id = db.paper_execution_eligibility(
        "Dual Momentum", run_id,
    )

    assert eligible is False
    assert "current validation suite" in reason
    assert selected_id == run_id


def test_experiment_registry_counts_the_actual_search_family(db):
    kwargs = dict(
        strategy_name="Dual Momentum",
        engine="cross_sectional",
        hypothesis="Frozen rule beats equal weight out of sample",
        config={"symbols": ["A", "B"], "params": {"lookback": 252}},
        primary_benchmark="Equal-weight selected universe",
        primary_criterion="positive holdout contribution",
        planned_universes=["primary", "mid-cap"],
        search_family="Dual Momentum:cross_sectional",
        is_preregistered=True,
    )

    first_id, first_number = db.register_experiment(**kwargs)
    second_id, second_number = db.register_experiment(**kwargs)
    db.complete_experiment(second_id, "completed", "Edge not established")

    assert first_number == 1
    assert second_number == 2
    assert first_id != second_id
    row = db.experiment(second_id)
    assert row["family_search_number"] == 2
    assert row["status"] == "completed"
    assert row["verdict"] == "Edge not established"


def test_search_family_number_increments_across_registered_universes(db):
    numbers = []
    family = "Frozen Strategy:standard"
    for universe_id in ("dow_pit", "sp500_pit", "sp400_pit", "sp600_pit"):
        _, number = db.register_experiment(
            strategy_name="Frozen Strategy", engine="standard", hypothesis="h",
            config={"universeId": universe_id, "params": {}},
            primary_benchmark="SPY", primary_criterion="positive gap",
            planned_universes=["dow_pit", "sp500_pit", "sp400_pit", "sp600_pit"],
            search_family=family, is_preregistered=True, universe_id=universe_id,
        )
        numbers.append(number)
    db.set_family_search_count(family, 4)

    assert numbers == [1, 2, 3, 4]
    rows = [db.experiment(i) for i in range(1, 5)]
    assert {row["search_family"] for row in rows if row} == {family}
    assert {row["family_search_count"] for row in rows if row} == {4}


def test_attach_validation_persists_experiment_manifest_and_lifecycle(db):
    experiment_id, _ = db.register_experiment(
        strategy_name="Dual Momentum", engine="cross_sectional", hypothesis="h",
        config={}, primary_benchmark="EW", primary_criterion="positive",
        planned_universes=[], search_family="family", is_preregistered=True,
    )
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    report = _validation_report(forward_test_worthy=True)
    report["research"] = {
        "experimentId": experiment_id,
        "manifest": {"runFingerprint": "abc123"},
        "lifecycleStage": "paper_eligible",
    }

    db.attach_validation("portfolio_runs", run_id, report)

    row = db.portfolio_run_history("Dual Momentum")[0]
    assert row["experiment_id"] == experiment_id
    assert db.json.loads(row["manifest_json"])["runFingerprint"] == "abc123"
    assert row["lifecycle_stage"] == "paper_eligible"


def test_both_writers_populate_the_same_provenance_set(tmp_path, monkeypatch):
    """log_run and log_portfolio_run must agree on the provenance columns.

    They diverged once already: the provenance migration added the columns to
    log_portfolio_run's INSERT and not log_run's, so every per-symbol row was
    written with metrics_version NULL -- and _migrate's legacy stamp then
    relabelled those fully-corrected rows "legacy_total_unadjusted" on the very
    next connection. A correct number wearing a wrong label survives any amount
    of recomputation, because the recomputed value matches; only the audit
    layer was wrong. This asserts the shape rather than re-checking the one
    instance, so adding a sixth provenance column to one writer fails here
    instead of silently mislabelling half the table.
    """
    import sqlite3

    from engine import logging_db
    from engine.metrics import BacktestMetrics

    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")

    metrics = BacktestMetrics(
        strategy_name="probe", symbol="ALL", start=None, end=None,
        trades_taken=1, wins=1, losses=0, win_rate=1.0, avg_win_r=1.0,
        avg_loss_r=0.0, expectancy_r=1.0, profit_factor=2.0,
        max_drawdown_pct=1.0, sharpe=1.0, sortino=1.0, status="probe",
        measured_start=date(2021, 1, 4), measured_end=date(2021, 6, 30),
    )
    logging_db.log_run(metrics, ["AAA"], slippage_bps=2.5, commission_bps=0.0)
    logging_db.log_portfolio_run(
        strategy_name="probe", symbols=["AAA"], start=None, end=None,
        final_equity=1.0, return_pct=1.0, cagr_pct=1.0, max_drawdown_pct=1.0,
        sharpe=1.0, sortino=1.0, risk_free_rate=0.03,
        slippage_bps=2.5, commission_bps=0.0,
        return_basis=logging_db.RETURN_BASIS_EXCESS,
        measured_start=date(2021, 1, 4), measured_end=date(2021, 6, 30),
    )

    conn = logging_db.get_connection()
    conn.row_factory = sqlite3.Row
    populated = {}
    for table in ("runs", "portfolio_runs"):
        row = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        populated[table] = {
            name for name, _type in logging_db._PROVENANCE_COLUMNS if row[name] is not None
        }
    conn.close()

    assert populated["runs"] == populated["portfolio_runs"], (
        f"writers disagree on provenance: runs populated {sorted(populated['runs'])}, "
        f"portfolio_runs populated {sorted(populated['portfolio_runs'])} -- a column "
        "present in one writer and absent from the other produces rows whose "
        "labels contradict their contents"
    )
    assert populated["runs"] == {c for c, _t in logging_db._PROVENANCE_COLUMNS}


def test_both_writers_reject_implausible_sustained_cagr(db):
    """log_run and log_portfolio_run must agree on rejecting the same class
    of out-of-band value, mirroring
    test_both_writers_populate_the_same_provenance_set above.

    Regression test for the real run this closes: Dual Momentum x
    sp500_current reported 92.27%/yr CAGR over ~5 years (+2477.8%
    cumulative) -- individually inside the flat single-period plausibility checks,
    caught only by the years-aware sustained-rate check. Only
    log_portfolio_run enforced it at first (it alone had a raw return_pct to
    check); this asserts the SHAPE -- both writers, given the same
    measured-window span and the same implausible CAGR, must both refuse --
    so a writer that regains this gap fails here instead of silently
    accepting an impossible number again.
    """
    span_start, span_end = date(2021, 8, 14), date(2026, 8, 13)  # ~5 years
    implausible_cagr = 92.27  # inside PLAUSIBLE_CAGR_PCT alone; not sustained

    metrics = compute_metrics(
        "probe", "ALL",
        pd.DataFrame([{"EntryPrice": 100.0, "SL": 99.0, "Size": 1, "PnL": 1.0}]),
        sharpe=1.77, cagr_pct=implausible_cagr,
        measured_start=span_start, measured_end=span_end,
    )
    with pytest.raises(logging_db.ImplausibleMetrics):
        db.log_run(metrics, ["AAA"])

    with pytest.raises(logging_db.ImplausibleMetrics):
        db.log_portfolio_run(**_portfolio_kwargs(
            start=span_start, end=span_end,
            return_pct=2477.8, cagr_pct=implausible_cagr, sharpe=1.77,
        ))


def test_legacy_stamp_runs_once_not_per_connection(tmp_path, monkeypatch):
    """A migration that re-runs on every connection is a trap, not a migration.

    The legacy stamp rewrites metrics_version NULL -> 0. Run per connection, it
    relabels any row a future code path forgets to version -- and merely
    OPENING the database to inspect it triggers the corruption. Observed: a
    diagnostic query was the act that mislabelled the row it was checking.
    """
    import sqlite3

    from engine import logging_db

    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")

    conn = logging_db.get_connection()
    with conn:
        conn.execute(
            "INSERT INTO portfolio_runs (run_at, strategy_name, symbols, is_canonical) "
            "VALUES ('2026-01-01', 'unversioned', '[]', 1)"
        )
    conn.close()

    # A fresh connection must NOT stamp the deliberately-unversioned row.
    conn = logging_db.get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT metrics_version, return_basis FROM portfolio_runs WHERE strategy_name='unversioned'"
    ).fetchone()
    markers = conn.execute("SELECT COUNT(*) n FROM schema_meta").fetchone()["n"]
    conn.close()

    assert row["metrics_version"] is None, (
        "opening the database relabelled an unversioned row as legacy -- a NULL "
        "that survives is a visible bug, a NULL silently rewritten is corrupted "
        "provenance that reads as intentional"
    )
    assert markers == 1, "legacy stamp marker written more than once (not committed atomically)"


def test_frozen_neighbor_result_persists_diagnostics_without_run_history_row(db):
    experiment_id, _ = db.register_experiment(
        strategy_name="Frozen probe", engine="standard", hypothesis="h",
        config={"threshold": 2}, primary_benchmark="matched SPY",
        primary_criterion="positive excess", planned_universes=["dow_pit"],
        search_family="frozen-probe:standard", is_preregistered=True,
    )
    db.record_frozen_neighbor_result(
        experiment_id=experiment_id, strategy_name="Frozen probe",
        search_family="frozen-probe:standard", config={"threshold": 2},
        status="completed", result={
            "returnPct": 1.25, "trades": 17, "expectancyR": 0.1,
            "benchmarkExcessPct": 0.4, "supportsHypothesis": True,
        },
    )
    row = db.frozen_neighbor_results("frozen-probe:standard")[0]
    assert row["experiment_id"] == experiment_id
    assert row["trades"] == 17
    assert row["supports_hypothesis"] == 1
    assert db.run_history("Frozen probe") == []
