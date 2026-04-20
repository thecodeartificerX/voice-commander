from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .registry import ToolEntry, ToolRegistry

_NORMALIZE_RE = re.compile(r"[^a-z0-9 ]+")


@dataclass(frozen=True)
class MatchResult:
    tool: ToolEntry | None
    phrase: str | None
    score: float
    candidates: tuple[tuple[str, str, float], ...]  # (phrase, tool_name, score)


class Matcher:
    def __init__(self, registry: ToolRegistry, threshold: float = 85.0) -> None:
        self._registry = registry
        self._threshold = threshold

    def match(self, utterance: str) -> MatchResult:
        text = _normalize(utterance)
        phrases = self._registry.flat_phrases()
        if not phrases:
            return MatchResult(None, None, 0.0, ())
        phrase_list = [p for p, _ in phrases]
        phrase_to_tool = {p: t for p, t in phrases}

        ranked = process.extract(text, phrase_list, scorer=fuzz.WRatio, limit=5)
        ranked_sorted = sorted(
            ranked,
            key=lambda r: (-r[1], phrase_to_tool[r[0]]),
        )
        candidates = tuple((phr, phrase_to_tool[phr], score) for phr, score, _ in ranked_sorted)

        top_phrase, top_score, _tool_name = candidates[0][0], candidates[0][2], candidates[0][1]
        if top_score < self._threshold:
            return MatchResult(None, None, top_score, candidates)
        tool = self._registry.by_name(phrase_to_tool[top_phrase])
        return MatchResult(tool, top_phrase, top_score, candidates)


def _normalize(text: str) -> str:
    lowered = text.lower().strip()
    cleaned = _NORMALIZE_RE.sub(" ", lowered)
    return " ".join(cleaned.split())
