"""Unit tests for voice_commander.validator.validate() and validate_config()."""

# NOTE: Do NOT add `from __future__ import annotations` here.
# validate() calls typing.get_type_hints(entry.func) which needs real runtime
# annotations, not stringified ones.

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from voice_commander.config import Config, LLMConfig
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ArgMetadata, ToolMetadata, ToolMetadataStore
from voice_commander.validator import validate, validate_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tools: dict[str, ToolMetadata]) -> MagicMock:
    """Return a mock ToolMetadataStore whose load_all() yields *tools*."""
    store = MagicMock(spec=ToolMetadataStore)
    store.load_all.return_value = tools
    return store


def _make_registry(*entries: ToolEntry) -> ToolRegistry:
    registry = ToolRegistry()
    for entry in entries:
        registry.register(entry)
    return registry


def _entry(
    name: str,
    func,
    *,
    module: str = "voice_commander.tools.fake",
    phrases: tuple[str, ...] = ("do thing",),
    llm_only: bool = False,
) -> ToolEntry:
    return ToolEntry(
        name=name,
        phrases=phrases,
        func=func,
        module=module,
        docstring=None,
        llm_only=llm_only,
    )


def _meta(
    name: str,
    *,
    phrases: tuple[str, ...] = ("do thing",),
    description: str = "A tool.",
    category: str = "test",
    enabled: bool = True,
    settle_ms: int = 0,
    llm_only: bool = False,
    args: dict[str, ArgMetadata] | None = None,
) -> ToolMetadata:
    return ToolMetadata(
        name=name,
        phrases=phrases,
        description=description,
        category=category,
        enabled=enabled,
        settle_ms=settle_ms,
        llm_only=llm_only,
        args=args or {},
    )


def _arg(name: str, type_str: str = "str", description: str = "A param.") -> ArgMetadata:
    return ArgMetadata(
        name=name,
        type_str=type_str,
        description=description,
        required=True,
        default=None,
    )


# ---------------------------------------------------------------------------
# Rule 2: sig param without TOML arg entry
# ---------------------------------------------------------------------------


def test_rule2_param_without_toml_arg():
    """A function parameter that has no matching [args.*] in TOML → [rule2]."""

    def my_tool(query: str) -> None:
        pass

    registry = _make_registry(_entry("my_tool", my_tool))
    # TOML metadata has no args block — 'query' is missing
    store = _make_store({"my_tool": _meta("my_tool", args={})})

    errors = validate(registry, store)

    assert any("[rule2]" in e for e in errors), f"Expected [rule2] error, got: {errors}"


# ---------------------------------------------------------------------------
# Rule 3: TOML arg not in signature
# ---------------------------------------------------------------------------


def test_rule3_toml_arg_not_in_signature():
    """TOML declares arg 'foo' but function has no such parameter → [rule3]."""

    def my_tool() -> None:
        pass

    registry = _make_registry(_entry("my_tool", my_tool))
    store = _make_store({"my_tool": _meta("my_tool", args={"foo": _arg("foo")})})

    errors = validate(registry, store)

    assert any("[rule3]" in e for e in errors), f"Expected [rule3] error, got: {errors}"


# ---------------------------------------------------------------------------
# Rule 4: unsupported type annotation
# ---------------------------------------------------------------------------


def test_rule4_unsupported_type():
    """A param annotated with `list` (unsupported) → [rule4]."""

    def my_tool(items: list) -> None:
        pass

    registry = _make_registry(_entry("my_tool", my_tool))
    store = _make_store(
        {"my_tool": _meta("my_tool", args={"items": _arg("items", type_str="list")})}
    )

    errors = validate(registry, store)

    assert any("[rule4]" in e for e in errors), f"Expected [rule4] error, got: {errors}"


# ---------------------------------------------------------------------------
# Rule 5: settle_ms out of range
# ---------------------------------------------------------------------------


def test_rule5_settle_ms_negative():
    """settle_ms=-1 is below the allowed range [0, 5000] → [rule5]."""

    def my_tool() -> None:
        pass

    registry = _make_registry(_entry("my_tool", my_tool))
    store = _make_store({"my_tool": _meta("my_tool", settle_ms=-1)})

    errors = validate(registry, store)

    assert any("[rule5]" in e for e in errors), f"Expected [rule5] error, got: {errors}"


def test_rule5_settle_ms_too_high():
    """settle_ms=5001 is above the allowed range [0, 5000] → [rule5]."""

    def my_tool() -> None:
        pass

    registry = _make_registry(_entry("my_tool", my_tool))
    store = _make_store({"my_tool": _meta("my_tool", settle_ms=5001)})

    errors = validate(registry, store)

    assert any("[rule5]" in e for e in errors), f"Expected [rule5] error, got: {errors}"


# ---------------------------------------------------------------------------
# Rule 7: missing required primitive
# ---------------------------------------------------------------------------


def test_rule7_missing_primitive():
    """Module is 'voice_commander.tools.primitives' but 'no_match' is absent → [rule7]."""

    def wait() -> None:
        pass

    # Register *only* 'wait' from the primitives module — 'no_match' is absent.
    entry = _entry(
        "wait",
        wait,
        module="voice_commander.tools.primitives",
        llm_only=True,
        phrases=(),
    )
    registry = _make_registry(entry)
    store = _make_store({"wait": _meta("wait", llm_only=True, phrases=())})

    errors = validate(registry, store)

    rule7_errors = [e for e in errors if "[rule7]" in e]
    assert rule7_errors, f"Expected [rule7] error, got: {errors}"
    assert any("no_match" in e for e in rule7_errors), (
        f"Expected 'no_match' mentioned in [rule7] errors, got: {rule7_errors}"
    )


# ---------------------------------------------------------------------------
# Happy path: no errors
# ---------------------------------------------------------------------------


def test_happy_path_all_valid():
    """A properly configured zero-arg tool produces an empty error list."""

    def my_tool() -> None:
        pass

    registry = _make_registry(_entry("my_tool", my_tool))
    store = _make_store({"my_tool": _meta("my_tool", args={})})

    errors = validate(registry, store)

    assert errors == [], f"Expected no errors, got: {errors}"


# ---------------------------------------------------------------------------
# Rule C1: llm_router.timeout_ms minimum
# ---------------------------------------------------------------------------


def _cfg_with_timeout(timeout_ms: int) -> "Config":
    """Build a default Config with llm_router.timeout_ms overridden."""
    llm_cfg = LLMConfig(timeout_ms=timeout_ms)
    return replace(Config(), llm=llm_cfg)


def test_rule_c1_timeout_ms_below_minimum():
    """timeout_ms=100 is below the 200 ms minimum → [rule_c1] error mentioning timeout_ms."""
    cfg = _cfg_with_timeout(100)

    errors = validate_config(cfg)

    assert any("[rule_c1]" in e for e in errors), f"Expected [rule_c1] error, got: {errors}"
    assert any("timeout_ms" in e for e in errors), (
        f"Expected 'timeout_ms' in error message, got: {errors}"
    )
    assert any("200" in e for e in errors), (
        f"Expected minimum value '200' mentioned in error message, got: {errors}"
    )


def test_rule_c1_timeout_ms_at_minimum_is_valid():
    """timeout_ms=200 is exactly at the minimum → no errors."""
    cfg = _cfg_with_timeout(200)

    errors = validate_config(cfg)

    assert errors == [], f"Expected no errors at timeout_ms=200, got: {errors}"


def test_rule_c1_timeout_ms_above_minimum_is_valid():
    """timeout_ms=600 is well above the minimum → no errors."""
    cfg = _cfg_with_timeout(600)

    errors = validate_config(cfg)

    assert errors == [], f"Expected no errors at timeout_ms=600, got: {errors}"


# ---------------------------------------------------------------------------
# Rule C2: speak.fuzzy_threshold range [0, 100] (ADR 0072)
# ---------------------------------------------------------------------------


def _cfg_with_speak_threshold(threshold: int) -> "Config":
    from voice_commander.config import SpeakConfig

    return replace(Config(), speak=SpeakConfig(fuzzy_threshold=threshold))


def test_rule_c2_fuzzy_threshold_below_zero_is_invalid():
    """fuzzy_threshold=-1 is below valid range → [rule_c2] error."""
    cfg = _cfg_with_speak_threshold(-1)

    errors = validate_config(cfg)

    assert any("[rule_c2]" in e for e in errors), f"Expected [rule_c2] error, got: {errors}"
    assert any("fuzzy_threshold" in e for e in errors), (
        f"Expected 'fuzzy_threshold' in error, got: {errors}"
    )


def test_rule_c2_fuzzy_threshold_above_100_is_invalid():
    """fuzzy_threshold=101 exceeds valid range → [rule_c2] error."""
    cfg = _cfg_with_speak_threshold(101)

    errors = validate_config(cfg)

    assert any("[rule_c2]" in e for e in errors), f"Expected [rule_c2] error, got: {errors}"


def test_rule_c2_fuzzy_threshold_at_boundaries_is_valid():
    """fuzzy_threshold=0 and fuzzy_threshold=100 are both valid."""
    for boundary in (0, 100):
        cfg = _cfg_with_speak_threshold(boundary)
        errors = validate_config(cfg)
        assert errors == [], f"Expected no errors at fuzzy_threshold={boundary}, got: {errors}"


def test_rule_c2_fuzzy_threshold_default_95_is_valid():
    """Default fuzzy_threshold=95 passes validation."""
    cfg = Config()
    errors = validate_config(cfg)
    assert not any("[rule_c2]" in e for e in errors), (
        f"Default config should not trigger [rule_c2], got: {errors}"
    )


# ---------------------------------------------------------------------------
# system = true flag: ToolMetadata with system=True must pass validation
# ---------------------------------------------------------------------------


def test_system_tool_passes_validation():
    """A tool marked system=True with no phrases still passes rule validation.

    Phrases are optional for all tools (rule2 only checks args, not phrases).
    This test confirms system tools do not trigger any unexpected validation rules.
    """

    def speak_fn() -> None:
        pass

    entry = _entry("speak", speak_fn, phrases=())
    meta = ToolMetadata(
        name="speak",
        phrases=(),
        description="Toggle Windows voice dictation on/off.",
        category="system",
        enabled=True,
        system=True,
        internal=False,
        llm_only=False,
    )
    registry = _make_registry(entry)
    store = _make_store({"speak": meta})

    errors = validate(registry, store)

    assert errors == [], f"system=True tool should pass validation, got: {errors}"


# ---------------------------------------------------------------------------
# validate_graphs_or_die: startup graph validation
# ---------------------------------------------------------------------------


def test_validate_graphs_or_die_raises_on_cycle(tmp_path):
    """validate_graphs_or_die raises SystemExit when a graph has a cycle."""
    import json

    from voice_commander.commands.graph import Edge, Graph, Node, PortRef
    from voice_commander.commands.store import GraphStore
    from voice_commander.registry import ToolRegistry
    from voice_commander.validator import validate_graphs_or_die

    # Build a graph with a cycle
    cmd_path = tmp_path / "commands.json"
    store = GraphStore(cmd_path, kind="command")
    g = Graph(
        name="cyclic",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("a", "pipeline.press", {}), Node("b", "pipeline.press", {})),
        edges=(
            Edge(PortRef("a", "ok"), PortRef("b", "in")),
            Edge(PortRef("b", "ok"), PortRef("a", "in")),
        ),
    )
    store.save_one(g)
    wf_path = tmp_path / "workflows.json"
    wf_path.write_text(json.dumps({"schema_version": 1, "graphs": {}}))
    wf_store = GraphStore(wf_path, kind="workflow")

    reg = ToolRegistry()

    with pytest.raises(SystemExit):
        validate_graphs_or_die(store, wf_store, reg)
