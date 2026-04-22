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


def test_all_llm_visible_filters_correctly():
    """all_llm_visible() must return exactly the enabled+llm_only=True tool.

    Registers three tools:
      - enabled=True,  llm_only=True   → visible
      - enabled=True,  llm_only=False  → hidden
      - enabled=False, llm_only=True   → hidden

    Only the first should appear in the result.
    """
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="alpha",
            phrases=(),
            func=lambda: None,
            module="t",
            docstring=None,
            enabled=True,
            llm_only=True,
        )
    )
    registry.register(
        ToolEntry(
            name="beta",
            phrases=(),
            func=lambda: None,
            module="t",
            docstring=None,
            enabled=True,
            llm_only=False,
        )
    )
    registry.register(
        ToolEntry(
            name="gamma",
            phrases=(),
            func=lambda: None,
            module="t",
            docstring=None,
            enabled=False,
            llm_only=True,
        )
    )

    visible = registry.all_llm_visible()
    visible_names = [e.name for e in visible]
    assert visible_names == ["alpha"], (
        f"Expected all_llm_visible() to return only the enabled+llm_only tool "
        f"('alpha'), got {visible_names}"
    )
