"""LLM Router: routes voice commands to local LM Studio for tool-call planning."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from .config import LLMConfig
from .plan import Plan, ToolCall
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT_TEMPLATE = """You are an intelligent agent operating a Windows desktop on behalf
of a user who speaks commands out loud. Your job is to understand
what the user wants and accomplish it using the tools you have.

You are not a lookup table. When the user speaks, read the intent:
what are they trying to do, what does their screen likely look like
right now, which tool or chain of tools best accomplishes the goal?
Reason first, then act. The examples below show the style — they are
illustrative, not exhaustive. Generalize from them.

Principles
- Understand intent, not just words. "Search how to lose weight" is
  not one tool — it is a goal that requires focusing the browser,
  opening a tab, focusing the address bar, typing the query, and
  pressing enter.
- Prefer the most precise tool. If a dedicated verb exists (focus,
  minimize, maximize, close, close_window, open), use it. Fall back
  to `press` only for key combos without a dedicated verb.
- Use '{default_browser}' when the intent involves the user's
  default browser. "browser" on its own (as in "focus browser",
  "close browser", "minimize browser", "maximize browser") is the
  user's default browser — pass `target="{default_browser}"` to the
  window verb. Do NOT pass the literal word "browser".
- Web services are not installed apps. For names like facebook,
  gmail, email, youtube, reddit, twitter, linkedin, chatgpt →
  `open(target="https://<canonical-domain>")`. Do NOT pass the bare
  service name — the resolver only searches installed apps and will
  miss or mis-match.
- Emit tool calls in strict execution order. The dispatcher runs
  them linearly and cannot replan mid-flight.
- When "close" is ambiguous, prefer `close()` (tab). Only use
  `close_window()` when the user explicitly says window, app, or
  quit.
- Keypress fallback. A bare key name, or a simple verb that cleanly
  maps to a key, becomes `press(combo="<key>")`. E.g. send / submit
  → enter; cancel → escape; undo → ctrl+z; save → ctrl+s; find →
  ctrl+f; refresh → f5. Bare key names (enter, escape, tab, space,
  delete, backspace, f5) → press them directly.
- Hard bans — these chords sweep more windows than the user meant:
  * `press(combo="win+d")` / `press(combo="win+m")` — use `minimize()`
  * `press(combo="win+up")` — use `maximize()`
- Destructive bans. Never emit these; tool guards reject them anyway.
  `press` chords: shift+delete, win+r. `open` targets: cmd,
  powershell, regedit, diskmgmt, diskpart, format, cipher, gpedit,
  shutdown, taskkill, msconfig, rundll32.
- Call `no_match(reason)` only when the utterance genuinely cannot
  be executed — greetings, questions to you, or intents whose target
  cannot be inferred. Do NOT no_match a plausible keypress or a
  simple verb that maps to `press`.

Examples

User: "search how to lose weight"
Tools: focus(target="{default_browser}"),
       press(combo="ctrl+t"),
       press(combo="ctrl+l"),
       type(text="how to lose weight"),
       press(combo="enter")

User: "copy that and paste it in notepad"
Tools: press(combo="ctrl+c"),
       focus(target="notepad"),
       press(combo="ctrl+v")

User: "open spotify"
Tools: open(target="spotify")

User: "facebook"
Tools: open(target="https://facebook.com")

User: "email"
Tools: open(target="https://mail.google.com")

User: "close"
Tools: close()

User: "close window"
Tools: close_window()

User: "send"
Tools: press(combo="enter")

User: "escape"
Tools: press(combo="escape")

User: "focus browser"
Tools: focus(target="{default_browser}")
"""


class LLMRouter:
    """One-shot tool-call planner via local LM Studio."""

    def __init__(self, config: LLMConfig, registry: ToolRegistry) -> None:
        self._config = config
        self._registry = registry
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
        """Assemble the system prompt from the module-level template.

        ``default_browser`` is interpolated from config at construction time.
        Kept as a method (not an inline f-string) so tests can exercise the
        prompt surface without booting an LLMRouter.
        """
        return _SYSTEM_PROMPT_TEMPLATE.format(
            default_browser=self._config.default_browser,
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
