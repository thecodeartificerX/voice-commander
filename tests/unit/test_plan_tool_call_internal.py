from voice_commander.plan import ToolCall


def test_tool_call_internal_defaults_false():
    tc = ToolCall(name="click", kwargs={})
    assert tc.internal is False


def test_tool_call_internal_can_be_true():
    tc = ToolCall(name="wait", kwargs={"ms": 255}, internal=True)
    assert tc.internal is True


def test_tool_call_equality_considers_internal():
    a = ToolCall(name="wait", kwargs={"ms": 255}, internal=False)
    b = ToolCall(name="wait", kwargs={"ms": 255}, internal=True)
    assert a != b
