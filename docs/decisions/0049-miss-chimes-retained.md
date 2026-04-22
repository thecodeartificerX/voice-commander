# ADR 0049: Miss Chimes Retained

**Date:** 2026-04-21
**Status:** Accepted
**References:** ADR 0014 (miss-only chimes)

## Context

With the sprite providing visual feedback for all states, we considered removing the audio miss chime.

## Decision

Keep the miss chime (ADR 0014 stays). The sprite **supplements** audio feedback, it does not replace it. Peripheral vision may miss sprite state changes during focused work; the audio chime provides an interrupt-level signal that a command was not understood.

## Consequences

- No changes to `WindowsFeedbackSink` or `winsound` usage.
- Users who prefer visual-only can mute system sounds independently.
