from __future__ import annotations

import logging

from .feedback import FeedbackSink
from .matcher import MatchResult

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, feedback: FeedbackSink) -> None:
        self._feedback = feedback

    def dispatch(self, transcript: str, match: MatchResult) -> None:
        if match.tool is None or match.phrase is None:
            self._feedback.on_miss(transcript, match.candidates)
            return
        self._feedback.on_match(match.tool.name, match.phrase, match.score)
        try:
            match.tool.func()
        except Exception as e:
            self._feedback.on_error(f"tool:{match.tool.name}", e)
