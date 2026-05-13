# ADR 0083 — Bare-Primitive Picker Framework

**Status:** Accepted
**Date:** 2026-05-13
**Spec:** `docs/superpowers/specs/2026-05-13-bare-primitive-picker-design.md`

## Context

A bare primitive verb (e.g. ``focus`` with no argument) was a miss. The user
wants bare verbs to open an inline disambiguation: a numbered modal of
recent targets, the next utterance picks one by number. The same shape
should generalise to future ``tab`` / ``open`` pickers without bespoke
infra per verb.

## Decision

We ship a pluggable framework rooted at ``voice_commander.picker``:

- **D1.** Picker UI is a centred always-on-top modal on the active monitor.
- **D2.** Default item cap is 5 (TOML-configurable per verb).
- **D3.** MRU source is a ``WinEventHook(EVENT_SYSTEM_FOREGROUND)`` ring buffer.
- **D4.** Picker is a sub-state of the running voice session: mic stays hot,
  original session continues after selection.
- **D5.** Pluggable via ``@bare_picker(verb)`` registering on a global
  ``BarePickerRegistry``.
- **D6.** Daemon owns state; the sprite process renders the modal via SSE
  ``picker.open`` / ``picker.close`` events.

The ``VerbRouter`` returns a synthetic ``Plan`` with a single
``__picker.open`` step when it sees a bare verb that has a provider; the
daemon intercepts that step name before dispatch and calls
``PickerSession.open(...)`` instead. While a picker is open the daemon's
pipeline routes the next transcript through ``PickerSession.handle_transcript``
rather than the ``VerbRouter``.

## Consequences

- New package: ``voice_commander.picker`` (registry / session / coerce / mru / types).
- New sprite module: ``voice_sprite.picker_modal``.
- ``focus()`` gains an optional ``_hwnd`` shortcut so picker selections skip
  fuzzy re-resolution.
- One new config section: ``[picker]`` + ``[picker.focus]``.
- New SSE events: ``picker.open``, ``picker.close``.
- One synthetic tool name reserved: ``__picker.open`` (never registered in
  ``ToolRegistry``; intercepted in the pipeline).

## Alternatives considered

- **Vimium-style per-window number badges.** Higher discoverability but
  much more rendering surface area (per-monitor overlay, hwnd-to-screen
  projection, DPI scaling). Deferred.
- **Daemon-rendered Tkinter modal.** Avoids growing sprite responsibility,
  but adds a new GUI lib in the daemon process — breaks the headless-
  daemon invariant.
- **Hardcoded focus-only branch.** Fastest to ship; future pickers would
  duplicate the modal + coerce path. Rejected — boils-the-ocean principle.

## References

- Spec: `docs/superpowers/specs/2026-05-13-bare-primitive-picker-design.md`
- Plan: `docs/superpowers/plans/2026-05-13-bare-primitive-picker.md`
