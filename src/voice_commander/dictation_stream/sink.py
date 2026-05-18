"""Output sink for streaming dictation.

Accumulates the words ``LocalAgreement`` confirms during a session, then on
``flush`` joins them, runs them through ``transform`` and pastes the result at
the cursor via a clipboard round-trip (reusing the shipped
``dictation.clipboard`` helper).

``transform`` is the seam for a future small-LLM rewrite step (spec section 3);
it is the identity function for now. Callers depend only on its ``str -> str``
signature — replacing the body is the swap path.
"""

from __future__ import annotations

import logging

from voice_commander.dictation.clipboard import paste_via_clipboard

logger = logging.getLogger(__name__)


def transform(text: str) -> str:
    """Post-process the full transcript before paste. Identity for now.

    The future small-LLM rewrite step replaces this body; callers and tests
    depend only on the ``str -> str`` signature.
    """
    return text


class TextSink:
    """Collect committed words; paste the transformed transcript on flush."""

    def __init__(self) -> None:
        self._words: list[str] = []

    def accumulate(self, words: list[str]) -> None:
        """Append newly-confirmed words to the pending transcript."""
        self._words.extend(words)

    @property
    def text(self) -> str:
        """The raw accumulated transcript so far."""
        return " ".join(self._words)

    def flush(self) -> str:
        """Transform the accumulated transcript and paste it.

        Returns the pasted text (empty string if nothing was accumulated).
        """
        raw = self.text
        if not raw:
            logger.info("TextSink: nothing to paste")
            return ""
        result = transform(raw)
        paste_via_clipboard(result)
        self._words.clear()
        logger.info("TextSink: pasted %d chars", len(result))
        return result
