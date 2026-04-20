from voice_commander.matcher import Matcher
from voice_commander.registry import ToolEntry, ToolRegistry


def _registry_with_disabled():
    r = ToolRegistry()
    r.register(
        ToolEntry(
            name="active_tool",
            phrases=("active phrase",),
            func=lambda: None,
            module="m",
            docstring=None,
            enabled=True,
        )
    )
    r.register(
        ToolEntry(
            name="disabled_tool",
            phrases=("disabled phrase",),
            func=lambda: None,
            module="m",
            docstring=None,
            enabled=False,
        )
    )
    return r


def test_disabled_tool_phrases_absent_from_flat_phrases_enabled():
    r = _registry_with_disabled()
    phrases = r.flat_phrases_enabled()
    names = [name for _, name in phrases]
    assert "active_tool" in names
    assert "disabled_tool" not in names


def test_matcher_does_not_match_disabled_tool():
    r = _registry_with_disabled()
    m = Matcher(r, threshold=50.0)
    result = m.match("disabled phrase")
    # Should not match since tool is disabled
    assert result.tool is None or result.tool.name != "disabled_tool"


def test_reenable_restores_phrases():
    r = _registry_with_disabled()
    # Re-enable
    entry = r.by_name("disabled_tool")
    entry.enabled = True

    phrases = r.flat_phrases_enabled()
    names = [name for _, name in phrases]
    assert "disabled_tool" in names
