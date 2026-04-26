from __future__ import annotations

import asyncio
import html as html_mod
import json as json_mod
import logging
import queue as _queue_mod
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..registry import ToolRegistry
from ..tool_metadata import ToolMetadata, ToolMetadataError, ToolMetadataStore
from .admin import attach_admin_routes

if TYPE_CHECKING:
    from ..commands.store import GraphStore
    from ..dispatcher import Dispatcher
    from ..event_bus import EventBus
    from ..llm_router import LLMRouter

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
    dispatcher: Dispatcher | None = None,
    llm_context: dict[str, object] | None = None,
    config_path: Path | None = None,
    llm_router: LLMRouter | None = None,
) -> FastAPI:
    """Create and return the FastAPI application for the command management dashboard.

    The admin surface (command/workflow CRUD + config editor + restart) is
    registered only when ``command_store``, ``workflow_store``, ``dispatcher``
    and ``config_path`` are all provided. Tests that spin up the app with just
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

    # ------------------------------------------------------------------
    # GET / — redirect to default page (Commands)
    # ------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index_redirect() -> RedirectResponse:
        return RedirectResponse(url="/page/commands", status_code=302)

    # ------------------------------------------------------------------
    # GET /page/{section} — full HTML pages, one per dashboard section
    # ------------------------------------------------------------------

    @app.get("/page/commands", response_class=HTMLResponse)
    async def page_commands(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "page_commands.html", {})

    @app.get("/page/workflows", response_class=HTMLResponse)
    async def page_workflows(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "page_workflows.html", {})

    @app.get("/page/prompt", response_class=HTMLResponse)
    async def page_prompt(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "page_prompt.html", {})

    @app.get("/page/config", response_class=HTMLResponse)
    async def page_config(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "page_config.html", {})

    @app.get("/page/primitives", response_class=HTMLResponse)
    async def page_primitives(request: Request) -> HTMLResponse:
        tools = registry.all()
        grouped: dict[str, list[object]] = {}
        for t in tools:
            grouped.setdefault(t.category, []).append(t)
        return templates.TemplateResponse(
            request,
            "page_primitives.html",
            {"groups": grouped},
        )

    # ------------------------------------------------------------------
    # GET /guide — in-UI architecture walkthrough
    # ------------------------------------------------------------------

    @app.get("/guide", response_class=HTMLResponse)
    async def guide(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "guide.html", {})

    # ------------------------------------------------------------------
    # GET /healthz — liveness probe
    # ------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # ------------------------------------------------------------------
    # GET /events — Server-Sent Events stream for sprite companion
    # ------------------------------------------------------------------

    @app.get("/events", response_model=None)
    async def events(request: Request) -> StreamingResponse | JSONResponse:
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
    # GET /tool/{name}/edit — swap card to edit form
    # ------------------------------------------------------------------

    @app.get("/tool/{name}/edit", response_class=HTMLResponse)
    async def tool_edit(request: Request, name: str) -> HTMLResponse:
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

        new_md = ToolMetadata(
            name=current_md.name,
            phrases=current_md.phrases,
            description=current_md.description,
            category=current_md.category,
            enabled=not current_md.enabled,
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
