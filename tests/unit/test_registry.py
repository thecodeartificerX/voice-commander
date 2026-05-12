import pytest

from voice_commander.registry import (
    DuplicateToolError,
    ToolEntry,
    ToolRegistry,
    _normalize,
    get_global_registry,
    reset_global_registry,
    tool,
)


def setup_function():
    reset_global_registry()


def test_decorator_registers_function():
    @tool
    def copy():
        return "copied"

    registry = get_global_registry()
    entry = registry.by_name("copy")
    assert entry is not None
    assert entry.func() == "copied"
    # Phrases are empty until bind_metadata is called
    assert entry.phrases == ()


def test_duplicate_raises():
    @tool
    def foo():
        pass

    with pytest.raises(DuplicateToolError):

        @tool
        def foo():  # noqa: F811
            pass


def test_phrases_are_normalized():
    # _normalize is the canonical normaliser; test it directly
    assert _normalize("  Copy THIS.  ") == "copy this"
    assert _normalize("Focus,  Browser!") == "focus browser"


def test_by_origin_groups_entries():
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="press",
            phrases=(),
            func=lambda: None,
            module="t",
            docstring=None,
            origin="primitive",
        )
    )
    registry.register(
        ToolEntry(
            name="new_tab",
            phrases=(),
            func=lambda: None,
            module="t",
            docstring=None,
            origin="command",
        )
    )
    registry.register(
        ToolEntry(
            name="search_web",
            phrases=(),
            func=lambda: None,
            module="t",
            docstring=None,
            origin="workflow",
        )
    )
    assert [e.name for e in registry.by_origin("primitive")] == ["press"]
    assert [e.name for e in registry.by_origin("command")] == ["new_tab"]
    assert [e.name for e in registry.by_origin("workflow")] == ["search_web"]


def test_remove_drops_entry():
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="ephemeral",
            phrases=(),
            func=lambda: None,
            module="t",
            docstring=None,
        )
    )
    assert registry.by_name("ephemeral") is not None
    assert registry.remove("ephemeral") is True
    assert registry.by_name("ephemeral") is None
    # Second remove returns False (no-op).
    assert registry.remove("ephemeral") is False
