"""Tests for LLMRouter system-prompt assembly (ADR 0043 few-shot prompt).

Covers:
- default_browser substitution in the template.
- Presence of all three canonical few-shot examples.
- Approximate token budget (prompt fits well under the LM Studio prefix cap).
- Prompt mentions strict execution order per the spec.
- No legacy tool names (focus_browser, new_tab, type_text, press_keys) remain.
"""

from __future__ import annotations

import threading

import pytest

from voice_commander.config import LLMConfig
from voice_commander.llm_router import LLMRouter
from voice_commander.registry import ToolRegistry


def _make_router(default_browser: str = "chrome") -> LLMRouter:
    cfg = LLMConfig(default_browser=default_browser, warmup_on_startup=False)
    return LLMRouter(cfg, ToolRegistry(), threading.Lock())


def test_prompt_substitutes_default_browser() -> None:
    router = _make_router(default_browser="comet")
    prompt = router._build_system_prompt()
    assert "'comet'" in prompt, "Expected default_browser='comet' in prompt"
    assert "{default_browser}" not in prompt, (
        "Placeholder {default_browser} was not fully substituted"
    )


def test_prompt_includes_all_three_examples() -> None:
    router = _make_router(default_browser="chrome")
    prompt = router._build_system_prompt()
    assert "search how to lose weight" in prompt
    assert "copy that and paste it in notepad" in prompt
    assert "open spotify" in prompt


def test_prompt_token_budget() -> None:
    """~4 chars/token rule-of-thumb → len(prompt) < 4000 keeps us under 1000 tokens."""
    router = _make_router()
    prompt = router._build_system_prompt()
    assert len(prompt) < 4000, f"System prompt length {len(prompt)} exceeds the 4000-char soft cap"


def test_prompt_mentions_strict_execution_order() -> None:
    router = _make_router()
    prompt = router._build_system_prompt()
    assert "strict execution order" in prompt


@pytest.mark.parametrize(
    "legacy_name",
    ["focus_browser", "new_tab", "type_text", "press_keys"],
)
def test_prompt_no_legacy_tool_names(legacy_name: str) -> None:
    router = _make_router()
    prompt = router._build_system_prompt()
    assert legacy_name not in prompt, (
        f"Legacy tool name {legacy_name!r} still appears in the few-shot prompt"
    )
