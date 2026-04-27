"""Replay tooling — LLM-replay (safe) and full-replay (footgun)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from voice_commander.observability.store import Store


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    old_plan: list[dict[str, Any]]
    new_plan: list[dict[str, Any]] | None
    changed: bool
    error: str | None = None


def replay_llm(store: Store, run_id: str, router: Any) -> ReplayResult:
    """Re-route the original transcript through the current LLMRouter.

    No tool fires. Returns old plan vs new plan + ``changed`` flag.
    """
    spans = store.get_spans(run_id)
    llm = next((s for s in spans if s["type"] == "llm_call"), None)
    if llm is None:
        return ReplayResult(run_id=run_id, old_plan=[], new_plan=None,
                            changed=False, error="no llm_call span")
    _run = store.get_run(run_id)
    transcript = llm["attrs"].get("transcript") or cast(dict[str, Any], _run)["transcript"]
    old_plan = (llm["output"] or {}).get("steps", [])
    plan = router.route(transcript)
    new_plan = (
        [{"name": s.name, "kwargs": dict(s.kwargs)} for s in plan.steps]
        if plan is not None else None
    )
    return ReplayResult(
        run_id=run_id,
        old_plan=old_plan,
        new_plan=new_plan,
        changed=new_plan != old_plan,
    )


def replay_full(
    store: Store, run_id: str, router: Any,
    dispatcher: Any, registry: Any, tracer: Any,
) -> str:
    """Re-route AND re-fire the plan. **DESTRUCTIVE** — re-types into foreground.

    Returns the run_id of the new replay run.
    """
    spans = store.get_spans(run_id)
    llm = next((s for s in spans if s["type"] == "llm_call"), None)
    if llm is None:
        raise ValueError("no llm_call span; cannot replay")
    _run = store.get_run(run_id)
    transcript = llm["attrs"].get("transcript") or cast(dict[str, Any], _run)["transcript"]

    with tracer.run(transcript) as new_run:
        with tracer.span("replay_marker", name="replay_marker", replay_of=run_id):
            pass
        plan = router.route(transcript)
        if plan is not None:
            dispatcher.run_plan(transcript, plan, registry)
    return cast(str, new_run.run_id)
