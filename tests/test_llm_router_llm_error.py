"""Test LLMPlanError is defined and classifies as 'llm'."""


def test_llm_plan_error_defined():
    from voice_commander.llm_router import LLMPlanError
    exc = LLMPlanError("bad JSON")
    assert str(exc) == "bad JSON"


def test_llm_plan_error_classifies_as_llm():
    from voice_commander.llm_router import LLMPlanError
    from voice_commander.observability.errors import classify
    exc = LLMPlanError("malformed plan")
    assert classify(exc, where="llm_router") == "llm"
