"""LLM Router: routes voice commands to local LM Studio for tool-call planning."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .config import LLMConfig
from .plan import Plan, ToolCall
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompt_template.txt"

_FALLBACK_TEMPLATE = """You are an intent matcher for a Windows voice assistant.

You do not plan multi-step actions. You do not choose keyboard
shortcuts. You pick exactly ONE tool from the list below whose
description best matches the user's spoken transcript.

The tools are user-curated commands and workflows. Each tool's
description starts with what it does and, after "Phrases:", lists
example utterances. Match the transcript to the tool whose phrases
or description most closely correspond. Paraphrases are fine — focus
on meaning, not exact wording.

Rules
- Pick the SINGLE best tool. Never chain multiple tool calls.
- If a workflow declares arguments, extract the corresponding
  substring from the transcript and pass it as the argument.
  Example: transcript "search how to feed a cat" with a workflow
  "search_web(query)" → search_web(query="how to feed a cat").
- Default browser, when referenced generically, is '{default_browser}'.
- If NO tool matches the intent — greetings, chit-chat, unrelated
  speech — call `no_match(reason)` with a short explanation. Do
  not force a match when none is appropriate.
- Never invent tool names. Only call tools that appear in the list.
"""


def _load_template() -> str:
    """Read the prompt template from the external file.

    Falls back to ``_FALLBACK_TEMPLATE`` if the file is missing, logging a
    warning so operators notice the degraded state.
    """
    try:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning(
            "prompt template not readable at %s — using fallback", _TEMPLATE_PATH
        )
        return _FALLBACK_TEMPLATE


class LLMRouter:
    """One-shot tool-call planner via local LM Studio."""

    def __init__(
        self, config: LLMConfig, registry: ToolRegistry, reload_lock: threading.Lock
    ) -> None:
        self._config = config
        self._registry = registry
        self._reload_lock = reload_lock
        # Metrics (simple counters, no external lib)
        self._total_calls: int = 0
        self._total_timeouts: int = 0
        self._total_errors: int = 0
        self._total_latency_ms: float = 0.0
        read_s = config.timeout_ms / 1000.0 - 0.1
        if read_s <= 0:
            raise ValueError(
                f"LLMConfig.timeout_ms={config.timeout_ms} too small; "
                f"must be > 100 to leave headroom for read timeout"
            )
        timeout = httpx.Timeout(
            connect=0.1,  # 100ms connect
            read=read_s,  # remainder for read
            write=5.0,
            pool=5.0,
        )
        self._client = httpx.Client(
            base_url=config.endpoint_url,
            timeout=timeout,
            http2=False,
        )
        self._system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Assemble the system prompt from the external template file.

        ``default_browser`` is interpolated from config.  Re-reads the file
        each time so ``reload_prompt()`` picks up on-disk changes.
        Falls back to ``_FALLBACK_TEMPLATE`` if the template has stray placeholders.
        """
        template = _load_template()
        try:
            return template.format(
                default_browser=self._config.default_browser,
            )
        except (KeyError, ValueError, IndexError) as exc:
            logger.warning(
                "prompt template has invalid placeholders (%s) — using fallback",
                exc,
            )
            return _FALLBACK_TEMPLATE.format(
                default_browser=self._config.default_browser,
            )

    def reload_prompt(self) -> None:
        """Re-read the template file and rebuild the cached system prompt.

        Called under reload_lock by the web layer after a template save.
        """
        self._system_prompt = self._build_system_prompt()

    def composed_prompt_data(self) -> dict[str, Any]:
        """Return structured data for the prompt inspector UI.

        Returns dict with keys:
        - template_raw: str — raw template text with placeholders
        - template_resolved: str — template with placeholders filled
        - placeholders: dict[str, str] — placeholder name → resolved value
        - tools: list[dict] — the tools JSON array as sent to LLM
        - tools_count: int
        - model_id: str
        - endpoint_url: str
        """
        with self._reload_lock:
            template_raw = _load_template()
            template_resolved = self._system_prompt
            tools = self._build_tools_array()
        return {
            "template_raw": template_raw,
            "template_resolved": template_resolved,
            "placeholders": {"default_browser": self._config.default_browser},
            "tools": tools,
            "tools_count": len(tools),
            "model_id": self._config.model_id,
            "endpoint_url": self._config.endpoint_url,
        }

    def _build_tools_array(self) -> list[dict[str, Any]]:
        """Build OpenAI-compatible tools array from registry.

        Only tools marked ``enabled=true`` + ``llm_only=true`` in their sidecar
        TOML reach the LLM. Perception functions and disabled semantic verbs
        (close, minimize, maximize, last, etc.) are filtered at this boundary.
        """
        tools: list[dict[str, Any]] = []
        for entry in self._registry.all_llm_visible():
            if entry.params_schema:
                tools.append(entry.params_schema)
        return tools

    def route(self, transcript: str, env_context: str | None = None) -> Plan | None:
        """Route a transcript through the LLM. Returns Plan or None on failure/no_match.

        When ``env_context`` is provided, it is inserted as an additional user
        message before the transcript — gives the model ground truth about the
        focused window and visible windows without burning an extra LLM turn.
        Called the second time a transcript misses; routes single-shot with
        richer context so the follow-up doesn't need a multi-turn loop.
        """
        with self._reload_lock:
            tools = self._build_tools_array()
        if not tools:
            return None

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
        ]
        if env_context:
            messages.append({"role": "user", "content": env_context})
        messages.append({"role": "user", "content": transcript})

        body: dict[str, Any] = {
            "model": self._config.model_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "temperature": 0,
            "stream": False,
            # Reasoning models (Gemma 4 26B-A4B, DeepSeek-R1, etc.) would otherwise
            # burn the token budget on <think> content before emitting the tool call,
            # blowing past timeout_ms. Non-reasoning models ignore this per OpenAI spec.
            "reasoning_effort": "none",
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
                "LLM router connect error for transcript=%r",
                transcript,
            )
            return None
        except httpx.HTTPStatusError as e:
            self._total_errors += 1
            logger.warning(
                "LLM router HTTP %d for transcript=%r",
                e.response.status_code,
                transcript,
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
            logger.warning("LLM router: malformed JSON response body=%s", resp.text[:200])
            return None

        logger.debug("LLM router response: %s", json.dumps(data, default=str))
        plan = self._parse_response(data)
        step_count = len(plan.steps) if plan else 0
        logger.info(
            "llm_router latency_ms=%d steps=%d transcript=%r",
            int(elapsed_ms),
            step_count,
            transcript,
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
        except (KeyError, IndexError, TypeError, AttributeError):
            logger.warning("LLM router: unexpected response structure")
            return None

        # Snapshot valid tool names under reload_lock so we can reject
        # hallucinated names (e.g. "find_focused_window_title_and_process_name",
        # "call_none") before they reach the Dispatcher.
        with self._reload_lock:
            valid_names = {e.name for e in self._registry.all_llm_visible()}

        steps: list[ToolCall] = []
        for i, tc in enumerate(tool_calls):
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
                if name not in valid_names:
                    logger.warning(
                        "llm_router: dropping hallucinated tool name %r at step %d",
                        name,
                        i,
                    )
                    continue
                steps.append(ToolCall(name=name, kwargs=kwargs))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("llm_router: skipping malformed tool_call at step %d: %s", i, exc)
                continue

        if not steps:
            return None

        # Check if first step is no_match — return None as signal
        if steps[0].name == "no_match":
            return None

        return Plan(steps=tuple(steps), raw_response=data)

    def warmup(self) -> bool:
        """Seed LM Studio's prefix KV cache by posting a real chat-completion request.

        Sends the exact same system prompt and tools array that real calls use,
        but with a minimal ``"__warmup__"`` user transcript, ``tool_choice="none"``
        (so the model is not forced into a useless tool invocation), and
        ``max_tokens=1`` to abort generation immediately after the prefix prefill.
        This ensures the ``system + tools`` prefix is in the KV cache before the
        first real user utterance arrives.

        Uses ``warmup_timeout_ms`` (not ``timeout_ms``) as the HTTP deadline,
        since this is a one-shot startup cost — not a per-call latency budget.

        Returns True on success, False on any error (best-effort; daemon continues).
        Does NOT mutate any router state visible to subsequent ``route()`` calls —
        the stateless-per-call contract is preserved.
        """
        with self._reload_lock:
            tools = self._build_tools_array()
        if not tools:
            logger.debug("LLM router warmup skipped: no tools in registry")
            return False

        body: dict[str, Any] = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": "__warmup__"},
            ],
            "tools": tools,
            # "none" prevents the model from emitting a tool call for a fake
            # transcript, while still prefilling the system+tools prefix in the
            # KV cache. Fall back to "auto" if a backend rejects "none".
            "tool_choice": "none",
            "temperature": 0,
            "stream": False,
            # max_tokens=1: abort generation after the prefix prefill so LM
            # Studio doesn't waste compute producing a full response.
            # Note: some model adapters may reject max_tokens=1; use 2 if needed.
            "max_tokens": 1,
            # Suppress reasoning tokens on reasoning models; no-op on others.
            "reasoning_effort": "none",
        }

        warmup_timeout = httpx.Timeout(
            connect=0.1,
            read=self._config.warmup_timeout_ms / 1000.0 - 0.1,
            write=5.0,
            pool=5.0,
        )

        start = time.perf_counter()
        try:
            resp = self._client.post(
                "/chat/completions",
                json=body,
                timeout=warmup_timeout,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.warning("LLM router warmup timed out: %s", exc)
            return False
        except httpx.ConnectError as exc:
            logger.warning("LLM router warmup connect error: %s", exc)
            return False
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "LLM router warmup HTTP %d: %s",
                exc.response.status_code,
                exc,
            )
            return False
        except httpx.HTTPError as exc:
            logger.warning("LLM router warmup HTTP error: %s", exc)
            return False

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "LLM router warmup succeeded (latency_ms=%d, model=%s)",
            elapsed_ms,
            self._config.model_id,
        )
        return True

    @property
    def metrics(self) -> dict[str, Any]:
        """Simple metrics snapshot."""
        avg = self._total_latency_ms / max(self._total_calls - self._total_errors, 1)
        return {
            "total_calls": self._total_calls,
            "total_timeouts": self._total_timeouts,
            "total_errors": self._total_errors,
            "avg_latency_ms": round(avg, 1),
        }

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
