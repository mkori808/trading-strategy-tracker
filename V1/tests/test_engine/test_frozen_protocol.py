from engine.frozen_protocol import family_record, load_protocol
from engine.research_governance import build_validation_spec
from strategies.swing.frozen_research import VolumeShockContinuation


def test_frozen_v1_protocol_is_registered_before_results():
    protocol = load_protocol()
    assert protocol["selectionRule"].startswith("Canonical V1 remains")
    assert protocol["primaryUniverse"] == "dow_pit"
    assert protocol["families"]["52-Week-High Momentum"]["canonical"]["top_n"] == 5
    assert protocol["families"]["Negative Return + Volume Shock Reversal"]["canonical"]["holding_sessions"] == 3


def test_unavailable_hypotheses_have_explicit_reasons_and_zero_searches():
    for name in (
        "Earnings Announcement Return Drift (EAR)",
        "Sector-Relative Momentum",
        "Overnight Idiosyncratic Shock Reversal",
    ):
        record = family_record(name)
        assert record["status"] == "unavailable"
        assert record["reason"]
        assert record["materialConfigurations"] == 0


def test_search_family_does_not_include_universe_id():
    for record in load_protocol()["families"].values():
        assert "dow" not in record["searchFamily"].lower()


def test_predeclared_event_diagnostic_parameters_match_implementation_names():
    volume = family_record("Volume-Shock Continuation")
    maximum = family_record("MAX Lottery-Return Reversal (Short)")
    assert volume["canonical"]["event_mode"] == "excluded"
    assert volume["neighbors"]["event_mode"] == [
        "excluded", "earnings_only_diagnostic",
    ]
    assert maximum["canonical"]["earningsExclusion"] is True


def test_validation_spec_uses_stable_frozen_family_not_display_name():
    spec = build_validation_spec(
        "Volume-Shock Continuation (Long)", "standard", ["AAA"],
        VolumeShockContinuation,
    )
    assert spec.search_family == "volume-shock-continuation:frozen_event"
