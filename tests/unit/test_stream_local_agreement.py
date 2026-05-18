"""Unit tests for the LocalAgreement word stabiliser."""

from __future__ import annotations

from voice_commander.dictation_stream.local_agreement import LocalAgreement


def test_first_chunk_commits_nothing():
    la = LocalAgreement()
    assert la.commit("The quick brown fox") == []


def test_overlap_commits_stable_prefix():
    la = LocalAgreement()
    la.commit("The quick brown fox")
    assert la.commit("brown fox jumps over") == ["The", "quick"]


def test_progressive_commits_across_three_chunks():
    la = LocalAgreement()
    assert la.commit("The quick brown fox") == []
    assert la.commit("brown fox jumps over") == ["The", "quick"]
    assert la.commit("jumps over the lazy") == ["brown", "fox"]
    assert la.finalize() == ["jumps", "over", "the", "lazy"]


def test_no_overlap_commits_everything():
    la = LocalAgreement()
    la.commit("alpha beta")
    assert la.commit("gamma delta") == ["alpha", "beta"]


def test_full_overlap_commits_nothing():
    la = LocalAgreement()
    la.commit("same words here")
    assert la.commit("same words here") == []


def test_empty_text_commits_nothing_and_keeps_hypothesis():
    la = LocalAgreement()
    la.commit("hello world")
    assert la.commit("") == []
    assert la.commit("   ") == []
    assert la.finalize() == ["hello", "world"]


def test_finalize_on_empty_returns_empty():
    assert LocalAgreement().finalize() == []


def test_finalize_is_idempotent():
    la = LocalAgreement()
    la.commit("one two")
    assert la.finalize() == ["one", "two"]
    assert la.finalize() == []


def test_single_word_chunks_stabilise_correctly():
    la = LocalAgreement()
    assert la.commit("hello") == []
    assert la.commit("world") == ["hello"]   # no overlap
    assert la.commit("world") == []           # full overlap (same word repeated)
    assert la.finalize() == ["world"]
