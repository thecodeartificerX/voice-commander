import pytest

from voice_commander.dictation.remote import DictationRemoteError, post_audio


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def test_post_audio_success(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["files"] = kwargs["files"]
        captured["data"] = kwargs["data"]
        return _FakeResponse(200, {"text": "  hello world  "})

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    result = post_audio(b"RIFFfake", "http://x/inference")
    assert result == "hello world"
    assert captured["url"] == "http://x/inference"
    assert captured["files"]["file"][0] == "audio.wav"
    assert captured["files"]["file"][2] == "audio/wav"
    assert captured["data"]["response_format"] == "json"
    assert captured["data"]["temperature"] == "0.0"


def test_post_audio_collapses_segment_newlines(monkeypatch):
    """whisper.cpp inserts a newline at every segment boundary. post_audio
    must collapse all whitespace runs so a multi-segment clip pastes as one
    continuous block with no spurious mid-sentence line breaks."""
    monkeypatch.setattr(
        "voice_commander.dictation.remote.httpx.post",
        lambda url, **kw: _FakeResponse(
            200, {"text": "\nThis is a dictation test\n of the fox\n"}
        ),
    )
    result = post_audio(b"RIFFfake", "http://x/inference")
    assert result == "This is a dictation test of the fox"


def test_post_audio_non_200_raises(monkeypatch):
    monkeypatch.setattr(
        "voice_commander.dictation.remote.httpx.post",
        lambda url, **kw: _FakeResponse(500, text="boom"),
    )
    with pytest.raises(DictationRemoteError, match="HTTP 500"):
        post_audio(b"x", "http://x/inference")


def test_post_audio_network_error_raises(monkeypatch):
    import httpx

    def fake_post(url, **kw):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    with pytest.raises(DictationRemoteError, match="unreachable"):
        post_audio(b"x", "http://x/inference")


def test_post_audio_missing_text_key_raises(monkeypatch):
    monkeypatch.setattr(
        "voice_commander.dictation.remote.httpx.post",
        lambda url, **kw: _FakeResponse(200, {"no_text": 1}),
    )
    with pytest.raises(DictationRemoteError, match="text"):
        post_audio(b"x", "http://x/inference")


def test_post_audio_non_httperror_exception_is_wrapped(monkeypatch):
    """A non-HTTPError exception from httpx.post must still surface as DictationRemoteError."""
    import httpx

    def fake_post(url, **kw):
        raise httpx.InvalidURL("scheme missing")

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    with pytest.raises(DictationRemoteError, match="request failed"):
        post_audio(b"x", "bad-endpoint-no-scheme")


def test_post_audio_sends_prompt_and_carry_flag_when_prompt_nonempty(monkeypatch):
    """When prompt is non-empty, post_audio must include 'prompt' and
    'carry_initial_prompt' form fields in the multipart POST."""
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["data"] = kwargs["data"]
        return _FakeResponse(200, {"text": "hello"})

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    result = post_audio(b"RIFFfake", "http://x/inference", prompt="Supabase, n8n")
    assert result == "hello"
    assert captured["data"]["prompt"] == "Supabase, n8n"
    assert captured["data"]["carry_initial_prompt"] == "true"


def test_post_audio_omits_prompt_fields_when_prompt_empty(monkeypatch):
    """When prompt is empty (default), neither 'prompt' nor 'carry_initial_prompt'
    must appear in the POST data."""
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["data"] = kwargs["data"]
        return _FakeResponse(200, {"text": "hello"})

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    post_audio(b"RIFFfake", "http://x/inference")
    assert "prompt" not in captured["data"]
    assert "carry_initial_prompt" not in captured["data"]


def test_post_audio_sends_prompt_verbatim_including_unicode_and_whitespace(monkeypatch):
    """post_audio must forward the prompt string byte-for-byte: no trimming,
    no whitespace collapsing, and non-ASCII characters preserved intact."""
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["data"] = kwargs["data"]
        return _FakeResponse(200, {"text": "hello"})

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    prompt = "  hello  Ångström, café  "
    post_audio(b"RIFFfake", "http://x/inference", prompt=prompt)
    assert captured["data"]["prompt"] == "  hello  Ångström, café  "
    assert captured["data"]["carry_initial_prompt"] == "true"


def test_post_audio_whitespace_only_prompt_is_sent_as_is(monkeypatch):
    """A whitespace-only prompt is truthy, so the `if prompt:` guard passes it
    through unmodified rather than dropping the prompt fields."""
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["data"] = kwargs["data"]
        return _FakeResponse(200, {"text": "hello"})

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    post_audio(b"RIFFfake", "http://x/inference", prompt="   ")
    # Documents current behavior: build_prompt never returns whitespace-only, so the if-prompt guard need not strip.
    assert captured["data"]["prompt"] == "   "
    assert captured["data"]["carry_initial_prompt"] == "true"


def test_timeout_exception_wraps_as_remote_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx.TimeoutException raised by httpx.post must be re-raised as
    DictationRemoteError (ADR 0090 §4).

    post_audio already has `except Exception as e: raise DictationRemoteError(...) from e`
    which catches ALL exceptions — including httpx.TimeoutException. This test verifies
    that the existing except-Exception clause covers the timeout case correctly, so that
    _finalize_dictation's `except remote.DictationRemoteError` handler catches it and
    publishes dictation.error + miss chime (keeping the executor worker unblocked).
    """
    import httpx
    from voice_commander.dictation.remote import DictationRemoteError, post_audio

    def _fake_post(url: str, **kwargs: object) -> object:
        raise httpx.TimeoutException("read timeout after 30 s")

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", _fake_post)
    with pytest.raises(DictationRemoteError, match="read timeout after 30 s"):
        post_audio(b"RIFFfake", "http://x/inference")
