from voice_commander.matcher import Matcher
from voice_commander.registry import ToolEntry, ToolRegistry


def _registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        ToolEntry(
            name="copy",
            phrases=("copy", "copy that"),
            func=lambda: None,
            module="m",
            docstring=None,
        )
    )
    r.register(
        ToolEntry(name="paste", phrases=("paste",), func=lambda: None, module="m", docstring=None)
    )
    r.register(
        ToolEntry(
            name="new_tab",
            phrases=("new tab", "open new tab"),
            func=lambda: None,
            module="m",
            docstring=None,
        )
    )
    return r


def test_exact_match():
    m = Matcher(_registry(), threshold=85.0)
    result = m.match("copy")
    assert result.tool is not None and result.tool.name == "copy"
    assert result.phrase == "copy"
    assert result.score >= 99.0


def test_fuzzy_match_with_punctuation():
    m = Matcher(_registry(), threshold=85.0)
    result = m.match("Copy, please!")
    assert result.tool is not None and result.tool.name == "copy"


def test_below_threshold_returns_none():
    m = Matcher(_registry(), threshold=85.0)
    result = m.match("xyzzy wibble")
    assert result.tool is None
    assert result.phrase is None
    assert len(result.candidates) > 0


def test_candidates_are_top_5_sorted_desc():
    m = Matcher(_registry(), threshold=85.0)
    result = m.match("new tab")
    scores = [c[2] for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
    assert len(result.candidates) <= 5


def test_tiebreak_alphabetical():
    r = ToolRegistry()
    r.register(
        ToolEntry(name="zulu", phrases=("same",), func=lambda: None, module="m", docstring=None)
    )
    r.register(
        ToolEntry(name="alpha", phrases=("same",), func=lambda: None, module="m", docstring=None)
    )
    m = Matcher(r, threshold=50.0)
    result = m.match("same")
    assert result.tool.name == "alpha"
