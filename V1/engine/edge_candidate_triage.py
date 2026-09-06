"""Cheap, fail-closed triage for edge candidates before formal research.

This module is deliberately a reporting layer over existing evidence. It does
not run a backtest, inspect returns, preregister a hypothesis, consume a
holdout, or write to the research database. Breadth uses ``screen_design``;
existing cost findings are read from the corrected dual-estimator report.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from engine.power_curve import screen_design


ROOT = Path(__file__).resolve().parent.parent
BACKLOG_REPORT = ROOT / "reports" / "hypothesis_backlog_screen" / "report.json"
COST_REPORT = ROOT / "reports" / "hypothesis_cost_screen" / "report.json"
INDEX_DELETION_REPORT = ROOT / "reports" / "index_deletion_screen" / "report.json"
REPORT_DIR = ROOT / "reports" / "edge_candidate_triage"

TARGET_EFFECT_PCT = 4.0

OBSERVABLE = "OBSERVABLE"
PARTIAL = "PARTIAL"
DATA_BLOCKED = "DATA_BLOCKED"

VIABLE = "VIABLE"
MARGINAL = "MARGINAL"
SCREENED_OUT = "SCREENED_OUT"

COST_OK = "COST_OK"
COST_SENSITIVE = "COST_SENSITIVE"
COST_DOMINATES = "COST_DOMINATES"
NOT_MEASURABLE = "NOT_MEASURABLE"

FINAL_STATUSES = {
    "DISCOVERED",
    "DATA_BLOCKED",
    "SCREENED_OUT_BREADTH",
    "SCREENED_OUT_COST",
    "COST_SENSITIVE",
    "MARGINAL",
    "STRONG_CANDIDATE",
    "NEEDS_HUMAN_REVIEW",
    "APPROVED_FOR_PREREGISTRATION",
    "ARCHIVED",
}


@dataclass
class EdgeCandidate:
    id: str
    name: str
    family: str
    origin: str
    mechanism_tier: int
    mechanism: str
    constrained_actor: str | None
    constraint: str | None
    persistence_reason: str | None
    trigger: str
    expected_distortion: str
    required_data: list[str]
    pit_observability: str
    observability_notes: str
    nominal_events_per_year: float | None = None
    effective_bets_per_year: float | None = None
    mda_pct_per_year: float | None = None
    expected_holding_period_years: float | None = None
    spread_cs_bps: float | None = None
    spread_ar_bps: float | None = None
    annual_cost_cs_pct: float | None = None
    annual_cost_ar_pct: float | None = None
    confounders: list[str] = field(default_factory=list)
    data_status: str = "DISCOVERED"
    breadth_status: str = "NOT_RUN"
    cost_status: str = NOT_MEASURABLE
    final_triage_status: str = "DISCOVERED"
    reason: str = ""
    sample_size: int | None = None
    source_urls: list[str] = field(default_factory=list)
    independence_notes: str = ""
    missing_data_scope: str | None = None
    missing_data_effort: str | None = None
    factor_explanation_risk: bool = False

    def validate(self) -> None:
        if not self.id or not self.name or not self.family:
            raise ValueError("candidate id, name, and family are required")
        if self.mechanism_tier not in range(1, 6):
            raise ValueError("mechanism_tier must be between 1 and 5")
        if self.pit_observability not in {OBSERVABLE, PARTIAL, DATA_BLOCKED}:
            raise ValueError(f"invalid observability: {self.pit_observability}")
        if self.final_triage_status not in FINAL_STATUSES:
            raise ValueError(f"invalid final status: {self.final_triage_status}")
        for name in (
            "nominal_events_per_year", "effective_bets_per_year", "mda_pct_per_year",
            "expected_holding_period_years", "spread_cs_bps", "spread_ar_bps",
            "annual_cost_cs_pct", "annual_cost_ar_pct",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.pit_observability != OBSERVABLE and self.final_triage_status not in {
            "DATA_BLOCKED", "DISCOVERED", "ARCHIVED"
        }:
            raise ValueError("PARTIAL/DATA_BLOCKED candidates must fail closed")


def annual_cost_from_holding_period(spread_bps: float, holding_period_years: float) -> float:
    """Annual percent drag per dollar of capital, not event-count times spread."""
    if spread_bps < 0:
        raise ValueError("spread_bps cannot be negative")
    if holding_period_years <= 0:
        raise ValueError("holding_period_years must be positive")
    return spread_bps / holding_period_years / 100.0


def classify_breadth(mda_pct: float | None, target_effect_pct: float = TARGET_EFFECT_PCT) -> str:
    if mda_pct is None:
        return "NOT_RUN"
    if mda_pct <= target_effect_pct:
        return VIABLE
    if mda_pct <= 2 * target_effect_pct:
        return MARGINAL
    return SCREENED_OUT


def classify_cost(
    annual_cost_cs_pct: float | None,
    annual_cost_ar_pct: float | None,
    *,
    effect_ceiling_pct: float,
) -> str:
    """Require estimator agreement; exact zero never means literal free execution."""
    if annual_cost_cs_pct is None or annual_cost_ar_pct is None:
        return NOT_MEASURABLE
    costs = (annual_cost_cs_pct, annual_cost_ar_pct)
    if all(cost >= effect_ceiling_pct for cost in costs):
        return COST_DOMINATES
    if all(0 < cost < effect_ceiling_pct for cost in costs):
        return COST_OK
    # Includes a zero reading, which is below estimator resolution, and any
    # disagreement across the economically relevant threshold.
    return COST_SENSITIVE


def _load_existing_evidence() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    breadth_payload = json.loads(BACKLOG_REPORT.read_text(encoding="utf-8"))
    breadth_rows = breadth_payload["results"] if isinstance(breadth_payload, dict) else breadth_payload
    cost_rows = json.loads(COST_REPORT.read_text(encoding="utf-8"))
    return (
        {row["candidate"]: row for row in breadth_rows},
        {row["candidate"]: row for row in cost_rows},
    )


def _existing_candidates() -> list[EdgeCandidate]:
    breadth, costs = _load_existing_evidence()
    index_deletion = json.loads(INDEX_DELETION_REPORT.read_text(encoding="utf-8"))

    def cost(name: str) -> dict[str, Any]:
        return costs.get(name, {})

    definitions: list[dict[str, Any]] = [
        dict(id="index_deletion", name="Index deletion / forced selling", family="index_constraints",
             mechanism_tier=1, mechanism="Benchmark removals force index trackers to sell.",
             constrained_actor="Index-tracking funds", constraint="Benchmark replication mandate",
             persistence_reason="Trackers are judged on tracking error, not execution alpha.",
             trigger="Published S&P 500 deletion effective date", expected_distortion="Temporary selling pressure after deletion",
             required_data=["PIT membership changes", "announcement/effective dates", "identity-safe prices"],
             pit_observability=OBSERVABLE, observability_notes="Existing S&P 500 action ledger is PIT-enumerable.",
             final_triage_status="SCREENED_OUT_BREADTH", reason="MDA 11.45%/yr exceeds the 4% target."),
        dict(id="index_addition", name="Index addition / forced buying", family="index_constraints",
             mechanism_tier=1, mechanism="Benchmark additions force index trackers to buy.",
             constrained_actor="Index-tracking funds", constraint="Benchmark replication mandate",
             persistence_reason="Tracking-error limits make the trade non-discretionary.",
             trigger="Published S&P 500 addition effective date", expected_distortion="Temporary buying pressure around inclusion",
             required_data=["PIT membership changes", "announcement/effective dates", "identity-safe prices"],
             pit_observability=OBSERVABLE, observability_notes="Existing S&P 500 action ledger is PIT-enumerable.",
             final_triage_status="MARGINAL", reason="Breadth MDA is 5.87%/yr; cost is below MDA but not enough to cure detectability."),
        dict(id="spinoff_dumping", name="Spinoff shares dumped by ineligible holders", family="corporate_events",
             mechanism_tier=1, mechanism="Parent holders receive a security their mandate may not permit.",
             constrained_actor="Mandate-constrained parent-company holders", constraint="Size, sector, or benchmark eligibility",
             persistence_reason="Distribution is mechanical and the child may initially lack a natural holder base.",
             trigger="Dated spinoff action naming the child security", expected_distortion="Forced child-share sales after distribution",
             required_data=["spinoff actions", "parent-child identity", "distribution date", "child prices"],
             pit_observability=OBSERVABLE, observability_notes="Market-wide actions enumerate parent and child, subject to normal identity audit.",
             final_triage_status="MARGINAL", reason="MDA 6.50%/yr is above the target despite acceptable measured costs."),
        dict(id="small_merger_arb", name="Small merger arb below institutional minimum deal size", family="corporate_events",
             mechanism_tier=2, mechanism="Large arbitrage funds cannot deploy meaningful dollars in small deals.",
             constrained_actor="Large event-driven funds", constraint="Fund-size economics and position limits",
             persistence_reason="Capacity remains uneconomic for large funds.", trigger="Verified cash-deal announcement",
             expected_distortion="Excess deal spread", required_data=["announcement dates", "consideration type", "deal size", "outcomes", "prices"],
             pit_observability=OBSERVABLE, observability_notes="A usable cash-deal/date filter was established, but it sharply reduced the population.",
             final_triage_status="SCREENED_OUT_BREADTH", reason="Honest eligibility filtering raised MDA to 4.10%/yr, above the fixed target."),
        dict(id="microcap_momentum", name="Sub-$300M cap cross-sectional anomalies (momentum)", family="microcap_factor",
             mechanism_tier=4, mechanism="Known momentum premium in a capacity-constrained universe.",
             constrained_actor="Large institutions", constraint="Capacity and liquidity",
             persistence_reason="Large funds cannot efficiently hold the smallest names.", trigger="PIT market cap plus lagged momentum rank",
             expected_distortion="Continuation among micro-cap winners", required_data=["PIT market cap", "identity-safe prices", "volume"],
             pit_observability=OBSERVABLE, observability_notes="Sharadar can construct the population fail-closed.",
             final_triage_status="SCREENED_OUT_COST", reason="Both spread estimators imply annual cost above MDA.",
             factor_explanation_risk=True, confounders=["momentum", "liquidity", "size"]),
        dict(id="sub5_mandate", name="Sub-$5 stocks excluded by fund charters", family="mandate_constraints",
             mechanism_tier=2, mechanism="Some fund policies prohibit very low-priced shares.",
             constrained_actor="Policy-constrained funds", constraint="Minimum share-price eligibility",
             persistence_reason="Charter rules change slowly.", trigger="PIT price below $5",
             expected_distortion="Reduced institutional demand", required_data=["identity-safe prices", "historical listings"],
             pit_observability=OBSERVABLE, observability_notes="Price-defined population is constructible.",
             final_triage_status="SCREENED_OUT_COST", reason="Both estimators put annual cost far above MDA.",
             confounders=["distress", "lottery demand", "liquidity"]),
        dict(id="tax_loss_reversal", name="Tax-loss selling reversal (December)", family="tax_constraints",
             mechanism_tier=2, mechanism="Taxable investors realize losses near year-end.", constrained_actor="Taxable investors",
             constraint="Calendar-year tax realization", persistence_reason="Tax calendar recurs annually.",
             trigger="Large YTD loss entering December", expected_distortion="Year-end selling followed by reversal",
             required_data=["PIT prices", "tax-year calendar", "historical membership"], pit_observability=OBSERVABLE,
             observability_notes="Signal is constructible, but positions cluster in the same macro drawdown.",
             final_triage_status="SCREENED_OUT_BREADTH", reason="Effective breadth is annual and highly correlated; MDA is 8.15%/yr.",
             factor_explanation_risk=True, confounders=["short-term reversal", "distress", "market beta"]),
        dict(id="post_reorg", name="Distressed / post-reorg equity", family="distress_constraints",
             mechanism_tier=2, mechanism="Mandates and operational frictions delay ownership of newly issued post-reorg equity.",
             constrained_actor="Traditional institutions", constraint="Distress, ratings, seasoning, and benchmark eligibility",
             persistence_reason="Security eligibility changes more slowly than legal emergence.", trigger="Tradable new equity after emergence",
             expected_distortion="Under-ownership and delayed rerating", required_data=["bankruptcy events", "old/new CIK", "new security identity", "emergence date", "prices"],
             pit_observability=DATA_BLOCKED, observability_notes="CIK verifies a known link but no field discovers successors market-wide.",
             final_triage_status="DATA_BLOCKED", reason="The prior breadth and cost passes cannot be carried forward to a discoverable population.",
             missing_data_scope="Build a market-wide CIK/date-proximity candidate index, then independently validate every proposed old/new security link.",
             missing_data_effort="LARGE"),
        dict(id="odd_lot_tender", name="Odd-lot tender provisions", family="corporate_events",
             mechanism_tier=1, mechanism="Tender terms can privilege sub-100-share holders.", constrained_actor="Issuer/tender agent",
             constraint="Contractual proration exception", persistence_reason="The provision is part of filed tender terms.",
             trigger="Filed odd-lot priority tender", expected_distortion="Small deterministic allocation advantage",
             required_data=["Schedule TO filings", "terms", "dates", "prices"], pit_observability=DATA_BLOCKED,
             observability_notes="No structured historical event population exists in the repository.",
             final_triage_status="DATA_BLOCKED", reason="Population count and PIT terms are not yet measurable.",
             missing_data_scope="A bounded SEC Schedule TO search/parser plus manual term validation on a sample.", missing_data_effort="MEDIUM"),
        dict(id="sp1500_migrations", name="S&P 500+400+600 additions, deletions, and migrations", family="index_constraints",
             mechanism_tier=1, mechanism="Index-family migration forces offsetting benchmark trades in liquid names.",
             constrained_actor="S&P index trackers", constraint="Index-family replication mandates",
             persistence_reason="Effective-date tracking requirements remain mechanical.", trigger="Published family migration effective date",
             expected_distortion="Temporary demand imbalance across index tracker groups",
             required_data=["PIT S&P 500/400/600 changes", "announcement/effective dates", "prices"],
             pit_observability=PARTIAL, observability_notes="S&P 500 side exists; 400/600 historical change ledgers do not.",
             final_triage_status="DATA_BLOCKED", reason="The 500-only proxy is marginal (4.16% MDA); the actual family population cannot be enumerated.",
             missing_data_scope="Acquire or construct PIT S&P 400 and 600 change ledgers; no strategy engine work is needed.", missing_data_effort="MEDIUM"),
    ]

    candidates: list[EdgeCandidate] = []
    for definition in definitions:
        name = definition["name"]
        candidate_id = definition["id"]
        breadth_aliases = {
            "microcap_momentum": "Sub-$300M cap cross-sectional anomalies",
        }
        cost_aliases = {
            "sp1500_migrations": "S&P 500+400+600 additions/deletions/migrations (500-only available; 400/600 DATA_BLOCKED)",
        }
        b = breadth.get(breadth_aliases.get(candidate_id, name), {})
        c = cost(cost_aliases.get(candidate_id, name))
        # The corrected cost report supersedes initial breadth numbers where
        # eligibility scrutiny changed them (notably merger arb).
        mda = c.get("mdaPct", b.get("bestCaseMdaPct"))
        annual = c.get("annualCostByEstimator", {})
        spread = c.get("spreadMeasurement", {})
        nominal_events = b.get("eventsOrBetsPerYear")
        effective_bets = min(
            (d.get("independent_bets_per_year") for d in b.get("designs", []) if d.get("independent_bets_per_year") is not None),
            default=None,
        )
        if candidate_id == "index_deletion":
            full = index_deletion["fullHistory"]
            nominal_events = full["eventsPerYear"]
            effective_bets = min(d["independent_bets_per_year"] for d in full["designs"])
        if candidate_id == "post_reorg":
            # The identity audit explicitly withdrew the provisional count,
            # MDA, and cost: those described raw cancellations, not a
            # discoverable post-reorg population.
            nominal_events = effective_bets = mda = None
            annual = {}
            spread = {}
            c = {}
        candidate = EdgeCandidate(
            origin="existing_backlog",
            nominal_events_per_year=nominal_events,
            effective_bets_per_year=effective_bets,
            mda_pct_per_year=mda,
            expected_holding_period_years=c.get("holdingPeriodYears"),
            spread_cs_bps=spread.get("corwinSchultzMedianBps"),
            spread_ar_bps=spread.get("abdiRanaldoMedianBps"),
            annual_cost_cs_pct=annual.get("corwinSchultz"),
            annual_cost_ar_pct=annual.get("abdiRanaldo"),
            sample_size=spread.get("sampleSize"),
            data_status=definition["pit_observability"],
            breadth_status=classify_breadth(mda),
            cost_status=(
                NOT_MEASURABLE if not annual else classify_cost(
                    annual.get("corwinSchultz"), annual.get("abdiRanaldo"),
                    effect_ceiling_pct=min(TARGET_EFFECT_PCT, mda) if mda else TARGET_EFFECT_PCT,
                )
            ),
            source_urls=["reports/hypothesis_backlog_screen/report.json", "reports/hypothesis_cost_screen/report.json"],
            independence_notes=b.get("independenceNote", ""),
            **definition,
        )
        candidate.validate()
        candidates.append(candidate)
    return candidates


def _new_candidates() -> list[EdgeCandidate]:
    liquid_spread_cs = 22.253372720172464
    liquid_spread_ar = 0.0
    dividend_holding = 20 / 252
    dividend_design = screen_design(
        "ex_dividend_date_clusters", positions=1, rebalances_per_year=248,
        years=10, tradable_alpha_pct=TARGET_EFFECT_PCT, avg_pairwise_corr=0.5,
    )
    equal_weight_design = screen_design(
        "equal_weight_quarterly_batches", positions=1, rebalances_per_year=4,
        years=20, tradable_alpha_pct=TARGET_EFFECT_PCT, avg_pairwise_corr=0.5,
    )
    stress_design = screen_design(
        "fed_stress_test_annual_batch", positions=1, rebalances_per_year=1,
        years=15, tradable_alpha_pct=TARGET_EFFECT_PCT, avg_pairwise_corr=0.5,
    )
    candidates = [
        EdgeCandidate(
            id="ex_dividend_clientele", name="Ex-dividend tax/clientele pressure", family="tax_constraints",
            origin="new_discovery", mechanism_tier=3,
            mechanism="Investors with different tax and income mandates may transact around ex-dates for non-price-maximizing reasons.",
            constrained_actor="Tax-sensitive and income-mandated holders", constraint="Tax treatment and income eligibility",
            persistence_reason="Tax and mandate differences persist, but published evidence disputes how much is tax versus microstructure.",
            trigger="Positive cash-dividend ex-date", expected_distortion="Temporary price pressure around the ex-date",
            required_data=["cash-dividend action", "PIT common-stock identity", "ex-date", "prices", "liquidity"],
            pit_observability=OBSERVABLE,
            observability_notes="2025 Sharadar check: 6,743 eligible domestic-common-stock dividend rows, 1,704 securities, 248 dates. No returns inspected.",
            nominal_events_per_year=6743, effective_bets_per_year=248,
            mda_pct_per_year=dividend_design["mda_pct"], expected_holding_period_years=dividend_holding,
            spread_cs_bps=liquid_spread_cs, spread_ar_bps=liquid_spread_ar,
            annual_cost_cs_pct=annual_cost_from_holding_period(liquid_spread_cs, dividend_holding),
            annual_cost_ar_pct=annual_cost_from_holding_period(liquid_spread_ar, dividend_holding),
            confounders=["dividend yield", "value", "quality", "microstructure/discrete prices"],
            factor_explanation_risk=True, data_status=OBSERVABLE, breadth_status=VIABLE,
            cost_status=COST_SENSITIVE, final_triage_status="COST_SENSITIVE",
            reason="Breadth clears conservatively at one independent date-cluster per trading day, but a 20-day holding assumption gives 2.80% CS cost while AR reads below resolution.",
            sample_size=45,
            source_urls=[
                "https://sharadar.com/docs/actions",
                "https://onlinelibrary.wiley.com/doi/pdf/10.1111/j.1540-6261.1982.tb03598.x",
                "https://doi.org/10.1016/S0304-405X(97)00041-X",
            ],
            independence_notes="6,743 nominal events collapse to at most 248 date clusters; sector/issuer dependence would reduce this further.",
        ),
        EdgeCandidate(
            id="sp500_equal_weight_rebalance", name="S&P 500 equal-weight quarterly reset pressure", family="index_constraints",
            origin="new_discovery", mechanism_tier=1,
            mechanism="Equal-weight trackers must reset every constituent toward 0.2% each quarter.",
            constrained_actor="Equal-weight index funds", constraint="Published benchmark weighting rule",
            persistence_reason="Tracking-error mandate requires the reset even when flows are predictable.",
            trigger="Quarterly equal-weight rebalance", expected_distortion="Common close-auction demand tied to weight drift",
            required_data=["PIT S&P 500 membership", "rebalance calendar", "reference-date prices", "historical fund AUM/shares"],
            pit_observability=OBSERVABLE,
            observability_notes="Trigger and member population are constructible; actual dollars require AUM but are not needed to reject breadth.",
            nominal_events_per_year=2000, effective_bets_per_year=4,
            mda_pct_per_year=equal_weight_design["mda_pct"], data_status=OBSERVABLE,
            breadth_status=SCREENED_OUT, cost_status=NOT_MEASURABLE,
            final_triage_status="SCREENED_OUT_BREADTH",
            reason="Roughly 2,000 security rows are only four common rule/batch events; MDA is far above 4%.",
            source_urls=["https://www.spglobal.com/spdji/en/education/article/sp-500-equal-weight-index-faq/"],
            independence_notes="All constituent trades share four rebalance batches and one underlying rule; nominal rows are not independent bets.",
        ),
        EdgeCandidate(
            id="reg_sho_rule_201", name="Reg SHO Rule 201 short-sale restriction pressure", family="regulatory_constraints",
            origin="new_discovery", mechanism_tier=1,
            mechanism="A 10% decline activates a price test restricting short-sale execution for the rest of the day and next day.",
            constrained_actor="Short sellers and broker-dealers", constraint="SEC Rule 201 execution restriction",
            persistence_reason="The rule is mandatory and trigger-based.", trigger="Intraday 10% decline from prior close",
            expected_distortion="Temporary imbalance in short-sale supply and execution priority",
            required_data=["consolidated intraday trades", "prior close", "official SSR activation timestamp", "quotes/spreads"],
            pit_observability=PARTIAL,
            observability_notes="Daily OHLC can suggest triggers but cannot establish exact trigger time or executable price-test state.",
            nominal_events_per_year=None, effective_bets_per_year=None, data_status=PARTIAL,
            breadth_status="NOT_RUN", cost_status=NOT_MEASURABLE, final_triage_status="DATA_BLOCKED",
            reason="Fail closed until official historical SSR flags or consolidated intraday trigger data are available.",
            missing_data_scope="Audit exchange/FINRA historical SSR lists; NYSE public halt-style history is only one year, so long-history coverage is uncertain.",
            missing_data_effort="SMALL discovery audit; potentially LARGE acquisition",
            source_urls=["https://www.sec.gov/news/speech/2010/spch022410tap-shortsales.htm"],
        ),
        EdgeCandidate(
            id="ipo_lockup_expiry", name="IPO lockup-expiration selling pressure", family="contractual_constraints",
            origin="new_discovery", mechanism_tier=1,
            mechanism="Insiders and pre-IPO holders are contractually barred from selling until a prospectus-specific release date.",
            constrained_actor="Insiders and pre-IPO holders", constraint="Underwriter lockup agreement",
            persistence_reason="Contract terms are binding, though waivers and durations vary.", trigger="Actual lockup expiration/waiver date",
            expected_distortion="Supply increase when locked shares become saleable",
            required_data=["IPO prospectus", "lockup duration", "effective date", "waivers", "shares unlocked", "identity-safe prices"],
            pit_observability=PARTIAL,
            observability_notes="SEC says terms vary and directs users to each prospectus; a 180-day approximation is not identity-safe enough.",
            data_status=PARTIAL, breadth_status="NOT_RUN", cost_status=NOT_MEASURABLE,
            final_triage_status="DATA_BLOCKED", reason="Exact terms and early waivers require filing-level extraction before events can be counted.",
            missing_data_scope="Parse a bounded sample of 424B4/S-1 prospectuses and 8-K waivers before deciding whether market-wide extraction is justified.",
            missing_data_effort="MEDIUM",
            source_urls=["https://www.sec.gov/answers/lockup.htm"],
        ),
        EdgeCandidate(
            id="leveraged_etf_rebalance", name="Leveraged-ETF daily rebalance pressure", family="operational_constraints",
            origin="new_discovery", mechanism_tier=1,
            mechanism="Daily-target leveraged and inverse funds must reset derivative exposure each day.",
            constrained_actor="Leveraged/inverse ETF sponsors", constraint="Prospectus daily-return objective",
            persistence_reason="Daily target and fund flows determine required exposure independent of expected next-day return.",
            trigger="Daily underlying move combined with fund NAV and leverage", expected_distortion="Directional close-period hedge demand",
            required_data=["historical daily ETF NAV/AUM/shares", "leverage objective", "holdings/swaps/futures", "underlying intraday data"],
            pit_observability=PARTIAL,
            observability_notes="The daily mandate is public, but the repository lacks historical AUM/shares and derivative exposure needed to size flow.",
            nominal_events_per_year=252, data_status=PARTIAL, breadth_status="NOT_RUN",
            cost_status=NOT_MEASURABLE, final_triage_status="DATA_BLOCKED",
            reason="Mechanism is hard and frequent, but trigger magnitude is not reconstructible with current holdings data.",
            missing_data_scope="Test whether sponsor NAV/share files provide stable daily history for 3-5 major funds; do not build a broad ETF crawler first.",
            missing_data_effort="SMALL/MEDIUM",
            source_urls=[
                "https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/sec",
                "https://www.proshares.com/resources/geared-faqs",
            ],
        ),
        EdgeCandidate(
            id="lagged_13f_fire_sales", name="Lagged 13F crowded-exit pressure", family="delegated_asset_management",
            origin="new_discovery", mechanism_tier=2,
            mechanism="Fund outflows or mandate changes can force managers holding the same liquid names to sell.",
            constrained_actor="Delegated asset managers", constraint="Redemptions and mandate compliance",
            persistence_reason="Agency and flow constraints recur, but the contemporaneous flow is not public.",
            trigger="Publicly known lagged crowding plus an independently observed fund-flow shock",
            expected_distortion="Temporary common-position fire-sale pressure",
            required_data=["13F acceptance timestamps", "holdings", "manager identity", "fund flows", "security identity"],
            pit_observability=PARTIAL,
            observability_notes="13F holdings become public up to 45 days after quarter end; current repository has no PIT fund-flow source.",
            data_status=PARTIAL, breadth_status="NOT_RUN", cost_status=NOT_MEASURABLE,
            final_triage_status="DATA_BLOCKED", reason="Using quarter-end holdings before filing acceptance would be lookahead; crowding alone does not identify a forced sale.",
            missing_data_scope="A 13F ingestion path is bounded, but a PIT fund-flow source is a separate unresolved dependency.",
            missing_data_effort="LARGE",
            source_urls=["https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f"],
            confounders=["momentum", "size", "liquidity", "common ownership"], factor_explanation_risk=True,
        ),
        EdgeCandidate(
            id="fed_stress_test_distributions", name="Fed stress-test capital-distribution constraint", family="regulatory_constraints",
            origin="new_discovery", mechanism_tier=1,
            mechanism="Stress-capital requirements constrain dividends and repurchases at large banks.",
            constrained_actor="Large bank holding companies", constraint="Regulatory capital requirements",
            persistence_reason="Supervisory capital rules are binding.", trigger="Annual bank-specific stress-test result",
            expected_distortion="Capital-distribution repricing around result disclosure",
            required_data=["official result timestamps", "bank roster", "capital requirements", "prices"],
            pit_observability=OBSERVABLE, observability_notes="Official annual publications identify participants and results.",
            nominal_events_per_year=22, effective_bets_per_year=1,
            mda_pct_per_year=stress_design["mda_pct"], data_status=OBSERVABLE,
            breadth_status=SCREENED_OUT, cost_status=NOT_MEASURABLE,
            final_triage_status="SCREENED_OUT_BREADTH",
            reason="Many bank rows share one annual supervisory event and macro scenario; MDA is roughly 40%/yr.",
            source_urls=["https://www.federalreserve.gov/publications/dodd-frank-act-stress-test-publications.htm"],
            independence_notes="Treat the annual release as one macro/regulatory batch, not 22 independent bank events.",
        ),
    ]
    for candidate in candidates:
        candidate.validate()
    return candidates


def rank_candidates(candidates: list[EdgeCandidate]) -> list[EdgeCandidate]:
    status_rank = {
        "STRONG_CANDIDATE": 0, "NEEDS_HUMAN_REVIEW": 1, "COST_SENSITIVE": 2,
        "MARGINAL": 3, "DATA_BLOCKED": 4, "SCREENED_OUT_BREADTH": 5,
        "SCREENED_OUT_COST": 6, "DISCOVERED": 7, "ARCHIVED": 8,
        "APPROVED_FOR_PREREGISTRATION": 9,
    }
    observability_rank = {OBSERVABLE: 0, PARTIAL: 1, DATA_BLOCKED: 2}
    return sorted(
        candidates,
        key=lambda c: (
            status_rank[c.final_triage_status], observability_rank[c.pit_observability],
            c.mda_pct_per_year if c.mda_pct_per_year is not None else float("inf"),
            c.mechanism_tier, len(c.confounders), c.name,
        ),
    )


def build_report() -> dict[str, Any]:
    candidates = rank_candidates([*_existing_candidates(), *_new_candidates()])
    strong = [c.id for c in candidates if c.final_triage_status == "STRONG_CANDIDATE"]
    next_questions = [
        "Do not spend infrastructure capacity on ex-dividend execution modeling unless consolidated NBBO/TAQ becomes available; the one-venue IEX sanity check failed.",
        "The bounded PIT S&P 400/600 source audit is insufficient on breadth; revisit only with an identity-safe, exhaustive family ledger covering at least 10.3 years.",
        "ProShares exposes historical NAV/shares/AUM, but leveraged-ETF reset-flow magnitude remains blocked until derivative exposure and an independent sponsor cross-check are established.",
    ]
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "evidenceOnly": True,
        "backtestsRun": False,
        "holdoutConsumed": False,
        "preregistrationsCreated": False,
        "targetEffectPctPerYear": TARGET_EFFECT_PCT,
        "standingRules": {
            "gateOrder": ["OBSERVABILITY", "BREADTH", "COST", "MECHANISM", "HUMAN_REVIEW"],
            "breadth": "universe size x observation frequency x independence; event rows are not presumed independent",
            "cost": "dual estimator plus holding-period capital turnover; exact zero means below estimator resolution",
            "identity": "fail closed on ticker reuse, succession, delisting, outcome conditioning, and unknown known-at timestamps",
            "humanGate": "automatic pipeline stops at STRONG_CANDIDATE",
        },
        "strongCandidates": strong,
        "formalResearchRecommendation": (
            "No candidate is approved for preregistration. One observable candidate is COST_SENSITIVE; the other leading mechanisms need bounded data audits."
            if not strong else "Human review is required before any preregistration."
        ),
        "nextResearchQuestions": next_questions,
        "candidates": [asdict(candidate) for candidate in candidates],
    }


def _fmt(value: float | None, decimals: int = 2) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"


def _cost(candidate: dict[str, Any]) -> str:
    cs, ar = candidate["annual_cost_cs_pct"], candidate["annual_cost_ar_pct"]
    if cs is None or ar is None:
        return "—"
    ar_text = "< estimator resolution" if ar == 0 else f"{ar:.2f}%"
    return f"{cs:.2f}% CS / {ar_text} AR"


def markdown(report: dict[str, Any]) -> str:
    rows = report["candidates"]
    lines = [
        "# Edge candidate discovery and pre-research triage",
        "",
        f"Generated: `{report['generatedAt']}`",
        "",
        "> Evidence-only triage. No strategy return was tested, no protected holdout was read, and no hypothesis was preregistered.",
        "",
        "## Outcome",
        "",
        report["formalResearchRecommendation"],
        "",
        "The pipeline reused `screen_design`, the existing backlog breadth report, the corrected dual-estimator/holding-period cost report, and the identity feasibility work. It did not create a second backtester, database, or dashboard.",
        "",
        "## Existing backlog triage",
        "",
        "| Candidate | Observability | Effective breadth/yr | MDA %/yr | Annual cost | Tier | Status | Reason |",
        "|---|---|---:|---:|---|---:|---|---|",
    ]
    for row in (r for r in rows if r["origin"] == "existing_backlog"):
        lines.append(
            f"| {row['name']} | {row['pit_observability']} | {_fmt(row['effective_bets_per_year'])} | "
            f"{_fmt(row['mda_pct_per_year'])} | {_cost(row)} | {row['mechanism_tier']} | "
            f"**{row['final_triage_status']}** | {row['reason']} |"
        )
    lines.extend([
        "", "## Newly discovered candidates", "",
        "| Candidate | Observability | Nominal events/yr | Effective bets/yr | MDA %/yr | Annual cost | Tier | Main confounder | Verdict |",
        "|---|---|---:|---:|---:|---|---:|---|---|",
    ])
    for row in (r for r in rows if r["origin"] == "new_discovery"):
        confounder = ", ".join(row["confounders"]) or "none obvious at triage"
        lines.append(
            f"| {row['name']} | {row['pit_observability']} | {_fmt(row['nominal_events_per_year'])} | "
            f"{_fmt(row['effective_bets_per_year'])} | {_fmt(row['mda_pct_per_year'])} | {_cost(row)} | "
            f"{row['mechanism_tier']} | {confounder} | **{row['final_triage_status']}** |"
        )
    lines.extend([
        "", "## Ranked shortlist for review", "",
        "1. **Ex-dividend tax/clientele pressure — COST_SENSITIVE.** Observable and broad even after collapsing 6,743 nominal 2025 events to 248 date clusters; 10-year MDA is 3.12%. It does not advance because the 20-session cost estimate is 2.80% under Corwin-Schultz while Abdi-Ranaldo is below resolution, and factor/microstructure explanations are material.",
        "2. **S&P 500+400+600 migrations — DATA_BLOCKED BUT POTENTIALLY WORTH UNLOCKING.** The liquid 500-only proxy is marginal at 4.16% MDA and low measured cost. The only next action justified is a bounded source audit for 400/600 PIT changes.",
        "3. **Leveraged-ETF daily rebalance pressure — DATA_BLOCKED BUT POTENTIALLY WORTH UNLOCKING.** Tier-1 daily mandate in liquid underlyings, but historical NAV/shares and derivative exposure are missing. Check a few sponsor files before building anything.",
        "",
        "The ex-dividend follow-up used a five-session mechanism horizon: Corwin-Schultz implies 11.22% annualized drag, Abdi-Ranaldo is below resolution, and one-venue IEX quotes failed the mega-cap sanity gate. The S&P family source audit also found current free 400/600 histories too short for the 4% MDA target. ProShares historical NAV/shares/AUM are available, but leveraged-ETF derivative exposure remains unverified.",
        "",
        "**No candidate reached `STRONG_CANDIDATE`; none should be preregistered yet.**",
        "",
        "## Dead versus potentially unlockable", "",
        "### Dead / screened out", "",
    ])
    dead = [r for r in rows if r["final_triage_status"].startswith("SCREENED_OUT")]
    lines.extend(f"- **{r['name']}** — {r['reason']}" for r in dead)
    lines.extend(["", "### Data-blocked but potentially worth a bounded unlock check", ""])
    worth = {"sp1500_migrations", "leveraged_etf_rebalance", "reg_sho_rule_201", "ipo_lockup_expiry"}
    lines.extend(
        f"- **{r['name']}** ({r.get('missing_data_effort')}) — {r.get('missing_data_scope')}"
        for r in rows if r["id"] in worth
    )
    lines.extend(["", "### Data-blocked with large or undefined scope", ""])
    lines.extend(
        f"- **{r['name']}** ({r.get('missing_data_effort') or 'UNKNOWN'}) — {r.get('missing_data_scope') or r['reason']}"
        for r in rows if r["final_triage_status"] == "DATA_BLOCKED" and r["id"] not in worth
    )
    lines.extend(["", "## Source-supported mechanism notes", ""])
    for row in (r for r in rows if r["origin"] == "new_discovery"):
        links = ", ".join(f"[source {i + 1}]({url})" for i, url in enumerate(row["source_urls"]))
        factor = " `FACTOR_EXPLANATION_RISK`." if row["factor_explanation_risk"] else ""
        lines.extend([
            f"### {row['name']}", "",
            f"- Mechanism: {row['mechanism']}",
            f"- Affected participant: {row['constrained_actor']}",
            f"- Constraint: {row['constraint']}",
            f"- Expected action/distortion: {row['expected_distortion']}",
            f"- Persistence prior: {row['persistence_reason']}",
            f"- Required data: {', '.join(row['required_data'])}",
            f"- Sources: {links}.{factor}", "",
        ])
    lines.extend([
        "## Next research questions", "",
        *[f"{i + 1}. {question}" for i, question in enumerate(report["nextResearchQuestions"])],
        "",
        "Stop after those cheap checks. Do not choose a holdout, inspect candidate returns, tune rules, or preregister until a candidate reaches `STRONG_CANDIDATE` and receives explicit human approval.",
        "",
    ])
    return "\n".join(lines)


def write_report(report: dict[str, Any], report_dir: Path = REPORT_DIR) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "report.json"
    markdown_path = report_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown(report), encoding="utf-8")
    return markdown_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    report = build_report()
    paths = write_report(report)
    print(json.dumps({"reports": [str(path) for path in paths], "strongCandidates": report["strongCandidates"]}, indent=2))


if __name__ == "__main__":
    main()
