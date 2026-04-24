"""Agentic fallback router — step-observe-decide loop with perception tools.

Wraps the one-shot LLMRouter with a bounded step loop. When a voice command
misses or errors, AgenticRouter enters the loop: observe environment → choose
one action → execute → check outcome → repeat or terminate.

Termination conditions:
- LLM emits ``done(success)`` tool call
- LLM emits ``ask_user(question, options)`` tool call
- Step counter reaches ``max_steps`` (miss chime)
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from .config import LLMConfig
from .plan import Plan, PlanOutcome, PlanStatus, ToolCall
from .registry import ToolRegistry

if TYPE_CHECKING:
    from .dispatcher import Dispatcher
    from .event_bus import EventBus

logger = logging.getLogger(__name__)


_AGENTIC_SYSTEM_PROMPT = """You are a one-step-at-a-time computer operator. Each turn you
receive the current environment state (focused window, visible windows)
and prior step history. Choose exactly ONE tool call per turn.

You have these tools available:
- All voice command tools (focus, type, open, close, press, wait,
  click, scroll, minimize, maximize, last)
- Perception tools (get_focused_window, list_windows, get_clipboard, list_processes)
- done(success, summary) — call when your goal is achieved or impossible
- ask_user(question, options) — call when ambiguity cannot be resolved from context

Hard constraints:
- Do NOT use close_window (Alt+F4 is restricted in agentic mode)
- Do NOT open terminals (cmd, powershell, etc.)
- Do NOT delete files or perform destructive operations
- Choose exactly ONE tool call per turn — no multi-step plans

Strategy:
1. First, observe: use perception tools to understand the current state
2. Then, act: choose the single best action to make progress
3. After acting, observe again to verify the result
4. Call done(success=true) when the goal is achieved
5. Call done(success=false) when the goal is impossible
6. Call ask_user when you need clarification (e.g. multiple matching windows)

The user's default browser is '{default_browser}'.

Examples:

Goal: "search how to feed a cat"
Step 1: get_focused_window() → see what's focused
Step 2: focus(target="{default_browser}") → focus browser
Step 3: press(combo="ctrl+t") → new tab
Step 4: press(combo="ctrl+l") → focus address bar
Step 5: type(text="how to feed a cat") → type query
Step 6: press(combo="enter"), then done(success=true)

Goal: "focus terminal" (3 terminals open)
Step 1: list_windows() → see all windows
Step 2: ask_user(question="Which terminal?",
  options="1. VS Code Terminal, 2. Windows Terminal, 3. Git Bash")
"""


class AgenticRouter:
    """Agentic fallback loop — step-observe-decide with perception tools.

    Wraps the one-shot LLMRouter. When a command misses or errors,
    enters a bounded loop: observe environment → choose one action →
    execute → check outcome → repeat or terminate.

    Termination conditions:
    - LLM emits ``done(success)`` tool call
    - LLM emits ``ask_user(question, options)`` tool call
    - Step counter reaches ``max_steps`` (miss chime)
    """

    def __init__(
        self,
        config: LLMConfig,
        registry: ToolRegistry,
        dispatcher: Dispatcher,
        event_bus: EventBus | None = None,
        max_steps: int = 6,
    ) -> None:
        self._config = config
        self._registry = registry
        self._dispatcher = dispatcher
        self._event_bus = event_bus
        self._max_steps = max_steps

        read_s = config.timeout_ms / 1000.0 - 0.1
        if read_s <= 0:
            raise ValueError(
                f"LLMConfig.timeout_ms={config.timeout_ms} too small; "
                f"must be > 100 to leave headroom for read timeout"
            )
        timeout = httpx.Timeout(
            connect=0.1,
            read=read_s,
            write=5.0,
            pool=5.0,
        )
        self._client = httpx.Client(
            base_url=config.endpoint_url,
            timeout=timeout,
            http2=False,
        )

        self._system_prompt = _AGENTIC_SYSTEM_PROMPT.format(
            default_browser=config.default_browser,
        )

        # Metrics counters
        self._total_runs: int = 0
        self._total_steps: int = 0
        self._total_cap_hits: int = 0
        self._total_errors: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_tools_array(self) -> list[dict[str, Any]]:
        """Build OpenAI-compatible tools array from registry.

        Includes all llm_visible tools except those that are restricted in
        agentic mode or that have no params schema.
        """
        excluded = {"close_window", "no_match", "mute", "summon_commander"}
        tools: list[dict[str, Any]] = []
        for entry in self._registry.all_llm_visible():
            if entry.name not in excluded and entry.params_schema:
                tools.append(entry.params_schema)
        return tools

    def _gather_context(self, transcript: str) -> str:
        """Build environment context string for the LLM.

        Always queries focused window and visible windows. Only fetches
        clipboard content when the transcript contains clipboard-related
        keywords to avoid unnecessary IPC overhead.
        """
        from .tools.perception import get_focused_window, list_windows

        focused = get_focused_window()
        windows = list_windows()

        parts = [
            f"Focused window: {json.dumps(focused)}",
            f"Visible windows ({len(windows)}): {json.dumps(windows)}",
        ]

        clipboard_keywords = {"clipboard", "paste", "copied", "copy"}
        if any(kw in transcript.lower() for kw in clipboard_keywords):
            from .tools.perception import get_clipboard

            clip = get_clipboard()
            parts.append(f"Clipboard: {json.dumps(clip)}")

        return "\n".join(parts)

    def _publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Publish an event to the event bus if one is configured."""
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)

    def _parse_single_tool_call(self, data: dict[str, Any]) -> ToolCall | None:
        """Extract the first tool call from an OpenAI-style LLM response dict.

        Returns ``None`` on any structural or JSON parse failure.
        """
        try:
            choices = data.get("choices", [])
            if not choices:
                return None
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                return None
            tc = tool_calls[0]
            func_data = tc.get("function", tc)
            name = func_data["name"]
            args_raw = func_data.get("arguments", "{}")
            if isinstance(args_raw, str):
                kwargs = json.loads(args_raw)
            elif isinstance(args_raw, dict):
                kwargs = args_raw
            else:
                kwargs = {}
            return ToolCall(name=name, kwargs=kwargs)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("agentic_router: malformed tool_call: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(
        self,
        transcript: str,
        failed_plan: PlanOutcome | None = None,
    ) -> PlanOutcome:
        """Execute the agentic step-observe-decide loop for a transcript.

        Parameters
        ----------
        transcript:
            The user's spoken command text.
        failed_plan:
            The outcome of an earlier one-shot routing attempt, if any.
            Included in the initial user message so the LLM knows what
            was tried before entering the agentic loop.

        Returns
        -------
        PlanOutcome
            Final outcome with status ``"ok"``, ``"miss"``, or ``"error"``.
        """
        self._total_runs += 1
        start_s = time.perf_counter()

        # Build initial message list
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
        ]

        env_context = self._gather_context(transcript)

        user_content = f"Goal: {transcript}\n\nCurrent environment:\n{env_context}"
        if failed_plan is not None:
            user_content += (
                f"\n\nPrevious attempt failed: {failed_plan.error_msg or 'no match'}"
            )

        messages.append({"role": "user", "content": user_content})

        tools = self._build_tools_array()
        if not tools:
            return PlanOutcome(
                transcript=transcript,
                steps=(),
                status="error",
                failed_step_index=None,
                error_msg="no tools available",
                duration_ms=int((time.perf_counter() - start_s) * 1000),
            )

        steps_taken: list[ToolCall] = []
        perception_tools = {"get_focused_window", "list_windows", "get_clipboard", "list_processes"}

        for step_num in range(1, self._max_steps + 1):
            self._total_steps += 1

            body: dict[str, Any] = {
                "model": self._config.model_id,
                "messages": messages,
                "tools": tools,
                "tool_choice": "required",
                "temperature": 0,
                "stream": False,
                "reasoning_effort": "none",
            }

            try:
                resp = self._client.post("/chat/completions", json=body)
                resp.raise_for_status()
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.HTTPStatusError,
                httpx.HTTPError,
            ) as e:
                self._total_errors += 1
                logger.warning("Agentic step %d failed: %s", step_num, e)
                return PlanOutcome(
                    transcript=transcript,
                    steps=tuple(steps_taken),
                    status="error",
                    failed_step_index=len(steps_taken) if steps_taken else None,
                    error_msg=f"LLM error at step {step_num}: {e}",
                    duration_ms=int((time.perf_counter() - start_s) * 1000),
                )

            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                self._total_errors += 1
                return PlanOutcome(
                    transcript=transcript,
                    steps=tuple(steps_taken),
                    status="error",
                    failed_step_index=len(steps_taken) if steps_taken else None,
                    error_msg=f"malformed JSON at step {step_num}",
                    duration_ms=int((time.perf_counter() - start_s) * 1000),
                )

            tool_call = self._parse_single_tool_call(data)
            if tool_call is None:
                self._total_errors += 1
                return PlanOutcome(
                    transcript=transcript,
                    steps=tuple(steps_taken),
                    status="error",
                    failed_step_index=len(steps_taken) if steps_taken else None,
                    error_msg=f"no tool call in response at step {step_num}",
                    duration_ms=int((time.perf_counter() - start_s) * 1000),
                )

            steps_taken.append(tool_call)
            self._publish(
                "agentic_step",
                {"step": step_num, "tool": tool_call.name, "kwargs": tool_call.kwargs},
            )
            logger.info(
                "agentic step %d/%d: %s(%s)",
                step_num,
                self._max_steps,
                tool_call.name,
                tool_call.kwargs,
            )

            # ---- Terminal: done() ----------------------------------------
            if tool_call.name == "done":
                success = tool_call.kwargs.get("success", True)
                status: PlanStatus = "ok" if success else "miss"
                self._publish("agentic_done", {"steps_taken": step_num, "status": status})
                return PlanOutcome(
                    transcript=transcript,
                    steps=tuple(steps_taken),
                    status=status,
                    failed_step_index=None,
                    error_msg=None,
                    duration_ms=int((time.perf_counter() - start_s) * 1000),
                )

            # ---- Terminal: ask_user() -------------------------------------
            if tool_call.name == "ask_user":
                question = tool_call.kwargs.get("question", "")
                options = tool_call.kwargs.get("options", "")
                self._publish("ask_user", {"question": question, "options": options})
                self._publish("agentic_done", {"steps_taken": step_num, "status": "ok"})
                return PlanOutcome(
                    transcript=transcript,
                    steps=tuple(steps_taken),
                    status="ok",
                    failed_step_index=None,
                    error_msg=None,
                    duration_ms=int((time.perf_counter() - start_s) * 1000),
                )

            # ---- Perception tool (observation, no dispatch) ---------------
            if tool_call.name in perception_tools:
                tool_entry = self._registry.by_name(tool_call.name)
                result_data = "{}"
                if tool_entry is not None:
                    try:
                        result = tool_entry.func(**tool_call.kwargs)  # type: ignore[func-returns-value]
                        result_data = json.dumps(result, default=str)
                    except Exception as exc:
                        result_data = json.dumps({"error": str(exc)})

                tc_id = f"call_{step_num}"
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tc_id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.name,
                                    "arguments": json.dumps(tool_call.kwargs),
                                },
                            }
                        ],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result_data,
                    }
                )
                continue

            # ---- Action tool — dispatch via Dispatcher -------------------
            single_plan = Plan(steps=(tool_call,), raw_response={}, strict=True)
            step_outcome = self._dispatcher.run_plan(transcript, single_plan, self._registry)

            tc_id = f"call_{step_num}"
            if step_outcome is not None and step_outcome.status == "ok":
                result_msg = f"Tool '{tool_call.name}' executed successfully."
            elif step_outcome is not None:
                result_msg = (
                    f"Tool '{tool_call.name}' failed: "
                    f"{step_outcome.error_msg or 'unknown error'}"
                )
            else:
                result_msg = f"Tool '{tool_call.name}' executed (no outcome returned)."

            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": json.dumps(tool_call.kwargs),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result_msg,
                }
            )

            # Refresh environment state for the next LLM turn
            fresh_env = self._gather_context(transcript)
            messages.append(
                {
                    "role": "user",
                    "content": f"Environment after step {step_num}:\n{fresh_env}",
                }
            )

        # ---- Step cap hit ------------------------------------------------
        self._total_cap_hits += 1
        self._publish("agentic_done", {"steps_taken": self._max_steps, "status": "miss"})
        logger.warning(
            "Agentic loop hit step cap (%d) for transcript=%r",
            self._max_steps,
            transcript,
        )
        return PlanOutcome(
            transcript=transcript,
            steps=tuple(steps_taken),
            status="miss",
            failed_step_index=None,
            error_msg=f"step cap ({self._max_steps}) reached",
            duration_ms=int((time.perf_counter() - start_s) * 1000),
        )

    # ------------------------------------------------------------------
    # Properties and lifecycle
    # ------------------------------------------------------------------

    @property
    def metrics(self) -> dict[str, Any]:
        """Simple metrics snapshot for health-check / diagnostics."""
        return {
            "total_runs": self._total_runs,
            "total_steps": self._total_steps,
            "total_cap_hits": self._total_cap_hits,
            "total_errors": self._total_errors,
            "avg_steps_per_run": round(
                self._total_steps / max(self._total_runs, 1), 1
            ),
        }

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
