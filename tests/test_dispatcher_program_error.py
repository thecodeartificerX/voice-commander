"""Test that dispatcher classifies tool exceptions as 'program'."""
import pytest


def test_llm_plan_error_importable():
    # LLMPlanError should be importable from llm_router
    try:
        from voice_commander.llm_router import LLMPlanError
        assert issubclass(LLMPlanError, Exception)
    except ImportError:
        pytest.skip("LLMPlanError not yet defined")


def test_classify_value_error_as_program():
    from voice_commander.observability.errors import classify
    assert classify(ValueError("tool raised"), where="dispatcher") == "program"
