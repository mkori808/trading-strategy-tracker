from __future__ import annotations

import pytest

from engine.event_timing import (
    ExecutionTiming,
    InformationAvailability,
    TimingContract,
    timing_contract_for,
    validate_timing_contract,
)
from strategies.base import Strategy
from strategies.cross_sectional import CrossSectionalStrategy
from strategies.swing.overnight_hold import OvernightHold


def test_current_close_signal_cannot_fill_at_same_close():
    contract = TimingContract(
        InformationAvailability.AT_CLOSE,
        ExecutionTiming.SAME_CLOSE,
        True,
        "invalid-test-engine",
    )
    with pytest.raises(ValueError, match="current completed close"):
        validate_timing_contract(contract)


def test_standard_strategy_contract_queues_to_next_open():
    contract = timing_contract_for(Strategy)
    assert contract.information_availability == InformationAvailability.AT_CLOSE
    assert contract.execution == ExecutionTiming.NEXT_OPEN
    validate_timing_contract(contract, actual_execution=ExecutionTiming.NEXT_OPEN)


def test_cross_sectional_contract_queues_to_next_open():
    contract = timing_contract_for(CrossSectionalStrategy)
    assert contract.execution == ExecutionTiming.NEXT_OPEN
    validate_timing_contract(contract, actual_execution=ExecutionTiming.NEXT_OPEN)


def test_overnight_contract_uses_prior_information_for_close_fill():
    contract = timing_contract_for(OvernightHold)
    assert contract.information_availability == InformationAvailability.PRE_MARKET
    assert contract.execution == ExecutionTiming.SAME_CLOSE
    assert contract.uses_current_close is False
    assert contract.exception_reason
    validate_timing_contract(contract, actual_execution=ExecutionTiming.SAME_CLOSE)


def test_engine_execution_must_match_declared_contract():
    with pytest.raises(ValueError, match="engine executes"):
        validate_timing_contract(
            timing_contract_for(OvernightHold),
            actual_execution=ExecutionTiming.NEXT_OPEN,
        )
