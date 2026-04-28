"""DAG execution engine for command/workflow graphs.

This module owns the sequential walk over a Graph's nodes, the port-value
cache (typed data + control flags), and the conversion of execution events
into a PlanOutcome wire object that the daemon publishes via EventBus.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from voice_commander.commands.graph import Edge, Graph, Node
from voice_commander.commands.graph_topo import CycleError, topo_sort
from voice_commander.observability.errors import classify as _classify_error
from voice_commander.plan import PlanOutcome, PlanStatus, ToolCall
from voice_commander.registry import ToolRegistry

if TYPE_CHECKING:
    from voice_commander.observability import Tracer

logger = logging.getLogger(__name__)

_MAX_CALL_DEPTH = 16  # cross-graph (command.X / workflow.X) recursion guard


class WiringError(Exception):
    """Raised when a graph node's required kwargs cannot be resolved from wires or literals.

    This is a structural graph authoring error, not a tool bug.
    Classified as 'wiring' by the error categorizer.
    """

_GraphLookup = Callable[[str], "Graph | None"]


class GraphRuntime:
    """Executes a Graph against a ToolRegistry; produces a PlanOutcome."""

    def __init__(
        self,
        registry: ToolRegistry,
        graph_lookup: _GraphLookup,
        tracer: Tracer | None = None,
    ) -> None:
        """Store tool registry and graph-lookup closure for use during execution."""
        self._registry = registry
        self._graph_lookup = graph_lookup
        self._tracer = tracer

    def run(
        self, graph: Graph, inputs: Mapping[str, Any], *, _call_depth: int = 0
    ) -> tuple[PlanOutcome, Any]:
        """Execute the graph. Returns (outcome, graph_return)."""
        if _call_depth > _MAX_CALL_DEPTH:
            return (
                PlanOutcome(
                    transcript=f"graph:{graph.name}",
                    steps=(),
                    status="error",
                    failed_step_index=None,
                    error_msg=(
                        f"cross-graph call depth {_call_depth} exceeds limit {_MAX_CALL_DEPTH}"
                    ),
                    duration_ms=0,
                ),
                None,
            )
        with self._span("graph", name=graph.name, kind=graph.kind) as _graph_span:
            outcome, graph_return = self._run_inner(graph, inputs, _call_depth=_call_depth)
            if _graph_span is not None and hasattr(_graph_span, "set_output"):
                _graph_span.set_output({"status": outcome.status, "steps": len(outcome.steps)})
        return outcome, graph_return

    def _run_inner(
        self, graph: Graph, inputs: Mapping[str, Any], *, _call_depth: int = 0
    ) -> tuple[PlanOutcome, Any]:
        """Inner execution logic extracted so graph span wraps cleanly."""
        start = time.monotonic()
        port_values: dict[str, Any] = {}
        fired_ok: set[str] = set()
        fired_err: set[str] = set()
        fired_branch_true: set[str] = set()
        fired_branch_false: set[str] = set()
        steps: list[ToolCall] = []
        failed_idx: int | None = None
        error_msg: str | None = None
        graph_return: Any = None

        # Park graph inputs
        for inp in graph.inputs:
            port_values[f"input.{inp.name}"] = inputs.get(inp.name)

        try:
            order = topo_sort(graph.nodes, graph.edges)
        except CycleError as exc:
            return (
                PlanOutcome(
                    transcript=f"graph:{graph.name}",
                    steps=(),
                    status="error",
                    failed_step_index=None,
                    error_msg=f"cycle in graph: {exc}",
                    duration_ms=int((time.monotonic() - start) * 1000),
                ),
                None,
            )

        # Pre-compute which nodes live exclusively inside a foreach body so we
        # can skip them in the outer walk (they are driven by the foreach loop).
        foreach_body_ids: set[str] = set()
        for node in order:
            if node.ref == "control.foreach":
                foreach_body_ids |= self._foreach_body_ids(node.id, graph)

        for node in order:
            # Skip nodes that are exclusively managed inside a foreach body
            if node.id in foreach_body_ids:
                continue

            # Timeout check
            if (time.monotonic() - start) * 1000 > graph.timeout_ms:
                error_msg = "graph timeout"
                if failed_idx is None:
                    failed_idx = len(steps)
                break

            if not self._control_satisfied(
                node, graph.edges, fired_ok, fired_err, fired_branch_true, fired_branch_false
            ):
                continue

            try:
                kwargs = self._resolve_kwargs(node, graph.edges, port_values)
            except WiringError as exc:
                logger.warning("graph %r node %r: %s", graph.name, node.id, exc)
                cat = "wiring"
                with self._span("node", name=node.ref, node_id=node.id) as _wire_span:
                    if _wire_span is not None and hasattr(_wire_span, "set_error_category"):
                        _wire_span.set_error_category(cat)
                if failed_idx is None:
                    failed_idx = len(steps)
                    error_msg = str(exc)[:256]
                fired_err.add(node.id)
                if graph.strict and not self._has_error_edge(node.id, graph.edges):
                    break
                continue

            # --- pipeline node ---
            if node.ref.startswith("pipeline."):
                tool_name = node.ref.removeprefix("pipeline.")
                entry = self._registry.by_name(tool_name)
                if entry is None:
                    logger.warning("unknown pipeline ref %r (node %s)", node.ref, node.id)
                    error_msg = error_msg or f"unknown pipeline ref: {node.ref}"
                    if failed_idx is None:
                        failed_idx = len(steps)
                    fired_err.add(node.id)
                    if graph.strict and not self._has_error_edge(node.id, graph.edges):
                        break
                    continue
                with self._span("node", name=node.ref, node_id=node.id) as _node_span:
                    try:
                        ret = entry.func(**kwargs)
                        if _node_span is not None and hasattr(_node_span, "set_output"):
                            _node_span.set_output(ret)
                    except Exception as exc:  # noqa: BLE001
                        cat = _classify_error(exc, where="graph_runtime")
                        if _node_span is not None and hasattr(_node_span, "set_error_category"):
                            _node_span.set_error_category(cat)
                        if failed_idx is None:
                            failed_idx = len(steps)
                            error_msg = str(exc)[:256]
                        fired_err.add(node.id)
                        steps.append(ToolCall(name=node.ref, kwargs=kwargs))
                        if graph.strict and not self._has_error_edge(node.id, graph.edges):
                            break
                        continue
                fired_ok.add(node.id)
                self._record_returns(node, entry, ret, port_values)
                steps.append(ToolCall(name=node.ref, kwargs=kwargs))
                continue

            # --- control.branch ---
            # Fire only the matching outgoing port so downstream sees one path, not both.
            if node.ref == "control.branch":
                cond_val = bool(kwargs.get("cond"))
                if cond_val:
                    fired_branch_true.add(node.id)
                else:
                    fired_branch_false.add(node.id)
                fired_ok.add(node.id)
                steps.append(ToolCall(name=node.ref, kwargs={"cond": cond_val}))
                continue

            # --- control.foreach ---
            # Body ports are cleared per iteration so prior items don't leak into the next.
            if node.ref == "control.foreach":
                items = list(kwargs.get("list") or [])
                cap = graph.foreach_iteration_cap
                if len(items) > cap:
                    items = items[:cap]
                body_ids = self._foreach_body_ids(node.id, graph)
                body_nodes = [n for n in order if n.id in body_ids]
                # Collect port keys written by body nodes so we can clear between iterations
                body_port_keys: list[str] = []
                for bn in body_nodes:
                    if not bn.ref.startswith("pipeline."):
                        continue
                    entry = self._registry.by_name(bn.ref.removeprefix("pipeline."))
                    if entry is None:
                        continue
                    for port in getattr(entry, "returns_meta", None) or {}:
                        body_port_keys.append(f"{bn.id}.{port}")
                foreach_has_error = False
                foreach_first_error: str | None = None
                foreach_timed_out = False
                for iter_idx, item_val in enumerate(items):
                    if (time.monotonic() - start) * 1000 > graph.timeout_ms:
                        foreach_timed_out = True
                        break
                    with self._span(
                        "foreach_iter",
                        name=f"{node.ref}[{iter_idx}]",
                        node_id=node.id,
                        iter_idx=iter_idx,
                        item=repr(item_val)[:128],
                    ):
                        for key in body_port_keys:
                            port_values.pop(key, None)
                        port_values[f"{node.id}.item"] = item_val
                        body_fired_ok: set[str] = set()
                        body_fired_ok.add(node.id)  # foreach node itself is "ok" for body
                        body_fired_err: set[str] = set()
                        body_fired_branch_true: set[str] = set()
                        body_fired_branch_false: set[str] = set()
                        for bn in body_nodes:
                            if (time.monotonic() - start) * 1000 > graph.timeout_ms:
                                foreach_timed_out = True
                                break
                            if not self._control_satisfied(
                                bn,
                                graph.edges,
                                body_fired_ok,
                                body_fired_err,
                                body_fired_branch_true,
                                body_fired_branch_false,
                            ):
                                continue
                            try:
                                bkwargs = self._resolve_kwargs(bn, graph.edges, port_values)
                            except WiringError as exc:
                                logger.warning(
                                    "graph %r foreach node %r: %s", graph.name, bn.id, exc
                                )
                                body_fired_err.add(bn.id)
                                if not foreach_has_error:
                                    foreach_has_error = True
                                    foreach_first_error = (
                                        f"foreach iter {iter_idx}: wiring error: {exc}"
                                    )
                                continue
                            action, body_err_msg = self._dispatch_pipeline_node(
                                bn,
                                bkwargs,
                                graph,
                                steps,
                                port_values,
                                body_fired_ok,
                                body_fired_err,
                            )
                            if body_err_msg is not None and not foreach_has_error:
                                foreach_has_error = True
                                foreach_first_error = f"foreach iter {iter_idx}: {body_err_msg}"
                            if action == "break":
                                break
                    if foreach_timed_out:
                        break
                    steps.append(ToolCall(name=f"{node.ref}/iter", kwargs={"item": item_val}))
                if foreach_timed_out:
                    error_msg = error_msg or "graph timeout"
                    if failed_idx is None:
                        failed_idx = len(steps)
                    break
                if foreach_has_error:
                    if failed_idx is None:
                        failed_idx = len(steps)
                        error_msg = foreach_first_error
                    fired_err.add(node.id)
                    if graph.strict:
                        break
                else:
                    fired_ok.add(node.id)
                continue

            # --- value.constant ---
            if node.ref == "value.constant":
                val = kwargs.get("value")
                port_values[f"{node.id}.value"] = val
                fired_ok.add(node.id)
                continue

            # --- value.input ---
            if node.ref == "value.input":
                # Re-park graph inputs under this node's id too
                for inp in graph.inputs:
                    port_values[f"{node.id}.{inp.name}"] = inputs.get(inp.name)
                fired_ok.add(node.id)
                continue

            # --- value.output ---
            if node.ref == "value.output":
                graph_return = kwargs.get("value")
                fired_ok.add(node.id)
                continue

            # --- cross-graph (command.X / workflow.X) ---
            # Recurse with depth guard; child steps merge into parent for a flat audit trail.
            if node.ref.startswith("command.") or node.ref.startswith("workflow."):
                child_name = node.ref.split(".", 1)[1]
                child = self._graph_lookup(child_name)
                if child is None:
                    if failed_idx is None:
                        failed_idx = len(steps)
                        error_msg = f"unknown cross-graph ref: {node.ref}"
                    fired_err.add(node.id)
                    steps.append(ToolCall(name=node.ref, kwargs=kwargs))
                    if graph.strict and not self._has_error_edge(node.id, graph.edges):
                        break
                    continue
                child_outcome, child_return = self.run(child, kwargs, _call_depth=_call_depth + 1)
                steps.extend(child_outcome.steps)
                if child_outcome.status == "error":
                    if failed_idx is None:
                        failed_idx = len(steps)
                        error_msg = child_outcome.error_msg
                    fired_err.add(node.id)
                    steps.append(ToolCall(name=node.ref, kwargs=kwargs))
                    if graph.strict and not self._has_error_edge(node.id, graph.edges):
                        break
                    continue
                if child_return is not None:
                    port_values[f"{node.id}.value"] = child_return
                fired_ok.add(node.id)
                steps.append(ToolCall(name=node.ref, kwargs=kwargs))
                continue

            logger.debug("graph_runtime: skipping unsupported ref %r", node.ref)

        status: PlanStatus = "error" if failed_idx is not None else "ok"
        duration_ms = int((time.monotonic() - start) * 1000)
        outcome = PlanOutcome(
            transcript=f"graph:{graph.name}",
            steps=tuple(steps),
            status=status,
            failed_step_index=failed_idx,
            error_msg=error_msg,
            duration_ms=duration_ms,
        )
        return outcome, graph_return

    # ------------------------------------------------------------------ helpers

    def _dispatch_pipeline_node(
        self,
        node: Node,
        kwargs: dict[str, Any],
        graph: Graph,
        steps: list[ToolCall],
        port_values: dict[str, Any],
        fired_ok: set[str],
        fired_err: set[str],
    ) -> tuple[str, str | None]:
        """Dispatch a pipeline node inside a foreach body. Returns (action, error_msg).

        Called exclusively from the foreach body iteration loop; *action* is
        ``"break"`` when strict mode should halt the foreach loop, otherwise
        ``"continue"``.  *error_msg* is ``None`` on success.
        """
        tool_name = node.ref.removeprefix("pipeline.")
        entry = self._registry.by_name(tool_name)
        if entry is None:
            logger.warning("foreach body: unknown pipeline ref %r (node %s)", node.ref, node.id)
            fired_err.add(node.id)
            steps.append(ToolCall(name=node.ref, kwargs=kwargs))
            action = "break" if graph.strict else "continue"
            return action, f"unknown pipeline ref: {node.ref}"
        ret = None
        with self._span("node", name=node.ref, node_id=node.id) as _node_span:
            try:
                ret = entry.func(**kwargs)
                if _node_span is not None and hasattr(_node_span, "set_output"):
                    _node_span.set_output(ret)
            except Exception as exc:  # noqa: BLE001
                cat = _classify_error(exc, where="graph_runtime")
                if _node_span is not None and hasattr(_node_span, "set_error_category"):
                    _node_span.set_error_category(cat)
                logger.warning("foreach body node %s raised: %s", node.ref, exc)
                fired_err.add(node.id)
                steps.append(ToolCall(name=node.ref, kwargs=kwargs))
                exc_str = str(exc)
                msg = exc_str[:256] + ("..." if len(exc_str) > 256 else "")
                if graph.strict and not self._has_error_edge(node.id, graph.edges):
                    return "break", msg
                return "continue", msg
        fired_ok.add(node.id)
        self._record_returns(node, entry, ret, port_values)
        steps.append(ToolCall(name=node.ref, kwargs=kwargs))
        return "continue", None

    def _control_satisfied(
        self,
        node: Node,
        edges: tuple[Edge, ...],
        fired_ok: set[str],
        fired_err: set[str],
        fired_branch_true: set[str],
        fired_branch_false: set[str],
    ) -> bool:
        """Check if a node's control-flow dependencies are satisfied.

        Returns True when the node has no incoming control edges (unconditional
        start) or when at least one incoming edge's source port (ok, error,
        true, false, item, after) has fired.
        """
        incoming = [e for e in edges if e.dst.node_id == node.id and e.dst.port == "in"]
        if not incoming:
            return True
        for e in incoming:
            src_id = e.src.node_id
            if e.src.port == "ok" and src_id in fired_ok:
                return True
            if e.src.port == "error" and src_id in fired_err:
                return True
            if e.src.port == "true" and src_id in fired_branch_true:
                return True
            if e.src.port == "false" and src_id in fired_branch_false:
                return True
            if e.src.port == "item" and src_id in fired_ok:
                return True
            if e.src.port == "after" and src_id in fired_ok:
                return True
        return False

    def _resolve_kwargs(
        self,
        node: Node,
        edges: tuple[Edge, ...],
        port_values: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the kwargs dict for a node.

        Starts from the node's static kwargs, then overlays values arriving
        on data-wire edges.  Uses the ``input.<port>`` shorthand key when the
        source is a graph-input node.

        Raises ``WiringError`` if a data-wire edge's source value has not been
        populated (source node was not executed or produced no output for that
        port), indicating a structural graph-authoring error.
        """
        out = dict(node.kwargs)
        for e in edges:
            if e.dst.node_id != node.id or e.dst.port == "in":
                continue
            src_key = (
                f"input.{e.src.port}"
                if e.src.is_input_shorthand
                else f"{e.src.node_id}.{e.src.port}"
            )
            if src_key in port_values:
                out[e.dst.port] = port_values[src_key]
            elif not e.src.is_input_shorthand:
                # A data wire exists from a peer node whose output was never
                # recorded — the graph is structurally broken.
                raise WiringError(
                    f"node {node.id!r}: data wire from {src_key!r} "
                    f"to port {e.dst.port!r} has no value "
                    f"(source node did not produce output)"
                )
        return out

    def _record_returns(
        self,
        node: Node,
        entry: Any,
        ret: Any,
        port_values: dict[str, Any],
    ) -> None:
        """Cache a tool's return value in port_values under the node's output ports.

        If the tool declares a single return port the value is stored directly.
        For multiple return ports, unpacks a tuple/list return into individual
        ports when the lengths match.
        """
        meta = getattr(entry, "returns_meta", None) or {}
        if not meta:
            return
        if len(meta) == 1:
            (port_name,) = meta.keys()
            port_values[f"{node.id}.{port_name}"] = ret
        else:
            keys = list(meta.keys())
            if isinstance(ret, (tuple, list)) and len(ret) == len(keys):
                for k, v in zip(keys, ret, strict=True):
                    port_values[f"{node.id}.{k}"] = v
            else:
                logger.warning(
                    "node %s: expected %d-item tuple/list return, got %s; "
                    "multi-port values not recorded",
                    node.id,
                    len(keys),
                    type(ret).__name__,
                )

    def _span(self, *args: Any, **kwargs: Any) -> contextlib.AbstractContextManager[Any]:
        """Return a live tracer span, or a no-op context if tracing is disabled."""
        if self._tracer is not None and getattr(self._tracer, "enabled", False):
            return self._tracer.span(*args, **kwargs)
        return contextlib.nullcontext()

    def _has_error_edge(self, node_id: str, edges: tuple[Edge, ...]) -> bool:
        """Return True if any outgoing edge from *node_id* has source port ``"error"``."""
        return any(e.src.node_id == node_id and e.src.port == "error" for e in edges)

    # Control ports that carry the body membership signal from a foreach node.
    # Only these ports form body-inclusion edges; data ports (e.g. typed returns)
    # must not pull downstream consumers into the body set.
    _FOREACH_BODY_CONTROL_PORTS: frozenset[str] = frozenset(
        {"item", "ok", "error", "true", "false"}
    )

    def _foreach_body_ids(self, foreach_node_id: str, graph: Graph) -> set[str]:
        """Find all node ids in the foreach body by walking control edges forward.

        Only edges whose source port is one of the recognised control ports
        (item, ok, error, true, false) are traversed.  Data-port edges (typed
        return values forwarded to downstream nodes) do NOT pull those downstream
        nodes into the foreach body, preventing the outer walk from skipping them.
        """
        body: set[str] = set()
        frontier = {foreach_node_id}
        while frontier:
            next_frontier: set[str] = set()
            for e in graph.edges:
                if (
                    e.src.node_id in frontier
                    and e.dst.node_id not in body
                    and e.src.port in self._FOREACH_BODY_CONTROL_PORTS
                ):
                    next_node = e.dst.node_id
                    if next_node != foreach_node_id and next_node not in body:
                        body.add(next_node)
                        next_frontier.add(next_node)
            frontier = next_frontier
        return body
