from voice_commander.dispatcher import Dispatcher
from voice_commander.matcher import MatchResult
from voice_commander.registry import ToolEntry
from voice_commander.feedback import CapturingFeedbackSink


def test_dispatch_match_invokes_tool():
    fired: list[int] = []
    entry = ToolEntry("copy", ("copy",), lambda: fired.append(1), "m", None)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    match = MatchResult(tool=entry, phrase="copy", score=95.0, candidates=(("copy","copy",95.0),))
    d.dispatch("copy please", match)
    assert fired == [1]
    assert any(c[0] == "on_match" for c in sink.calls)


def test_dispatch_miss_reports_miss():
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    match = MatchResult(tool=None, phrase=None, score=40.0, candidates=(("paste","paste",40.0),))
    d.dispatch("random", match)
    assert any(c[0] == "on_miss" for c in sink.calls)


def test_dispatch_tool_error_reports_error_and_does_not_raise():
    def boom(): raise RuntimeError("no")
    entry = ToolEntry("copy", ("copy",), boom, "m", None)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    match = MatchResult(tool=entry, phrase="copy", score=95.0, candidates=(("copy","copy",95.0),))
    d.dispatch("copy", match)  # must not raise
    assert any(c[0] == "on_error" for c in sink.calls)
