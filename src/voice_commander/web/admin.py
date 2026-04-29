"""Admin routes for commands, workflows, and runtime config.

Kept in a sibling module so ``app.py`` stays focused on the legacy tool
CRUD flow. :func:`attach_admin_routes` wires a self-contained router onto
an existing FastAPI application; when the necessary stores are absent (tests,
minimal deployments), the admin surface simply isn't registered.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..commands.registrar import reload_all as _reload_all
from ..commands.store import GraphStore, GraphStoreError
from ..config import Config, ConfigWriteError, update_user_config
from ..registry import ToolRegistry

if TYPE_CHECKING:
    from ..event_bus import EventBus

logger = logging.getLogger(__name__)


def attach_admin_routes(
    app: FastAPI,
    *,
    templates: Jinja2Templates,
    registry: ToolRegistry,
    reload_lock: threading.Lock,
    command_store: GraphStore,
    workflow_store: GraphStore,
    config_path: Path,
    event_bus: EventBus | None = None,
) -> None:
    """Register /command, /workflow, /config, /restart routes on *app*."""

    def _publish(event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire *event_type* on the event bus if one is configured."""
        if event_bus is not None:
            event_bus.publish(event_type, data)

    def _reload() -> None:
        """Reload commands + workflows under reload_lock."""
        with reload_lock:
            _reload_all(registry, command_store, workflow_store)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app.get("/commands", response_class=HTMLResponse)
    async def list_commands(request: Request) -> HTMLResponse:
        """``GET /commands`` — render the command list partial (HTMX fragment)."""
        cmds = command_store.load_all()
        return templates.TemplateResponse(
            request,
            "_command_list.html",
            {"commands": list(cmds.values())},
        )

    @app.post("/command/{name}/toggle", response_class=HTMLResponse)
    async def command_toggle(request: Request, name: str) -> HTMLResponse:
        """``POST /command/{name}/toggle`` — flip enabled state and return the updated card partial;
        404 if absent.
        """
        cmds = command_store.load_all()
        cmd = cmds.get(name)
        if cmd is None:
            return HTMLResponse(content=f"{name!r} not found", status_code=404)
        flipped = dataclasses.replace(cmd, enabled=not cmd.enabled)
        try:
            command_store.save_one(flipped)
        except GraphStoreError as exc:
            return HTMLResponse(content=str(exc), status_code=400)
        _reload()
        _publish("command_saved", {"name": cmd.name})
        return templates.TemplateResponse(request, "_command_card.html", {"cmd": flipped})

    @app.post("/command/{name}/delete", response_class=HTMLResponse)
    async def command_delete(request: Request, name: str) -> HTMLResponse:
        """``POST /command/{name}/delete`` — remove command from store
        and registry; return empty 200.
        """
        command_store.delete(name)
        _reload()
        _publish("command_deleted", {"name": name})
        return HTMLResponse(content="", status_code=200)

    @app.post("/command/{name}/duplicate", response_class=HTMLResponse)
    async def command_duplicate(request: Request, name: str) -> HTMLResponse:
        """``POST /command/{name}/duplicate`` — clone command under
        ``<name>_copy[<n>]`` and return the new card partial for HTMX
        ``afterend`` insertion.
        """
        try:
            new_cmd = command_store.duplicate(name)
        except GraphStoreError as exc:
            return HTMLResponse(content=str(exc), status_code=404)
        _reload()
        _publish("command_saved", {"name": new_cmd.name})
        return templates.TemplateResponse(request, "_command_card.html", {"cmd": new_cmd})

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    @app.get("/workflows", response_class=HTMLResponse)
    async def list_workflows(request: Request) -> HTMLResponse:
        """``GET /workflows`` — render the workflow list partial (HTMX fragment)."""
        wfs = workflow_store.load_all()
        return templates.TemplateResponse(
            request, "_workflow_list.html", {"workflows": list(wfs.values())}
        )

    @app.post("/workflow/{name}/toggle", response_class=HTMLResponse)
    async def workflow_toggle(request: Request, name: str) -> HTMLResponse:
        """``POST /workflow/{name}/toggle`` — flip enabled state and
        return the updated card partial; 404 if absent.
        """
        wfs = workflow_store.load_all()
        wf = wfs.get(name)
        if wf is None:
            return HTMLResponse(content=f"{name!r} not found", status_code=404)
        flipped = dataclasses.replace(wf, enabled=not wf.enabled)
        try:
            workflow_store.save_one(flipped)
        except GraphStoreError as exc:
            return HTMLResponse(content=str(exc), status_code=400)
        _reload()
        _publish("workflow_saved", {"name": wf.name})
        return templates.TemplateResponse(request, "_workflow_card.html", {"wf": flipped})

    @app.post("/workflow/{name}/delete", response_class=HTMLResponse)
    async def workflow_delete(request: Request, name: str) -> HTMLResponse:
        """``POST /workflow/{name}/delete`` — remove workflow from store
        and registry; return empty 200.
        """
        workflow_store.delete(name)
        _reload()
        _publish("workflow_deleted", {"name": name})
        return HTMLResponse(content="", status_code=200)

    @app.post("/workflow/{name}/duplicate", response_class=HTMLResponse)
    async def workflow_duplicate(request: Request, name: str) -> HTMLResponse:
        """``POST /workflow/{name}/duplicate`` — clone workflow and return the
        new card partial for HTMX ``afterend`` insertion.
        """
        try:
            new_wf = workflow_store.duplicate(name)
        except GraphStoreError as exc:
            return HTMLResponse(content=str(exc), status_code=404)
        _reload()
        _publish("workflow_saved", {"name": new_wf.name})
        return templates.TemplateResponse(request, "_workflow_card.html", {"wf": new_wf})

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @app.get("/config/edit", response_class=HTMLResponse)
    async def config_edit(request: Request) -> HTMLResponse:
        """``GET /config/edit`` — render the config editor form with current TOML values."""
        import tomllib

        raw = {}
        if config_path.exists():
            with config_path.open("rb") as fh:
                raw = tomllib.load(fh)
        return templates.TemplateResponse(request, "_config_form.html", {"cfg": raw})

    @app.post("/config", response_class=HTMLResponse)
    async def config_save(
        request: Request,
        llm_endpoint_url: str = Form(default=""),
        llm_model_id: str = Form(default=""),
        llm_default_browser: str = Form(default=""),
        llm_timeout_ms: int = Form(default=1200),
        audio_device: int = Form(default=-1),
        transcription_model_size: str = Form(default="small.en"),
        transcription_min_confidence: float = Form(default=0.3),
    ) -> HTMLResponse:
        """``POST /config`` — persist config changes from form fields.

        Form fields: llm_endpoint_url, llm_model_id, llm_default_browser,
        llm_timeout_ms, audio_device, transcription_model_size,
        transcription_min_confidence.  Returns a green banner on success, amber
        if a restart is needed (audio device or model changed), or an error
        banner on failure.
        """
        updates: dict[str, dict[str, Any]] = {
            "llm": {
                "endpoint_url": llm_endpoint_url,
                "model_id": llm_model_id,
                "default_browser": llm_default_browser,
                "timeout_ms": int(llm_timeout_ms),
            },
            "audio": {"device": int(audio_device)},
            "transcription": {
                "model_size": transcription_model_size,
                "min_confidence": float(transcription_min_confidence),
            },
        }
        # Snapshot config before write to detect which restart-required keys changed.
        prev_cfg = Config.load(config_path)
        try:
            update_user_config(config_path, updates)
        except ConfigWriteError as exc:
            return HTMLResponse(content=str(exc), status_code=400)
        new_cfg = Config.load(config_path)
        _publish("config_saved", {"path": str(config_path)})

        needs_restart = (
            prev_cfg.audio.device != new_cfg.audio.device
            or prev_cfg.transcription.model_size != new_cfg.transcription.model_size
        )

        if needs_restart:
            banner = (
                '<div class="text-amber-300 text-sm">'
                "Saved. Some changes require a restart. "
                '<button hx-post="/restart" hx-swap="none" '
                'class="underline hover:text-amber-200">Restart daemon</button>'
                " to apply."
                "</div>"
            )
        else:
            banner = '<div class="text-green-400 text-sm">Saved. Changes applied immediately.</div>'
        return HTMLResponse(content=banner)

    @app.post("/restart")
    async def restart() -> JSONResponse:
        """``POST /restart`` — request daemon restart; 202 if accepted, 503 if unavailable."""
        from ..commands.restart import RestartUnavailable, request_restart

        try:
            request_restart()
        except RestartUnavailable as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse({"status": "restarting"}, status_code=202)
