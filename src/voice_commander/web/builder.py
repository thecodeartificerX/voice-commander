"""Builder page + graph CRUD JSON endpoints.

Routes:
- GET /page/builder?(graph=<name>|kind=<command|workflow>)
- GET /graph/{name}            → canonical JSON
- POST /graph/{name}           → validate, save, reload, 200 or 422
- DELETE /graph/{name}         → delete + reload
- POST /graph/{name}/toggle    → flip enabled
- POST /graph/validate         → dry-run validation
- GET /graph/palette           → palette of pipeline + commands + workflows + control + value
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from voice_commander.commands.graph import Graph
from voice_commander.commands.graph_schema import (
    CURRENT_SCHEMA_VERSION,
    GraphSchemaError,
    parse_graph,
    serialise_graph,
)
from voice_commander.commands.graph_validator import ValidationSeverity
from voice_commander.commands.graph_validator import validate as validate_graph
from voice_commander.commands.store import GraphStore, GraphStoreError
from voice_commander.registry import ToolRegistry


def _describe_graph(g: Graph) -> dict[str, Any]:
    """Return a summary dict (ref, name, description, inputs) for a graph.

    ``ref`` is ``command.<name>`` or ``workflow.<name>`` — matching the runtime
    convention used by ``GraphRuntime`` for cross-graph calls so the frontend
    palette can drag the canonical ref directly.
    """
    return {
        "ref": f"{g.kind}.{g.name}",
        "name": g.name,
        "description": g.description,
        "inputs": [
            {"name": inp.name, "type": inp.type, "required": inp.required} for inp in g.inputs
        ],
    }


@dataclass
class BuilderContext:
    command_store: GraphStore
    workflow_store: GraphStore
    registry: ToolRegistry
    reload_lock: threading.Lock
    reload_all_fn: Callable[[], Any]


def make_router(*, templates: Jinja2Templates, ctx: BuilderContext) -> APIRouter:
    r = APIRouter()

    _SPA_INDEX = Path(__file__).resolve().parent / "static" / "builder" / "index.html"
    _SPA_STUB = (
        "<html><body style='font-family:monospace;padding:2rem'>"
        "<h2>Builder UI not built yet</h2>"
        "<p>Run <code>pnpm install &amp;&amp; pnpm build</code> in "
        "<code>web/builder-ui/</code>, or <code>make builder-install "
        "&amp;&amp; make builder-build</code> from the repo root.</p>"
        "</body></html>"
    )

    @r.get("/page/builder", response_model=None)
    def page_builder(
        request: Request,  # noqa: ARG001
        graph: str | None = None,  # noqa: ARG001
        kind: str = "command",  # noqa: ARG001
    ) -> FileResponse | HTMLResponse:
        """``GET /page/builder`` — serve the React SPA (built to web/static/builder/).

        If the build artefact is missing (fresh clone), returns a friendly stub HTML
        instead of a 404 so contributors know what build step to run.
        """
        if _SPA_INDEX.exists():
            return FileResponse(_SPA_INDEX)
        return HTMLResponse(content=_SPA_STUB, status_code=200)

    @r.get("/graph/palette")
    def palette() -> dict[str, Any]:
        """``GET /graph/palette`` — return the node palette for the builder canvas.

        Response keys: ``pipeline`` (action primitives), ``perception``
        (read-only observation primitives — same registry origin as pipeline,
        partitioned out so the Builder UI can render them as a separate
        category without duplicating refs), ``commands``, ``workflows``,
        ``control`` (branch/foreach shapes), ``value`` (constant/input/output
        nodes).
        """
        from voice_commander.tool_schema import (
            PERCEPTION_PRIMITIVE_NAMES,
            describe_tool_for_builder,
        )

        pipeline: list[dict[str, Any]] = []
        perception: list[dict[str, Any]] = []
        for e in ctx.registry.all():
            # Builder palette shows every user-composable primitive,
            # regardless of LLM visibility. ``internal`` only governs
            # whether the LLM tool list sees it; ``system`` hides
            # registry-internal hooks (e.g. ``no_match``) from all UI
            # surfaces.
            if not (e.origin == "primitive" and e.enabled and not e.system):
                continue
            descriptor = describe_tool_for_builder(e)
            if e.name in PERCEPTION_PRIMITIVE_NAMES:
                perception.append(descriptor)
            else:
                pipeline.append(descriptor)
        commands = [_describe_graph(g) for g in ctx.command_store.load_all().values()]
        workflows = [_describe_graph(g) for g in ctx.workflow_store.load_all().values()]
        return {
            "pipeline": pipeline,
            "perception": perception,
            "commands": commands,
            "workflows": workflows,
            "control": {
                "branch": {
                    "inputs": [{"name": "cond", "type": "boolean"}],
                    "outputs": ["true", "false"],
                },
                "foreach": {
                    "inputs": [{"name": "list", "type": "array"}],
                    "outputs": ["item", "after"],
                },
            },
            "value": {
                "constant": {
                    "args": [
                        {"name": "value", "type": "any"},
                        {"name": "value_type", "type": "string"},
                    ],
                    "outputs": ["value"],
                },
                "input": {"description": "Singleton; ports derived from graph.inputs[]"},
                "output": {"description": "Singleton sink; sets graph return value"},
            },
        }

    @r.get("/graph/{name}")
    def get_graph(name: str) -> dict[str, Any]:
        """``GET /graph/{name}`` — return the serialised graph JSON; 404 if not found."""
        for store in (ctx.command_store, ctx.workflow_store):
            graphs = store.load_all()
            if name in graphs:
                return serialise_graph(graphs[name])
        raise HTTPException(status_code=404, detail=f"graph {name!r} not found")

    @r.post("/graph/validate")
    async def validate_endpoint(request: Request) -> JSONResponse:
        """``POST /graph/validate`` — dry-run validation of a graph definition.

        Returns 422 on schema parse error, or 200 with an ``errors`` array on
        validation (array may be empty on clean pass).
        """
        body = await request.json()
        try:
            g = parse_graph(body)
        except GraphSchemaError as exc:
            return JSONResponse(status_code=422, content={"errors": [{"message": str(exc)}]})
        peers = {**ctx.command_store.load_all(), **ctx.workflow_store.load_all()}
        peers.pop(g.name, None)
        errors = validate_graph(g, registry=ctx.registry, peers=peers)
        return JSONResponse(
            content={
                "errors": [
                    {
                        "severity": e.severity.value,
                        "node_id": e.node_id,
                        "port": e.port,
                        "message": e.message,
                    }
                    for e in errors
                ]
            }
        )

    @r.post("/graph/{name}")
    async def save_graph(name: str, request: Request) -> JSONResponse:
        """``POST /graph/{name}`` — validate, persist, and hot-reload a graph.

        Returns 422 on schema error or validation failure; rejects name mismatch.
        On success returns ``{"ok": true, "name": name, "version": 1}``.
        """
        body = await request.json()
        try:
            g = parse_graph(body)
        except GraphSchemaError as exc:
            return JSONResponse(status_code=422, content={"errors": [{"message": str(exc)}]})
        if g.name != name:
            return JSONResponse(
                status_code=422,
                content={"errors": [{"message": f"name mismatch: {g.name!r} vs {name!r}"}]},
            )

        store = ctx.command_store if g.kind == "command" else ctx.workflow_store
        peers = {**ctx.command_store.load_all(), **ctx.workflow_store.load_all()}
        peers.pop(name, None)

        errors = validate_graph(g, registry=ctx.registry, peers=peers)
        hard = [e for e in errors if e.severity == ValidationSeverity.ERROR]
        if hard:
            return JSONResponse(
                status_code=422,
                content={
                    "errors": [
                        {"node_id": e.node_id, "port": e.port, "message": e.message} for e in hard
                    ]
                },
            )

        with ctx.reload_lock:
            store.save_one(g)
            ctx.reload_all_fn()
        return JSONResponse(
            status_code=200,
            content={"ok": True, "name": name, "version": CURRENT_SCHEMA_VERSION},
        )

    @r.delete("/graph/{name}")
    def delete_graph(name: str) -> dict[str, Any]:
        """``DELETE /graph/{name}`` — remove graph from store and hot-reload; 404 if absent."""
        with ctx.reload_lock:
            for store in (ctx.command_store, ctx.workflow_store):
                try:
                    found = store.delete(name)
                except GraphStoreError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                if found:
                    ctx.reload_all_fn()
                    return {"ok": True}
        raise HTTPException(status_code=404, detail=f"graph {name!r} not found")

    @r.post("/graph/{name}/rename")
    async def rename_graph(name: str, request: Request) -> JSONResponse:
        """``POST /graph/{name}/rename`` — rename a saved graph and hot-reload.

        Body: ``{"new_name": "<slug>"}``. Returns 422 on invalid/colliding name,
        404 if source absent, 200 with ``{"ok": true, "name": new_name}`` on success.
        After reload, the LLM router exposes the graph under the new tool name.
        """
        body = await request.json()
        new_name = body.get("new_name") if isinstance(body, dict) else None
        if not isinstance(new_name, str) or not new_name:
            return JSONResponse(
                status_code=422,
                content={"errors": [{"message": "new_name must be a non-empty string"}]},
            )
        if new_name == name:
            return JSONResponse(status_code=200, content={"ok": True, "name": name})
        # Cross-store collision check (workflow named foo blocks command rename to foo)
        all_names = {
            *ctx.command_store.load_all().keys(),
            *ctx.workflow_store.load_all().keys(),
        }
        if new_name in all_names:
            return JSONResponse(
                status_code=422,
                content={"errors": [{"message": f"graph {new_name!r} already exists"}]},
            )
        with ctx.reload_lock:
            for store in (ctx.command_store, ctx.workflow_store):
                graphs = store.load_all()
                if name in graphs:
                    try:
                        store.rename(name, new_name)
                    except GraphStoreError as exc:
                        return JSONResponse(
                            status_code=422, content={"errors": [{"message": str(exc)}]}
                        )
                    ctx.reload_all_fn()
                    return JSONResponse(status_code=200, content={"ok": True, "name": new_name})
        raise HTTPException(status_code=404, detail=f"graph {name!r} not found")

    @r.post("/graph/{name}/toggle")
    def toggle_graph(name: str) -> dict[str, Any]:
        """``POST /graph/{name}/toggle`` — flip a graph's enabled state, persist, and hot-reload."""
        with ctx.reload_lock:
            for store in (ctx.command_store, ctx.workflow_store):
                graphs = store.load_all()
                if name in graphs:
                    g = graphs[name]
                    flipped = dataclasses.replace(g, enabled=not g.enabled)
                    try:
                        store.save_one(flipped)
                    except GraphStoreError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                    ctx.reload_all_fn()
                    return {"ok": True, "enabled": flipped.enabled}
        raise HTTPException(status_code=404, detail=f"graph {name!r} not found")

    return r
