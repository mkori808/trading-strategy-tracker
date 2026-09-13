import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies.ibs_conditioning import (  # noqa: E402
    HYPOTHESES, TAXONOMY, ResearchSettings, build_features, classify_contrast,
    _hac_group_difference, evaluate_day, side_cost_rate, validate_preregistration, write_outputs,
)


def _price_panel(n_days=285, tickers=("AAA", "BBB")):
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    rows = []
    for j, ticker in enumerate(tickers):
        base = 20 + j * 10 + np.arange(n_days) * 0.02
        for i, date in enumerate(dates):
            close = float(base[i] + np.sin(i / 7) * 0.2)
            rows.append({
                "ticker": ticker, "date": date,
                "open": close - 0.05, "high": close + 0.20,
                "low": close - 0.20, "close": close,
                "volume": 1_000_000 + i * 100,
                "marketcap": 1_000_000_000 + j * 500_000_000 + i * 100_000,
                "sector": "Technology",
                "firstpricedate": dates[0] - pd.Timedelta(days=500),
                "lastpricedate": pd.NaT,
            })
    return pd.DataFrame(rows), dates


def test_registry_contains_each_roadmap_condition_once_and_no_composite():
    expected = {
        "earnings_event", "overnight_gap_share", "abnormal_volume", "turnover",
        "adv20_pre", "intraday_range", "idio_vol20_pre", "marketcap_pre",
        "total_vol20_pre", "market_regime",
    }
    assert {h.feature for h in HYPOTHESES} == expected
    assert len(HYPOTHESES) == len(expected)
    assert TAXONOMY == ("SUPPORTED", "FALSIFIED", "UNRESOLVED / UNDERPOWERED")


def test_features_use_exact_filing_date_and_next_session_execution():
    prices, dates = _price_panel()
    signal_date = dates[-3]
    earnings = pd.DataFrame({"ticker": ["AAA"], "date": [signal_date]})
    got = build_features(prices, earnings, signal_date, signal_date)
    aaa = got[got.ticker == "AAA"].iloc[0]
    bbb = got[got.ticker == "BBB"].iloc[0]

    assert aaa.earnings_event == "event"
    assert bbb.earnings_event == "non_event"
    assert aaa.entry_date == dates[-2]
    expected = prices[(prices.ticker == "AAA") & (prices.date == dates[-2])].iloc[0]
    assert np.isclose(aaa.forward_return, expected.close / expected.open - 1)


def test_future_mutation_cannot_change_signal_day_conditioners():
    prices, dates = _price_panel()
    signal_date = dates[-4]
    before = build_features(prices, pd.DataFrame(columns=["ticker", "date"]), signal_date, signal_date)
    mutated = prices.copy()
    future = mutated.date > signal_date
    mutated.loc[future, ["open", "high", "low", "close", "volume", "marketcap"]] *= 10
    after = build_features(mutated, pd.DataFrame(columns=["ticker", "date"]), signal_date, signal_date)
    condition_columns = [
        "ibs_score", "overnight_gap_share", "abnormal_volume", "turnover",
        "adv20_pre", "intraday_range", "idio_vol20_pre", "marketcap_pre",
        "total_vol20_pre", "market_regime",
    ]
    pd.testing.assert_frame_equal(
        before.set_index("ticker")[condition_columns],
        after.set_index("ticker")[condition_columns],
    )


def test_evaluate_day_ranks_ibs_globally_and_charges_both_sides():
    n = 120
    day = pd.DataFrame({
        "ticker": [f"T{i:03}" for i in range(n)],
        "date": pd.Timestamp("2025-01-03"),
        "entry_date": pd.Timestamp("2025-01-06"),
        "ibs_score": np.linspace(0, 1, n),
        "forward_return": np.linspace(-0.01, 0.02, n),
        "forward_return_h2": np.linspace(-0.015, 0.025, n),
        "forward_return_h5": np.linspace(-0.02, 0.03, n),
        "adv20_pre": 10_000_000.0,
        "abnormal_volume": np.tile([0.5, 1.0, 2.0], n // 3),
        "market_regime": "bull",
    })
    hypothesis = next(h for h in HYPOTHESES if h.feature == "abnormal_volume")
    observations = evaluate_day(day, hypothesis, min_names=100, min_names_per_leg=2)
    assert {row["bucket"] for row in observations} == {"low", "mid", "high"}
    high = next(row for row in observations if row["bucket"] == "high")
    assert high["long_gross"] > 0
    assert high["long_net"] < high["long_gross"]
    assert high["q1_return"] > high["q5_return"]


def test_cost_schedule_and_classification_preserve_research_taxonomy():
    assert np.allclose(
        side_cost_rate(np.array([1e6, 10e6, 50e6, 200e6])),
        [.0035, .0020, .00125, .00075],
    )
    supported, _ = classify_contrast(pd.Series(np.full(300, 0.001)), threshold=2.81)
    falsified, _ = classify_contrast(pd.Series(np.full(300, -0.001)), threshold=2.81)
    underpowered, _ = classify_contrast(pd.Series(np.full(100, 0.001)), threshold=2.81)
    assert supported == "SUPPORTED"
    assert falsified == "FALSIFIED"
    assert underpowered == "UNRESOLVED / UNDERPOWERED"


def test_mutually_exclusive_regimes_use_hac_group_difference_not_pairing():
    bull = pd.Series([0.005, 0.006, 0.004] * 60)
    bear = pd.Series([-0.004, -0.003, -0.005] * 60)
    result = _hac_group_difference(bull, bear)
    assert result["n"] == 360
    assert result["mean"] > 0
    assert result["tstat"] > 2.81


def test_preregistration_matches_executable_registry_and_dates():
    path = Path(__file__).resolve().parents[2] / "research" / "ibs_conditioning_preregistration.json"
    specification = validate_preregistration(path, ResearchSettings())
    assert specification["integrity"]["original_holdout_budget_remaining"] == 0
    with np.testing.assert_raises_regex(ValueError, "development window"):
        validate_preregistration(path, ResearchSettings(end="2019-12-31"))


def test_output_bundle_contains_strict_json_and_human_report(tmp_path):
    hypothesis = HYPOTHESES[0]
    bucket_results = pd.DataFrame([{
        "hypothesis_id": hypothesis.hypothesis_id, "feature": hypothesis.feature,
        "bucket": hypothesis.favorable_bucket, "cagr_net": 0.05, "spy_cagr": 0.10,
        "excess_cagr": -0.05, "alpha_annual": 0.02, "alpha_tstat": 1.5,
        "sharpe": 0.8, "max_drawdown": -0.1, "annual_two_way_turnover": 504.0,
        "ic_mean": 0.01,
    }])
    contrasts = pd.DataFrame([{
        "hypothesis_id": hypothesis.hypothesis_id, "favorable_bucket": "non_event",
        "adverse_bucket": "event", "contrast_annualized": np.nan,
        "contrast_hac_tstat": np.nan, "contrast_days": 0,
        "classification": "UNRESOLVED / UNDERPOWERED",
    }])
    dates = pd.bdate_range("2018-01-02", periods=2)
    prereg = Path(__file__).resolve().parents[2] / "research" / "ibs_conditioning_preregistration.json"
    out = write_outputs(
        bucket_results, contrasts, pd.DataFrame({"x": [1]}),
        {"evidence_stage": "test", "value": np.nan},
        pd.DataFrame(index=dates), pd.Series([0.0, 0.01], index=dates, name="SPY"),
        ResearchSettings(), tmp_path, prereg, tmp_path / "missing.db",
    )
    assert (out / "report.md").exists()
    assert (out / "hypothesis_contrasts.csv").exists()
    assert json.loads((out / "results.json").read_text())["value"] is None
    assert json.loads((out / "run_manifest.json").read_text())["prospective_confirmation_required"]
