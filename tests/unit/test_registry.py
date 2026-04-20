import pytest

from voice_commander.registry import (
    DuplicateToolError,
    get_global_registry,
    reset_global_registry,
    tool,
)


def setup_function():
    reset_global_registry()


def test_decorator_registers_function():
    @tool(phrases=["copy", "copy that"])
    def copy():
        return "copied"

    registry = get_global_registry()
    entry = registry.by_name("copy")
    assert entry is not None
    assert entry.phrases == ("copy", "copy that")
    assert entry.func() == "copied"


def test_duplicate_raises():
    @tool(phrases=["x"])
    def foo():
        pass

    with pytest.raises(DuplicateToolError):

        @tool(phrases=["y"])
        def foo():  # noqa: F811
            pass


def test_phrases_are_normalized():
    @tool(phrases=["  Copy THIS.  ", "Focus,  Browser!"])
    def bar():
        pass

    registry = get_global_registry()
    entry = registry.by_name("bar")
    assert entry.phrases == ("copy this", "focus browser")


def test_flat_phrases():
    @tool(phrases=["a", "b"])
    def t1():
        pass

    @tool(phrases=["c"])
    def t2():
        pass

    registry = get_global_registry()
    assert sorted(registry.flat_phrases()) == [("a", "t1"), ("b", "t1"), ("c", "t2")]
