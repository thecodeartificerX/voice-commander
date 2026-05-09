from __future__ import annotations

import asyncio
import dataclasses
import html as html_mod
import json as json_mod
import logging
import queue as _queue_mod
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..registry import ToolRegistry
from ..tool_metadata import ToolMetadata, ToolMetadataError, ToolMetadataStore
from .admin import attach_admin_routes

if TYPE_CHECKING:
    from ..commands.store import GraphStore
    from ..event_bus import EventBus
    from ..llm_router import LLMRouter
    from ..observability.store import Store
    from ..observability.tracer import Tracer
    from ..recorder import KeyRecorder

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    registry: ToolRegistry,
    store: ToolMetadataStore,
    reload_lock: threading.Lock,
    event_bus: EventBus | None = None,
    *,
    command_store: GraphStore | None = None,
    workflow_store: GraphStore | None = None,
    llm_context: dict[str, object] | None = None,
    config_path: Path | None = None,
    llm_router: LLMRouter | None = None,
    observability_store: Store | None = None,
    observability_tracer: Tracer | None = None,
    key_recorder: KeyRecorder | None = None,
) -> FastAPI:
    """Create and return the FastAPI application for the command management dashboard.

    The admin surface (command/workflow CRUD + config editor + restart) is
    registered only when ``command_store``, ``workflow_store`` and ``config_path``
    are all provided. Tests that spin up the app with just
    the core args get a minimal tool-management dashboard.
    """

    app = FastAPI(title="Voice Commander", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def csrf_protect(  # noqa: ARG001
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject mutating requests without the HX-Request header (HTMX sends it)."""
        if (
            request.method in ("POST", "PUT", "PATCH", "DELETE")
            and request.headers.get("HX-Request") != "true"
            and not request.headers.get("content-type", "").startswith("application/json")
        ):
            return HTMLResponse(
                content="Forbidden: missing HX-Request header",
                status_code=403,
            )
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.autoescape = True

    # ------------------------------------------------------------------
    # GET / — redirect to default page (Commands)
    # ------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index_redirect() -> RedirectResponse:
        """``GET /`` — redirect to ``/page/commands``."""
        return RedirectResponse(url="/page/commands", status_code=302)

    # ------------------------------------------------------------------
    # GET /page/{section} — full HTML pages, one per dashboard section
    # ------------------------------------------------------------------

    @app.get("/page/commands", response_class=HTMLResponse)
    async def page_commands(request: Request) -> HTMLResponse:
        """``GET /page/commands`` — render the commands dashboard."""
        return templates.TemplateResponse(request, "page_commands.html", {})

    @app.get("/page/workflows", response_class=HTMLResponse)
    async def page_workflows(request: Request) -> HTMLResponse:
        """``GET /page/workflows`` — render the workflows dashboard."""
        return templates.TemplateResponse(request, "page_workflows.html", {})

    @app.get("/page/prompt", response_class=HTMLResponse)
    async def page_prompt(request: Request) -> HTMLResponse:
        """``GET /page/prompt`` — render the prompt inspector."""
        return templates.TemplateResponse(request, "page_prompt.html", {})

    @app.get("/page/config", response_class=HTMLResponse)
    async def page_config(request: Request) -> HTMLResponse:
        """``GET /page/config`` — render the configuration dashboard."""
        return templates.TemplateResponse(request, "page_config.html", {})

    @app.get("/page/primitives", response_class=HTMLResponse)
    async def page_primitives(request: Request) -> HTMLResponse:
        """``GET /page/primitives`` — render the tools/primitives page
        with tools grouped by category. System tools and graph-backed
        commands/workflows are excluded so the Primitives tab shows
        only the underlying primitive verb catalog.
        """
        tools = [
            t for t in registry.all() if not t.system and t.origin == "primitive"
        ]
        grouped: dict[str, list[object]] = {}
        for t in tools:
            grouped.setdefault(t.category, []).append(t)
        return templates.TemplateResponse(
            request,
            "page_primitives.html",
            {"groups": grouped},
        )

    @app.get("/api/tools")
    async def api_tools() -> JSONResponse:
        """``GET /api/tools`` — return JSON list of primitive tools.

        Only ``origin == "primitive"`` and ``system == false`` entries are
        included so the Primitives tab and any consumer of this endpoint
        sees the verb catalog without graph-backed commands/workflows
        leaking in.
        """
        return JSONResponse(
            [
                {
                    "name": t.name,
                    "description": t.description,
                    "category": t.category,
                    "enabled": t.enabled,
                    "internal": t.internal,
                    "system": t.system,
                    "llm_only": t.llm_only,
                }
                for t in registry.all()
                if not t.system and t.origin == "primitive"
            ]
        )

    # ------------------------------------------------------------------
    # GET /guide — in-UI architecture walkthrough
    # ------------------------------------------------------------------

    @app.get("/guide", response_class=HTMLResponse)
    async def guide(request: Request) -> HTMLResponse:
        """``GET /guide`` — render the architecture guide page."""
        return templates.TemplateResponse(request, "guide.html", {})

    # ------------------------------------------------------------------
    # GET /healthz — liveness probe
    # ------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        """``GET /healthz`` — liveness probe; returns ``{"status": "ok"}``."""
        return JSONResponse({"status": "ok"})

    # ------------------------------------------------------------------
    # GET /events — Server-Sent Events stream for sprite companion
    # ------------------------------------------------------------------

    @app.get("/events", response_model=None)
    async def events(request: Request) -> StreamingResponse | JSONResponse:
        """``GET /events`` — Server-Sent Events stream for daemon state changes.

        Honours the ``Last-Event-ID`` header for replay from the ring buffer.
        Sends keepalive pings on idle. Returns 503 if EventBus is not configured.
        """
        if event_bus is None:
            return JSONResponse({"error": "EventBus not configured"}, status_code=503)

        last_id_str = request.headers.get("Last-Event-ID", "0")
        try:
            last_id = int(last_id_str)
        except ValueError:
            last_id = 0

        q, replay = event_bus.subscribe_with_replay(last_id)

        async def generate() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            try:
                # Replay missed events from ring buffer (atomic with subscribe)
                for ev in replay:
                    if await request.is_disconnected():
                        return
                    yield (
                        f"id: {ev.id}\n"
                        f"event: {ev.type}\n"
                        f"data: {json_mod.dumps(ev.data | {'ts': ev.ts})}\n\n"
                    )
                # Stream new events (queue.Queue drained via executor)
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        ev = await asyncio.wait_for(
                            loop.run_in_executor(None, q.get, True, 1.0),
                            timeout=2.0,
                        )
                        yield (
                            f"id: {ev.id}\n"
                            f"event: {ev.type}\n"
                            f"data: {json_mod.dumps(ev.data | {'ts': ev.ts})}\n\n"
                        )
                    except (_queue_mod.Empty, TimeoutError):
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
            finally:
                event_bus.unsubscribe(q)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------
    # POST /key_recorder/start, /key_recorder/cancel — backend chord capture
    # ------------------------------------------------------------------

    @app.post("/key_recorder/start")
    async def key_recorder_start(request: Request) -> JSONResponse:
        """``POST /key_recorder/start`` — begin a single-shot chord capture.

        Returns 202 if the recorder began, 409 if one is already running,
        503 if the daemon was started without a ``KeyRecorder``. The
        captured combo is delivered asynchronously over ``/events`` as a
        ``key_recorder_captured`` SSE event.
        """
        if key_recorder is None:
            return JSONResponse(
                {"error": "key recorder not configured"}, status_code=503
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        timeout_raw = body.get("timeout") if isinstance(body, dict) else None
        try:
            timeout = float(timeout_raw) if timeout_raw is not None else 15.0
        except (TypeError, ValueError):
            timeout = 15.0
        # Clamp to a sane window — no infinite suppress.
        timeout = max(1.0, min(timeout, 60.0))
        try:
            started = key_recorder.start(timeout=timeout)
        except Exception as exc:
            logger.exception("key_recorder.start raised")
            return JSONResponse(
                {"error": f"start failed: {exc}"}, status_code=500
            )
        if not started:
            return JSONResponse(
                {"error": "already recording"}, status_code=409
            )
        return JSONResponse({"status": "started", "timeout": timeout}, status_code=202)

    @app.post("/key_recorder/cancel")
    async def key_recorder_cancel() -> Response:
        """``POST /key_recorder/cancel`` — abort the active session (idempotent)."""
        if key_recorder is None:
            return JSONResponse(
                {"error": "key recorder not configured"}, status_code=503
            )
        key_recorder.cancel()
        return Response(status_code=204)

    # ------------------------------------------------------------------
    # GET /tool/{name}/edit — swap card to edit form
    # ------------------------------------------------------------------

    @app.get("/tool/{name}/edit", response_class=HTMLResponse)
    async def tool_edit(request: Request, name: str) -> HTMLResponse:
        """``GET /tool/{name}/edit`` — render the inline tool-edit form; 404 if tool not found."""
        tool = registry.by_name(name)
        if tool is None:
            return HTMLResponse(
                content=_render_error(f"Tool '{name}' not found."),
                status_code=404,
            )
        return templates.TemplateResponse(
            request,
            "_tool_edit.html",
            {"tool": tool},
        )

    # ------------------------------------------------------------------
    # GET /tool/{name}/cancel — swap form back to read-only card
    # ------------------------------------------------------------------

    @app.get("/tool/{name}/cancel", response_class=HTMLResponse)
    async def tool_cancel(request: Request, name: str) -> HTMLResponse:
        """``GET /tool/{name}/cancel`` — swap edit form back to read-only
        tool card; 404 if not found.
        """
        tool = registry.by_name(name)
        if tool is None:
            return HTMLResponse(
                content=_render_error(f"Tool '{name}' not found."),
                status_code=404,
            )
        return templates.TemplateResponse(
            request,
            "_tool_card.html",
            {"tool": tool},
        )

    # ------------------------------------------------------------------
    # POST /tool/{name} — save edits, reload registry, return card
    # ------------------------------------------------------------------

    @app.post("/tool/{name}", response_class=HTMLResponse)
    async def tool_save(
        request: Request,
        name: str,
        phrases: str = Form(default=""),
        description: str = Form(default=""),
        category: str = Form(default=""),
    ) -> HTMLResponse:
        """``POST /tool/{name}`` — update tool metadata (phrases, description, category).

        Validates that at least one phrase is provided and checks for phrase
        duplicates across other tools. Preserves the current enabled state.
        Returns updated card partial on success, or an error banner on failure.
        """
        # --- Parse phrases ---
        parsed_phrases = [p.strip() for p in phrases.splitlines() if p.strip()]
        if not parsed_phrases:
            return HTMLResponse(
                content=_render_error("At least one phrase is required."),
                status_code=400,
            )

        # --- Duplicate-phrase check across OTHER tools ---
        all_meta = store.load_all()
        for other_name, other_md in all_meta.items():
            if other_name == name:
                continue
            for phrase in parsed_phrases:
                if phrase.lower() in (p.lower() for p in other_md.phrases):
                    return HTMLResponse(
                        content=_render_error(
                            f"Phrase '{phrase}' already used by tool '{other_name}'."
                        ),
                        status_code=400,
                    )

        # --- Preserve current enabled state ---
        current = registry.by_name(name)
        enabled = current.enabled if current is not None else True

        new_md = ToolMetadata(
            name=name,
            phrases=tuple(parsed_phrases),
            description=description.strip(),
            category=category.strip(),
            enabled=enabled,
        )

        try:
            store.save(name, new_md)
        except (ToolMetadataError, OSError) as exc:
            return HTMLResponse(
                content=_render_error(f"Save failed: {exc}"),
                status_code=500,
            )

        try:
            with reload_lock:
                registry.reload_metadata(store)
        except Exception:
            logger.exception("reload_metadata failed after saving %s", name)
            return HTMLResponse(
                content=_render_error(
                    "Saved to disk, but live reload failed. Restart daemon to apply."
                ),
                status_code=200,
            )

        tool = registry.by_name(name)
        return templates.TemplateResponse(
            request,
            "_tool_card.html",
            {"tool": tool},
        )

    # ------------------------------------------------------------------
    # POST /tool/{name}/toggle — flip enabled state
    # ------------------------------------------------------------------

    @app.post("/tool/{name}/toggle", response_class=HTMLResponse)
    async def tool_toggle(request: Request, name: str) -> HTMLResponse:
        """``POST /tool/{name}/toggle`` — flip tool's enabled state
        via sidecar TOML and hot-reload registry.
        """
        current = registry.by_name(name)
        if current is None:
            return HTMLResponse(
                content=_render_error(f"Tool '{name}' not found."),
                status_code=404,
            )

        # Load fresh from disk to avoid race with file-level edit
        try:
            current_md = store.load_one(name)
        except ToolMetadataError as exc:
            return HTMLResponse(
                content=_render_error(f"Could not load metadata: {exc}"),
                status_code=500,
            )

        new_md = dataclasses.replace(current_md, enabled=not current_md.enabled)

        try:
            store.save(name, new_md)
        except (ToolMetadataError, OSError) as exc:
            return HTMLResponse(
                content=_render_error(f"Save failed: {exc}"),
                status_code=500,
            )

        try:
            with reload_lock:
                registry.reload_metadata(store)
        except Exception:
            logger.exception("reload_metadata failed after saving %s", name)
            return HTMLResponse(
                content=_render_error(
                    "Saved to disk, but live reload failed. Restart daemon to apply."
                ),
                status_code=200,
            )

        tool = registry.by_name(name)
        return templates.TemplateResponse(
            request,
            "_tool_card.html",
            {"tool": tool},
        )

    # ------------------------------------------------------------------
    # Admin surface (commands, workflows, config, restart)
    # ------------------------------------------------------------------

    if command_store is not None and workflow_store is not None and config_path is not None:
        attach_admin_routes(
            app,
            templates=templates,
            registry=registry,
            reload_lock=reload_lock,
            command_store=command_store,
            workflow_store=workflow_store,
            config_path=config_path,
            event_bus=event_bus,
        )

    if llm_router is not None:
        from .prompt import attach_prompt_routes

        attach_prompt_routes(
            app,
            templates=templates,
            llm_router=llm_router,
            reload_lock=reload_lock,
            event_bus=event_bus,
        )

    if command_store is not None and workflow_store is not None:
        from ..commands.registrar import reload_all as _registrar_reload_all
        from .builder import BuilderContext
        from .builder import make_router as builder_router

        builder_ctx = BuilderContext(
            command_store=command_store,
            workflow_store=workflow_store,
            registry=registry,
            reload_lock=reload_lock,
            reload_all_fn=lambda: _registrar_reload_all(registry, command_store, workflow_store),
        )
        app.include_router(builder_router(templates=templates, ctx=builder_ctx))

    if observability_store is not None:
        import json as _json

        from ..observability.api import build_observability_router

        app.include_router(
            build_observability_router(
                observability_store,
                tracer=observability_tracer,
                bus=event_bus,
                llm_router=llm_router,
            )
        )

        @app.get("/page/runs", response_class=HTMLResponse)
        async def page_runs(request: Request) -> HTMLResponse:
            """``GET /page/runs`` — render the run inspector page."""
            runs = observability_store.list_runs(limit=50)
            return templates.TemplateResponse(request, "runs.html", {"runs": runs})

        @app.get("/page/runs/list", response_class=HTMLResponse)
        async def page_runs_list(
            request: Request,
            limit: int = Query(50, ge=1, le=500),
            status: str | None = None,
            q: str | None = None,
        ) -> HTMLResponse:
            """Return rendered <tr> rows for htmx swap into #runs-tbody."""
            runs = observability_store.list_runs(
                limit=limit,
                status=status or None,
                transcript_like=q or None,
            )
            return HTMLResponse(
                "".join(templates.get_template("_runs_row.html").render({"r": r}) for r in runs)
            )

        @app.get("/page/runs/{run_id}", response_class=HTMLResponse)
        async def page_runs_detail(request: Request, run_id: str) -> HTMLResponse:
            """``GET /page/runs/{run_id}`` — render the run detail partial."""
            run = observability_store.get_run(run_id)
            if run is None:
                return HTMLResponse("<p>not found</p>", status_code=404)
            spans = observability_store.get_spans(run_id)
            by_id = {s["span_id"]: s for s in spans}
            depth_cache: dict[str, int] = {}

            def _depth(s: dict[str, Any]) -> int:
                if s["span_id"] in depth_cache:
                    return depth_cache[s["span_id"]]
                parent = by_id.get(s["parent_span_id"])
                d = 0 if parent is None else _depth(parent) + 1
                depth_cache[s["span_id"]] = d
                return d

            tree = []
            for s in spans:
                tree.append(
                    {
                        **s,
                        "_depth": _depth(s),
                        "attrs_json": (
                            _json.dumps(s["attrs"], indent=2, default=str) if s["attrs"] else ""
                        ),
                        "output_json": (
                            _json.dumps(s["output"], indent=2, default=str) if s["output"] else ""
                        ),
                    }
                )
            return templates.TemplateResponse(
                request, "_run_detail.html", {"run": run, "tree": tree}
            )

    return app


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_error(message: str) -> str:
    """Return raw HTML for the error banner (used before templates are available)."""
    escaped = html_mod.escape(message)
    return (
        f'<div id="error-banner" '
        f'class="bg-red-900/50 border border-red-700 text-red-300 rounded-lg p-3 mb-4 text-sm" '
        f'hx-swap-oob="afterbegin:body > div">'
        f'<div class="flex items-center justify-between">'
        f"<span>{escaped}</span>"
        f'<button onclick="this.parentElement.parentElement.remove()" '
        f'class="text-red-400 hover:text-red-200 ml-4">&times;</button>'
        f"</div></div>"
    )
