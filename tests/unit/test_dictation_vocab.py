"""Unit tests for voice_commander.dictation.vocab.VocabStore."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from voice_commander.dictation.vocab import (
    Command,
    Correction,
    Vocabulary,
    VocabStore,
)


def test_load_missing_file_returns_empty_vocabulary(tmp_path: Path):
    store = VocabStore(tmp_path / "vocab.json")
    vocab = store.load()
    assert vocab == Vocabulary()
    assert vocab.vocab == ()
    assert vocab.corrections == ()
    assert vocab.commands == ()


def test_load_empty_file_returns_empty_vocabulary(tmp_path: Path):
    path = tmp_path / "vocab.json"
    path.write_text("", encoding="utf-8")
    store = VocabStore(path)
    vocab = store.load()
    assert vocab == Vocabulary()


def test_load_corrupt_file_returns_empty_vocabulary_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    path = tmp_path / "vocab.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = VocabStore(path)
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.vocab"):
        vocab = store.load()
    assert vocab == Vocabulary()
    assert any("WARNING" in r.levelname or r.levelno >= logging.WARNING for r in caplog.records)


def test_load_valid_file_returns_populated_vocabulary(tmp_path: Path):
    path = tmp_path / "vocab.json"
    path.write_text(
        json.dumps(
            {
                "vocab": ["n8n", "Supabase", "CTranslate2"],
                "corrections": [
                    {"wrong": "supa base", "right": "Supabase"},
                    {"wrong": "n-8-n", "right": "n8n"},
                ],
                "commands": [
                    {"phrase": "next line", "action": "newline"},
                    {"phrase": "new paragraph", "action": "paragraph"},
                ],
            }
        ),
        encoding="utf-8",
    )
    store = VocabStore(path)
    vocab = store.load()

    assert vocab.vocab == ("n8n", "Supabase", "CTranslate2")
    assert vocab.corrections == (
        Correction(wrong="supa base", right="Supabase"),
        Correction(wrong="n-8-n", right="n8n"),
    )
    assert vocab.commands == (
        Command(phrase="next line", action="newline"),
        Command(phrase="new paragraph", action="paragraph"),
    )


def test_load_unknown_action_dropped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    path = tmp_path / "vocab.json"
    path.write_text(
        json.dumps(
            {
                "vocab": [],
                "corrections": [],
                "commands": [
                    {"phrase": "do thing", "action": "unknown_action"},
                    {"phrase": "next line", "action": "newline"},
                ],
            }
        ),
        encoding="utf-8",
    )
    store = VocabStore(path)
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.vocab"):
        vocab = store.load()

    assert len(vocab.commands) == 1
    assert vocab.commands[0] == Command(phrase="next line", action="newline")
    assert any("unknown_action" in r.message for r in caplog.records)


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "vocab.json"
    store = VocabStore(path)

    original = Vocabulary(
        vocab=("Artificer", "CTranslate2"),
        corrections=(Correction(wrong="art if icer", right="Artificer"),),
        commands=(Command(phrase="next line", action="newline"),),
    )
    store.save(original)
    loaded = store.load()

    assert loaded == original


def test_save_creates_parent_directories(tmp_path: Path):
    path = tmp_path / "nested" / "deep" / "vocab.json"
    store = VocabStore(path)
    store.save(Vocabulary(vocab=("test",)))
    assert path.exists()


def test_save_overwrites_previous(tmp_path: Path):
    path = tmp_path / "vocab.json"
    store = VocabStore(path)
    store.save(Vocabulary(vocab=("first",)))
    store.save(Vocabulary(vocab=("second",)))
    loaded = store.load()
    assert loaded.vocab == ("second",)


def test_vocabulary_is_frozen():
    vocab = Vocabulary(vocab=("test",))
    with pytest.raises((AttributeError, TypeError)):
        vocab.vocab = ("other",)  # type: ignore[misc]


def test_correction_is_frozen():
    c = Correction(wrong="a", right="b")
    with pytest.raises((AttributeError, TypeError)):
        c.wrong = "x"  # type: ignore[misc]


def test_command_is_frozen():
    cmd = Command(phrase="next line", action="newline")
    with pytest.raises((AttributeError, TypeError)):
        cmd.phrase = "other"  # type: ignore[misc]
