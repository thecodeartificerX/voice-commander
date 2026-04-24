"""Tests for the intent-matcher system prompt used by LLMRouter.

The prompt is now minimal — no few-shot examples, no shortcut cheat-sheet.
Commands and workflows supply the per-intent description/phrases directly
through their tool schemas, so the prompt only needs to explain the task
(pick one tool whose description matches the transcript).
"""

from __future__ import annotations

import threading

from voice_commander.config import LLMConfig
from voice_commander.llm_router import LLMRouter
from voice_commander.registry import ToolRegistry


def _make_router(default_browser: str = "chrome") -> LLMRouter:
    cfg = LLMConfig(default_browser=default_browser, warmup_on_startup=False)
    return LLMRouter(cfg, ToolRegistry(), threading.Lock())


def test_prompt_substitutes_default_browser() -> None:
    router = _make_router(default_browser="comet")
    prompt = router._build_system_prompt()
    assert "'comet'" in prompt
    assert "{default_browser}" not in prompt


def test_prompt_token_budget() -> None:
    """Stays well under the 4000-char soft cap — intent matching is cheap."""
    router = _make_router()
    prompt = router._build_system_prompt()
    assert len(prompt) < 4000, f"System prompt length {len(prompt)} exceeds the 4000-char soft cap"


def test_prompt_explains_intent_matching() -> None:
    """Prompt must steer the model toward single-tool selection."""
    router = _make_router()
    prompt = router._build_system_prompt()
    assert "no_match" in prompt
    assert "Phrases:" in prompt
    # No multi-step chaining instructions — the old few-shot prompt leaked
    # phrases like "Tools:" or "ctrl+t" into the prompt itself, which the
    # commands-based architecture replaces with per-tool schemas.
    assert "shortcut cheat-sheet" not in prompt.lower()


def test_prompt_no_primitive_leakage() -> None:
    """The intent-matcher prompt should not hand-roll primitive examples;
    tool descriptions carry that info now."""
    router = _make_router()
    prompt = router._build_system_prompt()
    for primitive in ("ctrl+t", "ctrl+w", "alt+f4", "win+down"):
        assert primitive not in prompt, (
            f"Primitive shortcut {primitive!r} leaked into the system prompt"
        )
