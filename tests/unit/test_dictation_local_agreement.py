"""Unit tests for LocalAgreement-2 — whole-window prefix word stabiliser.

LocalAgreement-2 is fed consecutive WHOLE-WINDOW hypotheses (each transcribes
the same growing audio from t=0). It commits the longest common prefix of the
last two hypotheses and reports the absolute end-time of the last committed word
(ADR 0095).
"""

from __future__ import annotations

from voice_commander.dictation.local_agreement import LocalAgreement, TimedWord


def _tw(text: str, end: float) -> TimedWord:
    return TimedWord(text=text, end_s=end)


def test_first_hypothesis_commits_nothing():
    la = LocalAgreement()
    committed, end = la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    assert committed == []
    assert end is None


def test_agreeing_prefix_commits_on_second_hypothesis():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    # Second window: prefix "the quick" agrees; "brown"/"green" disagree.
    committed, end = la.commit(
        [_tw("the", 0.3), _tw("quick", 0.6), _tw("green", 0.95), _tw("fox", 1.3)]
    )
    assert committed == ["the", "quick"]
    assert end == 0.6  # end-time of the last committed word


def test_progressive_commit_across_three_windows():
    la = LocalAgreement()
    assert la.commit([_tw("the", 0.3), _tw("quick", 0.6)]) == ([], None)
    committed, end = la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    assert committed == ["the", "quick"]
    committed, end = la.commit(
        [_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9), _tw("fox", 1.2)]
    )
    # "the quick" already committed — only the NEWLY agreed word is returned.
    assert committed == ["brown"]
    assert end == 0.9


def test_no_new_agreement_commits_nothing():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    # Two identical windows with no growth — prefix already committed.
    committed, end = la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    assert committed == []


def test_unstable_tail_never_committed_until_confirmed():
    la = LocalAgreement()
    la.commit([_tw("hello", 0.4), _tw("wurld", 0.8)])  # whisper guessed wrong
    committed, _ = la.commit([_tw("hello", 0.4), _tw("world", 0.8)])
    # "hello" agreed across both; "wurld" != "world" so it stays uncommitted.
    assert committed == ["hello"]


def test_finalize_flushes_uncommitted_tail():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    # "the quick" committed; "brown" still in the latest hypothesis tail.
    assert la.finalize() == ["brown"]


def test_finalize_idempotent():
    la = LocalAgreement()
    la.commit([_tw("one", 0.3)])
    la.commit([_tw("one", 0.3), _tw("two", 0.6)])
    assert la.finalize() == ["two"]
    assert la.finalize() == []
    # Second finalize must NOT wipe _committed — transcript is preserved.
    assert la.committed_text() == "one two"


def test_empty_hypothesis_commits_nothing():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3)])
    committed, end = la.commit([])
    assert committed == []
    assert end is None


def test_committed_text_helper_joins_all_committed_words():
    la = LocalAgreement()
    la.commit([_tw("the", 0.3), _tw("quick", 0.6)])
    la.commit([_tw("the", 0.3), _tw("quick", 0.6), _tw("brown", 0.9)])
    assert la.committed_text() == "the quick"
