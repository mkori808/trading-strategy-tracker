import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies.short_term_mean_reversion import (  # noqa: E402
    ResearchConfig, adjusted_open, attach_fundamentals, build_parser, classify,
    config_from_args, filter_eligible, research_grid, select_losers, side_cost_rate,
)


def test_adjusted_open_uses_same_day_adjustment_factor():
    frame = pd.DataFrame({"open": [50.0], "close": [55.0], "closeadj": [110.0]})
    assert adjusted_open(frame).iloc[0] == 100.0


def test_loser_selection_is_deterministic_and_rounds_up():
    frame = pd.DataFrame({"ticker": [f"T{i:03}" for i in range(101)],
                          "raw_1d": np.arange(101, dtype=float)})
    chosen = select_losers(frame, "raw_1d", "pct_2")
    assert chosen.ticker.tolist() == ["T000", "T001", "T002"]
    assert len(select_losers(frame, "raw_1d", "top_20")) == 20


def test_costs_are_monotone_with_liquidity():
    got = side_cost_rate(np.array([1e6, 10e6, 50e6, 200e6]))
    assert np.allclose(got, [.0035, .0020, .00125, .00075])
    assert np.all(np.diff(got) < 0)


def test_grid_contains_required_variants_holds_and_primary():
    grid = research_grid()
    assert sum(c.primary for c in grid) == 1
    for signal in ("idio_1d", "beta_idio_1d", "volnorm_20d", "volnorm_60d", "gap_down"):
        assert {c.hold_days for c in grid if c.signal == signal and c.min_price == 1} == {1, 2, 3, 4, 5}
    for signal in ("raw_1d", "raw_3d"):
        assert {1, 2, 3, 5}.issubset({c.hold_days for c in grid if c.signal == signal})


def test_classification_does_not_accept_a_cost_failure():
    primary = pd.Series({"net_return_after_costs": -.01, "five_factor_alpha_annual": .03,
                         "alpha_tstat_hac": 2.5, "number_of_trades": 5000,
                         "max_drawdown_net": -.2, "minimum_adv": 5e6,
                         "annualized_return_gross": .10})
    verdict, gates = classify(primary)
    assert verdict == "UNRESOLVED"
    assert "FAIL: net annual return is positive" in gates


def test_cli_parameters_create_one_primary_configuration():
    args = build_parser().parse_args([
        "--signal", "raw_3d", "--hold-days", "3", "--selection", "top_20",
        "--min-price", "2.5", "--min-adv", "1000000", "--max-vol20", "none",
    ])
    config = config_from_args(args)
    assert config == ResearchConfig(
        "raw_3d", 3, selection="top_20", min_price=2.5,
        min_adv=1_000_000, max_vol20=None, primary=True,
    )


def test_invalid_selection_is_rejected_by_configuration():
    with np.testing.assert_raises_regex(ValueError, "pct_N or top_N"):
        ResearchConfig("raw_1d", 1, selection="quintile")


def test_fundamentals_are_not_visible_until_reporting_lag_has_elapsed():
    prices = pd.DataFrame({
        "ticker": ["ABC", "ABC"],
        "date": pd.to_datetime(["2025-03-31", "2025-06-29"]),
    })
    fundamentals = pd.DataFrame({
        "ticker": ["ABC"], "date": pd.to_datetime(["2025-03-31"]),
        "equity": [10.0], "assets": [100.0], "debt": [90.0],
        "netinc": [-1.0], "fcf": [-2.0],
    })
    got = attach_fundamentals(prices, fundamentals, reporting_lag_days=90)
    assert not got.iloc[0].fundamental_data_available
    assert got.iloc[1].fundamental_data_available
    assert got.iloc[1].fundamental_red_flags == 2


def test_momentum_pullback_filter_requires_persistent_strength_and_clean_fundamentals():
    day = pd.DataFrame({
        "adj_close": [10.0] * 4, "adv20": [10_000_000.0] * 4,
        "vol20": [.02] * 4,
        "mom_3m_skip5": [.10, .10, -.01, .10],
        "mom_6m_skip5": [.20, .20, .20, .20],
        "mom_12m_skip5": [.30, .30, .30, .30],
        "momentum_strength": [.30, .25, .40, .20],
        "fundamental_red_flags": [0, 1, 0, 0],
    }, index=list("ABCD"))
    config = ResearchConfig(
        "momentum_pullback", 3, min_price=5, min_adv=5_000_000,
        max_vol20=None, min_momentum_percentile=.75,
    )
    assert filter_eligible(day, config).index.tolist() == ["A"]
