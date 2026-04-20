import pytest
from pathlib import Path
from voice_commander.feedback import WindowsFeedbackSink


@pytest.mark.hardware
def test_toast_does_not_raise(tmp_path):
    sink = WindowsFeedbackSink(sounds_dir=Path("assets/sounds"))
    sink.on_match("copy", "copy", 95.0)  # should dispatch a toast
    sink.on_miss("xyzzy", (("paste", "paste", 40.0),))
