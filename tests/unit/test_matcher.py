from voice_commander.matcher import Matcher
from voice_commander.registry import ToolEntry, ToolRegistry


def _registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ToolEntry("copy", ("copy", "copy that"), lambda: None, "m", None))
    r.register(ToolEntry("paste", ("paste",), lambda: None, "m", None))
    r.register(ToolEntry("new_tab", ("new tab", "open new tab"), lambda: None, "m", None))
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
    r.register(ToolEntry("zulu", ("same",), lambda: None, "m", None))
    r.register(ToolEntry("alpha", ("same",), lambda: None, "m", None))
    m = Matcher(r, threshold=50.0)
    result = m.match("same")
    assert result.tool.name == "alpha"
