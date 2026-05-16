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
    assert captured["data"]["response_format"] == "verbose_json"
    assert captured["data"]["temperature"] == "0.0"


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
