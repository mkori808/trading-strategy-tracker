import pytest

from engine.sharadar_pit_policy import SupplementalEvidence, delisting_return, policy_status


def test_delisting_return_requires_sourced_terminal_value() -> None:
    assert delisting_return(10.0, SupplementalEvidence(known_at="2020-01-01")) is None
    evidence = SupplementalEvidence(
        known_at="2020-01-01", terminal_value_per_share=2.0, terminal_value_source="court distribution"
    )
    assert delisting_return(10.0, evidence) == pytest.approx(-0.8)
    assert policy_status(evidence)["publishable"] is True


def test_action_date_is_not_silently_used_as_known_at() -> None:
    status = policy_status(SupplementalEvidence())
    assert status["publishable"] is False
    assert status["missing"] == ["known_at", "DelistingReturn"]


def test_negative_terminal_value_is_rejected() -> None:
    with pytest.raises(ValueError):
        delisting_return(10.0, SupplementalEvidence(terminal_value_per_share=-1.0, terminal_value_source="source"))

