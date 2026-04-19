from voice_commander.feedback import NullFeedbackSink, CapturingFeedbackSink


def test_null_sink_all_methods_noop():
    sink = NullFeedbackSink()
    sink.on_recording_start()
    sink.on_recording_stop()
    sink.on_transcript("hello", 0.9)
    sink.on_match("copy", "copy", 100.0)
    sink.on_miss("huh", ())
    sink.on_error("x", RuntimeError("y"))


def test_capturing_sink_records_calls():
    sink = CapturingFeedbackSink()
    sink.on_recording_start()
    sink.on_match("copy", "copy", 95.0)
    sink.on_miss("nope", (("paste", "paste", 40.0),))
    assert [c[0] for c in sink.calls] == ["on_recording_start", "on_match", "on_miss"]
    assert sink.calls[1][1] == ("copy", "copy", 95.0)
