"""HTTP client for one-shot LM Studio summarization of plan outcomes."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from voice_commander.plan import PlanOutcome

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "Summarize this Windows voice-command outcome in at most 6 words, "
    "past tense. If status is 'error', include the failure reason. "
    "Do not add quotes, prefixes, or explanations. Output plain text."
)

_MAX_LEN = 80


class LLMSummaryClient:
    """Calls LM Studio to synthesize a short HUD summary from a PlanOutcome."""

    def __init__(self, endpoint_url: str, model_id: str, timeout_ms: int) -> None:
        self._endpoint_url = endpoint_url.rstrip("/")
        self._model_id = model_id
        read_s = max(0.05, timeout_ms / 1000.0 - 0.1)
        self._client = httpx.Client(
            base_url=self._endpoint_url,
            timeout=httpx.Timeout(connect=0.1, read=read_s, write=1.0, pool=1.0),
        )
        # Log-once gates: first error → WARNING, subsequent → DEBUG
        self._warned_offline: bool = False
        self._warned_malformed: bool = False

    def summarize(self, outcome: PlanOutcome) -> str | None:
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(outcome.to_event_dict())},
            ],
            "tool_choice": "none",
            "temperature": 0,
            "stream": False,
            "max_tokens": 24,
        }
        try:
            resp = self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as exc:
            if not self._warned_offline:
                logger.warning("LLM summary unavailable (first occurrence): %s", exc)
                self._warned_offline = True
            else:
                logger.debug("LLM summary HTTP error: %s", exc)
            return None
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            if not self._warned_malformed:
                logger.warning("LLM summary malformed response (first occurrence): %s", exc)
                self._warned_malformed = True
            else:
                logger.debug("LLM summary malformed response: %s", exc)
            return None
        if not isinstance(content, str):
            return None
        content = content.strip()
        if not content:
            return None
        return content[:_MAX_LEN]

    def close(self) -> None:
        self._client.close()
