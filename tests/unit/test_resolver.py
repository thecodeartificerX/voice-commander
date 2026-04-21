"""Unit tests for Resolver.

The LLMRouter and FeedbackSink are fully mocked — no network, no audio.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from voice_commander.plan import Plan, ToolCall
from voice_commander.resolver import Resolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan() -> Plan:
    return Plan(
        steps=(ToolCall(name="copy", kwargs={}),),
        raw_response={"choices": []},
    )


def _make_resolver() -> tuple[Resolver, MagicMock, MagicMock]:
    """Return (resolver, mock_llm_router, mock_feedback)."""
    mock_router = MagicMock()
    mock_feedback = MagicMock()
    resolver = Resolver(llm_router=mock_router, feedback=mock_feedback)
    return resolver, mock_router, mock_feedback


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolverSuccess:
    def test_resolve_returns_plan_on_success(self):
        """When llm_router.route() returns a Plan, resolve() returns it unchanged."""
        resolver, mock_router, mock_feedback = _make_resolver()
        expected_plan = _make_plan()
        mock_router.route.return_value = expected_plan

        result = resolver.resolve("copy that")

        assert result is expected_plan

    def test_resolve_does_not_call_on_miss_on_success(self):
        """on_miss must NOT be fired when the router returns a valid Plan."""
        resolver, mock_router, mock_feedback = _make_resolver()
        mock_router.route.return_value = _make_plan()

        resolver.resolve("copy that")

        mock_feedback.on_miss.assert_not_called()


class TestResolverMiss:
    def test_resolve_returns_none_on_miss(self):
        """When llm_router.route() returns None, resolve() returns None."""
        resolver, mock_router, mock_feedback = _make_resolver()
        mock_router.route.return_value = None

        result = resolver.resolve("mumble mumble")

        assert result is None

    def test_resolve_calls_on_miss_with_transcript(self):
        """on_miss IS called with the original transcript when route() returns None."""
        resolver, mock_router, mock_feedback = _make_resolver()
        mock_router.route.return_value = None
        transcript = "some unrecognised utterance"

        resolver.resolve(transcript)

        mock_feedback.on_miss.assert_called_once_with(transcript, ())


class TestResolverTranscriptForwarding:
    def test_resolve_passes_transcript_to_router(self):
        """The exact transcript string is forwarded verbatim to llm_router.route()."""
        resolver, mock_router, mock_feedback = _make_resolver()
        mock_router.route.return_value = _make_plan()
        transcript = "open a new tab"

        resolver.resolve(transcript)

        mock_router.route.assert_called_once_with(transcript)

    def test_resolve_passes_transcript_to_router_on_miss(self):
        """Transcript forwarding also works on the miss path."""
        resolver, mock_router, mock_feedback = _make_resolver()
        mock_router.route.return_value = None
        transcript = "completely unknown command"

        resolver.resolve(transcript)

        mock_router.route.assert_called_once_with(transcript)
