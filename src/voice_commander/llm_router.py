"""LLM Router: escalates low-confidence matches to local LM Studio for tool-call planning."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from .config import LLMRouterConfig
from .plan import Plan, ToolCall
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


class LLMRouter:
    """Stateless one-shot tool-call planner via local LM Studio."""

    def __init__(self, config: LLMRouterConfig, registry: ToolRegistry) -> None:
        self._config = config
        self._registry = registry
        # Metrics (simple counters, no external lib)
        self._total_calls: int = 0
        self._total_timeouts: int = 0
        self._total_errors: int = 0
        self._total_latency_ms: float = 0.0
        timeout = httpx.Timeout(
            connect=0.1,  # 100ms connect
            read=config.timeout_ms / 1000.0 - 0.1,  # remainder for read
            write=5.0,
            pool=5.0,
        )
        self._client = httpx.Client(
            base_url=config.endpoint_url,
            timeout=timeout,
            http2=False,
        )
        self._system_prompt = (
            "You are a voice command router. The user speaks a command and you "
            "call tools to execute it. Call no_match if no tool fits the utterance. "
            "Call multiple tools in sequence for chained commands."
        )

    def _build_tools_array(self) -> list[dict[str, Any]]:
        """Build OpenAI-compatible tools array from registry."""
        tools: list[dict[str, Any]] = []
        for entry in self._registry.all_llm_visible():
            if entry.params_schema:
                tools.append(entry.params_schema)
        return tools

    def route(self, transcript: str) -> Plan | None:
        """Route a transcript through the LLM. Returns Plan or None on failure/no_match."""
        tools = self._build_tools_array()
        if not tools:
            return None

        body: dict[str, Any] = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": transcript},
            ],
            "tools": tools,
            "tool_choice": "required",
            "temperature": 0,
            "stream": False,
        }

        self._total_calls += 1
        logger.debug("LLM router request: %s", json.dumps(body, default=str))

        start = time.perf_counter()
        try:
            resp = self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
        except httpx.TimeoutException:
            self._total_timeouts += 1
            self._total_errors += 1
            logger.warning("LLM router timeout for transcript=%r", transcript)
            return None
        except httpx.ConnectError:
            self._total_errors += 1
            logger.warning(
                "LLM router connect error for transcript=%r", transcript,
            )
            return None
        except httpx.HTTPStatusError as e:
            self._total_errors += 1
            logger.warning(
                "LLM router HTTP %d for transcript=%r",
                e.response.status_code, transcript,
            )
            return None
        except httpx.HTTPError as e:
            self._total_errors += 1
            logger.warning("LLM router HTTP error: %s", e)
            return None

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._total_latency_ms += elapsed_ms

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            self._total_errors += 1
            logger.warning("LLM router: malformed JSON response")
            return None

        logger.debug("LLM router response: %s", json.dumps(data, default=str))
        plan = self._parse_response(data)
        step_count = len(plan.steps) if plan else 0
        logger.info(
            "llm_router latency_ms=%d steps=%d transcript=%r",
            int(elapsed_ms), step_count, transcript,
        )
        return plan

    def _parse_response(self, data: dict[str, Any]) -> Plan | None:
        """Parse OpenAI-style response into a Plan."""
        try:
            choices = data.get("choices", [])
            if not choices:
                return None
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                return None
        except (KeyError, IndexError, TypeError):
            logger.warning("LLM router: unexpected response structure")
            return None

        steps: list[ToolCall] = []
        for tc in tool_calls:
            if len(steps) >= self._config.max_plan_steps:
                break
            try:
                func_data = tc.get("function", tc)
                name = func_data["name"]
                args_raw = func_data.get("arguments", "{}")
                # LM Studio may return arguments as string or dict
                if isinstance(args_raw, str):
                    kwargs = json.loads(args_raw)
                elif isinstance(args_raw, dict):
                    kwargs = args_raw
                else:
                    kwargs = {}
                steps.append(ToolCall(name=name, kwargs=kwargs))
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning("LLM router: malformed tool_call entry, skipping")
                continue

        if not steps:
            return None

        # Check if first step is no_match — return None as signal
        if steps[0].name == "no_match":
            return None

        return Plan(steps=tuple(steps), raw_response=data)

    def warmup(self) -> bool:
        """Ping LM Studio /models endpoint. Returns True if reachable."""
        try:
            resp = self._client.get("/models")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    @property
    def metrics(self) -> dict[str, Any]:
        """Simple metrics snapshot."""
        avg = (
            self._total_latency_ms / max(self._total_calls - self._total_errors, 1)
        )
        return {
            "total_calls": self._total_calls,
            "total_timeouts": self._total_timeouts,
            "total_errors": self._total_errors,
            "avg_latency_ms": round(avg, 1),
        }

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
