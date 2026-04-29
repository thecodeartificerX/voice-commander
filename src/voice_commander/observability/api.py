"""FastAPI router for /api/runs/*."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from voice_commander.observability.store import Store

logger = logging.getLogger(__name__)


def _build_export_md(run: dict[str, Any], spans: list[dict[str, Any]]) -> str:
    """Build the copy-as-prompt markdown payload (matches client-side markdownExport.ts)."""
    import json as _json
    from datetime import datetime

    run_id = run["run_id"]
    short_id = run_id[:6]
    started_at_s: float = run["started_at"]
    started_iso = datetime.fromtimestamp(started_at_s, tz=UTC).isoformat()
    dur_ms: int | None = run.get("duration_ms")
    dur_str = (
        f"{dur_ms}ms"
        if dur_ms is not None and dur_ms < 1000
        else (f"{dur_ms / 1000:.1f}s" if dur_ms is not None else "—")
    )
    status = run["status"]
    transcript = run["transcript"]
    error_category = run.get("error_category")
    error_summary = run.get("error_summary") or run.get("error_msg")
    daemon_pid = run["daemon_pid"]
    schema_version = run.get("schema_version", 1)

    lines: list[str] = []
    lines.append(f"# Voice Commander run {short_id} — {status}\n")
    lines.append(f'**Transcript:** "{transcript}"')
    lines.append(f"**Started:** {started_iso}")
    lines.append(f"**Duration:** {dur_str}")
    lines.append(f"**Status:** {status}")
    if error_category:
        lines.append(f"**Error category:** {error_category}")
    if error_summary:
        lines.append(f"**Error summary:** {error_summary}")
    lines.append(f"**Daemon PID:** {daemon_pid}")
    lines.append(f"**Schema:** runs.db v{schema_version}")

    # Find LLM plan output
    llm_span = next((s for s in spans if s["type"] == "llm_call"), None)
    if llm_span and llm_span.get("output") is not None:
        lines.append("\n## Plan returned by LLM\n")
        lines.append("```json")
        lines.append(_json.dumps(llm_span["output"], indent=2))
        lines.append("```")

    # Span tree
    lines.append("\n## Span tree\n")

    def render_spans(parent_id: str | None, depth: int) -> list[str]:
        children = [s for s in spans if s["parent_span_id"] == parent_id]
        out: list[str] = []
        for s in children:
            indent = "  " * depth
            st = "✓" if s["status"] == "ok" else ("✗" if s["status"] == "error" else s["status"])
            dur_s = s.get("duration_ms")
            d = (
                f"{dur_s}ms"
                if dur_s is not None and dur_s < 1000
                else (f"{dur_s / 1000:.1f}s" if dur_s is not None else "—")
            )
            out.append(f"{indent}- {st} {s['name']} · {d} · {s['status']}")
            if s.get("error_type"):
                out.append(f"{indent}  - error_type: {s['error_type']}")
            if s.get("error_msg"):
                out.append(f"{indent}  - error_msg: {s['error_msg']}")
            if s.get("error_category"):
                out.append(f"{indent}  - error_category: {s['error_category']}")
            attrs = s.get("attrs") or {}
            if attrs.get("node_id"):
                out.append(f"{indent}  - canvas node id: {attrs['node_id']}")
            out.extend(render_spans(s["span_id"], depth + 1))
        return out

    lines.extend(render_spans(None, 0))

    # Failure summary
    if status == "error":
        error_span = next(
            (s for s in reversed(spans) if s["status"] == "error"),
            None,
        )
        if error_span:
            cat = error_category or "unknown"
            cat_fixes = {
                "wiring": "fix the graph, not a tool",
                "program": "fix the tool implementation or its dependencies",
                "llm": "tweak prompt template, swap model, or adjust temperature",
                "infra": "check service health, restart daemon, replug device",
            }
            fix = cat_fixes.get(cat, "investigate the error")
            msg = error_span.get("error_msg", "unknown error")
            lines.append("\n## Failure summary\n")
            lines.append(
                f"The graph failed at span `{error_span['name']}` because: {msg}. "
                f"This is a **{cat} error** — {fix}."
            )

    # B-H1: deterministic output — derive footer timestamp from run.ended_at
    # so two consecutive calls produce identical bytes.
    ended_at = run.get("ended_at")
    if ended_at is not None:
        ended_iso = datetime.fromtimestamp(ended_at, tz=UTC).isoformat()
    else:
        ended_iso = started_iso
    lines.append(f"\n---\n\n_Generated {ended_iso} by Voice Commander Builder UI_\n")
    return "\n".join(lines)


def build_observability_router(
    store: Store,
    *,
    tracer: Any = None,
    bus: Any = None,
    llm_router: Any = None,
    dispatcher: Any = None,
    registry: Any = None,
) -> APIRouter:
    """Build the /api/runs/* router bound to a specific Store + optional Tracer/Bus."""
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    def _run_with_spans(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        run["spans"] = store.get_spans(run_id)
        return run

    @router.get("")
    def list_runs(
        limit: int = Query(50, ge=1, le=500),
        status: str | None = None,
        graph: str | None = None,
        since: float | None = None,
        before: str | None = None,
        category: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        before_ts: float | None = None
        if before is not None:
            from datetime import datetime

            try:
                dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail=f"invalid before timestamp: {before!r}"
                ) from exc
            # B-H2: only assume UTC when the user supplied a naive timestamp;
            # never clobber an explicit offset.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            before_ts = dt.timestamp()
        runs = store.list_runs(
            limit=limit,
            status=status,
            since_ts=since,
            before_ts=before_ts,
            transcript_like=q,
            graph_name=graph,
            category=category,
        )
        return {"count": len(runs), "runs": runs}

    @router.get("/last")
    def last_run() -> dict[str, Any]:
        run = store.get_last_run()
        if run is None:
            raise HTTPException(status_code=404, detail="no runs yet")
        run["spans"] = store.get_spans(run["run_id"])
        return run

    @router.get("/stream")
    async def stream(request: Request) -> Any:
        if bus is None:
            raise HTTPException(status_code=503, detail="event bus not configured")

        async def gen() -> AsyncGenerator[str, None]:
            import asyncio
            import json as _json
            import queue

            q = bus.subscribe()
            try:
                # Immediate flush so client sees headers
                yield ": keepalive\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.get_running_loop().run_in_executor(
                            None, q.get, True, 1.0
                        )
                    except queue.Empty:
                        # Timeout — no events pending; send keepalive
                        yield ": keepalive\n\n"
                        continue
                    except (asyncio.CancelledError, ConnectionResetError):
                        # Client disconnected — exit silently
                        break
                    except Exception:
                        # Unknown error — log and exit to prevent infinite loop;
                        # client will reconnect.
                        logger.exception("SSE generator error")
                        break
                    # Forward trace events and run.appended to SSE clients
                    if not ev.type.startswith("trace.") and ev.type != "run.appended":
                        continue
                    yield f"event: {ev.type}\ndata: {_json.dumps(ev.data)}\n\n"
            finally:
                if hasattr(bus, "unsubscribe"):
                    bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @router.get("/{run_id}/export.md")
    def export_run_md(run_id: str) -> Any:
        from fastapi.responses import Response as _Response

        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
        spans = store.get_spans(run_id)
        md = _build_export_md(run, spans)
        return _Response(content=md, media_type="text/markdown")

    @router.get("/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return _run_with_spans(run_id)

    @router.get("/{run_id}/llm")
    def get_run_llm(run_id: str) -> dict[str, Any]:
        spans = store.get_spans(run_id)
        for s in spans:
            if s["type"] == "llm_call":
                return {
                    "run_id": run_id,
                    "model": s["attrs"].get("model"),
                    "endpoint_url": s["attrs"].get("endpoint_url"),
                    "prompt_full": s["attrs"].get("prompt_full"),
                    "transcript": s["attrs"].get("transcript"),
                    "tools": s["attrs"].get("tools"),
                    "raw_response": s["attrs"].get("raw_response"),
                    "output": s["output"],
                    "duration_ms": s["duration_ms"],
                    "status": s["status"],
                    "error_msg": s["error_msg"],
                }
        raise HTTPException(status_code=404, detail="no llm_call span on this run")

    @router.post("/prune")
    def prune_runs(body: dict[str, int]) -> dict[str, Any]:
        keep = int(body.get("keep", 0))
        if keep > 0:
            store.set_keep_runs(keep)
        return {"keep_runs": store.keep_runs}

    @router.post("/{run_id}/replay-llm")
    def replay_llm_ep(run_id: str) -> dict[str, Any]:
        if llm_router is None:
            raise HTTPException(503, detail="llm_router not configured")
        from voice_commander.observability.replay import replay_llm as _replay_llm

        result = _replay_llm(store, run_id, llm_router)
        return {
            "run_id": result.run_id,
            "old_plan": result.old_plan,
            "new_plan": result.new_plan,
            "changed": result.changed,
            "error": result.error,
        }

    @router.post("/{run_id}/replay-full")
    def replay_full_ep(
        run_id: str,
        x_replay_confirm: str = Header(default=""),
    ) -> dict[str, Any]:
        if x_replay_confirm.lower() != "yes":
            raise HTTPException(412, detail="missing X-Replay-Confirm: yes header")
        if not all([llm_router, dispatcher, registry, tracer]):
            raise HTTPException(503, detail="full replay requires daemon services")
        from voice_commander.observability.replay import replay_full as _replay_full

        new_id = _replay_full(store, run_id, llm_router, dispatcher, registry, tracer)
        return {"new_run_id": new_id, "replay_of": run_id}

    return router
