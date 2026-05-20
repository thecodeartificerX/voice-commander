"""Unit tests for voice_commander.dictation.postprocess."""
from __future__ import annotations

import pytest

from voice_commander.dictation.postprocess import (
    _PROMPT_CHAR_CAP,
    _SENTENCE_ENDERS,
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


def test_apply_corrections_right_with_backslash_does_not_raise():
    # right may contain a Windows path / literal backslash — must not crash re.sub.
    corrections = (Correction(wrong="PACT", right="C:\\path"),)
    result = apply_corrections("I said PACT today", corrections)
    assert result == "I said C:\\path today"


def test_apply_corrections_right_with_group_reference_is_literal():
    # A literal "\1" in right must be inserted verbatim, not treated as a group ref.
    corrections = (Correction(wrong="token", right="\\1"),)
    result = apply_corrections("the token here", corrections)
    assert result == "the \\1 here"


def test_apply_corrections_empty_wrong_is_skipped():
    corrections = (Correction(wrong="", right="X"),)
    text = "hello world"
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


def test_apply_commands_empty_phrase_is_skipped():
    commands = (Command(phrase="", action="newline"),)
    text = "hello world"
    assert apply_commands(text, commands) == text


def test_apply_commands_phrase_at_start_produces_leading_newline():
    # Documented contract: a command phrase at position 0 yields a leading control char.
    commands = (Command(phrase="next line", action="newline"),)
    result = apply_commands("next line world", commands)
    assert result == "\nworld"


# ---------------------------------------------------------------------------
# apply_commands — insert mode (arbitrary literal strings, ADR 0088 amendment)
# ---------------------------------------------------------------------------


def test_apply_commands_insert_joiner_consumes_both_spaces():
    # Joiner chars (/ - _) consume surrounding spaces so words bind together.
    commands = (Command(phrase="dash", insert="-"),)
    result = apply_commands("picked dash up dash in dash flight", commands)
    assert result == "picked-up-in-flight"


def test_apply_commands_insert_slash_joins_path_segments():
    commands = (Command(phrase="backslash", insert="/"),)
    result = apply_commands("kizen-OS backslash memory backslash handover.md", commands)
    assert result == "kizen-OS/memory/handover.md"


def test_apply_commands_insert_sentence_ender_preserves_trailing_space():
    # Sentence-ending punctuation keeps trailing space so next word is not run-together.
    commands = (Command(phrase="full stop", insert="."),)
    result = apply_commands("handover.md full stop 151 lines", commands)
    assert result == "handover.md. 151 lines"


def test_apply_commands_insert_comma_preserves_trailing_space():
    commands = (Command(phrase="comma", insert=","),)
    result = apply_commands("151 lines comma status", commands)
    assert result == "151 lines, status"


def test_apply_commands_insert_colon_preserves_trailing_space():
    commands = (Command(phrase="colon", insert=":"),)
    result = apply_commands("status colon value", commands)
    assert result == "status: value"


def test_apply_commands_insert_spacer_phrase_consumes_both_sides():
    # " - " has its own surrounding spaces; original surrounding spaces eaten.
    commands = (Command(phrase="space dash space", insert=" - "),)
    result = apply_commands("no tbd space dash space full state", commands)
    assert result == "no tbd - full state"


def test_apply_commands_insert_newline_consumes_both_spaces():
    # Newline is not a sentence-ender; both spaces consumed, no padding.
    commands = (Command(phrase="new line", insert="\n"),)
    result = apply_commands("hello new line world", commands)
    assert result == "hello\nworld"


def test_apply_commands_insert_case_insensitive():
    commands = (Command(phrase="full stop", insert="."),)
    result = apply_commands("hello FULL STOP world", commands)
    assert result == "hello. world"


def test_apply_commands_insert_word_boundary_no_mid_word_hit():
    # "dash" must not match inside "eyelash"
    commands = (Command(phrase="dash", insert="-"),)
    result = apply_commands("the eyelash and dash here", commands)
    assert "eyelash" in result
    assert result == "the eyelash and-here"


def test_apply_commands_insert_empty_string_is_skipped():
    # An entry with insert="" and no action should not modify text.
    commands = (Command(phrase="full stop", insert=""),)
    text = "no change full stop here"
    assert apply_commands(text, commands) == text


def test_apply_commands_insert_takes_precedence_over_action():
    # When both insert and action are set, insert wins.
    commands = (Command(phrase="separator", insert="/", action="newline"),)
    result = apply_commands("a separator b", commands)
    assert result == "a/b"


def test_apply_commands_insert_open_bracket_consumes_both_spaces():
    # Opening brackets are not sentence-enders; they bind to the following word.
    commands = (
        Command(phrase="open bracket", insert="("),
        Command(phrase="close bracket", insert=")"),
    )
    result = apply_commands("open bracket arg close bracket", commands)
    assert result == "(arg)"


def test_apply_commands_insert_full_pipeline_smoke():
    """Simulate the live-transcript scenario from ADR 0088 task spec."""
    raw = (
        "kizen-OS backslash kizen-OS backslash memory backslash handover.md "
        "full stop 151 lines comma status picked dash up dash in dash flight "
        "full stop no tbd space dash space full state captured full stop."
    )
    commands = (
        Command(phrase="space dash space", insert=" - "),
        Command(phrase="backslash", insert="/"),
        Command(phrase="full stop", insert="."),
        Command(phrase="comma", insert=","),
        Command(phrase="dash", insert="-"),
    )
    result = apply_commands(raw, commands)
    # Path segments joined, sentence-enders spaced correctly, dashes joined.
    assert "kizen-OS/kizen-OS/memory/handover.md." in result
    assert "151 lines," in result
    assert "picked-up-in-flight." in result
    assert "no tbd - full state captured" in result


def test_apply_commands_sentence_enders_constant_is_frozenset():
    assert isinstance(_SENTENCE_ENDERS, frozenset)
    assert "." in _SENTENCE_ENDERS
    assert "," in _SENTENCE_ENDERS
