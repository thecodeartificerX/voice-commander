"""Plan and ToolCall dataclasses for LLM router output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation within a plan."""

    name: str
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class Plan:
    """Ordered sequence of tool calls produced by the LLM router.

    Attributes:
        strict: When True (default), the dispatcher halts on the first failed
            step. When False, it continues executing remaining steps and records
            only the first failure in PlanOutcome.
    """

    steps: tuple[ToolCall, ...]
    raw_response: dict[str, Any]
    strict: bool = True  # True → halt on first error; False → continue-on-error


PlanStatus = Literal["ok", "error", "miss"]


@dataclass(frozen=True)
class PlanOutcome:
    """Final outcome of a single voice-command cycle.

    Published as the ``plan_outcome`` SSE event by ``Dispatcher.run_plan``
    (status=ok|error) and ``StreamingDaemon._process_utterance`` (status=miss).
    Consumed by ``voice_sprite`` to synthesize a one-line HUD entry.
    """

    transcript: str
    steps: tuple[ToolCall, ...]
    status: PlanStatus
    failed_step_index: int | None
    error_msg: str | None
    duration_ms: int

    def to_event_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "steps": [{"name": s.name, "kwargs": dict(s.kwargs)} for s in self.steps],
            "status": self.status,
            "failed_step_index": self.failed_step_index,
            "error_msg": self.error_msg,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_event_dict(cls, d: dict[str, Any]) -> PlanOutcome:
        """Parse a ``PlanOutcome`` from a raw SSE event dict.

        Raises:
            KeyError: if ``transcript``, ``status``, or a step's ``name`` is absent.
            TypeError: if ``steps`` is not iterable or a step's ``kwargs`` is not
                dict-constructible.
            ValueError: if ``duration_ms`` cannot be converted to ``int``.
        """
        steps = tuple(
            ToolCall(name=s["name"], kwargs=dict(s.get("kwargs", {}))) for s in d.get("steps", [])
        )
        return cls(
            transcript=d["transcript"],
            steps=steps,
            status=d["status"],
            failed_step_index=d.get("failed_step_index"),
            error_msg=d.get("error_msg"),
            duration_ms=int(d.get("duration_ms", 0)),
        )

    @classmethod
    def try_from_event_dict(cls, d: dict[str, Any]) -> PlanOutcome | None:
        """Return a PlanOutcome parsed from *d*, or ``None`` on any parse failure.

        Catches ``KeyError``, ``ValueError``, and ``TypeError`` — the three
        exceptions ``from_event_dict`` can raise on malformed input.  Callers
        that cannot guarantee a well-formed dict should prefer this over a bare
        ``try/except``.

        Note: parse failures are swallowed silently.  Callers that need
        diagnostic logging should log at the call site when ``None`` is returned.
        """
        try:
            return cls.from_event_dict(d)
        except (KeyError, ValueError, TypeError):
            return None
