"""Replay tooling — re-route a past transcript through VerbRouter and optionally re-fire it."""

from __future__ import annotations

from typing import Any

from voice_commander.observability.store import Store
from voice_commander.observability.tracer import Tracer



def replay_full(
    store: Store,
    run_id: str,
    dispatcher: Any,
    registry: Any,
    tracer: Tracer,
) -> str:
    """Re-route AND re-fire the plan via VerbRouter. **DESTRUCTIVE** — re-types into foreground.

    Returns the run_id of the new replay run.
    """
    from voice_commander.verb_router import VerbRouter, build_default_rules

    _run = store.get_run(run_id)
    if _run is None:
        raise ValueError(f"run {run_id} not found")
    transcript = _run["transcript"]
    if not transcript:
        raise ValueError("run has no transcript; cannot replay")

    verb_router = VerbRouter(build_default_rules())

    with tracer.run(transcript) as new_run:
        with tracer.span("replay_marker", name="replay_marker", replay_of=run_id):
            pass
        plan = verb_router.route(transcript)
        if plan is not None:
            dispatcher.run_plan(transcript, plan, registry)
    return new_run.run_id
