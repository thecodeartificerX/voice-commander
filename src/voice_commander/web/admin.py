"""Admin routes for commands, workflows, and runtime config.

Kept in a sibling module so ``app.py`` stays focused on the legacy tool
CRUD flow. :func:`attach_admin_routes` wires a self-contained router onto
an existing FastAPI application; when the necessary stores + dispatcher
are absent (tests, minimal deployments), the admin surface simply isn't
registered.
"""

from __future__ import annotations

import json as json_mod
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..commands.registrar import reload_all as reload_commands_all
from ..commands.store import (
    CommandDef,
    CommandStore,
    CommandStoreError,
    WorkflowArg,
    WorkflowDef,
    WorkflowStep,
    WorkflowStore,
)
from ..config import ConfigWriteError, update_user_config
from ..registry import ToolRegistry

if TYPE_CHECKING:
    from ..dispatcher import Dispatcher
    from ..event_bus import EventBus

logger = logging.getLogger(__name__)


def attach_admin_routes(
    app: FastAPI,
    *,
    templates: Jinja2Templates,
    registry: ToolRegistry,
    reload_lock: threading.Lock,
    command_store: CommandStore,
    workflow_store: WorkflowStore,
    dispatcher: Dispatcher,
    llm_context: dict[str, Any],
    config_path: Path,
    event_bus: EventBus | None = None,
) -> None:
    """Register /command, /workflow, /config, /restart routes on *app*."""

    def _publish(event_type: str, data: dict[str, Any] | None = None) -> None:
        if event_bus is not None:
            event_bus.publish(event_type, data)

    def _reload() -> None:
        """Reload commands + workflows under reload_lock."""
        with reload_lock:
            reload_commands_all(
                registry, command_store, workflow_store, dispatcher, llm_context
            )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app.get("/commands", response_class=HTMLResponse)
    async def list_commands(request: Request) -> HTMLResponse:
        cmds = command_store.load_all()
        return templates.TemplateResponse(
            request,
            "_command_list.html",
            {"commands": list(cmds.values())},
        )

    @app.get("/command/{name}/edit", response_class=HTMLResponse)
    async def command_edit(request: Request, name: str) -> HTMLResponse:
        cmds = command_store.load_all()
        cmd = cmds.get(name)
        if cmd is None:
            return HTMLResponse(content=f"Command {name!r} not found", status_code=404)
        return templates.TemplateResponse(
            request, "_command_edit.html", {"cmd": cmd, "is_new": False}
        )

    @app.get("/command/new", response_class=HTMLResponse)
    async def command_new(request: Request) -> HTMLResponse:
        blank = CommandDef(
            name="",
            description="",
            synonyms=(),
            primitive="press",
            kwargs={"combo": ""},
        )
        return templates.TemplateResponse(
            request, "_command_edit.html", {"cmd": blank, "is_new": True}
        )

    @app.get("/command/{name}/cancel", response_class=HTMLResponse)
    async def command_cancel(request: Request, name: str) -> HTMLResponse:
        cmds = command_store.load_all()
        cmd = cmds.get(name)
        if cmd is None:
            return HTMLResponse(status_code=200)
        return templates.TemplateResponse(
            request, "_command_card.html", {"cmd": cmd}
        )

    @app.post("/command/{name}", response_class=HTMLResponse)
    async def command_save(
        request: Request,
        name: str,
        description: str = Form(default=""),
        synonyms: str = Form(default=""),
        primitive: str = Form(default="press"),
        kwargs_json: str = Form(default="{}"),
        enabled: str = Form(default="true"),
    ) -> HTMLResponse:
        parsed = _parse_command_form(
            name=name,
            description=description,
            synonyms=synonyms,
            primitive=primitive,
            kwargs_json=kwargs_json,
            enabled=enabled,
        )
        if isinstance(parsed, str):
            return HTMLResponse(content=parsed, status_code=400)
        try:
            command_store.save_one(parsed)
        except CommandStoreError as exc:
            return HTMLResponse(content=str(exc), status_code=400)
        _reload()
        _publish("command_saved", {"name": parsed.name})
        return templates.TemplateResponse(
            request, "_command_card.html", {"cmd": parsed}
        )

    @app.post("/command/{name}/toggle", response_class=HTMLResponse)
    async def command_toggle(request: Request, name: str) -> HTMLResponse:
        cmds = command_store.load_all()
        cmd = cmds.get(name)
        if cmd is None:
            return HTMLResponse(content=f"{name!r} not found", status_code=404)
        flipped = CommandDef(
            name=cmd.name,
            description=cmd.description,
            synonyms=cmd.synonyms,
            primitive=cmd.primitive,
            kwargs=dict(cmd.kwargs),
            enabled=not cmd.enabled,
        )
        command_store.save_one(flipped)
        _reload()
        _publish("command_saved", {"name": cmd.name})
        return templates.TemplateResponse(
            request, "_command_card.html", {"cmd": flipped}
        )

    @app.post("/command/{name}/delete", response_class=HTMLResponse)
    async def command_delete(request: Request, name: str) -> HTMLResponse:
        command_store.delete(name)
        registry.remove(name)
        _reload()
        _publish("command_deleted", {"name": name})
        return HTMLResponse(content="", status_code=200)

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    @app.get("/workflows", response_class=HTMLResponse)
    async def list_workflows(request: Request) -> HTMLResponse:
        wfs = workflow_store.load_all()
        return templates.TemplateResponse(
            request, "_workflow_list.html", {"workflows": list(wfs.values())}
        )

    @app.get("/workflow/{name}/edit", response_class=HTMLResponse)
    async def workflow_edit(request: Request, name: str) -> HTMLResponse:
        wfs = workflow_store.load_all()
        wf = wfs.get(name)
        if wf is None:
            return HTMLResponse(content=f"{name!r} not found", status_code=404)
        return templates.TemplateResponse(
            request, "_workflow_edit.html", {"wf": wf, "is_new": False}
        )

    @app.get("/workflow/new", response_class=HTMLResponse)
    async def workflow_new(request: Request) -> HTMLResponse:
        blank = WorkflowDef(
            name="",
            description="",
            synonyms=(),
            args=(),
            steps=(),
        )
        return templates.TemplateResponse(
            request, "_workflow_edit.html", {"wf": blank, "is_new": True}
        )

    @app.get("/workflow/{name}/cancel", response_class=HTMLResponse)
    async def workflow_cancel(request: Request, name: str) -> HTMLResponse:
        wfs = workflow_store.load_all()
        wf = wfs.get(name)
        if wf is None:
            return HTMLResponse(status_code=200)
        return templates.TemplateResponse(request, "_workflow_card.html", {"wf": wf})

    @app.post("/workflow/{name}", response_class=HTMLResponse)
    async def workflow_save(
        request: Request,
        name: str,
        description: str = Form(default=""),
        synonyms: str = Form(default=""),
        args_json: str = Form(default="[]"),
        steps_json: str = Form(default="[]"),
        enabled: str = Form(default="true"),
    ) -> HTMLResponse:
        parsed = _parse_workflow_form(
            name=name,
            description=description,
            synonyms=synonyms,
            args_json=args_json,
            steps_json=steps_json,
            enabled=enabled,
        )
        if isinstance(parsed, str):
            return HTMLResponse(content=parsed, status_code=400)
        try:
            workflow_store.save_one(parsed)
        except CommandStoreError as exc:
            return HTMLResponse(content=str(exc), status_code=400)
        _reload()
        _publish("workflow_saved", {"name": parsed.name})
        return templates.TemplateResponse(
            request, "_workflow_card.html", {"wf": parsed}
        )

    @app.post("/workflow/{name}/toggle", response_class=HTMLResponse)
    async def workflow_toggle(request: Request, name: str) -> HTMLResponse:
        wfs = workflow_store.load_all()
        wf = wfs.get(name)
        if wf is None:
            return HTMLResponse(content=f"{name!r} not found", status_code=404)
        flipped = WorkflowDef(
            name=wf.name,
            description=wf.description,
            synonyms=wf.synonyms,
            args=wf.args,
            steps=wf.steps,
            enabled=not wf.enabled,
        )
        workflow_store.save_one(flipped)
        _reload()
        _publish("workflow_saved", {"name": wf.name})
        return templates.TemplateResponse(
            request, "_workflow_card.html", {"wf": flipped}
        )

    @app.post("/workflow/{name}/delete", response_class=HTMLResponse)
    async def workflow_delete(request: Request, name: str) -> HTMLResponse:
        workflow_store.delete(name)
        registry.remove(name)
        _reload()
        _publish("workflow_deleted", {"name": name})
        return HTMLResponse(content="", status_code=200)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @app.get("/config/edit", response_class=HTMLResponse)
    async def config_edit(request: Request) -> HTMLResponse:
        import tomllib

        raw = {}
        if config_path.exists():
            with config_path.open("rb") as fh:
                raw = tomllib.load(fh)
        return templates.TemplateResponse(
            request, "_config_form.html", {"cfg": raw}
        )

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
        try:
            update_user_config(config_path, updates)
        except ConfigWriteError as exc:
            return HTMLResponse(content=str(exc), status_code=400)
        _publish("config_saved", {"path": str(config_path)})
        return HTMLResponse(
            content=(
                '<div class="text-green-400 text-sm">Saved. '
                '<button hx-post="/restart" hx-swap="none" '
                'class="underline hover:text-green-300">Restart daemon</button> '
                "to apply.</div>"
            )
        )

    @app.post("/restart")
    async def restart() -> JSONResponse:
        from ..commands.restart import schedule_restart

        schedule_restart()
        return JSONResponse({"status": "restarting"})


# ---------------------------------------------------------------------------
# Form parsers
# ---------------------------------------------------------------------------


def _parse_command_form(
    *,
    name: str,
    description: str,
    synonyms: str,
    primitive: str,
    kwargs_json: str,
    enabled: str,
) -> CommandDef | str:
    synonyms_list = tuple(s.strip() for s in synonyms.splitlines() if s.strip())
    try:
        kwargs = json_mod.loads(kwargs_json) if kwargs_json.strip() else {}
    except json_mod.JSONDecodeError as exc:
        return f"kwargs JSON invalid: {exc}"
    if not isinstance(kwargs, dict):
        return "kwargs must decode to a JSON object"
    return CommandDef(
        name=name.strip(),
        description=description.strip(),
        synonyms=synonyms_list,
        primitive=primitive.strip(),
        kwargs=kwargs,
        enabled=enabled.lower() in {"true", "on", "1", "yes"},
    )


def _parse_workflow_form(
    *,
    name: str,
    description: str,
    synonyms: str,
    args_json: str,
    steps_json: str,
    enabled: str,
) -> WorkflowDef | str:
    synonyms_list = tuple(s.strip() for s in synonyms.splitlines() if s.strip())
    try:
        args_raw = json_mod.loads(args_json) if args_json.strip() else []
        steps_raw = json_mod.loads(steps_json) if steps_json.strip() else []
    except json_mod.JSONDecodeError as exc:
        return f"JSON invalid: {exc}"
    if not isinstance(args_raw, list) or not isinstance(steps_raw, list):
        return "args and steps must be JSON arrays"

    try:
        args = tuple(
            WorkflowArg(
                name=str(a["name"]),
                type_str=str(a.get("type", "string")),
                required=bool(a.get("required", True)),
                description=str(a.get("description", "")),
            )
            for a in args_raw
        )
        steps = tuple(
            WorkflowStep(
                ref=str(s["ref"]),
                kwargs=s.get("kwargs", {}) or {},
            )
            for s in steps_raw
        )
    except (KeyError, TypeError) as exc:
        return f"args/steps malformed: {exc}"

    return WorkflowDef(
        name=name.strip(),
        description=description.strip(),
        synonyms=synonyms_list,
        args=args,
        steps=steps,
        enabled=enabled.lower() in {"true", "on", "1", "yes"},
    )
