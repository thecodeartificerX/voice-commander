"""Placeholder substitution for workflow step kwargs.

Workflow steps store their kwargs as templates containing ``{name}`` tokens
that reference workflow args or a small set of built-in context variables
(e.g. ``{default_browser}``). At dispatch time, ``substitute()`` walks the
kwargs dict and replaces every placeholder with its resolved value.

Nested placeholders are not supported — substitution is a single pass.
Unknown placeholders raise :class:`TemplateError` so the dispatcher can
surface a clear error back through the plan_outcome event instead of
silently typing the literal ``{foo}`` into the user's window.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class TemplateError(ValueError):
    """Raised when a workflow kwarg references an unknown placeholder."""


def substitute(
    kwargs: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a copy of *kwargs* with every ``{placeholder}`` resolved.

    Parameters
    ----------
    kwargs:
        Source mapping. Only string values are templated; int/bool/float
        values pass through unchanged.
    context:
        Mapping of placeholder name → replacement value. Values are
        stringified via ``str()`` before insertion.

    Raises
    ------
    TemplateError
        If a ``{placeholder}`` token has no matching context key.
    """
    out: dict[str, Any] = {}
    for key, val in kwargs.items():
        if isinstance(val, str):
            out[key] = _substitute_string(val, context)
        else:
            out[key] = val
    return out


def _substitute_string(template: str, context: Mapping[str, Any]) -> str:
    """Replace every ``{placeholder}`` in *template* using *context*.

    Literal ``{`` / ``}`` are not supported — the grammar is intentionally
    narrow: ``{name}`` with name matching ``[a-zA-Z_][a-zA-Z0-9_]*``.
    Anything outside that shape is copied verbatim.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise TemplateError(f"Unknown placeholder {{{key}}} in template {template!r}")
        return str(context[key])

    return _PLACEHOLDER.sub(_replace, template)
