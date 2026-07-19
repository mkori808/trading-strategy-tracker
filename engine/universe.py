"""Pre-registered symbol lists and date ranges for backtesting.

Fixed before any backtest is run, per CLAUDE.md: picking symbols after
seeing which ones moved is survivorship bias. Do not add/remove symbols
based on backtest results.
"""

from __future__ import annotations

from datetime import date, timedelta

TIMEZONE = "America/New_York"

# Historical DJIA constituents as of July 2021 -- the roster after the
# Aug 31, 2020 reconstitution (added AMGN/HON/CRM, dropped XOM/PFE/RTX) and
# before the Feb 26, 2024 one (WBA -> AMZN). Source: Wikipedia's "Historical
# components of the Dow Jones Industrial Average".
#
# This replaced an earlier universe (SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA)
# that was picked in 2026 with full hindsight of the 2021-2026 window -- every
# one of those seven went up (mean +287%, NVDA +1035%), so any long-only
# strategy tested against them looked good almost by construction. See
# LESSONS.md, "the test universe is exactly the survivorship bias CLAUDE.md
# warns about".
#
# This list is an externally-defined, point-in-time index membership instead
# -- chosen for what the Dow *was* in mid-2021, not for what did well since.
# It deliberately includes names that lagged or declined over the backtest
# window (INTC, VZ, DIS, BA, MMM) right alongside the strong performers, so a
# strategy's apparent edge here can't just be beta to stocks we already know
# did well. It is NOT a complete fix: Dow membership itself selects for
# "large and established," and unlike a true point-in-time universe it can't
# include names that were delisted/went bankrupt before 2026 (yfinance can't
# fetch a ticker that no longer exists) -- so some residual survivorship bias
# remains, just far less than hand-picking 2026's biggest winners.
#
# WBA (Walgreens Boots Alliance) was also a July-2021 Dow component and is
# the clearest illustration of that residual gap: it was dropped from the
# index in Feb 2024 for poor performance and then taken private in 2025, so
# its ticker no longer resolves via yfinance at all. It's excluded here
# rather than swapped for a substitute -- picking a replacement now, with
# hindsight of which stocks are still tradeable, would just reintroduce the
# bias this universe exists to avoid. 29 names, not 30.
EQUITY_UNIVERSE: list[str] = [
    "MMM", "GS", "NKE", "AXP", "HD", "PG", "AMGN", "HON", "CRM", "AAPL",
    "INTC", "TRV", "BA", "IBM", "UNH", "CAT", "JNJ", "VZ", "CVX", "JPM",
    "V", "CSCO", "MCD", "KO", "MRK", "WMT", "DOW", "MSFT", "DIS",
]

# A second, smaller-cap universe -- built to test whether "no strategy here
# clears the shortlist bar" is a property of the strategies, or a property
# of testing exclusively on the Dow: the 29 names above are literally among
# the most heavily-traded, most-analyzed stocks on earth, arguably the
# hardest place left to find retail-accessible inefficiency.
#
# Methodology (weaker point-in-time rigor than EQUITY_UNIVERSE, disclosed):
# unlike the Dow, Wikipedia has no maintained "historical S&P MidCap 400
# components as of a date" page -- 400 names with high turnover doesn't get
# that treatment the way 30 rarely-changed Dow slots do. So this starts from
# *today's* S&P 400 membership (List of S&P 400 companies, Wikipedia),
# ~280 tickers pulled in the table's own (alphabetical, performance-blind)
# order, filtered by an objective, data-driven rule -- actual price history
# starting on/before the backtest window's start date -- rather than by
# memory or by which ones did well. That filter correctly excluded obvious
# 2021+ IPOs (BROS, CART, CAVA, CRBG, DUOL, KD, KNF, NXT, ...), leaving 267
# real candidates. From those, this is every ~10th ticker in that same
# alphabetical order (267/27), a mechanical sample with no cherry-picking of
# individual names in either direction.
#
# Residual bias, same shape as EQUITY_UNIVERSE's: today's index membership
# still selects for "grew enough to still be mid-cap-or-larger and still
# investable today," so this is not a true 2021 point-in-time snapshot the
# way the Dow list is. Weaker rigor than EQUITY_UNIVERSE, disclosed rather
# than hidden -- see LESSONS.md.
MIDCAP_UNIVERSE: list[str] = [
    "AA", "AIT", "AN", "ATI", "BC", "BRX", "CCK", "CHRD", "COLB", "CVLT",
    "DLB", "ELAN", "EWBC", "FHI", "FOUR", "GLPI", "HALO", "HRB", "ITT",
    "KNX", "LNTH", "MKSI", "MTSI", "NNN", "OC", "OPCH", "PBF",
]

# A small-cap universe, same methodology and same disclosed limitations as
# MIDCAP_UNIVERSE above: today's S&P 600 membership (List of S&P 600
# companies, Wikipedia), not a maintained historical-membership list (small
# caps churn even faster than mid caps, so no such list exists), mechanically
# sampled (every ~10th ticker in the page's own alphabetical order) rather
# than hand-picked, then filtered by the same objective rule -- actual price
# history starting on/before the 5-year swing backtest window -- which
# dropped AMTM, COCO, and CURB here (all 2021+ IPOs/spinoffs, the same
# failure mode MIDCAP_UNIVERSE's methodology note describes).
#
# Extra caveat specific to this list: the source fetch truncated past the
# "K" tickers, so the candidate pool sampled from was alphabetically A-K
# only, not the full S&P 600. That skews this pool toward the first half of
# the alphabet -- a real gap, disclosed rather than silently accepted as a
# full-index sample.
SMALL_CAP_UNIVERSE: list[str] = [
    "AAMI", "ACIW", "AEO", "ALG", "AROC", "AWI", "BCPC", "BLFS", "BXMT",
    "CBRL", "CFFN", "CRC", "CXW", "DFIN", "EAT", "ENPH", "EVTC", "FCPT",
    "FORM", "GEO", "GRBK", "HCC", "HOPE", "HZO", "INVA", "JBSS",
]

# The three market-cap tiers the Lab tab's "random sample" filter draws from.
# Large = EQUITY_UNIVERSE (the Dow roster) rather than a separate mega-cap
# list -- reusing it keeps a single definition of "large cap" instead of two
# overlapping ones.
CAP_TIER_POOLS: dict[str, list[str]] = {
    "small": SMALL_CAP_UNIVERSE,
    "mid": MIDCAP_UNIVERSE,
    "large": EQUITY_UNIVERSE,
}

# Sector SPDR ETFs vs. SPY, for Sector Rotation Play.
SECTOR_UNIVERSE: list[str] = [
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLRE", "XLU", "XLC",
]
SECTOR_BENCHMARK = "SPY"

# Liquid, index-like ETFs (the sector SPDRs + SPY) -- the natural instruments
# for the ETF-oriented strategies (Pivot-Level ETF Reversal, Overnight Hold).
ETF_UNIVERSE: list[str] = [*SECTOR_UNIVERSE, SECTOR_BENCHMARK]

# For strategies the user wants tested on BOTH ETFs and single names: the ETF
# set plus the Dow universe. The per-symbol backtest breakdown then separates
# ETF behavior from single-stock behavior within one run (an effect that's
# ETF-specific vs. general shows up as dispersion between the two groups).
ETF_AND_EQUITY_UNIVERSE: list[str] = [*ETF_UNIVERSE, *EQUITY_UNIVERSE]

DAILY_LOOKBACK_YEARS = 5

# Intraday bars now come from Alpaca (engine/data.py routes intraday intervals
# there), whose IEX history goes back years -- so day-trading strategies no
# longer have to be judged on a ~60-day sample the way they were on yfinance.
# 5-minute bars remain the resolution/coverage sweet spot. Two years spans a
# couple of market regimes and gives every strategy a real sample size; bump
# this to go deeper (Alpaca free-tier IEX history reaches ~2016).
#
# History: this constant was 57 to dodge yfinance's hard ~60-day intraday
# cutoff (58 days succeeded, 59 failed outright). That cap is a yfinance
# limitation, not Alpaca's; engine/data.py still clamps to it only when
# falling back to yfinance because no Alpaca keys are configured.
INTRADAY_INTERVAL = "5m"
INTRADAY_LOOKBACK_DAYS = 730


def daily_date_range() -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=365 * DAILY_LOOKBACK_YEARS)
    return start, end


def intraday_date_range() -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=INTRADAY_LOOKBACK_DAYS)
    return start, end
