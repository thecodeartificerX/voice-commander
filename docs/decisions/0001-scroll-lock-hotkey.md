# ADR 0001: Scroll Lock as Hotkey

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander needs a single global hotkey to toggle recording on and off while the user is doing other work — typing text, using terminals, running applications. The chosen key must not interfere with normal keyboard usage. Printable keys (letters, punctuation such as `\`) would produce characters in whatever window currently has focus, corrupting the user's work. Standard function keys F1–F12 are often captured by the OS, applications, or system utilities. The user's physical keyboard does not include F13–F24 keys that would otherwise be ideal repurposing candidates. A push-to-talk modifier chord (e.g. `Ctrl+Shift+R`) was also considered but the user explicitly preferred a simple single-tap toggle over a held modifier.

## Decision

Use the **Scroll Lock** key as a single-tap toggle: one press starts recording, the next press stops it and triggers transcription. Scroll Lock is present on standard 104/105-key keyboards, is almost entirely unused in modern software, and does not produce a character in any application. Its LED provides a free visual recording-state indicator without any additional UI code. The listener is implemented with `pynput` (see ADR 0002).

## Consequences

### Positive
- Zero interference with typing or terminal input — Scroll Lock produces no character event visible to applications.
- Built-in LED feedback: the Scroll Lock LED turns on when recording starts and off when it stops, giving the user a hardware-level status indicator at no implementation cost.
- Key is universally available on the target platform (Windows, 104-key keyboards).
- Single-tap toggle is the simplest possible UX — no chord timing, no held-key detection.

### Negative
- Users on compact laptop keyboards (60%, 65%, TKL) may not have a physical Scroll Lock key; they would need to remap or use a different key via config.
- The Scroll Lock LED toggling on key press (OS-level behaviour) cannot be suppressed by `pynput`; some users may find the LED blink on the down-stroke slightly distracting before the app takes over.
- Scroll Lock is occasionally captured by remote-desktop or KVM software; conflicts are possible in those environments.

### Neutral
- The hotkey is configurable in `config.toml` (`[hotkey] key = "scroll_lock"`), so this decision establishes the default, not a hard constraint.
- `pynput` represents the key as `Key.scroll_lock`; any future migration to a different listener library must map that symbolic name.

## Alternatives considered

### F13 (or any F13–F24 extended function key)
F13–F24 keys are not captured by any standard application and would be ideal. However, the user's keyboard does not include them, making this option physically unavailable. It remains the recommended alternative for users who do have such keys.

### `Ctrl+\` (or similar modifier chord)
A chord like `Ctrl+\` is easy to type but conflicts with the Unix "send SIGQUIT" binding widely used in terminals (e.g. `bash`, `zsh`, `fish`). Other chords such as `Ctrl+Shift+R` could work but require holding the modifier for the duration of the press-detection and introduce chord-timing complexity in the listener. The user preferred the simplicity of a dedicated toggle key.

### Push-to-talk (hold key = record, release = stop)
Push-to-talk is familiar from gaming and conferencing software and eliminates accidental double-presses. However, it requires holding a key for the full recording duration, which is fatiguing during extended sessions and incompatible with hands-on-keyboard use cases. The user explicitly preferred a latching toggle.

### `Pause/Break` key
`Pause/Break` is similarly obscure and unused, but it lacks an LED and has inconsistent `pynput` key-event delivery on some Windows keyboard drivers. Scroll Lock was preferred for the LED benefit and more reliable event behaviour.

## References

- pynput documentation: https://pynput.readthedocs.io/
