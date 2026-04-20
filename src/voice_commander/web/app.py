from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..registry import ToolRegistry
from ..tool_metadata import ToolMetadata, ToolMetadataError, ToolMetadataStore

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    registry: ToolRegistry,
    store: ToolMetadataStore,
    reload_lock: threading.Lock,
) -> FastAPI:
    """Create and return the FastAPI application for the command management dashboard."""

    app = FastAPI(title="Voice Commander", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def csrf_protect(request: Request, call_next):  # noqa: ARG001
        """Reject POST requests without the HX-Request header (HTMX sends it)."""
        if request.method == "POST" and request.headers.get("HX-Request") != "true":
            return HTMLResponse(
                content="Forbidden: missing HX-Request header",
                status_code=403,
            )
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # ------------------------------------------------------------------
    # GET / — full dashboard
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        tools = registry.all()
        grouped: dict[str, list[object]] = {}
        for t in tools:
            grouped.setdefault(t.category, []).append(t)
        return templates.TemplateResponse(
            request,
            "index.html",
            {"groups": grouped},
        )

    # ------------------------------------------------------------------
    # GET /healthz — liveness probe
    # ------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

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
                content=_render_error("Saved to disk, but live reload failed. Restart daemon to apply."),
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
                content=_render_error("Saved to disk, but live reload failed. Restart daemon to apply."),
                status_code=200,
            )

        tool = registry.by_name(name)
        return templates.TemplateResponse(
            request,
            "_tool_card.html",
            {"tool": tool},
        )

    return app


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_error(message: str) -> str:
    """Return raw HTML for the error banner (used before templates are available)."""
    escaped = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
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
