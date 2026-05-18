"""LocalAgreement word stabiliser for streaming dictation.

Whisper transcribes each audio chunk independently. The last few words of a
chunk are unstable — the model has not heard what follows, so it guesses.
LocalAgreement holds back the unstable suffix of every chunk transcript and
commits a word only once the next chunk's transcript confirms it by overlap.

Pure module — no I/O, no threads. See ADR 0092
(`docs/decisions/0092-streaming-dictation-integration.md`).
"""

from __future__ import annotations

from collections import deque


class LocalAgreement:
    """Commit words confirmed stable across consecutive chunk transcripts."""

    def __init__(self) -> None:
        self._hypothesis: deque[str] = deque()

    def commit(self, new_text: str) -> list[str]:
        """Fold a new chunk transcript in; return newly-confirmed words.

        The longest suffix of the current hypothesis equal to a prefix of
        ``new_text`` is the overlap. Hypothesis words *before* that overlap are
        confirmed and returned. The hypothesis is then replaced by ``new_text``
        (the overlap tail plus whatever follows it).
        """
        new_words = new_text.split()
        if not new_words:
            return []

        hyp = list(self._hypothesis)
        overlap = 0
        for k in range(min(len(hyp), len(new_words)), 0, -1):
            if hyp[-k:] == new_words[:k]:
                overlap = k
                break

        stable_count = len(hyp) - overlap
        committed = hyp[:stable_count]
        self._hypothesis = deque(new_words)
        return committed

    def finalize(self) -> list[str]:
        """Flush every remaining hypothesis word — call at session end."""
        remaining = list(self._hypothesis)
        self._hypothesis.clear()
        return remaining
