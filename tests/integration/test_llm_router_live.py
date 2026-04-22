"""Live integration tests for LLM router against real LM Studio.

Skipped unless LM_STUDIO_URL environment variable is set.
Requires LM Studio running with a model loaded.
"""

import os
import time

import pytest

from voice_commander.config import LLMConfig
from voice_commander.llm_router import LLMRouter
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_schema import sig_to_json_schema

LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not LM_STUDIO_URL, reason="LM_STUDIO_URL not set"),
]


def _make_router() -> LLMRouter:
    """Build a router with real primitives and a few test tools."""
    reg = ToolRegistry()

    # Register some tools with schemas
    def copy() -> None:
        """Copy the current selection."""

    def paste() -> None:
        """Paste from clipboard."""

    def press_keys(combo: str) -> None:
        """Press a key combination."""

    def type_text(text: str) -> None:
        """Type arbitrary text."""

    def no_match(reason: str) -> None:
        """No tool matches."""

    from voice_commander.tool_metadata import ArgMetadata

    tools = [
        ("copy", copy, {}),
        ("paste", paste, {}),
        (
            "press_keys",
            press_keys,
            {
                "combo": ArgMetadata(
                    name="combo",
                    type_str="str",
                    description="Key combo like ctrl+c",
                    required=True,
                    default=None,
                )
            },
        ),
        (
            "type_text",
            type_text,
            {
                "text": ArgMetadata(
                    name="text",
                    type_str="str",
                    description="Text to type",
                    required=True,
                    default=None,
                )
            },
        ),
        (
            "no_match",
            no_match,
            {
                "reason": ArgMetadata(
                    name="reason",
                    type_str="str",
                    description="Why no tool matched",
                    required=True,
                    default=None,
                )
            },
        ),
    ]

    for name, func, args_meta in tools:
        schema = sig_to_json_schema(func, args_meta, tool_name=name)
        entry = ToolEntry(
            name=name,
            phrases=(),
            func=func,
            module="test",
            docstring=func.__doc__,
            params_schema=schema,
            llm_only=(name in ("no_match",)),
        )
        reg.register(entry)

    config = LLMConfig(
        endpoint_url=LM_STUDIO_URL,
        timeout_ms=600,
        max_plan_steps=8,
    )
    return LLMRouter(config, reg)


class TestLLMRouterLive:
    def test_simple_single_tool(self):
        """Simple command should produce a 1-step plan."""
        router = _make_router()
        try:
            plan = router.route("copy this text")
            assert plan is not None
            assert len(plan.steps) >= 1
            assert plan.steps[0].name in ("copy", "press_keys")
        finally:
            router.close()

    def test_chained_command(self):
        """Chained command should produce multi-step plan."""
        router = _make_router()
        try:
            plan = router.route("press ctrl+a then copy")
            assert plan is not None
            assert len(plan.steps) >= 2
        finally:
            router.close()

    def test_nonsense_returns_no_match(self):
        """Nonsense utterance should produce no_match (returns None)."""
        router = _make_router()
        try:
            plan = router.route("flurble blorb zippity doo completely meaningless")
            # Should be None (no_match detected by router)
            assert plan is None
        finally:
            router.close()

    def test_latency_under_budget(self):
        """LLM response should arrive within 600ms budget."""
        router = _make_router()
        try:
            start = time.perf_counter()
            router.route("copy")
            elapsed = (time.perf_counter() - start) * 1000
            assert elapsed < 600, f"Latency {elapsed:.0f}ms exceeded 600ms budget"
        finally:
            router.close()
