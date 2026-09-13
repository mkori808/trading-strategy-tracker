from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategies.residual_momentum import SPECS, _classify, _residual_returns, _scores, _write_report


def test_residual_decomposition_removes_same_day_sector_return():
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    # Two sectors whose members have identical daily returns: residuals vanish.
    base = np.cumprod(np.r_[100.0, np.repeat(1.001, len(dates) - 1)])
    C = pd.DataFrame({"A": base, "B": base, "C": base * 2, "D": base * 2}, index=dates)
    eligible = pd.DataFrame(True, index=dates, columns=C.columns)
    sectors = pd.Series({"A": "X", "B": "X", "C": "Y", "D": "Y"})
    residual, _, _ = _residual_returns(C, eligible, sectors)
    assert np.nanmax(np.abs(residual.iloc[1:].to_numpy())) < 1e-12


def test_preregistered_252_window_excludes_recent_twenty_sessions():
    dates = pd.date_range("2020-01-01", periods=600, freq="B")
    C = pd.DataFrame({x: np.arange(100, 700, dtype=float) for x in "ABCDE"}, index=dates)
    H, L = C * 1.01, C * .99
    eligible = pd.DataFrame(True, index=dates, columns=C.columns)
    residual = pd.DataFrame(0.0, index=dates, columns=C.columns)
    # At the final date the frozen window is exactly t-251 through t-20.
    residual.iloc[-253, :] = 7.0
    residual.iloc[-252:-20, :] = 1.0
    residual.iloc[-20:, :] = 11.0
    raw, _ = _scores(residual, C, H, L, eligible, pd.Series({x: "X" for x in "ABCDE"}))
    assert SPECS["residual_momentum_252d_ex20"]["skip"] == 20
    assert SPECS["residual_momentum_252d_ex20"]["lookback"] == 232
    assert np.allclose(raw["residual_momentum_252d_ex20"].iloc[-1], 232.0)


def test_classification_uses_spread_alpha_not_long_only_alpha():
    primary = {"spread_mean": .01, "spread_hac_tstat": 3.0, "ic_mean": .02}
    implementation = {
        "factor_alpha_annual": .10,
        "spread_factor_alpha_annual": -.03,
        "spread_factor_alpha_tstat": -1.2,
    }
    assert _classify(primary, implementation) == "FALSIFIED"


def test_report_writer_has_no_optional_tabulate_dependency(tmp_path):
    result = {
        "classification": "SUPPORTED",
        "summary": {20: {"spread_annualized": .10, "spread_hac_tstat": 3.0, "ic_mean": .02}},
        "implementation": {"spread_factor_alpha_annual": .03, "net_cagr": .05, "turnover_annual": 2.0, "max_drawdown": -.2},
    }
    bundle = {"results": {"test": result}, "correlations": pd.DataFrame([{"signal": "test", "comparison": "IBS", "correlation": .1}])}
    _write_report(bundle, tmp_path, "2001-01-01", "2018-12-31")
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| test | IBS | 0.100000 |" in report


def test_all_three_preregistrations_are_distinct_and_locked():
    prereg = ROOT / "research" / "preregistrations"
    ids = []
    for spec in SPECS.values():
        prereg_path = prereg / spec["file"]
        item = json.loads(prereg_path.read_text())
        ids.append(item["experiment_id"])
        assert hashlib.sha256(prereg_path.read_bytes()).hexdigest() == spec["sha256"]
        assert item["primary_forward_horizon_days"] == 20
        assert item["integrity"]["used_holdout_rule"].startswith("Not evaluated")
    assert len(set(ids)) == 3
