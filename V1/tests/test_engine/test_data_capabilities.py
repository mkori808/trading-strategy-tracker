from engine.data_capabilities import (
    CapabilityStatus,
    DataCapability,
    DataRequirement,
    Hypothesis,
    LimitationPolicy,
    Readiness,
    backlog_hypotheses,
    default_registry,
    evaluate_data_readiness,
)


def test_supported_capability_allows_hypothesis() -> None:
    hypothesis = Hypothesis("x", "x", (DataRequirement("daily_prices", {"SUPPORTED"}),))
    assert evaluate_data_readiness(hypothesis).readiness == Readiness.DATA_READY


def test_unsupported_critical_capability_blocks() -> None:
    hypothesis = next(h for h in backlog_hypotheses() if h.hypothesis_id == "distressed_post_reorg")
    result = evaluate_data_readiness(hypothesis)
    assert result.readiness == Readiness.DATA_BLOCKED
    assert any("successor_security_linkage" in blocker for blocker in result.blockers)


def test_unsupported_noncritical_capability_does_not_block() -> None:
    hypothesis = Hypothesis("x", "x", (DataRequirement("successor_security_linkage", {"SUPPORTED"}, critical=False),))
    result = evaluate_data_readiness(hypothesis)
    assert result.readiness == Readiness.DATA_READY_WITH_LIMITATIONS
    assert result.blockers == ()


def test_adjusted_price_carveout_is_preserved() -> None:
    capability = default_registry()["adjusted_prices"]
    assert capability.status == CapabilityStatus.SUPPORTED_WITH_LIMITATIONS
    assert capability.limitation_policy == LimitationPolicy.EXCLUDE_AMBIGUOUS_CASES
    assert "reconstruct" in " ".join(capability.safe_use_cases).lower()


def test_materiality_threshold_blocks_only_above_threshold() -> None:
    for security_pct, expected in ((5.0, Readiness.DATA_READY_WITH_LIMITATIONS), (5.01, Readiness.DATA_MATERIAL_LIMITATION)):
        capability = DataCapability("partial", "partial", CapabilityStatus.PARTIAL, "test", [], "scope", [], [], [], [], None)
        hypothesis = Hypothesis("x", "x", (DataRequirement("partial", {"SUPPORTED"}, True, affected_security_period_pct=security_pct),))
        result = evaluate_data_readiness(hypothesis, {"partial": capability})
        assert result.readiness == expected


def test_s_and_p_family_examples_match_required_policy() -> None:
    registry = default_registry()
    deletion = next(h for h in backlog_hypotheses() if h.hypothesis_id == "sp500_index_deletion")
    distressed = next(h for h in backlog_hypotheses() if h.hypothesis_id == "distressed_post_reorg")
    assert evaluate_data_readiness(deletion, registry).readiness == Readiness.DATA_READY
    assert evaluate_data_readiness(distressed, registry).readiness == Readiness.DATA_BLOCKED
