"""Tests that _TIMEOUT_S is <= 30 s and that the default timeout is forwarded to httpx.

These tests enforce the executor-stall prevention requirement from ADR 0090:
a 300 s hung POST blocks _dictation_executor for all subsequent dictation
operations; reducing to 30 s bounds the stall to an acceptable window.

NOTE: The test that httpx.TimeoutException surfaces as DictationRemoteError
lives in tests/unit/test_dictation_remote.py (test_timeout_exception_wraps_as_remote_error),
because that wrapping behaviour belongs to post_audio itself, not to _TIMEOUT_S.
"""
from __future__ import annotations

import voice_commander.dictation.remote as _remote
from voice_commander.dictation.remote import post_audio

import pytest


def test_timeout_constant_is_at_most_30_seconds() -> None:
    """_TIMEOUT_S must be <= 30.0 to prevent executor stall (ADR 0090)."""
    assert _remote._TIMEOUT_S <= 30.0, (
        f"_TIMEOUT_S={_remote._TIMEOUT_S} exceeds 30 s — a hung POST would "
        "block _dictation_executor indefinitely (ADR 0090 §4)"
    )


def test_post_audio_default_timeout_is_passed_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """post_audio must forward _TIMEOUT_S as the httpx timeout kwarg."""
    captured: dict[str, object] = {}

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self) -> dict:
            return {"text": "hello"}

    def _fake_post(url: str, **kwargs: object) -> _FakeResp:
        captured["timeout"] = kwargs.get("timeout")
        return _FakeResp()

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", _fake_post)
    post_audio(b"RIFFfake", "http://x/inference")
    assert captured["timeout"] == _remote._TIMEOUT_S
    assert captured["timeout"] <= 30.0
