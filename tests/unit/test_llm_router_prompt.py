"""Tests for the intent-matcher system prompt used by LLMRouter.

The prompt is now minimal — no few-shot examples, no shortcut cheat-sheet.
Commands and workflows supply the per-intent description/phrases directly
through their tool schemas, so the prompt only needs to explain the task
(pick one tool whose description matches the transcript).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from voice_commander.config import LLMConfig
from voice_commander.llm_router import (
    LLMRouter,
    _FALLBACK_TEMPLATE,
    _TEMPLATE_PATH,
    _load_template,
)
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


def test_load_template_from_file() -> None:
    """_load_template() reads from prompt_template.txt when it exists."""
    text = _load_template()
    assert "{default_browser}" in text
    assert "intent matcher" in text


def test_fallback_when_template_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falls back to _FALLBACK_TEMPLATE when file is absent."""
    monkeypatch.setattr(
        "voice_commander.llm_router._TEMPLATE_PATH",
        tmp_path / "nonexistent.txt",
    )
    text = _load_template()
    assert text == _FALLBACK_TEMPLATE


def test_reload_prompt_picks_up_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reload_prompt() re-reads the file and updates _system_prompt."""
    template_file = tmp_path / "prompt_template.txt"
    template_file.write_text(
        "Custom prompt for {default_browser}.", encoding="utf-8"
    )
    monkeypatch.setattr(
        "voice_commander.llm_router._TEMPLATE_PATH", template_file
    )
    router = _make_router(default_browser="firefox")
    assert "Custom prompt for firefox." in router._system_prompt

    # Modify file and reload
    template_file.write_text(
        "Updated prompt for {default_browser}.", encoding="utf-8"
    )
    router.reload_prompt()
    assert "Updated prompt for firefox." in router._system_prompt


def test_composed_prompt_data_structure() -> None:
    """composed_prompt_data() returns dict with expected keys and types."""
    router = _make_router(default_browser="comet")
    data = router.composed_prompt_data()
    assert isinstance(data, dict)
    assert "template_raw" in data
    assert "template_resolved" in data
    assert "placeholders" in data
    assert "tools" in data
    assert "tools_count" in data
    assert "model_id" in data
    assert "endpoint_url" in data
    assert isinstance(data["template_raw"], str)
    assert isinstance(data["template_resolved"], str)
    assert isinstance(data["placeholders"], dict)
    assert isinstance(data["tools"], list)
    assert isinstance(data["tools_count"], int)
    assert data["placeholders"]["default_browser"] == "comet"
    assert "{default_browser}" in data["template_raw"]
    assert "'comet'" in data["template_resolved"]


def test_composed_prompt_data_includes_tools() -> None:
    """When a tool has params_schema, it appears in composed_prompt_data()['tools']."""
    cfg = LLMConfig(default_browser="chrome", warmup_on_startup=False)
    reg = ToolRegistry()

    # Register a fake tool entry with params_schema
    from voice_commander.registry import ToolEntry

    entry = ToolEntry(
        name="test_tool",
        phrases=(),
        func=lambda: None,
        module="test",
        docstring=None,
        description="A test tool",
        llm_only=True,
    )
    entry.params_schema = {
        "type": "function",
        "function": {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    reg._by_name["test_tool"] = entry

    router = LLMRouter(cfg, reg, threading.Lock())
    data = router.composed_prompt_data()
    assert data["tools_count"] >= 1
    tool_names = [t["function"]["name"] for t in data["tools"]]
    assert "test_tool" in tool_names


def test_fallback_when_template_file_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falls back to _FALLBACK_TEMPLATE when file raises a non-FileNotFoundError OSError."""
    from unittest.mock import patch

    template_file = tmp_path / "prompt_template.txt"
    template_file.write_text("content", encoding="utf-8")
    monkeypatch.setattr(
        "voice_commander.llm_router._TEMPLATE_PATH",
        template_file,
    )

    with patch.object(Path, "read_text", side_effect=PermissionError("Access denied")):
        text = _load_template()
    assert text == _FALLBACK_TEMPLATE


def test_build_system_prompt_fallback_on_stray_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stray {foo} in template causes fallback, not crash."""
    template_file = tmp_path / "prompt_template.txt"
    template_file.write_text(
        "Prompt with {default_browser} and stray {foo}.", encoding="utf-8"
    )
    monkeypatch.setattr(
        "voice_commander.llm_router._TEMPLATE_PATH", template_file
    )
    router = _make_router(default_browser="firefox")
    # Should have fallen back to _FALLBACK_TEMPLATE, not crashed
    assert "'firefox'" in router._system_prompt
    assert "{foo}" not in router._system_prompt
