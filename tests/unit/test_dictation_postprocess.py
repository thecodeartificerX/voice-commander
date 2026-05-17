"""Unit tests for voice_commander.dictation.postprocess."""
from __future__ import annotations

import pytest

from voice_commander.dictation.postprocess import (
    _PROMPT_CHAR_CAP,
    apply_commands,
    apply_corrections,
    build_prompt,
)
from voice_commander.dictation.vocab import Command, Correction, Vocabulary


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


def test_build_prompt_comma_joins_words():
    vocab = Vocabulary(vocab=("Supabase", "n8n", "CTranslate2"))
    result = build_prompt(vocab)
    assert result == "Supabase, n8n, CTranslate2"


def test_build_prompt_empty_vocab_returns_empty_string():
    vocab = Vocabulary()
    assert build_prompt(vocab) == ""


def test_build_prompt_truncates_whole_trailing_words():
    long_words = tuple(f"Word{'X' * 46}_{i:03d}" for i in range(20))
    vocab = Vocabulary(vocab=long_words)
    result = build_prompt(vocab)
    assert len(result) <= _PROMPT_CHAR_CAP
    if result:
        for token in result.split(", "):
            assert token in long_words


def test_build_prompt_never_splits_a_word():
    giant_word = "A" * (_PROMPT_CHAR_CAP + 10)
    vocab = Vocabulary(vocab=(giant_word,))
    result = build_prompt(vocab)
    assert result == ""


def test_build_prompt_respects_cap_constant():
    assert isinstance(_PROMPT_CHAR_CAP, int)
    assert _PROMPT_CHAR_CAP == 800


def test_build_prompt_single_word_under_cap():
    vocab = Vocabulary(vocab=("Artificer",))
    assert build_prompt(vocab) == "Artificer"


# ---------------------------------------------------------------------------
# apply_corrections
# ---------------------------------------------------------------------------


def test_apply_corrections_case_insensitive():
    corrections = (Correction(wrong="supa base", right="Supabase"),)
    assert apply_corrections("I use Supa Base daily", corrections) == "I use Supabase daily"


def test_apply_corrections_word_boundary_no_mid_word_hit():
    corrections = (Correction(wrong="base", right="BASE"),)
    result = apply_corrections("the database and base", corrections)
    assert "database" in result
    assert result == "the database and BASE"


def test_apply_corrections_multiword_wrong():
    corrections = (Correction(wrong="n-8-n", right="n8n"),)
    assert apply_corrections("I love n-8-n workflows", corrections) == "I love n8n workflows"


def test_apply_corrections_ordered_application():
    corrections = (
        Correction(wrong="supa base", right="Supabase"),
        Correction(wrong="Supabase", right="SUPABASE"),
    )
    result = apply_corrections("using supa base today", corrections)
    assert result == "using SUPABASE today"


def test_apply_corrections_empty_list_noop():
    text = "nothing changes here"
    assert apply_corrections(text, ()) == text


def test_apply_corrections_no_match_unchanged():
    corrections = (Correction(wrong="xyz", right="ABC"),)
    text = "no match in this sentence"
    assert apply_corrections(text, corrections) == text


# ---------------------------------------------------------------------------
# apply_commands
# ---------------------------------------------------------------------------


def test_apply_commands_newline_replaces_phrase():
    commands = (Command(phrase="next line", action="newline"),)
    result = apply_commands("hello next line world", commands)
    assert result == "hello\nworld"


def test_apply_commands_paragraph_replaces_phrase():
    commands = (Command(phrase="new paragraph", action="paragraph"),)
    result = apply_commands("intro new paragraph body", commands)
    assert result == "intro\n\nbody"


def test_apply_commands_surrounding_whitespace_consumed():
    commands = (Command(phrase="next line", action="newline"),)
    result = apply_commands("hello   next line   world", commands)
    assert result == "hello\nworld"


def test_apply_commands_word_boundary_no_mid_phrase_hit():
    commands = (Command(phrase="line", action="newline"),)
    result = apply_commands("the baseline and line end", commands)
    assert "baseline" in result
    assert result == "the baseline and\nend"


def test_apply_commands_case_insensitive():
    commands = (Command(phrase="next line", action="newline"),)
    result = apply_commands("hello NEXT LINE world", commands)
    assert result == "hello\nworld"


def test_apply_commands_empty_list_noop():
    text = "nothing changes here"
    assert apply_commands(text, ()) == text


def test_apply_commands_no_match_unchanged():
    commands = (Command(phrase="next line", action="newline"),)
    text = "no command phrase present"
    assert apply_commands(text, commands) == text
