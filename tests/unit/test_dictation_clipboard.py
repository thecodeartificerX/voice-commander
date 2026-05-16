import pytest

clipboard = pytest.importorskip("voice_commander.dictation.clipboard")


@pytest.fixture
def preserve_clipboard():
    saved = clipboard.read_clipboard_text()
    yield
    if saved is not None:
        clipboard.set_clipboard_text(saved)


def test_set_then_read_roundtrip(preserve_clipboard):
    clipboard.set_clipboard_text("dictation-test-value")
    assert clipboard.read_clipboard_text() == "dictation-test-value"


def test_paste_via_clipboard_restores_original(preserve_clipboard, monkeypatch):
    pastes = []
    monkeypatch.setattr(clipboard, "send_paste", lambda: pastes.append(
        clipboard.read_clipboard_text()))
    clipboard.set_clipboard_text("ORIGINAL")
    clipboard.paste_via_clipboard("NEW TRANSCRIPTION", settle_ms=10)
    # The paste happened while the clipboard held the transcription...
    assert pastes == ["NEW TRANSCRIPTION"]
    # ...and the original is restored afterward.
    assert clipboard.read_clipboard_text() == "ORIGINAL"


def test_paste_via_clipboard_restores_original_on_send_paste_error(
    preserve_clipboard, monkeypatch
):
    def boom():
        raise RuntimeError("paste failed")

    monkeypatch.setattr(clipboard, "send_paste", boom)
    clipboard.set_clipboard_text("ORIGINAL")
    with pytest.raises(RuntimeError, match="paste failed"):
        clipboard.paste_via_clipboard("NEW TRANSCRIPTION", settle_ms=10)
    # Despite the failure, the original clipboard content is restored.
    assert clipboard.read_clipboard_text() == "ORIGINAL"
