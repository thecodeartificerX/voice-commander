"""Unit tests for LLMRouter.

All HTTP calls are intercepted via patch.object on the router's internal
httpx.Client instance — no real network traffic is made.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import httpx

from voice_commander.config import LLMConfig
from voice_commander.llm_router import LLMRouter
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="copy",
            phrases=("copy",),
            func=lambda: None,
            module="test",
            docstring=None,
            description="Copy",
            llm_only=True,
            params_schema={
                "type": "function",
                "function": {
                    "name": "copy",
                    "description": "Copy",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
        )
    )
    reg.register(
        ToolEntry(
            name="no_match",
            phrases=(),
            func=lambda reason: None,
            module="test",
            docstring=None,
            description="No match",
            llm_only=True,
            params_schema={
                "type": "function",
                "function": {
                    "name": "no_match",
                    "description": "No match",
                    "parameters": {
                        "type": "object",
                        "properties": {"reason": {"type": "string"}},
                        "required": ["reason"],
                    },
                },
            },
        )
    )
    return reg


def _make_router(max_plan_steps: int = 8, warmup_timeout_ms: int = 5000) -> LLMRouter:
    cfg = LLMConfig(
        endpoint_url="http://localhost:1234/v1",
        model_id="test-model",
        timeout_ms=600,
        warmup_timeout_ms=warmup_timeout_ms,
        max_plan_steps=max_plan_steps,
        warmup_on_startup=False,
    )
    return LLMRouter(cfg, _make_registry(), threading.Lock())


def _mock_response(tool_calls: list[dict]) -> MagicMock:
    """Build a fake httpx Response wrapping the given tool_calls list."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "tool_calls": tool_calls,
                }
            }
        ]
    }
    # raise_for_status is a no-op for 200
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _tool_call_dict(name: str, arguments: str | dict = "{}") -> dict:
    return {"function": {"name": name, "arguments": arguments}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRouteHappyPath:
    def test_route_happy_path(self):
        """Single valid tool_call → Plan with one step."""
        router = _make_router()
        mock_resp = _mock_response([_tool_call_dict("copy", "{}")])

        with patch.object(router._client, "post", return_value=mock_resp):
            plan = router.route("copy that")

        assert isinstance(plan, Plan)
        assert len(plan.steps) == 1
        assert plan.steps[0] == ToolCall(name="copy", kwargs={})
        assert "choices" in plan.raw_response

    def test_route_arguments_as_dict(self):
        """Arguments may arrive as a dict instead of a JSON string."""
        router = _make_router()
        mock_resp = _mock_response([_tool_call_dict("copy", {"extra": "val"})])

        with patch.object(router._client, "post", return_value=mock_resp):
            plan = router.route("copy")

        assert plan is not None
        assert plan.steps[0].kwargs == {"extra": "val"}


class TestRouteNetworkErrors:
    def test_route_timeout(self):
        """httpx.TimeoutException → None."""
        router = _make_router()
        with patch.object(router._client, "post", side_effect=httpx.TimeoutException("timed out")):
            assert router.route("copy") is None

    def test_route_connect_error(self):
        """httpx.ConnectError → None."""
        router = _make_router()
        with patch.object(router._client, "post", side_effect=httpx.ConnectError("refused")):
            assert router.route("copy") is None

    def test_route_http_status_error(self):
        """4xx/5xx HTTP response → None."""
        router = _make_router()
        raw_resp = MagicMock()
        raw_resp.status_code = 503
        http_err = httpx.HTTPStatusError("503", request=MagicMock(), response=raw_resp)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = http_err

        with patch.object(router._client, "post", return_value=mock_resp):
            assert router.route("copy") is None


class TestRouteMalformedResponse:
    def test_route_malformed_json(self):
        """resp.json() raises ValueError → None."""
        router = _make_router()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError("bad json")

        with patch.object(router._client, "post", return_value=mock_resp):
            assert router.route("copy") is None

    def test_route_malformed_arguments_string(self):
        """Invalid JSON string in arguments → that step is skipped.

        If ALL steps are malformed, route() returns None.
        A registry with only 'copy' and 'no_match' means an unknown name
        still produces a ToolCall; the malformed-arguments guard is what
        matters here — the step is dropped and None is returned.
        """
        router = _make_router()
        mock_resp = _mock_response(
            [
                _tool_call_dict("copy", "{not valid json"),
            ]
        )

        with patch.object(router._client, "post", return_value=mock_resp):
            result = router.route("copy")

        # The single step had malformed args → skipped → no steps → None
        assert result is None

    def test_route_empty_tool_calls(self):
        """Empty tool_calls list → None."""
        router = _make_router()
        mock_resp = _mock_response([])

        with patch.object(router._client, "post", return_value=mock_resp):
            assert router.route("something") is None

    def test_route_missing_choices(self):
        """Response without 'choices' key → None."""
        router = _make_router()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {}

        with patch.object(router._client, "post", return_value=mock_resp):
            assert router.route("copy") is None


class TestRouteNoMatch:
    def test_route_no_match_first_step(self):
        """First tool_call is 'no_match' → None (signal: no tool fits)."""
        router = _make_router()
        mock_resp = _mock_response(
            [
                _tool_call_dict("no_match", json.dumps({"reason": "unclear"})),
            ]
        )

        with patch.object(router._client, "post", return_value=mock_resp):
            assert router.route("mumble mumble") is None

    def test_route_no_match_not_first_step_is_kept(self):
        """If no_match is NOT the first step, the plan is still returned
        (router only suppresses when no_match leads)."""
        router = _make_router()
        # 'copy' first, then 'no_match' second — plan should still succeed
        mock_resp = _mock_response(
            [
                _tool_call_dict("copy", "{}"),
                _tool_call_dict("no_match", json.dumps({"reason": "second"})),
            ]
        )

        with patch.object(router._client, "post", return_value=mock_resp):
            plan = router.route("copy then something weird")

        assert plan is not None
        assert plan.steps[0].name == "copy"


class TestRouteMultiStepAndTruncation:
    def test_route_multi_step(self):
        """3 tool_calls → Plan with 3 steps."""
        router = _make_router()
        mock_resp = _mock_response(
            [
                _tool_call_dict("copy", "{}"),
                _tool_call_dict("copy", "{}"),
                _tool_call_dict("copy", "{}"),
            ]
        )

        with patch.object(router._client, "post", return_value=mock_resp):
            plan = router.route("copy copy copy")

        assert plan is not None
        assert len(plan.steps) == 3

    def test_route_max_plan_steps(self):
        """10 tool_calls with max_plan_steps=8 → Plan with exactly 8 steps."""
        router = _make_router(max_plan_steps=8)
        ten_calls = [_tool_call_dict("copy", "{}") for _ in range(10)]
        mock_resp = _mock_response(ten_calls)

        with patch.object(router._client, "post", return_value=mock_resp):
            plan = router.route("copy ten times")

        assert plan is not None
        assert len(plan.steps) == 8

    def test_route_max_plan_steps_one(self):
        """max_plan_steps=1 truncates to a single step."""
        router = _make_router(max_plan_steps=1)
        mock_resp = _mock_response(
            [
                _tool_call_dict("copy", "{}"),
                _tool_call_dict("copy", "{}"),
            ]
        )

        with patch.object(router._client, "post", return_value=mock_resp):
            plan = router.route("copy twice")

        assert plan is not None
        assert len(plan.steps) == 1


class TestWarmup:
    # ------------------------------------------------------------------
    # Core contract: warmup POSTs a real chat-completion request so that
    # LM Studio's prefix KV cache is seeded before the first user call.
    # ------------------------------------------------------------------

    def test_warmup_sends_chat_completion_request(self):
        """warmup() POSTs to /chat/completions (not /models), with tools and messages,
        and tool_choice != "required"."""
        router = _make_router()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        with patch.object(router._client, "post", return_value=mock_resp) as mock_post:
            result = router.warmup()

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args

        # Must POST to /chat/completions
        url_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        # The url is the first positional argument
        assert "/chat/completions" in url_arg, (
            f"warmup() must POST to /chat/completions, got: {url_arg!r}"
        )

        body = call_kwargs[1].get("json") or call_kwargs[0][1]
        # Must include 'tools' array
        assert "tools" in body, "warmup body must include 'tools'"
        assert len(body["tools"]) > 0, "warmup 'tools' must be non-empty"
        # Must include 'messages'
        assert "messages" in body, "warmup body must include 'messages'"
        # tool_choice must NOT be "required" — we don't want a real tool call
        assert body.get("tool_choice") != "required", (
            "warmup tool_choice must not be 'required' (use 'none' or 'auto')"
        )

    def test_warmup_max_tokens_is_1(self):
        """warmup() sends max_tokens=1 so LM Studio aborts after prefix prefill."""
        router = _make_router()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        with patch.object(router._client, "post", return_value=mock_resp) as mock_post:
            router.warmup()

        body = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
        assert body.get("max_tokens") == 1, (
            f"warmup max_tokens must be 1, got: {body.get('max_tokens')!r}"
        )

    def test_warmup_uses_configured_timeout(self):
        """warmup() uses warmup_timeout_ms for the httpx timeout, not timeout_ms."""
        router = _make_router(warmup_timeout_ms=3000)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        with patch.object(router._client, "post", return_value=mock_resp) as mock_post:
            router.warmup()

        call_kwargs = mock_post.call_args[1]
        timeout_arg = call_kwargs.get("timeout")
        assert timeout_arg is not None, "warmup() must pass an explicit timeout to post()"
        # The read timeout should be derived from warmup_timeout_ms (3000ms → ~2.9s),
        # not the per-call timeout_ms (600ms → ~0.5s).
        assert isinstance(timeout_arg, httpx.Timeout)
        assert timeout_arg.read > 1.0, (
            f"warmup read timeout should be ~{3000 / 1000 - 0.1}s (from warmup_timeout_ms=3000), "
            f"got: {timeout_arg.read}"
        )
        # Specifically must NOT be derived from the per-call timeout_ms=600
        assert timeout_arg.read > 0.5, "warmup timeout must not be the per-call timeout_ms (600ms)"

    def test_warmup_on_failure_returns_false_without_raising(self):
        """warmup() returns False on ConnectError — no exception bubbles up."""
        router = _make_router()
        with patch.object(router._client, "post", side_effect=httpx.ConnectError("refused")):
            result = router.warmup()  # must not raise

        assert result is False

    def test_warmup_messages_shape_matches_real_call(self):
        """warmup body's system prompt and tools array are identical to a real route() call.

        This is the critical invariant: only if the prefix bytes match exactly will
        LM Studio's KV cache be reused on the first real call.
        """
        router = _make_router()
        captured_warmup: dict = {}
        captured_route: dict = {}

        warmup_resp = MagicMock()
        warmup_resp.status_code = 200
        warmup_resp.raise_for_status.return_value = None

        route_resp = _mock_response([_tool_call_dict("copy", "{}")])

        def capture_post(url, *, json=None, **kwargs):
            if captured_warmup:
                # second call = route()
                captured_route.update(json or {})
                return route_resp
            else:
                # first call = warmup()
                captured_warmup.update(json or {})
                return warmup_resp

        with patch.object(router._client, "post", side_effect=capture_post):
            router.warmup()
            router.route("copy")

        # System prompt must be byte-identical
        warmup_system = next(
            m["content"] for m in captured_warmup["messages"] if m["role"] == "system"
        )
        route_system = next(
            m["content"] for m in captured_route["messages"] if m["role"] == "system"
        )
        assert warmup_system == route_system, (
            "warmup system prompt must be identical to route() system prompt "
            "so LM Studio's prefix KV cache is reused"
        )

        # Tools array must be identical (same schemas in same order)
        assert captured_warmup["tools"] == captured_route["tools"], (
            "warmup tools array must be identical to route() tools array "
            "so the full system+tools prefix seeds the KV cache"
        )

    def test_warmup_timeout_error_returns_false(self):
        """httpx.TimeoutException during warmup → False, no exception."""
        router = _make_router()
        with patch.object(router._client, "post", side_effect=httpx.TimeoutException("timed out")):
            assert router.warmup() is False

    def test_warmup_http_status_error_returns_false(self):
        """4xx/5xx from LM Studio during warmup → False, no exception."""
        router = _make_router()
        raw_resp = MagicMock()
        raw_resp.status_code = 503
        http_err = httpx.HTTPStatusError("503", request=MagicMock(), response=raw_resp)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = http_err

        with patch.object(router._client, "post", return_value=mock_resp):
            assert router.warmup() is False


class TestClose:
    def test_close_delegates_to_client(self):
        """close() calls httpx.Client.close()."""
        router = _make_router()
        with patch.object(router._client, "close") as mock_close:
            router.close()
        mock_close.assert_called_once()


class TestBuildToolsArray:
    def test_tools_array_excludes_entries_without_schema(self):
        """_build_tools_array() skips entries with empty/falsy params_schema."""
        reg = ToolRegistry()
        reg.register(
            ToolEntry(
                name="schemaless",
                phrases=("schemaless",),
                func=lambda: None,
                module="test",
                docstring=None,
                # params_schema defaults to {} which is falsy
            )
        )
        cfg = LLMConfig(timeout_ms=600)
        router = LLMRouter(cfg, reg, threading.Lock())
        assert router._build_tools_array() == []

    def test_tools_array_includes_llm_only_entries(self):
        """llm_only tools with a schema are included in the tools array."""
        router = _make_router()
        tools = router._build_tools_array()
        names = [t["function"]["name"] for t in tools]
        assert "no_match" in names
        assert "copy" in names
