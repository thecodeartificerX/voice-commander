"""Integration test: concurrent LLMRouter.route() + registry.reload_metadata().

Asserts that the reload_lock wiring eliminates the race described in
GitHub issue #11. No real HTTP traffic — LLMRouter._client is patched.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from voice_commander.config import LLMConfig
from voice_commander.llm_router import LLMRouter
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="copy",
            phrases=("copy",),
            func=lambda: None,
            module="test",
            docstring=None,
            description="Copy selection to clipboard",
            llm_only=True,
            params_schema={
                "type": "function",
                "function": {
                    "name": "copy",
                    "description": "Copy selection to clipboard",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
        )
    )
    return reg


def _make_config() -> LLMConfig:
    return LLMConfig(
        endpoint_url="http://localhost:1234/v1",
        model_id="test-model",
        timeout_ms=5000,
        default_browser="chrome",
    )


def test_concurrent_route_and_reload_no_crash() -> None:
    """Spawn a thread calling route() 50 times; main thread reloads registry 10 times.

    The test passes if no exception is raised on either thread and all route
    calls return without hanging (join timeout = 5 s).
    """
    registry = _make_registry()
    reload_lock = threading.Lock()
    config = _make_config()
    router = LLMRouter(config, registry, reload_lock)

    # Patch HTTP client to return a valid no_match tool call response
    no_match_response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "no_match",
                                "arguments": '{"reason": "test"}',
                            }
                        }
                    ]
                }
            }
        ]
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = no_match_response

    errors: list[Exception] = []

    def route_worker() -> None:
        with patch.object(router._client, "post", return_value=mock_resp):
            for _ in range(50):
                try:
                    router.route("open spotify")
                except Exception as exc:
                    errors.append(exc)
                    return

    t = threading.Thread(target=route_worker, daemon=True)
    t.start()

    # Main thread: simulate web-server reload_metadata() calls
    store = MagicMock(spec=ToolMetadataStore)
    store.load_all.return_value = {}  # no-op reload
    for _ in range(10):
        with reload_lock:
            registry.reload_metadata(store)
        time.sleep(0.001)

    t.join(timeout=5.0)
    assert not t.is_alive(), "route_worker thread hung — possible deadlock"
    assert not errors, f"route_worker raised: {errors}"
