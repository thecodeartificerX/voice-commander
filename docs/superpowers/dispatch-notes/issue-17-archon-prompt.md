# Archon dispatch prompt — issue #17 (SpriteWindow coverage)

**Workflow:** `archon-fix-github-issue`
**Not dispatched yet.** This file exists so the prompt is ready when Tier
B issues (#11) have merged.

## Why this one needs a custom prompt

Previous commit `daf2f07` already added `tests/unit/test_window.py` with
four passing tests (`on_draw` × 3 + `set_muted`). Issue #17's body still
says *create `tests/unit/test_sprite_window.py`*. Without explicit
guardrails archon will either duplicate the existing test file under a
new name or delete the existing tests during a refactor pass. Both are
regressions.

## Dispatch command

```sh
ARCHON_SUPPRESS_NESTED_CLAUDE_WARNING=1 \
  archon workflow run archon-fix-github-issue \
  "fix issue #17 — see extra notes below before touching any file"
```

## Message to paste into the workflow prompt (copy verbatim)

> Fix issue #17 (SpriteWindow zero coverage) with the following
> hard constraints:
>
> 1. **Do NOT create `tests/unit/test_sprite_window.py`.** The test
>    file for `src/voice_sprite/window.py` already exists at
>    `tests/unit/test_window.py` (added in commit `daf2f07`). Extend
>    that file; do not create a second test file.
>
> 2. **Do NOT delete or rename any test in the existing
>    `tests/unit/test_window.py`.** The four passing tests —
>    `test_on_draw_returns_early_when_no_image`,
>    `test_on_draw_calls_hud_renderer_when_image_set`,
>    `test_on_draw_skips_hud_when_hud_renderer_none`, and
>    `test_set_muted_toggles_flag` — must all remain green after your
>    refactor.
>
> 3. **Extraction pattern.** Per the issue body, extract rendering math
>    (region selection, sprite position, label placement) into pure
>    functions inside `src/voice_sprite/window.py` (or a new sibling
>    module). Leave the pyglet `Window` subclass as a thin shell that
>    calls the pure functions. Pure functions get unit tests; the shell
>    keeps the existing `__new__`-bypass tests.
>
> 4. **Coverage target.** After the refactor, coverage for
>    `src/voice_sprite/window.py` must rise meaningfully (file was
>    removed from the coverage-omit list in `daf2f07`, so this is
>    measurable). Aim for >= 70 % line coverage on the module.
>
> 5. **No changes to `pyproject.toml`.** The coverage-omit entry for
>    `src/voice_sprite/window.py` is already gone.
>
> 6. **Validator + existing test suite must pass.** Run
>    `uv run voice-commander --validate` and `uv run pytest -q --no-cov`
>    before opening the PR. Report any pre-existing failures (e.g.
>    `test_hotkey.py::test_toggle_fires_on_scroll_lock`) separately —
>    don't try to fix them as part of this PR.
>
> 7. **Scope.** Touch `src/voice_sprite/window.py` and
>    `tests/unit/test_window.py` only, plus whatever new pure-function
>    module you introduce. No edits to other sprite modules, daemon,
>    or configs.

## Post-merge checklist

- [ ] Confirm pre-existing `test_window.py` tests still pass.
- [ ] Spot-check the new pure-function tests do not call real pyglet / GL.
- [ ] Note module coverage delta in the PR description.
