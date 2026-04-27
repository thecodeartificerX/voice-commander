from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from voice_commander.config import LLMConfig
from voice_commander.event_bus import EventBus
from voice_commander.llm_router import LLMRouter
from voice_commander.observability.store import Store
from voice_commander.observability.tracer import Tracer
from voice_commander.registry import ToolEntry, ToolRegistry


def _drain(store: Store) -> None:
    store.flush()


def test_llm_router_writes_prompt_and_response_to_span(tmp_path):
    cfg = LLMConfig()
    registry = ToolRegistry()
    # Add an llm-visible tool so route() doesn't early-return on empty tools list
    focus_entry = ToolEntry(
        name="focus", phrases=("focus",), func=lambda target=None: None,
        module="m", docstring=None, llm_only=True, internal=False, enabled=True,
        params_schema={"type": "function", "function": {"name": "focus", "parameters": {}}},
    )
    registry.register(focus_entry)
    lock = threading.Lock()
    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "choices": [{"message": {"tool_calls": []}}],
    }
    fake_resp.raise_for_status = MagicMock()

    router = LLMRouter(cfg, registry, lock)
    router.set_tracer(tracer)

    with patch.object(router._client, "post", return_value=fake_resp), tracer.run("hi") as run:
        router.route("hi")

    _drain(store)

    spans = store.get_spans(run.run_id)
    llm_spans = [s for s in spans if s["type"] == "llm_call"]
    assert llm_spans, f"Expected llm_call span, got types: {[s['type'] for s in spans]}"
    attrs = llm_spans[0]["attrs"]
    assert "prompt_full" in attrs, f"Expected 'prompt_full' in attrs, got {list(attrs.keys())}"
    assert "raw_response" in attrs, f"Expected 'raw_response' in attrs, got {list(attrs.keys())}"
    assert "model" in attrs, f"Expected 'model' in attrs, got {list(attrs.keys())}"

    store.stop()
