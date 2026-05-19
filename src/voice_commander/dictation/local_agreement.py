"""LocalAgreement-2 word stabiliser for streaming-window dictation.

The transcription server re-decodes a *growing audio window* every
``window_step_ms``. Each decode is a WHOLE-WINDOW hypothesis: a transcript of
the same audio from t=0, just longer each time. The newest words of any
hypothesis are unstable — the model has not heard what follows.

LocalAgreement-2 commits a word only once **two consecutive whole-window
hypotheses agree on it as part of their common prefix**. The unstable tail past
the agreement point is discarded each round (ADR 0095). This is what removes the
stray ``...`` ellipses of the old per-chunk path: an isolated short clip is never
decoded alone, and a hallucinated word never survives to the committed prefix
because the next pass disagrees.

Each word carries an **absolute-stream end-time** (:class:`TimedWord`) so the
caller can trim the audio window at the last committed word's boundary.

Pure module — no I/O, no threads. The session serialises ``commit``/``finalize``
under its existing ``_agreement_lock``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimedWord:
    """One word from a window hypothesis, with its absolute-stream end-time.

    ``end_s`` is seconds from the start of the whole dictation stream — the
    caller adds ``DictationWindow.committed_offset_s`` to the server's
    window-relative segment end-time before constructing a ``TimedWord``.
    """

    text: str
    end_s: float


def _common_prefix_len(a: list[str], b: list[str]) -> int:
    """Length of the longest common prefix of two word lists."""
    n = 0
    for wa, wb in zip(a, b):
        if wa != wb:
            break
        n += 1
    return n


class LocalAgreement:
    """Commit words confirmed stable across consecutive whole-window hypotheses."""

    def __init__(self) -> None:
        # The previous whole-window hypothesis (timed words).
        self._prev: list[TimedWord] = []
        # Every word committed so far, in order.
        self._committed: list[TimedWord] = []

    def commit(self, hypothesis: list[TimedWord]) -> tuple[list[str], float | None]:
        """Fold one whole-window hypothesis in; return newly-committed words.

        Returns ``(words, end_s)`` where *words* is the list of words newly
        confirmed this round (already-committed prefix excluded) and *end_s* is
        the absolute-stream end-time of the last newly-committed word — the
        timestamp the caller passes to ``DictationWindow.commit`` to trim the
        buffer. *end_s* is ``None`` when nothing new was committed.
        """
        if not hypothesis:
            return [], None

        prev_words = [w.text for w in self._prev]
        new_words = [w.text for w in hypothesis]
        agreed = _common_prefix_len(prev_words, new_words)

        # The agreed prefix is stable. Anything in it past what we already
        # committed is newly committed this round.
        already = len(self._committed)
        newly: list[str] = []
        end_s: float | None = None
        if agreed > already:
            newly_words = hypothesis[already:agreed]
            newly = [w.text for w in newly_words]
            end_s = newly_words[-1].end_s
            self._committed.extend(newly_words)

        self._prev = hypothesis
        return newly, end_s

    def finalize(self) -> list[str]:
        """Flush every uncommitted word from the last hypothesis — call at end.

        After the final window is decoded there is no "next" hypothesis to
        agree with, so the uncommitted tail of the last hypothesis is accepted
        verbatim. Idempotent — a second call returns ``[]`` and leaves
        ``_committed`` intact.
        """
        if not self._prev:
            return []
        tail = [w.text for w in self._prev[len(self._committed):]]
        self._committed = list(self._prev)
        self._prev = []
        return tail

    def committed_text(self) -> str:
        """All committed words joined by single spaces (the live HUD prefix)."""
        return " ".join(w.text for w in self._committed)
