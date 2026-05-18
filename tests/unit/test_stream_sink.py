"""Unit tests for the streaming dictation output sink."""

from __future__ import annotations

import voice_commander.dictation_stream.sink as sink_mod
from voice_commander.dictation_stream.sink import TextSink, transform


def test_transform_is_identity_for_now():
    assert transform("hello world") == "hello world"


def test_accumulate_builds_transcript():
    sink = TextSink()
    sink.accumulate(["the", "quick"])
    sink.accumulate(["brown", "fox"])
    assert sink.text == "the quick brown fox"


def test_flush_transforms_and_pastes(monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)
    sink = TextSink()
    sink.accumulate(["hello", "world"])
    result = sink.flush()
    assert result == "hello world"
    assert pasted == ["hello world"]


def test_flush_with_no_words_pastes_nothing(monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)
    sink = TextSink()
    assert sink.flush() == ""
    assert pasted == []


def test_flush_runs_text_through_transform(monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)
    monkeypatch.setattr(sink_mod, "transform", lambda text: text.upper())
    sink = TextSink()
    sink.accumulate(["quiet"])
    assert sink.flush() == "QUIET"
    assert pasted == ["QUIET"]
