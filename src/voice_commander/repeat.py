"""Repeat-count modifier parsing and plan expansion (ADR 0098).

A *repeat modifier* is a trailing phrase on a normal-mode utterance asking for
the same command to run several times in a row:

  * ``"<base> twice"``   → run ``<base>`` 2×
  * ``"<base> thrice"``  → run ``<base>`` 3×
  * ``"<base> once"``    → run ``<base>`` 1× (a no-op wrapper)
  * ``"<base> N times"`` → run ``<base>`` N× where ``N`` is a number word
    (``one``..``twenty``) or a digit string (``"4"``). The singular ``time``
    is accepted too so ``"one time"`` parses.

Examples::

    "go down twice"         -> ("go down", 2)
    "scroll up thrice"      -> ("scroll up", 3)
    "scroll down 4 times"   -> ("scroll down", 4)
    "press enter two times" -> ("press enter", 2)

Like the ``chain`` meta-verb (ADR 0085), the expanded plan interleaves a
synthetic, HUD-suppressed ``wait`` step between repetitions so the target app
registers each repeat as a distinct event rather than coalescing them.

This module is intentionally pure — no I/O, no registry, no daemon state — so
it is trivially unit-testable. ``VerbRouter.route`` owns the wiring: it strips
the suffix with :func:`parse_repeat_suffix`, recursively routes the base
phrase, then fans the resulting plan out with :func:`expand_repeat`.
"""

from __future__ import annotations

from .plan import Plan, ToolCall

# Settle gap inserted between repetitions, mirroring chain's ``INTER_STEP_MS``
# so repeated keystrokes / clicks land as discrete events instead of a single
# coalesced one.
REPEAT_SETTLE_MS: int = 255

# Upper bound on a single utterance's repeat count. Guards against a
# mistranscription (e.g. a large number heard before "times") firing a runaway
# burst of real input. Counts above this are rejected so the router miss-chimes
# rather than hammering the foreground app.
MAX_REPEAT: int = 50

# Standalone multiplier words that carry their own count with no "times" token.
_MULTIPLIER_WORDS: dict[str, int] = {
    "once": 1,
    "twice": 2,
    "thrice": 3,
}

# Number words usable immediately before "time" / "times".
_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}

_TIMES_WORDS: frozenset[str] = frozenset({"time", "times"})

# Synthetic router-intercept step names are consumed by the daemon, not the
# dispatcher (``__picker.open``, ``__dictation.start``). Repeating them is
# meaningless, so a base plan containing any such step cannot be expanded.
_SYNTHETIC_PREFIX = "__"


def _clean(token: str) -> str:
    """Lowercase a token and strip trailing sentence punctuation."""
    return token.lower().strip(".,!?")


def _word_to_int(token: str) -> int | None:
    """Return the integer value of a number *token*, or ``None``.

    Accepts spelled-out numbers (``"two"``) and digit strings (``"2"``).
    """
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def parse_repeat_suffix(text: str) -> tuple[str, int] | None:
    """Split a trailing repeat modifier off *text*.

    Returns ``(base_text, count)`` when *text* ends in a recognised repeat
    modifier and a non-empty base phrase precedes it. The base phrase keeps its
    original casing (so ``"type Hello twice"`` yields ``("type Hello", 2)`` and
    the typed text is preserved).

    Returns ``None`` — non-destructively, so the caller falls back to routing
    the full original text — when:

      * *text* does not end in a repeat modifier;
      * the base phrase would be empty (``"twice"`` on its own);
      * the ``"<n> time(s)"`` form has no recognised number before it
        (``"end times"`` is left alone); or
      * the parsed count falls outside ``[1, MAX_REPEAT]``.
    """
    tokens = text.split()
    if not tokens:
        return None

    last = _clean(tokens[-1])

    if last in _MULTIPLIER_WORDS:
        # Form 1: standalone multiplier ("twice", "thrice", "once").
        count = _MULTIPLIER_WORDS[last]
        base_tokens = tokens[:-1]
    elif last in _TIMES_WORDS:
        # Form 2: "<number> time(s)".
        if len(tokens) < 2:
            return None
        n = _word_to_int(_clean(tokens[-2]))
        if n is None:
            return None
        count = n
        base_tokens = tokens[:-2]
    else:
        return None

    if not base_tokens:
        return None
    if count < 1 or count > MAX_REPEAT:
        return None

    return " ".join(base_tokens), count


def expand_repeat(base_plan: Plan | None, count: int) -> Plan | None:
    """Fan *base_plan* into *count* sequential executions.

    Returns ``None`` (router miss) when:

      * *base_plan* is ``None`` (the base phrase did not route), or
      * *base_plan* has no steps, or
      * *base_plan* contains a synthetic intercept step (``__picker.open``,
        ``__dictation.start``) which cannot be sensibly repeated.

    When ``count == 1`` the base plan is returned unchanged. Otherwise the base
    plan's steps are concatenated *count* times, separated by a single synthetic
    ``wait`` step (``internal=True``, HUD-suppressed) between repetitions —
    mirroring the chain expansion (ADR 0085). ``Plan.strict`` is inherited from
    the base plan.
    """
    if base_plan is None or not base_plan.steps:
        return None
    if any(step.name.startswith(_SYNTHETIC_PREFIX) for step in base_plan.steps):
        return None
    if count == 1:
        return base_plan

    steps: list[ToolCall] = []
    for i in range(count):
        if i > 0:
            steps.append(
                ToolCall(name="wait", kwargs={"ms": REPEAT_SETTLE_MS}, internal=True)
            )
        steps.extend(base_plan.steps)

    return Plan(
        steps=tuple(steps),
        raw_response={
            "router": "repeat",
            "count": count,
            "base": base_plan.raw_response,
        },
        strict=base_plan.strict,
    )
