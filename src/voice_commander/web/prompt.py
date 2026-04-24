"""Prompt Inspector routes: inspect, edit, and save the LLM system prompt.

Mirrors the ``admin.py`` attachment pattern — :func:`attach_prompt_routes`
wires endpoints onto an existing FastAPI application when an ``LLMRouter``
is available.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..llm_router import _TEMPLATE_PATH, LLMRouter

if TYPE_CHECKING:
    from ..event_bus import EventBus

logger = logging.getLogger(__name__)


def _validate_template(text: str) -> str | None:
    """Validate a prompt template before saving.

    Returns an error message string if invalid, or ``None`` if valid.
    """
    stripped = text.strip()
    if not stripped:
        return "Template must not be empty."
    if len(stripped) > 10_000:
        return f"Template too long ({len(stripped)} chars); limit is 10,000."
    if "{default_browser}" not in text:
        return "Template must contain the {default_browser} placeholder."
    # Reject stray placeholders that would crash .format().
    # {default_browser} is the only allowed single-brace placeholder.
    # Doubled braces {{ }} are valid Python format escaping and OK.
    # Strategy: strip doubled braces (valid Python format escaping),
    # replace known placeholders, then check for remaining {word}.
    test_text = re.sub(r"\{\{.*?\}\}", "", text)
    test_text = test_text.replace("{default_browser}", "")
    stray = re.search(r"\{(\w+)\}", test_text)
    if stray:
        return (
            f"Unknown placeholder {{{stray.group(1)}}} — "
            f"only {{default_browser}} is supported."
        )
    return None


def attach_prompt_routes(
    app: FastAPI,
    *,
    templates: Jinja2Templates,
    llm_router: LLMRouter,
    reload_lock: threading.Lock,
    event_bus: EventBus | None = None,
) -> None:
    """Register /prompt endpoints on *app*."""

    def _publish(event_type: str, data: dict[str, Any] | None = None) -> None:
        if event_bus is not None:
            event_bus.publish(event_type, data)

    @app.get("/prompt", response_class=HTMLResponse)
    async def prompt_inspect(request: Request) -> HTMLResponse:
        """Prompt inspector section — shows template, resolved preview, tools."""
        data = llm_router.composed_prompt_data()
        return templates.TemplateResponse(
            request,
            "_prompt_inspector.html",
            {
                "template_raw": data["template_raw"],
                "template_resolved": data["template_resolved"],
                "placeholders": data["placeholders"],
                "tools": data["tools"],
                "tools_count": data["tools_count"],
                "tools_json": json.dumps(data["tools"], indent=2),
                "model_id": data["model_id"],
                "endpoint_url": data["endpoint_url"],
            },
        )

    @app.get("/prompt/edit", response_class=HTMLResponse)
    async def prompt_edit(request: Request) -> HTMLResponse:
        """Return the editable prompt form (same data, same template)."""
        data = llm_router.composed_prompt_data()
        return templates.TemplateResponse(
            request,
            "_prompt_inspector.html",
            {
                "template_raw": data["template_raw"],
                "template_resolved": data["template_resolved"],
                "placeholders": data["placeholders"],
                "tools": data["tools"],
                "tools_count": data["tools_count"],
                "tools_json": json.dumps(data["tools"], indent=2),
                "model_id": data["model_id"],
                "endpoint_url": data["endpoint_url"],
            },
        )

    @app.post("/prompt/template", response_class=HTMLResponse)
    async def prompt_save(template_text: str = Form(...)) -> HTMLResponse:
        """Save edited template, validate, reload, publish event."""
        error = _validate_template(template_text)
        if error is not None:
            escaped = (
                error.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            return HTMLResponse(
                content=f'<div class="text-red-400 text-sm">{escaped}</div>',
                status_code=400,
            )

        # Atomic write: tmp file → os.replace()
        tmp_path = _TEMPLATE_PATH.with_suffix(".txt.tmp")
        try:
            tmp_path.write_text(template_text, encoding="utf-8")
            os.replace(str(tmp_path), str(_TEMPLATE_PATH))
        except OSError as exc:
            logger.exception("Failed to write prompt template")
            return HTMLResponse(
                content=f'<div class="text-red-400 text-sm">Save failed: {exc}</div>',
                status_code=500,
            )

        with reload_lock:
            llm_router.reload_prompt()

        _publish("prompt_template_saved", {"path": str(_TEMPLATE_PATH)})
        logger.info("Prompt template saved and reloaded from %s", _TEMPLATE_PATH)

        return HTMLResponse(
            content='<div class="text-green-400 text-sm">Saved &amp; reloaded ✓</div>'
        )

    @app.get("/prompt/tools", response_class=HTMLResponse)
    async def prompt_tools(request: Request) -> HTMLResponse:
        """Return tools catalog HTML fragment."""
        data = llm_router.composed_prompt_data()
        return templates.TemplateResponse(
            request,
            "_prompt_inspector.html",
            {
                "template_raw": data["template_raw"],
                "template_resolved": data["template_resolved"],
                "placeholders": data["placeholders"],
                "tools": data["tools"],
                "tools_count": data["tools_count"],
                "tools_json": json.dumps(data["tools"], indent=2),
                "model_id": data["model_id"],
                "endpoint_url": data["endpoint_url"],
            },
        )
