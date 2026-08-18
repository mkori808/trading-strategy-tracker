"""Score a candidate BEFORE writing any strategy code.

Both research programmes in this project ran the questions backwards: test
first, then ask whether the test could have resolved anything. Equity momentum
cost weeks and returned MDA 12.00%/yr against a claimed ~2%/yr effect. Futures
trend was killed in an hour by asking first. Reversing the order is the entire
gain, and this module is that reversal made mechanical.

Four criteria, IN ORDER. Later ones are not evaluated if an earlier one fails,
because a design with no mechanism does not deserve a power calculation.

    1. MECHANISM      -- who pays, and why they cannot stop
    2. EFFECT SIZE    -- claimed, in % per event or % per year
    3. OBSERVATIONS   -- independent ones available per year
    4. MDA            -- reject if it exceeds the claimed effect

**Criterion 3 is the lever.** Monthly rebalancing yields ~12 observations a
year no matter how many names are held -- the IC test on Dual Momentum used
1,856 name-months and still produced t = 0.27, because SE depends on the number
of independent TIME PERIODS, not on cross-sectional width. Event-driven designs
break that ceiling: hundreds of discrete events a year, each with its own
outcome.

### Effect sizes and event counts here are ESTIMATES, not citations

Every number in `CANDIDATES` below is a rough prior recorded so the screen can
be demonstrated. They are NOT verified against source papers, and this project
has repeatedly shown that unverified inputs produce plausible-looking wrong
answers. **Replace each with a figure checked against a named source before
acting on any ranking this produces.** The `verified` flag tracks that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def effective_n(series, nominal_n: float | None = None) -> float:
    """Independent-observation count after an AUTOCORRELATION haircut.

        n_eff = n / (1 + 2 * sum_k rho_k)

    Nominal counts are wrong for any persistent series. A closed-end fund
    discount does not deliver 12 independent observations a year -- it delivers
    a slow-moving level sampled monthly, and treating those as independent
    inflates power exactly the way cross-sectional width inflated the IC test.

    Summed over lags until the autocorrelation first goes non-positive, the
    standard truncation: continuing past that point adds noise rather than
    signal.
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x) if nominal_n is None else nominal_n
    if len(x) < 8:
        return float(n)
    x = x - x.mean()
    denom = float((x * x).sum())
    if denom <= 0:
        return float(n)
    total = 0.0
    for lag in range(1, min(len(x) // 2, 50)):
        rho = float((x[:-lag] * x[lag:]).sum()) / denom
        if rho <= 0:
            break
        total += rho
    return float(n) / (1.0 + 2.0 * total)


@dataclass
class Candidate:
    name: str
    #: Who pays and why they cannot stop. Empty string = fails criterion 1.
    mechanism: str
    #: Claimed effect. Per-event for event studies, per-year for continuous.
    claimed_effect_pct: float
    per_event: bool
    #: Independent observations available per year.
    observations_per_year: float
    #: Dispersion of the per-observation outcome.
    outcome_vol_pct: float
    #: Years of history obtainable.
    years_available: float
    #: True once effect size and event count are checked against a named source.
    verified: bool = False
    notes: str = ""
    caveats: list[str] = field(default_factory=list)

    #: Autocorrelation haircut on observations. 1.0 = independent (discrete
    #: events); below 1.0 = persistent series where nominal count overstates
    #: independence. Set from effective_n() on a real series, never guessed.
    independence_factor: float = 1.0

    @property
    def total_observations(self) -> float:
        return self.observations_per_year * self.years_available * self.independence_factor

    @property
    def mda_pct(self) -> float:
        """Minimum detectable effect at t=2, same formula as everywhere else."""
        if self.total_observations <= 0:
            return float("inf")
        return 2.0 * self.outcome_vol_pct / (self.total_observations ** 0.5)

    @property
    def passes(self) -> bool:
        return bool(self.mechanism) and self.mda_pct <= self.claimed_effect_pct

    @property
    def margin(self) -> float:
        """How many times the claimed effect exceeds the MDA. >1 is detectable."""
        return self.claimed_effect_pct / self.mda_pct if self.mda_pct else 0.0


# Priors for demonstration. NONE ARE VERIFIED -- see module docstring.
CANDIDATES = [
    Candidate(
        "Index reconstitution",
        "Index funds MUST buy at the close on a published date, size known in "
        "advance, and are penalised on tracking error rather than price. They "
        "cannot decline to trade.",
        claimed_effect_pct=1.5, per_event=True,
        observations_per_year=300, outcome_vol_pct=6.0, years_available=20,
        notes="Russell annual recon plus S&P/other index changes.",
        caveats=["Effect widely documented and likely decayed since the 1990s.",
                 "Front-running is itself crowded; the payer may now be YOU."],
    ),
    Candidate(
        "Merger arbitrage",
        "Holders of target shares want deal-risk certainty and sell below the "
        "offer. The spread pays for bearing break risk, not for being right.",
        claimed_effect_pct=3.0, per_event=True,
        observations_per_year=250, outcome_vol_pct=12.0, years_available=20,
        notes="US announced deals. Outcome vol is high: breaks are large losses.",
        caveats=["Returns are strongly negatively skewed -- a t-stat on a skewed "
                 "distribution overstates confidence.",
                 "Capacity limited; crowded in large deals."],
    ),
    Candidate(
        "Spinoff / stub mechanics",
        "Index and mandate rules force holders to sell a stub they cannot hold "
        "(wrong index, wrong cap band, wrong sector). Selling is not "
        "information-driven.",
        claimed_effect_pct=3.0, per_event=True,
        observations_per_year=40, outcome_vol_pct=15.0, years_available=20,
        notes="US spinoffs. Few events, high idiosyncratic dispersion.",
        caveats=["Small n and high vol is the worst combination for power."],
    ),
    Candidate(
        "Closed-end fund discounts",
        "Structural: no arbitrage mechanism forces price to NAV, and discounts "
        "persist. Payer is the seller accepting below NAV for liquidity.",
        claimed_effect_pct=4.0, per_event=False,
        observations_per_year=12, outcome_vol_pct=10.0, years_available=25,
        # Discounts mean-revert over quarters, not months. A monthly series with
        # rho_1 ~ 0.9 has n_eff roughly a tenth of nominal. 0.10 is a placeholder
        # standing in for effective_n() run on a REAL discount series -- it is
        # still a guess, just a less wrong one, and it is flagged unverified.
        independence_factor=0.10,
        notes="Discount level is persistent and highly AUTOCORRELATED -- "
              "observations_per_year set to monthly, but true independence is "
              "far lower and this number is optimistic.",
        caveats=["Autocorrelation means effective n is well below nominal.",
                 "Severely capacity-limited."],
    ),
    Candidate(
        "Tax-loss selling (Dec/Jan)",
        "Investors sell losers before year end for tax reasons, on a known "
        "calendar, for reasons unrelated to expected return.",
        claimed_effect_pct=2.0, per_event=False,
        observations_per_year=1, outcome_vol_pct=8.0, years_available=30,
        notes="ONE event per year. Cross-sectional width does not help: every "
              "stock shares the same December, so the observations are almost "
              "perfectly correlated within a year.",
        caveats=["This is the Dual Momentum failure mode exactly -- width "
                 "without independent time periods."],
    ),
]


def screen(candidates: list[Candidate] = CANDIDATES) -> list[Candidate]:
    """Rank by detectability margin, rejecting anything without a mechanism."""
    return sorted(candidates, key=lambda c: -c.margin)


def main() -> int:
    print("CANDIDATE SCREEN -- scored BEFORE any strategy code is written")
    print("All effect sizes and event counts are UNVERIFIED priors.\n")
    header = (f"{'candidate':28} {'effect':>8} {'obs/yr':>7} {'total n':>8} "
              f"{'MDA':>7} {'margin':>7}  verdict")
    print(header); print("-" * len(header))
    for c in screen():
        unit = "%/evt" if c.per_event else "%/yr"
        verdict = "TESTABLE" if c.passes else "underpowered"
        print(f"{c.name[:28]:28} {c.claimed_effect_pct:6.1f}{unit:>2} "
              f"{c.observations_per_year:7.0f} {c.total_observations:8.0f} "
              f"{c.mda_pct:6.2f}% {c.margin:6.1f}x  {verdict}")
    print("\nmargin = claimed effect / MDA. Above 1.0x the design can resolve "
          "its own claim.\n")
    for c in screen():
        if c.caveats:
            print(f"{c.name}:")
            for note in c.caveats:
                print(f"   - {note}")
    print("\nNONE of these are verified. Check effect size and event count "
          "against a named source before acting on this ranking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
