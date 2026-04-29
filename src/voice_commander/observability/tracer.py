"""Tracer + Span context manager for the observability pipeline.

Single-process, contextvars-based. ``run`` / ``span`` are context managers
that auto-parent to the current span and write on ``__exit__``.

The tracer NEVER raises out of instrumentation paths — all internal
errors are logged and swallowed so the daemon keeps running even if
observability is misbehaving.
"""

from __future__ import annotations

import contextlib
import logging
import time
import traceback as tb_mod
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from voice_commander.event_bus import EventBus
from voice_commander.observability.protocols import StoreProtocol
from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
)

logger = logging.getLogger(__name__)

_current_run_id: ContextVar[str] = ContextVar("vc_obs_run_id", default="")
_current_span_id: ContextVar[str] = ContextVar("vc_obs_span_id", default="")
_current_span_depth: ContextVar[int] = ContextVar("vc_obs_span_depth", default=0)
_run_has_error: ContextVar[bool] = ContextVar("vc_obs_run_has_error", default=False)
_current_run_handle: ContextVar[RunHandle | None] = ContextVar("vc_obs_run_handle", default=None)


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class RunHandle:
    run_id: str
    started_at: float = 0.0
    _override_status: str = ""
    _step_count: int = 0

    def __post_init__(self) -> None:
        # List of (depth, error_category, error_msg) tuples for completed
        # error spans during the run. Used by write_run_end to roll up
        # the run-level error_category/error_summary from the deepest
        # span with a category set.
        self._error_spans: list[tuple[int, str, str | None]] = []

    def set_status(self, status: str) -> None:
        """Override the run's final status (e.g. 'miss') from outside the tracer."""
        self._override_status = status

    def increment_step(self) -> None:
        """Increment the tool-call step counter for this run."""
        self._step_count += 1

    def record_error_span(self, depth: int, error_category: str, error_msg: str | None) -> None:
        """Record a span that ended in error so the run-level rollup can use it."""
        self._error_spans.append((depth, error_category, error_msg))

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def deepest_error(self) -> tuple[str, str | None] | None:
        """Return (error_category, error_msg) for the deepest categorized error span."""
        if not self._error_spans:
            return None
        # Highest depth wins; ties broken by insertion order (earliest seen).
        depth, cat, msg = max(self._error_spans, key=lambda t: t[0])
        return cat, msg


class Span:
    """One-shot mutable span row. Use via ``Tracer.span(...)`` context manager."""

    def __init__(
        self,
        *,
        span_id: str,
        run_id: str,
        parent_span_id: str | None,
        type: str,
        name: str,
        attrs: dict[str, Any],
    ) -> None:
        self.span_id = span_id
        self.run_id = run_id
        self.parent_span_id = parent_span_id
        self.type = type
        self.name = name
        self.attrs: dict[str, Any] = dict(attrs)
        self.output: Any = None
        self.error_type: str | None = None
        self.error_msg: str | None = None
        self.traceback: str | None = None
        self.error_category: str | None = None
        self.started_at = time.time()
        self._start_mono = time.monotonic()
        self.ended_at: float = 0.0
        self.duration_ms: int = 0
        self.status: str = "ok"

    def set_output(self, value: Any) -> None:
        self.output = value

    def set_attr(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def set_error_category(self, category: str) -> None:
        """Set the 4-bucket error taxonomy category on this span."""
        self.error_category = category

    def mark_skipped(self) -> None:
        self.status = "skipped"


class _NullSpan:
    """Returned when tracer is disabled. Silently absorbs mutations."""

    span_id = ""
    run_id = ""
    parent_span_id = None
    type = ""
    name = ""
    status = "ok"
    output: Any = None
    error_msg: str | None = None
    error_category: str | None = None

    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {}

    def set_output(self, value: Any) -> None: ...
    def set_attr(self, key: str, value: Any) -> None: ...
    def set_error_category(self, category: str) -> None: ...
    def mark_skipped(self) -> None: ...


_NULL_SPAN = _NullSpan()


class Tracer:
    """Singleton-style tracer. One instance per daemon."""

    def __init__(
        self,
        *,
        store: StoreProtocol,
        bus: EventBus,
        enabled: bool,
        slow_run_ms: int = 2000,
        daemon_pid: int = 0,
    ) -> None:
        self._store = store
        self._bus = bus
        self._enabled = enabled
        self._slow_run_ms = slow_run_ms
        self._daemon_pid = daemon_pid

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # run lifecycle
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def run(self, transcript: str) -> Iterator[RunHandle]:
        """Bracket a per-utterance run. Auto-rolls up status from spans."""
        if not self._enabled:
            yield RunHandle(run_id="", started_at=0.0)
            return

        run_id = _short_id()
        token = _current_run_id.set(run_id)
        err_token = _run_has_error.set(False)
        handle = RunHandle(run_id=run_id, started_at=0.0)
        handle_token = _current_run_handle.set(handle)
        started_at = time.time()
        start_mono = time.monotonic()

        try:
            self._store.write_run_start(
                RunRecord(
                    run_id=run_id,
                    started_at=started_at,
                    transcript=transcript,
                    daemon_pid=self._daemon_pid,
                )
            )
            self._publish_safe(
                "trace.run_started",
                {"run_id": run_id, "transcript": transcript, "started_at": started_at},
            )
        except Exception:
            logger.exception("tracer: failed to start run")

        handle.started_at = started_at
        # Open the synthetic root "run" span so all children parent under it.
        with self.span("run", name="run", transcript=transcript) as root:
            captured_exc: BaseException | None = None
            try:
                yield handle
            except BaseException as exc:
                root.status = "error"
                captured_exc = exc
                raise
            finally:
                # End the run row; status = "error" if exception propagated OR
                # any child span errored (even if the exception was caught upstream).
                # Use handle._override_status to allow callers (e.g. daemon miss paths)
                # to mark the run status without raising.
                duration_ms = int((time.monotonic() - start_mono) * 1000)
                status = (
                    root.status
                    if root.status == "error"
                    else ("error" if _run_has_error.get() else (handle._override_status or "ok"))
                )
                # Capture error_msg directly from the caught exception; root.error_msg
                # is populated by the span() context manager's except block which runs
                # after this finally block, so it would always be None here.
                error_msg = str(captured_exc)[:512] if captured_exc is not None else None
                # Roll up error_category/error_summary from the deepest
                # categorized span (B-C1).
                deepest = handle.deepest_error
                run_error_category = deepest[0] if deepest is not None else None
                run_error_summary = deepest[1] if deepest is not None else None
                ended_at = time.time()
                try:
                    self._store.write_run_end(
                        RunUpdate(
                            run_id=run_id,
                            ended_at=ended_at,
                            status=status,
                            error_msg=error_msg,
                            duration_ms=duration_ms,
                            error_category=run_error_category,
                            error_summary=run_error_summary,
                        )
                    )
                    self._publish_safe(
                        "trace.run_completed",
                        {
                            "run_id": run_id,
                            "status": status,
                            "duration_ms": duration_ms,
                        },
                    )
                    # B-C3: emit run.appended for SPA live-tail SSE consumers.
                    self._publish_safe(
                        "run.appended",
                        {
                            "run_id": run_id,
                            "transcript": transcript,
                            "status": status,
                            "started_at": started_at,
                            "ended_at": ended_at,
                            "duration_ms": duration_ms,
                            "error_category": run_error_category,
                            "error_summary": run_error_summary,
                        },
                    )
                except Exception:
                    logger.exception("tracer: failed to end run")
                steps = handle.step_count
                logger.info(
                    "run %s %-5s %5d ms  %d step(s)  %r",
                    run_id[:8],
                    status,
                    duration_ms,
                    steps,
                    transcript[:80],
                )
                _current_run_id.reset(token)
                _run_has_error.reset(err_token)
                _current_run_handle.reset(handle_token)

    # ------------------------------------------------------------------
    # span context manager
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def span(
        self,
        span_type: str,
        *,
        name: str | None = None,
        **attrs: Any,
    ) -> Iterator[Span | _NullSpan]:
        if not self._enabled or _current_run_id.get() == "":
            yield _NULL_SPAN
            return

        span_id = _short_id()
        run_id = _current_run_id.get()
        parent = _current_span_id.get() or None
        depth = _current_span_depth.get()
        s = Span(
            span_id=span_id,
            run_id=run_id,
            parent_span_id=parent,
            type=span_type,
            name=name or span_type,
            attrs=attrs,
        )
        token = _current_span_id.set(span_id)
        depth_token = _current_span_depth.set(depth + 1)
        try:
            self._publish_safe(
                "trace.span_started",
                {
                    "run_id": run_id,
                    "span_id": span_id,
                    "parent_span_id": parent,
                    "type": span_type,
                    "name": s.name,
                    "started_at": s.started_at,
                    "attrs": s.attrs,
                },
            )
        except Exception:
            logger.exception("tracer: failed to publish span_started")

        try:
            yield s
        except BaseException as exc:
            s.status = "error"
            s.error_type = exc.__class__.__name__
            s.error_msg = str(exc)[:512]
            s.traceback = "".join(tb_mod.format_tb(exc.__traceback__)[-10:])
            _run_has_error.set(True)
            raise
        finally:
            s.ended_at = time.time()
            s.duration_ms = int((time.monotonic() - s._start_mono) * 1000)
            try:
                self._store.write_span(
                    SpanRecord(
                        span_id=s.span_id,
                        run_id=s.run_id,
                        parent_span_id=s.parent_span_id,
                        type=s.type,
                        name=s.name,
                        started_at=s.started_at,
                        ended_at=s.ended_at,
                        duration_ms=s.duration_ms,
                        status=s.status,
                        attrs=s.attrs,
                        output=s.output,
                        error_type=s.error_type,
                        error_msg=s.error_msg,
                        traceback=s.traceback,
                        error_category=s.error_category,
                    )
                )
                self._publish_safe(
                    "trace.span_ended",
                    {
                        "run_id": s.run_id,
                        "span_id": s.span_id,
                        "type": s.type,
                        "name": s.name,
                        "status": s.status,
                        "duration_ms": s.duration_ms,
                        "output": s.output,
                        "error_msg": s.error_msg,
                    },
                )
                # Count tool_call spans per run for the summary line
                if span_type == "tool_call" and s.run_id:
                    _h = _current_run_handle.get()
                    if _h is not None:
                        _h.increment_step()
                # Record error spans for run-level rollup of error_category.
                if s.error_category and s.run_id:
                    _h = _current_run_handle.get()
                    if _h is not None:
                        _h.record_error_span(depth, s.error_category, s.error_msg)
            except Exception:
                logger.exception("tracer: failed to write span")
            _current_span_id.reset(token)
            _current_span_depth.reset(depth_token)

    def update_transcript(self, run_id: str, transcript: str) -> None:
        if not self._enabled or not run_id:
            return
        try:
            self._store.write_run_transcript_update(run_id, transcript)
        except Exception:
            logger.exception("tracer: failed to update transcript")

    def _publish_safe(self, event: str, data: dict[str, Any]) -> None:
        try:
            self._bus.publish(event, data)
        except Exception:
            logger.exception("tracer: failed to publish %s", event)
