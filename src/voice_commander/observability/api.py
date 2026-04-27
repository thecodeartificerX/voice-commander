"""FastAPI router for /api/runs/*."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from voice_commander.observability.store import Store


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
        q: str | None = None,
    ) -> dict[str, Any]:
        runs = store.list_runs(
            limit=limit, status=status,
            since_ts=since, transcript_like=q,
        )
        if graph:
            runs = [r for r in runs if any(
                sp["type"] == "graph" and sp["name"] == graph
                for sp in store.get_spans(r["run_id"])
            )]
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
            q = bus.subscribe()
            try:
                yield ": keepalive\n\n"  # immediate flush so client sees headers
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.get_event_loop().run_in_executor(
                            None, q.get, True, 1.0
                        )
                    except Exception:
                        yield ": keepalive\n\n"
                        continue
                    if not ev.type.startswith("trace."):
                        continue
                    yield f"event: {ev.type}\ndata: {_json.dumps(ev.data)}\n\n"
            finally:
                pass  # queue is daemon-thread; GC handles cleanup

        return StreamingResponse(gen(), media_type="text/event-stream")

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
            store._keep_runs = keep
        return {"keep_runs": store._keep_runs}

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
