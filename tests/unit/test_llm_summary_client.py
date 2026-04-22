"""Tests for LLMSummaryClient — httpx mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from voice_commander.plan import PlanOutcome, ToolCall
from voice_sprite.llm_summary_client import LLMSummaryClient


def _outcome() -> PlanOutcome:
    return PlanOutcome(
        transcript="minimize",
        steps=(ToolCall("minimize", {}),),
        status="error",
        failed_step_index=0,
        error_msg="FocusWindowError",
        duration_ms=42,
    )


def _mock_client_response(content: str = "minimize failed — no window"):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_returns_content_on_success():
    client = LLMSummaryClient("http://localhost:1234/v1", "gemma-4-e4b", timeout_ms=800)
    mock_post = MagicMock(return_value=_mock_client_response("minimize failed"))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) == "minimize failed"


def test_returns_none_on_timeout():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    mock_post = MagicMock(side_effect=httpx.TimeoutException("slow"))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) is None


def test_returns_none_on_connect_error():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    mock_post = MagicMock(side_effect=httpx.ConnectError("down"))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) is None


def test_returns_none_on_malformed_json():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.side_effect = ValueError("bad json")
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    with patch.object(client, "_client", MagicMock(post=MagicMock(return_value=resp))):
        assert client.summarize(_outcome()) is None


def test_returns_none_on_empty_content():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    mock_post = MagicMock(return_value=_mock_client_response(""))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) is None


def test_strips_and_truncates_content():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    long = "x" * 120
    mock_post = MagicMock(return_value=_mock_client_response(f"  {long}  "))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        out = client.summarize(_outcome())
    assert out is not None
    assert len(out) <= 80
    assert out.startswith("x")


def test_log_once_offline_first_call_warns_second_call_debugs(caplog):
    """First ConnectError → WARNING; second → DEBUG; flag flips exactly once."""
    import logging

    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    mock_post = MagicMock(side_effect=httpx.ConnectError("refused"))

    with patch.object(client, "_client", MagicMock(post=mock_post)), caplog.at_level(
        logging.DEBUG, logger="voice_sprite.llm_summary_client"
    ):
        result1 = client.summarize(_outcome())
        result2 = client.summarize(_outcome())

    assert result1 is None
    assert result2 is None
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    debug_records = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "HTTP error" in r.message
    ]
    assert len(warning_records) == 1, "expected exactly one WARNING"
    assert len(debug_records) == 1, "expected exactly one DEBUG on second call"
    assert client._warned_offline is True


def test_log_once_malformed_first_call_warns_second_call_debugs(caplog):
    """First malformed response → WARNING; second → DEBUG; flag flips exactly once."""
    import logging

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.side_effect = ValueError("bad json")

    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)

    with patch.object(
        client, "_client", MagicMock(post=MagicMock(return_value=resp))
    ), caplog.at_level(logging.DEBUG, logger="voice_sprite.llm_summary_client"):
        result1 = client.summarize(_outcome())
        result2 = client.summarize(_outcome())

    assert result1 is None
    assert result2 is None
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    debug_records = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "malformed" in r.message
    ]
    assert len(warning_records) == 1, "expected exactly one WARNING"
    assert len(debug_records) == 1, "expected exactly one DEBUG on second call"
    assert client._warned_malformed is True
