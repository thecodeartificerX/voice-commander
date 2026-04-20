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


def test_flat_phrases():
    registry = ToolRegistry()
    registry.register(
        ToolEntry(name="t1", phrases=("a", "b"), func=lambda: None, module="m", docstring=None)
    )
    registry.register(
        ToolEntry(name="t2", phrases=("c",), func=lambda: None, module="m", docstring=None)
    )
    assert sorted(registry.flat_phrases()) == [("a", "t1"), ("b", "t1"), ("c", "t2")]
